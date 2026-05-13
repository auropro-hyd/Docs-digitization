"""Compliance review API routes.

POST /{doc_id}/run        — launch compliance audit as background task
GET  /{doc_id}/report     — return full ComplianceReport JSON
GET  /{doc_id}/status     — return current run status + progress
POST /{doc_id}/findings/{finding_id}/resolve — mark finding resolved
POST /{doc_id}/findings/{finding_id}/review  — HITL review (approve/reject/modify)
GET  /{doc_id}/hitl-summary                  — summary of HITL review status
GET  /{doc_id}/export     — export client-aligned compliance report (PDF / HTML / MD)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from app.compliance.models import ComplianceReport
from app.compliance.report_renderer.builder import build_report_document
from app.compliance.report_renderer.mitigation import (
    SynthesisResult,
    synthesize_mitigations,
)
from app.compliance.report_renderer.render_html import render_html
from app.compliance.report_renderer.render_md import render_md
from app.compliance.report_renderer.render_pdf import PdfRenderError, render_pdf
from app.compliance.report_renderer.types import report_document_to_dict
from app.config.container import get_container
from app.config.settings import get_settings
from app.core.task_manager import task_manager

router = APIRouter()
logger = logging.getLogger(__name__)

_COMPLIANCE_LOCK = "compliance_running.lock"
_COMPLIANCE_RESULT = "compliance_result.json"

ALL_AGENTS = ["alcoa", "gmp", "checklist", "sop", "reconciliation"]

AGENT_LABELS: dict[str, str] = {
    "alcoa": "ALCOA+",
    "gmp": "GMP Validation",
    "checklist": "Checklist Review",
    "sop": "SOP Compliance",
    "reconciliation": "Cross-Page Reconciliation",
}


class RunComplianceRequest(BaseModel):
    agents: list[str] = Field(
        default_factory=lambda: list(ALL_AGENTS),
        description="Which compliance agents to run. Defaults to all.",
    )


def _doc_dir(doc_id: str) -> Path:
    settings = get_settings()
    d = Path(settings.storage.base_path) / doc_id
    if not d.exists():
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return d


def _load_report(doc_id: str) -> dict | None:
    d = _doc_dir(doc_id)
    result_path = d / _COMPLIANCE_RESULT
    if not result_path.exists():
        return None
    return json.loads(result_path.read_text(encoding="utf-8"))


def _deduction_weights(report_data: dict) -> dict[str, int]:
    weights = report_data.get("score_methodology", {}).get("deduction_weights", {})
    default = {
        "critical": 10,
        "major": 5,
        "minor": 2,
        "observation": 1,
    }
    if not isinstance(weights, dict):
        return default
    merged = dict(default)
    for k, v in weights.items():
        try:
            merged[str(k).lower()] = int(v)
        except Exception:
            continue
    return merged


# Known HITL state values. Any other string (or a missing field) is
# classified as ``unknown`` by _normalize_hitl_status below — we never
# silently impute ``auto_approved`` (FR-013).
_KNOWN_HITL_STATUSES = frozenset(
    {
        "auto_approved",
        "system_confirmed",
        "needs_review",
        "user_approved",
        "user_rejected",
        "user_modified",
        "unknown",
    }
)


def _normalize_hitl_status(raw: object) -> str:
    if isinstance(raw, str) and raw in _KNOWN_HITL_STATUSES:
        return raw
    return "unknown"


def _score_from_findings(
    findings: list[dict],
    weights: dict[str, int],
    *,
    include_unknown: bool = False,
) -> dict:
    """Compute the review-adjusted score.

    ``user_rejected`` findings and resolved findings contribute no
    penalty. Findings with an unparseable or missing ``hitl_status`` are
    classified ``unknown``; by default they are excluded from penalty
    (defence-in-depth — we refuse to score what we cannot trust). Set
    ``include_unknown=True`` to include them (e.g. for an operator CLI
    that wants a pessimistic view).
    """

    penalties_by_severity = {k: 0 for k in ["critical", "major", "minor", "observation"]}
    entries: list[dict] = []
    total_penalty = 0
    included = 0
    unknown_skipped = 0

    for f in findings:
        status = _normalize_hitl_status(f.get("hitl_status"))
        if status == "user_rejected" or f.get("resolved", False):
            continue
        if status == "unknown" and not include_unknown:
            unknown_skipped += 1
            continue
        sev = str(f.get("severity", "observation")).lower()
        penalty = int(weights.get(sev, 1))
        total_penalty += penalty
        penalties_by_severity[sev] = penalties_by_severity.get(sev, 0) + penalty
        included += 1
        entries.append({
            "finding_id": f.get("finding_id"),
            "rule_id": f.get("rule_id"),
            "severity": sev,
            "hitl_status": status,
            "penalty": penalty,
        })

    return {
        "score": round(max(0.0, 100.0 - float(total_penalty)), 1),
        "total_penalty": total_penalty,
        "included_findings": included,
        "unknown_skipped": unknown_skipped,
        "penalties_by_severity": penalties_by_severity,
        "penalty_entries": entries,
    }


def _recompute_review_adjusted_scores(report_data: dict) -> None:
    """Deterministically update review-adjusted scoring fields in-place."""
    weights = _deduction_weights(report_data)
    methodology = report_data.setdefault("score_methodology", {})
    methodology.setdefault(
        "review_adjusted_formula",
        "review_adjusted_score = max(0, 100 - sum(finding penalties)); user_rejected findings contribute 0 penalty",
    )
    methodology.setdefault("policy", {
        "not_applicable": "excluded from denominator",
        "uncertain": "counted as non-compliant in model score",
        "retry_exhausted_or_error": "excluded from denominator",
        "review_adjustment": "severity-weight penalties from non-rejected findings",
    })

    agent_rows = []
    for ar in report_data.get("agent_reports", []):
        model_score = float(ar.get("model_score", ar.get("score", 100.0)))
        ar["model_score"] = round(model_score, 1)
        ar["score"] = ar["model_score"]  # preserve model score in legacy key
        dec = _score_from_findings(ar.get("findings", []), weights)
        ar["review_adjusted_score"] = dec["score"]
        ar["score_decomposition"] = dec
        agent_rows.append({
            "agent": ar.get("agent"),
            "total_rules": int(ar.get("total_rules", 0) or 0),
            "model_score": ar["model_score"],
            "review_adjusted_score": ar["review_adjusted_score"],
            "total_penalty": dec["total_penalty"],
        })

    model_overall = float(report_data.get("model_score", report_data.get("overall_score", 0.0)))
    report_data["model_score"] = round(model_overall, 1)
    report_data["overall_score"] = report_data["model_score"]  # preserve legacy key

    weight_sum = sum(max(0, r["total_rules"]) for r in agent_rows)
    if weight_sum > 0:
        review_score = sum(
            r["review_adjusted_score"] * max(0, r["total_rules"])
            for r in agent_rows
        ) / weight_sum
    elif agent_rows:
        review_score = sum(r["review_adjusted_score"] for r in agent_rows) / len(agent_rows)
    else:
        review_score = report_data["model_score"]

    report_data["review_adjusted_score"] = round(float(review_score), 1)
    report_data["score_decomposition"] = {
        "policy": methodology.get("policy", {}),
        "agent_scores": agent_rows,
        "overall_review_adjusted_score": report_data["review_adjusted_score"],
    }


# ── POST /run ─────────────────────────────────────────────────


@router.get("/agents")
async def list_available_agents():
    """Return the list of available compliance agents with display labels."""
    return [
        {"id": agent_id, "label": AGENT_LABELS.get(agent_id, agent_id)}
        for agent_id in ALL_AGENTS
    ]


@router.post("/{doc_id}/run")
async def run_compliance_review(
    doc_id: str,
    body: RunComplianceRequest | None = None,
):
    """Trigger a compliance review for a processed document."""
    d = _doc_dir(doc_id)

    result_path = d / "result.json"
    if not result_path.exists():
        raise HTTPException(
            status_code=400,
            detail="Document must be processed before running compliance review.",
        )

    lock_path = d / _COMPLIANCE_LOCK
    if lock_path.exists():
        import time
        age = time.time() - lock_path.stat().st_mtime
        if age > _STALE_LOCK_SECONDS:
            lock_path.unlink(missing_ok=True)
            logger.warning("Cleaned stale lock before re-run for %s (%.0fs old)", doc_id, age)
        else:
            raise HTTPException(status_code=409, detail="Compliance review already running.")

    selected = (body.agents if body else None) or list(ALL_AGENTS)
    invalid = [a for a in selected if a not in ALL_AGENTS]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown agent(s): {', '.join(invalid)}. Valid: {', '.join(ALL_AGENTS)}",
        )

    lock_path.write_text("running", encoding="utf-8")

    task_key = f"compliance:{doc_id}"
    task_manager.spawn(task_key, _run_compliance_pipeline(doc_id, d, selected), replace=True)

    return {"status": "started", "doc_id": doc_id, "agents": selected}


async def _run_compliance_pipeline(doc_id: str, doc_dir: Path, selected_agents: list[str] | None = None) -> None:
    """Background task that runs the full compliance pipeline."""
    lock_path = doc_dir / _COMPLIANCE_LOCK
    try:
        result_path = doc_dir / "result.json"
        data = json.loads(result_path.read_text(encoding="utf-8"))

        extractions: list[dict] = data.get("extractions", [])
        filename = data.get("filename", "")
        total_pages = data.get("total_pages", 0)
        key_value_pairs = data.get("key_value_pairs", [])

        if not extractions:
            raw_md: dict = data.get("raw_markdown", {})
            for page_key, md in sorted(raw_md.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
                page_num = int(page_key) if str(page_key).isdigit() else 0
                extractions.append({"page_num": page_num, "markdown": md})

        # Apply HITL corrections: overlay user-edited content from
        # component_decisions into extractions so compliance evaluates
        # the corrected text, not the original OCR output.
        comp_decisions: dict = data.get("component_decisions", {})
        if comp_decisions:
            raw_md_overlay: dict = data.get("raw_markdown", {})
            for ext in extractions:
                pn = ext.get("page_num", 0)
                content_key = f"p{pn}-content"
                decision = comp_decisions.get(content_key, {})
                if decision.get("action") == "edit" and decision.get("value"):
                    ext["markdown"] = decision["value"]
                elif str(pn) in raw_md_overlay:
                    # Also pick up any raw_markdown overrides from the
                    # page-level edit endpoint (POST .../pages/{n}/edit)
                    ext["markdown"] = raw_md_overlay[str(pn)]

                # Overlay edited KV field values
                for kv in ext.get("key_value_pairs", []):
                    kv_key = kv.get("component_id", "")
                    kv_decision = comp_decisions.get(kv_key, {})
                    if kv_decision.get("action") == "edit" and kv_decision.get("value"):
                        kv["value"] = kv_decision["value"]

        from app.workflow.compliance_graph import run_compliance_pipeline

        await run_compliance_pipeline(
            doc_id=doc_id,
            extractions=extractions,
            filename=filename,
            total_pages=total_pages,
            key_value_pairs=key_value_pairs,
            selected_agents=selected_agents,
        )

    except asyncio.CancelledError:
        logger.info("Compliance pipeline cancelled for %s", doc_id)
        # Notify the frontend that the run was killed mid-flight so
        # the UI can show an actionable "interrupted" state instead
        # of going silent forever. Common triggers: an uvicorn
        # ``--reload`` rewriting the worker process while a long
        # audit is running, an explicit ``DELETE /run``, or a task-
        # manager shutdown during graceful drain. Broadcast happens
        # before the shutdown drain completes — the WS adapter's
        # ``send_update`` is non-blocking, and the lifespan waits for
        # in-flight tasks before closing the loop.
        from app.api.websocket import manager as ws_manager

        try:
            await ws_manager.broadcast(doc_id, {
                "type": "compliance_progress",
                "phase": "cancelled",
                "status": "cancelled",
                "label": "Compliance audit was interrupted. Please re-run.",
            })
        except Exception:  # pragma: no cover — broadcast is best-effort
            # If the WS connection is also being torn down (likely on
            # a reload), the broadcast may fail. The lock-file cleanup
            # in ``finally`` is what matters; the UI will reconcile
            # next time it polls ``/status``.
            pass
        # Re-raise so asyncio's task-cancellation contract is honoured —
        # callers waiting on the task see CancelledError, not a normal
        # return that would mask the cancel.
        raise
    except Exception:
        logger.exception("Compliance pipeline failed for %s", doc_id)
        from app.api.websocket import manager as ws_manager

        await ws_manager.broadcast(doc_id, {
            "type": "compliance_progress",
            "phase": "error",
            "status": "error",
            "label": "Compliance audit failed. Please try again.",
        })
    finally:
        lock_path.unlink(missing_ok=True)


# ── GET /report ───────────────────────────────────────────────


@router.get("/{doc_id}/report")
async def get_compliance_report(doc_id: str):
    """Return the full compliance report."""
    report = _load_report(doc_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No compliance report found. Run an audit first.")
    _recompute_review_adjusted_scores(report)
    return report


# ── GET /status ───────────────────────────────────────────────


_STALE_LOCK_SECONDS = 600  # 10 minutes


@router.get("/{doc_id}/status")
async def get_compliance_status(doc_id: str):
    """Return the current compliance run status."""
    d = _doc_dir(doc_id)

    lock_path = d / _COMPLIANCE_LOCK
    result_path = d / _COMPLIANCE_RESULT

    task_alive = task_manager.is_running(f"compliance:{doc_id}")

    if lock_path.exists():
        if not task_alive:
            import time
            age = time.time() - lock_path.stat().st_mtime
            if age > _STALE_LOCK_SECONDS:
                lock_path.unlink(missing_ok=True)
                logger.warning("Cleaned stale compliance lock for %s (%.0fs old)", doc_id, age)
            else:
                return {"status": "running", "doc_id": doc_id}
        else:
            return {"status": "running", "doc_id": doc_id}
    if result_path.exists():
        return {"status": "complete", "doc_id": doc_id}
    return {"status": "idle", "doc_id": doc_id}


@router.delete("/{doc_id}/run")
async def cancel_compliance_run(doc_id: str):
    """Force-cancel a stuck compliance run by cancelling the task and removing the lock."""
    d = _doc_dir(doc_id)
    lock_path = d / _COMPLIANCE_LOCK
    was_running = lock_path.exists()

    cancelled = task_manager.cancel(f"compliance:{doc_id}")

    lock_path.unlink(missing_ok=True)
    logger.info("Force-cancelled compliance run for %s (was_running=%s, task_cancelled=%s)", doc_id, was_running, cancelled)
    return {"status": "cancelled", "was_running": was_running, "doc_id": doc_id}


# ── POST /findings/{finding_id}/resolve ───────────────────────


@router.post("/{doc_id}/findings/{finding_id}/resolve")
async def resolve_finding(doc_id: str, finding_id: str):
    """Toggle the resolved status of a specific finding."""
    d = _doc_dir(doc_id)
    result_path = d / _COMPLIANCE_RESULT
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found.")

    report_data = json.loads(result_path.read_text(encoding="utf-8"))

    # Find the finding once to determine current state, then apply the toggled value everywhere
    current_resolved = None
    for finding in report_data.get("findings", []):
        if finding.get("finding_id") == finding_id:
            current_resolved = finding.get("resolved", False)
            break

    if current_resolved is None:
        raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found.")

    new_resolved = not current_resolved

    for finding in report_data.get("findings", []):
        if finding.get("finding_id") == finding_id:
            finding["resolved"] = new_resolved
            break

    for ar in report_data.get("agent_reports", []):
        for finding in ar.get("findings", []):
            if finding.get("finding_id") == finding_id:
                finding["resolved"] = new_resolved
                break

    _recompute_review_adjusted_scores(report_data)
    result_path.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")

    return {
        "finding_id": finding_id,
        "resolved": new_resolved,
        "review_adjusted_score": report_data.get("review_adjusted_score"),
    }


# ── POST /findings/{finding_id}/review (HITL) ─────────────────


class HITLReviewRequest(BaseModel):
    action: str = Field(
        ...,
        description="One of: approve, reject, modify, reset",
        pattern="^(approve|reject|modify|reset)$",
    )
    note: str = Field("", description="Optional reviewer note")
    modified_severity: str | None = Field(None, description="New severity if action=modify")
    modified_description: str | None = Field(None, description="New description if action=modify")


@router.post("/{doc_id}/findings/{finding_id}/review")
async def review_finding(doc_id: str, finding_id: str, body: HITLReviewRequest):
    """HITL review: approve, reject, or modify a finding."""
    from datetime import datetime

    d = _doc_dir(doc_id)
    result_path = d / _COMPLIANCE_RESULT
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found.")

    report_data = json.loads(result_path.read_text(encoding="utf-8"))

    action_map = {
        "approve": "user_approved",
        "reject": "user_rejected",
        "modify": "user_modified",
        "reset": "needs_review",
    }
    new_hitl_status = action_map[body.action]
    now_iso = datetime.now(UTC).isoformat()

    def _update_finding(f: dict) -> bool:
        if f.get("finding_id") != finding_id:
            return False
        f["hitl_status"] = new_hitl_status
        f["hitl_note"] = "" if body.action == "reset" else body.note
        f["hitl_reviewed_at"] = now_iso if body.action != "reset" else None
        if body.action == "reject":
            f["resolved"] = True
        if body.action == "reset":
            f["resolved"] = False
        if body.action == "modify":
            if body.modified_severity:
                f["severity"] = body.modified_severity
            if body.modified_description:
                f["description"] = body.modified_description
        return True

    found = False
    for finding in report_data.get("findings", []):
        if _update_finding(finding):
            found = True
            break

    for ar in report_data.get("agent_reports", []):
        for finding in ar.get("findings", []):
            _update_finding(finding)

    if not found:
        raise HTTPException(status_code=404, detail=f"Finding {finding_id} not found.")

    _recompute_review_adjusted_scores(report_data)
    result_path.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")

    updated = next(
        (f for f in report_data.get("findings", []) if f["finding_id"] == finding_id),
        {},
    )
    # Return the recomputed scores so the UI can update its scorecards
    # immediately without a full report refetch (otherwise the displayed
    # score doesn't budge after a reject — the fix for the
    # "score-not-improving" client issue).
    agent_scores = [
        {
            "agent": ar.get("agent"),
            "model_score": ar.get("model_score"),
            "review_adjusted_score": ar.get("review_adjusted_score"),
        }
        for ar in report_data.get("agent_reports", [])
    ]
    return {
        "finding_id": finding_id,
        "hitl_status": updated.get("hitl_status"),
        "hitl_note": updated.get("hitl_note"),
        "hitl_reviewed_at": updated.get("hitl_reviewed_at"),
        "severity": updated.get("severity"),
        "resolved": updated.get("resolved"),
        "model_score": report_data.get("model_score"),
        "review_adjusted_score": report_data.get("review_adjusted_score"),
        "overall_score": report_data.get("overall_score"),
        "agent_scores": agent_scores,
    }


# ── GET /hitl-summary ─────────────────────────────────────────


@router.get("/{doc_id}/hitl-summary")
async def hitl_summary(doc_id: str):
    """Return a summary of HITL review status for a compliance report."""
    report = _load_report(doc_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No compliance report found.")

    findings = report.get("findings", [])
    counts = {
        "total": len(findings),
        "auto_approved": 0,
        "needs_review": 0,
        "user_approved": 0,
        "user_rejected": 0,
        "user_modified": 0,
    }
    for f in findings:
        status = f.get("hitl_status", "auto_approved")
        if status in counts:
            counts[status] += 1

    counts["reviewed"] = counts["user_approved"] + counts["user_rejected"] + counts["user_modified"]
    counts["pending_review"] = counts["needs_review"]

    return counts


# ── GET /export ───────────────────────────────────────────────


def _slugify_filename_part(value: str) -> str:
    """Return a filesystem-safe, lowercase slug for filenames."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "agent"


# Cover-page labels the BPCR/BMR form factor uses for the
# identifiers Akhilesh expects in the report header. Mapped from
# the raw OCR key to the canonical label the renderer surfaces.
_DOC_METADATA_KEYS: dict[str, str] = {
    "product name": "Product",
    "product": "Product",
    "batch no": "Batch No",
    "batch no.": "Batch No",
    "batch number": "Batch No",
    "bpcr number": "BPCR Number",
    "batch size": "Batch Size",
    "mpcr no.": "MPCR No.",
    "mpcr no": "MPCR No.",
    "revision number": "Revision Number",
    "stage": "Stage",
}


def _extract_doc_metadata(doc_dir: Path) -> dict[str, str]:
    """Pull product / batch / BPCR-style identifiers off the OCR
    ``result.json``.

    The compliance pipeline doesn't capture these fields on
    ``ComplianceReport`` — they live as key-value pairs in the OCR
    output. We surface them in the report header by reading from
    the earliest page that carries each label (typically page 1
    where the cover sheet lives). Empty values are skipped so a
    later page's populated value wins.

    Returns an empty dict when ``result.json`` is missing or
    unparseable — the renderer falls back to the default ``-``
    placeholders.
    """

    result_path = doc_dir / "result.json"
    if not result_path.exists():
        return {}
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read OCR result.json for metadata at %s", doc_dir)
        return {}

    # Track the earliest page each label was found on so the cover
    # sheet wins over later-page restatements.
    by_label: dict[str, tuple[int, str]] = {}
    for kv in data.get("key_value_pairs", []) or []:
        raw_key = (kv.get("key") or "").strip().lower()
        label = _DOC_METADATA_KEYS.get(raw_key)
        if not label:
            continue
        value = (kv.get("value") or "").strip()
        if not value:
            continue
        page = int(kv.get("page_num") or 0)
        prev = by_label.get(label)
        if prev is None or page < prev[0]:
            by_label[label] = (page, value)

    return {label: value for label, (_, value) in by_label.items()}


_FORMAT_EXT: dict[str, str] = {"pdf": "pdf", "html": "html", "md": "md"}
_FORMAT_MEDIA: dict[str, str] = {
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}

# Bump this whenever the renderer / template / logo-resolution
# logic changes in a way that would make pre-existing cached
# exports look wrong (e.g. PR #67's logo-anchor fix). New requests
# write to a fresh filename, so the broken cached file becomes an
# orphan and the user gets the corrected output without needing
# to pass ``?nocache=1``.
#
# Disk impact is bounded: one orphan per (format, agent) per
# bump. The synth-endpoint's cache-busting sweep also clears
# anything starting with ``report`` so HITL flows reclaim space
# eventually.
_RENDERER_CACHE_VERSION: str = "v2"


def _cache_filename(delivered_format: str, agent: str | None) -> str:
    """Compose the cache filename for a rendered export. The version
    segment auto-invalidates stale outputs after a renderer fix."""
    agent_suffix = f"_{_slugify_filename_part(agent)}" if agent else ""
    return f"report_{_RENDERER_CACHE_VERSION}{agent_suffix}.{_FORMAT_EXT[delivered_format]}"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file + replace.

    Best-effort: cache write failures are swallowed so they can't
    break the export response (logged for ops visibility).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        logger.exception("Failed to write export cache to %s", path)


@router.get("/{doc_id}/export")
async def export_compliance_report(
    doc_id: str,
    format: str = Query("pdf", pattern="^(pdf|html|md)$"),
    agent: str | None = Query(None, description="Optional agent ID for scoped export."),
    operator: str = Query("System", description="Operator name surfaced in the footer disclaimer."),
    nocache: bool = Query(False, description="Bypass the export cache and re-render."),
):
    """Export the client-aligned compliance report (Spec 008).

    PDF is the default; HTML and Markdown are available for clients
    that can't display PDF. The PDF path falls back to HTML when
    WeasyPrint's native deps aren't loadable on the host — the
    fallback response carries ``X-Render-Fallback: html`` so the
    caller can detect it.

    Per FR-007 the exported artifact carries no score fields; scores
    remain available via ``GET /report`` for the on-screen view.

    Rendered artifacts are cached at
    ``data/documents/{doc_id}/exports/report[_<agent>].{ext}`` and
    served back when the cached file's mtime is newer than
    ``compliance_result.json`` (so HITL edits invalidate the cache
    automatically). Pass ``?nocache=1`` to force a fresh render.
    """

    import time

    doc_dir = _doc_dir(doc_id)
    result_path = doc_dir / _COMPLIANCE_RESULT
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found.")

    stem = Path(doc_id).stem
    # Real filename comes after we load the report; for now use
    # doc_id as a placeholder so we can compute the cache path
    # before parsing.
    cache_dir = doc_dir / "exports"
    # The header pulls Product / Batch No / BPCR Number from OCR
    # ``result.json`` (via ``_extract_doc_metadata``), so a touched
    # OCR file must also invalidate the cache — otherwise a stale
    # export with empty metadata sticks around forever.
    ocr_result_path = doc_dir / "result.json"
    cache_inputs_mtime = result_path.stat().st_mtime
    if ocr_result_path.exists():
        cache_inputs_mtime = max(cache_inputs_mtime, ocr_result_path.stat().st_mtime)

    # ── Cache lookup ──
    cache_path = cache_dir / _cache_filename(format, agent)
    if (
        not nocache
        and cache_path.exists()
        and cache_path.stat().st_mtime >= cache_inputs_mtime
    ):
        body = cache_path.read_bytes()
        report_data = _load_report(doc_id) or {}
        stem = Path(report_data.get("filename", doc_id)).stem
        filename_stem = f"{stem}_compliance"
        if agent:
            filename_stem = f"{filename_stem}_{_slugify_filename_part(agent)}"
        logger.info(
            "compliance.report_rendered",
            extra={
                "doc_id": doc_id,
                "requested_format": format,
                "delivered_format": format,
                "agent_filter": agent,
                "fallback": False,
                "cache": "hit",
                "byte_size": len(body),
            },
        )
        return Response(
            content=body,
            media_type=_FORMAT_MEDIA[format],
            headers={
                "Content-Disposition": f'attachment; filename="{filename_stem}.{_FORMAT_EXT[format]}"',
                "X-Cache": "hit",
            },
        )

    report_data = _load_report(doc_id)
    if report_data is None:
        raise HTTPException(status_code=404, detail="No compliance report found.")
    # Keeps the in-memory dict's score fields current for callers
    # that hit /report; the export pipeline never reads them.
    _recompute_review_adjusted_scores(report_data)

    if agent and not any(
        ar.get("agent") == agent for ar in report_data.get("agent_reports", [])
    ):
        raise HTTPException(status_code=404, detail=f"Agent report not found for '{agent}'.")

    try:
        report = ComplianceReport.model_validate(report_data)
    except ValidationError as exc:
        logger.exception("Stored compliance report failed validation for %s", doc_id)
        raise HTTPException(
            status_code=500,
            detail="Stored compliance report failed schema validation.",
        ) from exc

    settings = get_settings()
    logo_raw = settings.compliance.report_logo_path
    logo_path = Path(logo_raw) if logo_raw else None

    doc = build_report_document(
        report,
        operator=operator,
        product_name=settings.compliance.report_product_name,
        logo_path=logo_path,
        agent_filter=agent,
        metadata_overrides=_extract_doc_metadata(doc_dir),
    )

    stem = Path(report.filename or doc_id).stem
    filename_stem = f"{stem}_compliance"
    if agent:
        filename_stem = f"{filename_stem}_{_slugify_filename_part(agent)}"

    t0 = time.perf_counter()
    delivered_format = format
    fallback = False
    extra_headers: dict[str, str] = {}
    if format == "md":
        body = render_md(doc).encode("utf-8")
    elif format == "html":
        body = render_html(doc).encode("utf-8")
    else:  # format == "pdf"
        try:
            body = render_pdf(doc)
        except PdfRenderError as exc:
            # WeasyPrint native deps missing — degrade gracefully to
            # HTML so the user still gets the report.
            logger.warning("PDF render unavailable for %s, falling back to HTML (%s)", doc_id, exc)
            body = render_html(doc).encode("utf-8")
            delivered_format = "html"
            fallback = True
            extra_headers["X-Render-Fallback"] = "html"
            extra_headers["X-Render-Fallback-Reason"] = "weasyprint_unavailable"

    # Cache the delivered artifact for future requests. Fallback
    # HTML gets cached under report[_agent].html — same path a
    # direct format=html request would hit.
    _atomic_write(cache_dir / _cache_filename(delivered_format, agent), body)

    logger.info(
        "compliance.report_rendered",
        extra={
            "doc_id": doc_id,
            "requested_format": format,
            "delivered_format": delivered_format,
            "agent_filter": agent,
            "fallback": fallback,
            "cache": "miss",
            "rows": doc.stats.row_count,
            "compliant": doc.stats.compliant_count,
            "action_required": doc.stats.action_required_count,
            "needs_attention": doc.stats.needs_attention_count,
            "byte_size": len(body),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    )

    headers = {
        "Content-Disposition": f'attachment; filename="{filename_stem}.{_FORMAT_EXT[delivered_format]}"',
        "X-Cache": "miss",
        **extra_headers,
    }
    return Response(
        content=body,
        media_type=_FORMAT_MEDIA[delivered_format],
        headers=headers,
    )


# ── GET /report-rows ──────────────────────────────────────────


@router.get("/{doc_id}/report-rows")
async def get_report_rows(
    doc_id: str,
    agent: str | None = Query(None, description="Optional agent ID for scoped view."),
    operator: str = Query("System", description="Operator name (cosmetic; surfaced in footer)."),
):
    """JSON view of the client-aligned report — feeds the frontend
    rule table without re-deriving the shape in TypeScript.

    Same transform as ``/export`` runs: scores are intentionally
    NOT in the response (the on-screen scorecard reads from
    ``/report`` separately). No file caching — the frontend keeps
    its own React Query cache and the transform is cheap.
    """

    report_data = _load_report(doc_id)
    if report_data is None:
        raise HTTPException(status_code=404, detail="No compliance report found.")
    _recompute_review_adjusted_scores(report_data)

    if agent and not any(
        ar.get("agent") == agent for ar in report_data.get("agent_reports", [])
    ):
        raise HTTPException(status_code=404, detail=f"Agent report not found for '{agent}'.")

    try:
        report = ComplianceReport.model_validate(report_data)
    except ValidationError as exc:
        logger.exception("Stored compliance report failed validation for %s", doc_id)
        raise HTTPException(
            status_code=500,
            detail="Stored compliance report failed schema validation.",
        ) from exc

    settings = get_settings()
    doc = build_report_document(
        report,
        operator=operator,
        product_name=settings.compliance.report_product_name,
        agent_filter=agent,
        metadata_overrides=_extract_doc_metadata(_doc_dir(doc_id)),
    )
    return report_document_to_dict(doc)


# ── GET /preview ──────────────────────────────────────────────


@router.get("/{doc_id}/preview")
async def preview_compliance_report(
    doc_id: str,
    agent: str | None = Query(None, description="Optional agent ID for scoped preview."),
    operator: str = Query("System", description="Operator name surfaced in the footer disclaimer."),
):
    """Render the compliance report inline (for iframe embedding).

    Same renderer + cache as ``/export?format=pdf`` — the only
    difference is ``Content-Disposition: inline`` so the browser
    renders the PDF in-page rather than offering it as a download.
    Falls back to HTML when WeasyPrint native deps are unavailable.
    """

    doc_dir = _doc_dir(doc_id)
    result_path = doc_dir / _COMPLIANCE_RESULT
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found.")

    cache_dir = doc_dir / "exports"
    cache_path = cache_dir / _cache_filename("pdf", agent)
    ocr_result_path = doc_dir / "result.json"
    cache_inputs_mtime = result_path.stat().st_mtime
    if ocr_result_path.exists():
        cache_inputs_mtime = max(cache_inputs_mtime, ocr_result_path.stat().st_mtime)
    if (
        cache_path.exists()
        and cache_path.stat().st_mtime >= cache_inputs_mtime
    ):
        return Response(
            content=cache_path.read_bytes(),
            media_type=_FORMAT_MEDIA["pdf"],
            headers={"Content-Disposition": "inline", "X-Cache": "hit"},
        )

    report_data = _load_report(doc_id)
    if report_data is None:
        raise HTTPException(status_code=404, detail="No compliance report found.")
    _recompute_review_adjusted_scores(report_data)

    if agent and not any(
        ar.get("agent") == agent for ar in report_data.get("agent_reports", [])
    ):
        raise HTTPException(status_code=404, detail=f"Agent report not found for '{agent}'.")

    try:
        report = ComplianceReport.model_validate(report_data)
    except ValidationError as exc:
        logger.exception("Stored compliance report failed validation for %s", doc_id)
        raise HTTPException(
            status_code=500,
            detail="Stored compliance report failed schema validation.",
        ) from exc

    settings = get_settings()
    logo_raw = settings.compliance.report_logo_path
    logo_path = Path(logo_raw) if logo_raw else None
    doc = build_report_document(
        report,
        operator=operator,
        product_name=settings.compliance.report_product_name,
        logo_path=logo_path,
        agent_filter=agent,
        metadata_overrides=_extract_doc_metadata(doc_dir),
    )

    try:
        body = render_pdf(doc)
        media_type = _FORMAT_MEDIA["pdf"]
        cache_format = "pdf"
        extra_headers: dict[str, str] = {}
    except PdfRenderError as exc:
        logger.warning("PDF preview unavailable for %s, falling back to HTML (%s)", doc_id, exc)
        body = render_html(doc).encode("utf-8")
        media_type = _FORMAT_MEDIA["html"]
        cache_format = "html"
        extra_headers = {
            "X-Render-Fallback": "html",
            "X-Render-Fallback-Reason": "weasyprint_unavailable",
        }

    _atomic_write(cache_dir / _cache_filename(cache_format, agent), body)

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": "inline", "X-Cache": "miss", **extra_headers},
    )


# ── POST /mitigation/synthesize ──────────────────────────────


class MitigationSynthesizeRequest(BaseModel):
    rule_ids: list[str] | None = Field(
        default=None,
        description="When set, only findings matching one of these "
        "rule IDs are synthesised. Otherwise every needs-mitigation "
        "finding is processed.",
    )
    force: bool = Field(
        default=False,
        description="Re-synthesise even when the finding already has "
        "a rule-author recommendation or a cached mitigation_text. "
        "Operators use this to refresh stale text after rule edits.",
    )


def _summarise_synthesis(results: list[SynthesisResult]) -> dict:
    """Group per-finding results into a stable response shape."""
    counts: dict[str, int] = {}
    total_cost = 0.0
    total_duration = 0.0
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        total_cost += r.cost_estimate_usd
        total_duration += r.duration_ms
    return {
        "counts": counts,
        "cost_estimate_usd": round(total_cost, 6),
        "duration_ms": round(total_duration, 1),
        "per_finding": [
            {
                "rule_id": r.rule_id,
                "finding_id": r.finding_id,
                "agent": r.agent,
                "status": r.status,
                "cost_estimate_usd": round(r.cost_estimate_usd, 6),
                "duration_ms": r.duration_ms,
                "error": r.error,
            }
            for r in results
        ],
    }


@router.post("/{doc_id}/mitigation/synthesize")
async def synthesize_mitigation(
    doc_id: str,
    body: MitigationSynthesizeRequest | None = None,
):
    """Warm the mitigation cache via the evaluator LLM.

    Walks every non-compliant / uncertain finding lacking both a
    rule-author ``recommendation`` and a cached ``mitigation_text``,
    asking the LLM for one to three sentences of remediation
    guidance. Persists the result back into
    ``compliance_result.json`` via an atomic rename.

    Cost-bounded by ``compliance.mitigation_synth_cost_ceiling_usd``:
    once the next call's estimate would push cumulative spend over
    the ceiling, the run stops and the response reports the count
    of skipped findings.
    """

    body = body or MitigationSynthesizeRequest()
    doc_dir = _doc_dir(doc_id)
    result_path = doc_dir / _COMPLIANCE_RESULT
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found.")

    report_data = json.loads(result_path.read_text(encoding="utf-8"))
    settings = get_settings()
    container = get_container()

    results = await synthesize_mitigations(
        report_data,
        llm=container.compliance_evaluator_llm,
        cost_ceiling_usd=settings.compliance.mitigation_synth_cost_ceiling_usd,
        rule_ids=set(body.rule_ids) if body.rule_ids else None,
        force=body.force,
    )

    summary = _summarise_synthesis(results)

    # Atomic write so a crash mid-write can't leave a half-rendered
    # report on disk. Also invalidates the export cache via mtime.
    tmp = result_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")
    tmp.replace(result_path)

    # Bust the export disk cache so the next /export call re-renders
    # with the freshly-synthesised mitigation text. Removing the
    # files is cheaper + simpler than touching mtimes.
    cache_dir = doc_dir / "exports"
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.name.startswith("report") and not f.name.endswith(".tmp"):
                try:
                    f.unlink()
                except OSError:
                    logger.warning("Could not invalidate cache entry %s", f)

    logger.info(
        "compliance.mitigation_synthesised",
        extra={
            "doc_id": doc_id,
            "force": body.force,
            "rule_ids": body.rule_ids,
            **{f"count_{k}": v for k, v in summary["counts"].items()},
            "cost_estimate_usd": summary["cost_estimate_usd"],
            "duration_ms": summary["duration_ms"],
        },
    )

    return {
        "doc_id": doc_id,
        "force": body.force,
        **summary,
    }


# ── Segmentation endpoints ────────────────────────────────────


@router.get("/{doc_id}/segmentation")
async def get_segmentation(doc_id: str):
    """Return the stored document segmentation."""
    d = _doc_dir(doc_id)
    seg_path = d / "segmentation.json"
    if not seg_path.exists():
        raise HTTPException(status_code=404, detail="No segmentation found. Run compliance audit first.")
    return json.loads(seg_path.read_text(encoding="utf-8"))


@router.put("/{doc_id}/segmentation")
async def update_segmentation(doc_id: str, body: dict):
    """Update segmentation (user edits section boundaries/types)."""
    d = _doc_dir(doc_id)
    seg_path = d / "segmentation.json"
    seg_path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"status": "updated"}


@router.post("/{doc_id}/segment")
async def trigger_segmentation(doc_id: str):
    """Force re-segmentation via LLM."""
    d = _doc_dir(doc_id)
    result_path = d / "result.json"
    if not result_path.exists():
        raise HTTPException(status_code=400, detail="Document must be processed first.")

    task_manager.spawn(f"segmentation:{doc_id}", _run_segmentation(doc_id, d), replace=True)
    return {"status": "started"}


async def _run_segmentation(doc_id: str, doc_dir: Path) -> None:
    """Background task for re-segmentation."""
    try:
        result_path = doc_dir / "result.json"
        data = json.loads(result_path.read_text(encoding="utf-8"))

        extractions = data.get("extractions", [])
        if not extractions:
            raw_md = data.get("raw_markdown", {})
            for pk, md_text in sorted(raw_md.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
                pn = int(pk) if str(pk).isdigit() else 0
                extractions.append({"page_num": pn, "markdown": md_text})

        from app.compliance.segmentation import DocumentSegmenter, store_segmentation
        from app.config.container import get_container

        container = get_container()
        llm = container.compliance_cross_page_llm
        segmenter = DocumentSegmenter(llm)
        seg = await segmenter.segment(
            extractions,
            data.get("key_value_pairs", []),
            data.get("filename", ""),
            data.get("total_pages", len(extractions)),
        )
        store_segmentation(doc_dir, seg)

        from app.api.websocket import manager as ws_manager
        await ws_manager.broadcast(doc_id, {
            "type": "compliance_progress",
            "phase": "segmentation",
            "status": "complete",
            "sections_count": len(seg.sections),
        })
    except Exception:
        logger.exception("Segmentation failed for %s", doc_id)


# ── Discovered rules endpoints ────────────────────────────────


@router.get("/{doc_id}/discovered-rules")
async def get_discovered_rules(doc_id: str):
    """Return auto-discovered cross-page rules for this document."""
    d = _doc_dir(doc_id)
    path = d / "auto_discovered_rules.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{doc_id}/discovered-rules/{index}/promote")
async def promote_discovered_rule(doc_id: str, index: int):
    """Promote an auto-discovered rule to predefined (append to reconciliation_rules.md)."""
    d = _doc_dir(doc_id)
    path = d / "auto_discovered_rules.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No discovered rules found.")

    rules = json.loads(path.read_text(encoding="utf-8"))
    if index < 0 or index >= len(rules):
        raise HTTPException(status_code=404, detail=f"Rule index {index} out of range.")

    rule = rules[index]
    if rule.get("promoted"):
        return {"status": "already_promoted"}

    from app.compliance.rules.registry import get_registry, invalidate_registry

    registry = get_registry()
    sections_semantic = rule.get("sections_semantic", [])
    section_tag = f" [sections: {', '.join(sections_semantic)}]" if sections_semantic else ""

    registry.add_rule(
        agent="reconciliation",
        category="auto_promoted",
        category_display="Auto-Promoted",
        text=rule["description"] + section_tag,
        severity_hint="observation",
    )

    rule["promoted"] = True
    path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
    invalidate_registry()

    return {"status": "promoted", "index": index}
