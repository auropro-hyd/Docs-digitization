"""Compliance review API routes.

POST /{doc_id}/run        — launch compliance audit as background task
GET  /{doc_id}/report     — return full ComplianceReport JSON
GET  /{doc_id}/status     — return current run status + progress
POST /{doc_id}/findings/{finding_id}/resolve — mark finding resolved
POST /{doc_id}/findings/{finding_id}/review  — HITL review (approve/reject/modify)
GET  /{doc_id}/hitl-summary                  — summary of HITL review status
GET  /{doc_id}/export     — export compliance report as HTML or Markdown
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
from pydantic import BaseModel, Field

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


_EXPORT_CSS = """<style>
:root { --bg: #ffffff; --fg: #1a1a2e; --muted: #6b7280; --border: #e5e7eb;
  --critical: #dc2626; --major: #ea580c; --minor: #ca8a04; --observation: #6b7280;
  --success: #16a34a; --info: #2563eb; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Inter', system-ui, -apple-system, sans-serif; max-width: 960px;
  margin: 0 auto; padding: 2rem 1.5rem; color: var(--fg); line-height: 1.6; }
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.2rem; margin-top: 2rem; padding-bottom: 0.5rem; border-bottom: 2px solid var(--border); }
h3 { font-size: 1rem; margin-top: 1.25rem; color: #374151; }
p, li { font-size: 0.9rem; }
.meta { color: var(--muted); font-size: 0.8rem; }
.cover { text-align: center; padding: 2rem 0; border-bottom: 3px solid var(--fg); margin-bottom: 2rem; }
.cover .score-ring { display: inline-block; font-size: 3rem; font-weight: 800; padding: 0.5rem 1rem;
  border-radius: 50%; width: 100px; height: 100px; line-height: 100px; margin: 1rem 0; }
.score-high { background: #dcfce7; color: var(--success); border: 3px solid var(--success); }
.score-med  { background: #fef3c7; color: var(--minor); border: 3px solid var(--minor); }
.score-low  { background: #fef2f2; color: var(--critical); border: 3px solid var(--critical); }
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.85rem; }
th, td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
th { background: #f9fafb; font-weight: 600; }
.finding { border-left: 4px solid var(--muted); padding: 0.75rem 1rem; margin: 0.75rem 0;
  background: #f9fafb; border-radius: 0 6px 6px 0; }
.finding.resolved { border-left-color: var(--success); background: #f0fdf4; }
.finding.severity-critical { border-left-color: var(--critical); background: #fef2f2; }
.finding.severity-major { border-left-color: var(--major); background: #fff7ed; }
.finding.severity-minor { border-left-color: var(--minor); background: #fefce8; }
.finding.severity-observation { border-left-color: var(--observation); background: #f9fafb; }
.sev { font-weight: 700; text-transform: uppercase; font-size: 0.7rem; padding: 2px 6px;
  border-radius: 3px; display: inline-block; }
.sev-critical { background: #fee2e2; color: var(--critical); }
.sev-major { background: #ffedd5; color: var(--major); }
.sev-minor { background: #fef9c3; color: var(--minor); }
.sev-observation { background: #f3f4f6; color: var(--observation); }
.hitl { font-size: 0.75rem; padding: 1px 5px; border-radius: 3px; }
.hitl-needs_review { background: #fef3c7; color: #92400e; }
.hitl-approved { background: #dcfce7; color: #166534; }
.hitl-user_rejected { background: #fee2e2; color: #991b1b; }
.hitl-user_modified { background: #e0e7ff; color: #3730a3; }
.reasoning-block { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: #eff6ff;
  border-left: 3px solid var(--info); border-radius: 0 4px 4px 0; font-size: 0.82rem; }
.evidence-block { margin-top: 0.5rem; padding: 0.5rem 0.75rem; background: #f9fafb;
  border-left: 3px solid var(--minor); border-radius: 0 4px 4px 0; font-style: italic; font-size: 0.82rem; }
.section { margin-top: 1.5rem; }
ol, ul { padding-left: 1.5rem; margin: 0.5rem 0; }
.page-break { page-break-before: always; }
.rule-detail-group { margin-top: 1rem; }
.rule-detail-group h4 { font-size: 0.92rem; margin-bottom: 0.5rem; padding: 4px 8px;
  background: #f3f4f6; border-radius: 4px; display: flex; justify-content: space-between; }
.rule-row { display: flex; gap: 0.5rem; align-items: baseline; padding: 6px 8px;
  border-bottom: 1px solid #f3f4f6; font-size: 0.82rem; }
.rule-row:hover { background: #fafbfc; }
.rule-id { font-weight: 600; white-space: nowrap; min-width: 80px; font-family: 'SF Mono', 'Cascadia Code', monospace; }
.rule-text { flex: 1; color: #374151; }
.rule-pages { color: var(--muted); font-size: 0.75rem; white-space: nowrap; }
.rule-reasoning { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
.st { font-weight: 700; text-transform: uppercase; font-size: 0.65rem; padding: 1px 5px;
  border-radius: 3px; display: inline-block; white-space: nowrap; }
.st-compliant { background: #dcfce7; color: #166534; }
.st-non_compliant { background: #fee2e2; color: #991b1b; }
.st-not_applicable { background: #f3f4f6; color: #6b7280; }
.st-uncertain { background: #fef3c7; color: #92400e; }
.st-error { background: #fce7f3; color: #9d174d; }
.cat-summary { font-size: 0.75rem; color: var(--muted); font-weight: 400; }
@media print { body { max-width: 100%; padding: 1rem; } .page-break { break-before: page; }
  .rule-row { break-inside: avoid; } }
</style>"""


def _format_page_ranges(pages: list) -> str:
    """Collapse consecutive page numbers into ranges for the export."""
    if not pages:
        return ""
    nums = sorted(set(int(p) for p in pages if isinstance(p, int) or (isinstance(p, str) and p.isdigit())))
    if not nums:
        return ""
    ranges: list[str] = []
    start = end = nums[0]
    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            ranges.append(f"{start}" if start == end else f"{start}–{end}")
            start = end = n
    ranges.append(f"{start}" if start == end else f"{start}–{end}")
    return ", ".join(ranges)


def _smart_page_display(pages: list, total_pages: int, status: str) -> str:
    """Human-friendly page display that avoids listing all 185 pages."""
    if not pages:
        if status == "not_applicable":
            return "Document scope"
        return "Document scope"
    n = len(pages)
    if total_pages > 0 and n >= total_pages - 5:
        if status == "not_applicable":
            return "N/A across all pages"
        return "All pages"
    return _format_page_ranges(pages)


_STATUS_LABELS = {
    "compliant": "Compliant",
    "non_compliant": "Non-Compliant",
    "not_applicable": "N/A",
    "uncertain": "Uncertain",
    "error": "Error",
}


def _score_class(score: float) -> str:
    if score >= 75:
        return "score-high"
    if score >= 45:
        return "score-med"
    return "score-low"


def _slugify_filename_part(value: str) -> str:
    """Return a filesystem-safe, lowercase slug for filenames."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "agent"


def _build_agent_scoped_report(report: dict, agent_id: str) -> dict:
    """Return a report payload scoped to a single agent."""
    target = None
    for ar in report.get("agent_reports", []):
        if ar.get("agent") == agent_id:
            target = ar
            break
    if target is None:
        raise HTTPException(status_code=404, detail=f"Agent report not found for '{agent_id}'.")

    scoped = dict(report)
    scoped["agent_reports"] = [target]
    scoped_findings = [f for f in report.get("findings", []) if f.get("agent") == agent_id]
    scoped["findings"] = scoped_findings
    scoped["total_findings"] = len(scoped_findings)
    scoped["severity_counts"] = target.get("severity_counts", {})
    scoped["overall_score"] = target.get("model_score", target.get("score", report.get("overall_score", 0)))
    scoped["model_score"] = target.get("model_score", target.get("score", report.get("overall_score", 0)))
    scoped["review_adjusted_score"] = target.get("review_adjusted_score", scoped["overall_score"])
    scoped["score_decomposition"] = target.get("score_decomposition", {})
    scoped["skipped_agents"] = []

    summary = dict(report.get("executive_summary", {}))
    summary["overall_assessment"] = (
        f"Agent-specific compliance report for "
        f"{target.get('agent_display', AGENT_LABELS.get(agent_id, agent_id))}."
    )
    scoped["executive_summary"] = summary
    return scoped


@router.get("/{doc_id}/export")
async def export_compliance_report(
    doc_id: str,
    format: str = Query("html", pattern="^(md|html)$"),
    agent: str | None = Query(None, description="Optional agent ID for scoped export."),
):
    """Export the compliance report as HTML or Markdown."""
    report = _load_report(doc_id)
    if report is None:
        raise HTTPException(status_code=404, detail="No compliance report found.")
    _recompute_review_adjusted_scores(report)

    export_report = report
    stem = Path(report.get("filename", doc_id)).stem
    filename_stem = f"{stem}_compliance"
    if agent:
        export_report = _build_agent_scoped_report(report, agent)
        agent_slug = _slugify_filename_part(agent)
        filename_stem = f"{filename_stem}_{agent_slug}"

    if format == "md":
        md_content = _build_report_markdown(export_report)
        return Response(
            content=md_content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename_stem}.md"'},
        )

    html_doc = _build_report_html(export_report, stem)
    return Response(
        content=html_doc,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_stem}.html"'},
    )


def _esc(text: object) -> str:
    """HTML escape, safe for None and non-string values."""
    from html import escape
    if text is None:
        return ""
    return escape(str(text))


def _build_report_html(report: dict, stem: str) -> str:
    """Build a professional HTML compliance report."""
    score = report.get("model_score", report.get("overall_score", 0))
    review_score = report.get("review_adjusted_score", score)
    summary = report.get("executive_summary", {})
    sev = report.get("severity_counts", {})
    trail = report.get("audit_trail", {})
    methodology = report.get("score_methodology", {})

    h = []
    h.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    h.append(f"<title>{_esc(stem)} — Compliance Audit Report</title>")
    h.append(_EXPORT_CSS)
    h.append("</head><body>")

    # --- Cover ---
    h.append('<div class="cover">')
    h.append("<h1>Compliance Audit Report</h1>")
    h.append(f'<p class="meta">{_esc(report.get("filename", stem))} &mdash; '
             f'{_esc(report.get("document_type", ""))} &mdash; '
             f'{report.get("total_pages", "?")} pages</p>')
    h.append(f'<p class="meta">Generated: {_esc(str(report.get("generated_at", "")))}</p>')
    h.append(f'<div class="score-ring {_score_class(score)}">{score:.0f}</div>')
    h.append('<p class="meta">Model Score (out of 100)</p>')
    if review_score is not None:
        h.append(f'<p class="meta" style="margin-top:0.35rem">Review-Adjusted Score: <strong>{float(review_score):.1f}</strong>/100</p>')
    h.append("</div>")

    # --- Executive Summary ---
    if summary:
        h.append("<h2>Executive Summary</h2>")
        if summary.get("overall_assessment"):
            h.append(f'<p>{_esc(summary["overall_assessment"])}</p>')

        if summary.get("strengths"):
            h.append("<h3>Strengths</h3><ul>")
            for s in summary["strengths"]:
                h.append(f"<li>{_esc(s)}</li>")
            h.append("</ul>")

        if summary.get("key_risks"):
            h.append("<h3>Key Risks</h3><ul>")
            for r in summary["key_risks"]:
                h.append(f"<li>{_esc(r)}</li>")
            h.append("</ul>")

        if summary.get("priority_actions"):
            h.append("<h3>Priority Actions</h3><ol>")
            for a in summary["priority_actions"]:
                h.append(f"<li>{_esc(a)}</li>")
            h.append("</ol>")

    # --- Severity Distribution ---
    if sev:
        h.append("<h2>Severity Distribution</h2>")
        h.append("<table><tr><th>Severity</th><th>Count</th></tr>")
        for s in ["critical", "major", "minor", "observation"]:
            cnt = sev.get(s, 0)
            if cnt:
                h.append(f'<tr><td><span class="sev sev-{s}">{s.upper()}</span></td><td>{cnt}</td></tr>')
        total = sum(sev.values())
        h.append(f'<tr><th>Total</th><th>{total}</th></tr>')
        h.append("</table>")

    # --- Per-Agent Breakdown ---
    for ar in report.get("agent_reports", []):
        agent_name = ar.get("agent_display", ar.get("agent", "?"))
        h.append(f'<h2 class="page-break">{_esc(agent_name)}</h2>')
        model_score = ar.get("model_score", ar.get("score", "N/A"))
        review_adj = ar.get("review_adjusted_score")
        score_text = f'Model: <strong>{model_score}/100</strong>'
        if review_adj is not None:
            score_text += f' &mdash; Review-adjusted: <strong>{review_adj}/100</strong>'
        h.append(f'<p>{score_text} &mdash; '
                 f'Rules: {ar.get("total_rules", 0)} &mdash; '
                 f'Findings: {ar.get("total_findings", 0)}</p>')

        cat_scores = ar.get("category_scores", [])
        if cat_scores:
            h.append("<table><tr><th>Category</th><th>Score</th>"
                     "<th>Compliant</th><th>Non-Compliant</th><th>N/A</th></tr>")
            for cs in cat_scores:
                h.append(f'<tr><td>{_esc(cs.get("category_display", ""))}</td>'
                         f'<td>{cs.get("score", "")}/100</td>'
                         f'<td>{cs.get("compliant", 0)}</td>'
                         f'<td>{cs.get("non_compliant", 0)}</td>'
                         f'<td>{cs.get("not_applicable", 0)}</td></tr>')
            h.append("</table>")

        findings = ar.get("findings", [])
        if findings:
            h.append(f"<h3>Findings ({len(findings)})</h3>")
            for f in findings:
                sev_cls = f.get("severity", "observation")
                resolved_cls = " resolved" if f.get("resolved") else ""
                hitl = f.get("hitl_status", "")
                hitl_label = {
                    "needs_review": "Needs Review",
                    "auto_approved": "Auto Approved",
                    "user_approved": "Approved",
                    "user_rejected": "Rejected",
                    "user_modified": "Modified",
                }.get(hitl, hitl)
                hitl_cls = "approved" if "approved" in hitl else hitl

                pages_str = _format_page_ranges(f.get("page_numbers", []))

                h.append(f'<div class="finding severity-{sev_cls}{resolved_cls}">')
                hitl_html = f' &mdash; <span class="hitl hitl-{hitl_cls}">{_esc(hitl_label)}</span>' if hitl else ""
                resolved_html = "  &#10003; Resolved" if f.get("resolved") else ""
                h.append(
                    f'<p><strong>{_esc(f.get("rule_id", ""))}</strong> '
                    f'<span class="sev sev-{sev_cls}">{sev_cls.upper()}</span>'
                    f'{hitl_html}{resolved_html}</p>'
                )

                desc = f.get("description", "")
                if desc:
                    h.append(f"<p>{_esc(desc)}</p>")

                reasoning = f.get("reasoning", "")
                if reasoning:
                    h.append(f'<div class="reasoning-block"><strong>Reasoning:</strong> {_esc(reasoning)}</div>')

                evidence = f.get("evidence", "")
                if evidence:
                    h.append(f'<div class="evidence-block"><strong>Evidence:</strong> &ldquo;{_esc(evidence)}&rdquo;</div>')

                rec = f.get("recommendation", "")
                if rec:
                    h.append(f'<p><strong>Recommendation:</strong> {_esc(rec)}</p>')

                if pages_str:
                    h.append(f'<p class="meta">Pages: {pages_str}</p>')

                h.append("</div>")

        # --- Rule-Level Detail ---
        all_evals = ar.get("all_evaluations", [])
        if all_evals:
            total_pages = report.get("total_pages", 0)
            cat_order = [cs.get("category_id", "") for cs in cat_scores]
            by_cat: dict[str, list[dict]] = {}
            for ev in all_evals:
                cat = ev.get("rule_category", "other")
                by_cat.setdefault(cat, []).append(ev)

            for cat in by_cat:
                if cat not in cat_order:
                    cat_order.append(cat)

            cat_display_map = {
                cs.get("category_id", ""): cs.get("category_display", cs.get("category_id", ""))
                for cs in cat_scores
            }

            h.append('<h3 class="page-break">Rule-Level Detail</h3>')
            for cat_id in cat_order:
                rules_in_cat = by_cat.get(cat_id, [])
                if not rules_in_cat:
                    continue
                rules_in_cat.sort(key=lambda r: r.get("rule_id", ""))
                cat_label = _esc(cat_display_map.get(cat_id, cat_id.title()))

                n_comp = sum(1 for r in rules_in_cat if r.get("status") == "compliant")
                n_nc = sum(1 for r in rules_in_cat if r.get("status") == "non_compliant")
                n_na = sum(1 for r in rules_in_cat if r.get("status") == "not_applicable")
                n_unc = sum(1 for r in rules_in_cat if r.get("status") in ("uncertain", "error"))

                h.append('<div class="rule-detail-group">')
                h.append(
                    f'<h4>{cat_label} '
                    f'<span class="cat-summary">{len(rules_in_cat)} rules &mdash; '
                    f'{n_comp} pass, {n_nc} fail, {n_na} N/A'
                    f'{f", {n_unc} uncertain" if n_unc else ""}'
                    f'</span></h4>'
                )

                for rv in rules_in_cat:
                    st = rv.get("status", "unknown")
                    st_label = _STATUS_LABELS.get(st, st.replace("_", " ").title())
                    pages_display = _smart_page_display(
                        rv.get("page_numbers", []), total_pages, st,
                    )
                    rule_text = _esc(rv.get("rule_text", ""))
                    reasoning = rv.get("reasoning", "")

                    h.append('<div class="rule-row">')
                    h.append(f'<span class="rule-id">{_esc(rv.get("rule_id", ""))}</span>')
                    h.append(f'<span class="st st-{st}">{st_label}</span>')
                    h.append(f'<span class="rule-text">{rule_text}</span>')
                    h.append(f'<span class="rule-pages">{_esc(pages_display)}</span>')
                    h.append('</div>')
                    if reasoning:
                        trimmed = reasoning[:250] + ("..." if len(reasoning) > 250 else "")
                        h.append(f'<div class="rule-reasoning" style="padding-left:90px">{_esc(trimmed)}</div>')

                h.append("</div>")

    # --- Skipped Agents ---
    skipped = report.get("skipped_agents", [])
    if skipped:
        h.append("<h2>Skipped Agents</h2><ul>")
        for s in skipped:
            h.append(f'<li><strong>{_esc(s.get("category", ""))}</strong>: {_esc(s.get("reason", ""))}</li>')
        h.append("</ul>")

    # --- Audit Trail ---
    if trail:
        h.append('<h2>Audit Trail &amp; Methodology</h2>')
        h.append("<table>")
        h.append(f'<tr><td>Duration</td><td>{trail.get("duration_seconds", 0):.1f}s</td></tr>')
        h.append(f'<tr><td>LLM Calls</td><td>{trail.get("total_llm_calls", 0)}</td></tr>')
        h.append(f'<tr><td>Rules Evaluated</td><td>{trail.get("total_rules_evaluated", 0)}</td></tr>')
        h.append(f'<tr><td>Evaluator Model</td><td>{_esc(trail.get("evaluator_model", "N/A"))}</td></tr>')
        h.append(f'<tr><td>Orchestrator Model</td><td>{_esc(trail.get("orchestrator_model", "N/A"))}</td></tr>')
        h.append("</table>")

    if methodology:
        h.append(f'<p class="meta" style="margin-top:0.5rem">'
                 f'<strong>Scoring formula:</strong> {_esc(methodology.get("formula", ""))}</p>')

    h.append("</body></html>")
    return "\n".join(h)


def _build_report_markdown(report: dict) -> str:
    """Build a markdown version of the compliance report."""
    lines: list[str] = []
    lines.append(f"# Compliance Audit Report — {report.get('filename', 'Document')}")
    lines.append("")

    summary = report.get("executive_summary", {})
    model_score = report.get("model_score", report.get("overall_score", "N/A"))
    review_score = report.get("review_adjusted_score")
    if review_score is not None:
        lines.append(f"**Model Score: {model_score}/100**  \n**Review-Adjusted Score: {review_score}/100**")
    else:
        lines.append(f"**Overall Score: {model_score}/100**")
    lines.append("")
    if summary.get("overall_assessment"):
        lines.append(f"## Executive Summary\n\n{summary['overall_assessment']}")
        lines.append("")

    if summary.get("strengths"):
        lines.append("### Strengths\n")
        for s in summary["strengths"]:
            lines.append(f"- {s}")
        lines.append("")

    if summary.get("key_risks"):
        lines.append("### Key Risks\n")
        for risk in summary["key_risks"]:
            lines.append(f"- {risk}")
        lines.append("")

    if summary.get("priority_actions"):
        lines.append("### Priority Actions\n")
        for i, action in enumerate(summary["priority_actions"], 1):
            lines.append(f"{i}. {action}")
        lines.append("")

    sev = report.get("severity_counts", {})
    if sev:
        lines.append("## Severity Breakdown\n")
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for s in ["critical", "major", "minor", "observation"]:
            if sev.get(s, 0):
                lines.append(f"| {s.title()} | {sev[s]} |")
        lines.append("")

    for ar in report.get("agent_reports", []):
        lines.append(f"## {ar.get('agent_display', ar.get('agent', '?'))}")
        ar_model = ar.get("model_score", ar.get("score", "N/A"))
        ar_review = ar.get("review_adjusted_score")
        score_line = f"Model Score: **{ar_model}/100**"
        if ar_review is not None:
            score_line += f" | Review-Adjusted: **{ar_review}/100**"
        lines.append(f"\n{score_line} | "
                      f"Rules: {ar.get('total_rules', 0)} | "
                      f"Findings: {ar.get('total_findings', 0)}\n")

        for cs in ar.get("category_scores", []):
            lines.append(f"### {cs.get('category_display', cs.get('category_id', '?'))}")
            lines.append(f"Score: {cs.get('score', 'N/A')}/100 | "
                          f"Compliant: {cs.get('compliant', 0)} | "
                          f"Non-compliant: {cs.get('non_compliant', 0)}\n")

        if ar.get("findings"):
            lines.append("### Findings\n")
            for f in ar["findings"]:
                pages = _format_page_ranges(f.get("page_numbers", []))
                resolved = " [Resolved]" if f.get("resolved") else ""
                hitl = f.get("hitl_status", "")
                lines.append(f"**{f.get('rule_id', '')}** [{f.get('severity', '').upper()}]{resolved}")
                if hitl:
                    lines.append(f"  *Review: {hitl}*")
                desc = f.get("description", "")
                if desc:
                    lines.append(f"  {desc}")
                reasoning = f.get("reasoning", "")
                if reasoning:
                    lines.append(f"  > **Reasoning:** {reasoning}")
                evidence = f.get("evidence", "")
                if evidence:
                    lines.append(f'  > **Evidence:** "{evidence}"')
                rec = f.get("recommendation", "")
                if rec:
                    lines.append(f"  **Recommendation:** {rec}")
                if pages:
                    lines.append(f"  Pages: {pages}")
                lines.append("")

        all_evals = ar.get("all_evaluations", [])
        if all_evals:
            total_pages = report.get("total_pages", 0)
            cat_order = [cs.get("category_id", "") for cs in ar.get("category_scores", [])]
            by_cat: dict[str, list[dict]] = {}
            for ev in all_evals:
                cat = ev.get("rule_category", "other")
                by_cat.setdefault(cat, []).append(ev)
            for cat in by_cat:
                if cat not in cat_order:
                    cat_order.append(cat)

            cat_display_map = {
                cs.get("category_id", ""): cs.get("category_display", cs.get("category_id", ""))
                for cs in ar.get("category_scores", [])
            }

            lines.append("### Rule-Level Detail\n")
            for cat_id in cat_order:
                rules_in_cat = by_cat.get(cat_id, [])
                if not rules_in_cat:
                    continue
                rules_in_cat.sort(key=lambda r: r.get("rule_id", ""))
                cat_label = cat_display_map.get(cat_id, cat_id.title())
                lines.append(f"#### {cat_label} ({len(rules_in_cat)} rules)\n")
                lines.append("| Rule | Status | Rule Text | Pages | Reasoning |")
                lines.append("|------|--------|-----------|-------|-----------|")
                for rv in rules_in_cat:
                    st = rv.get("status", "unknown")
                    st_label = _STATUS_LABELS.get(st, st.replace("_", " ").title())
                    pages_display = _smart_page_display(
                        rv.get("page_numbers", []), total_pages, st,
                    )
                    rule_text = rv.get("rule_text", "")[:80]
                    reasoning = rv.get("reasoning", "")[:120]
                    reasoning = reasoning.replace("|", "/").replace("\n", " ")
                    rule_text = rule_text.replace("|", "/").replace("\n", " ")
                    lines.append(
                        f"| {rv.get('rule_id', '')} | {st_label} "
                        f"| {rule_text} | {pages_display} | {reasoning} |"
                    )
                lines.append("")

    skipped = report.get("skipped_agents", [])
    if skipped:
        lines.append("## Skipped Agents\n")
        for s in skipped:
            lines.append(f"- **{s.get('category', '?')}**: {s.get('reason', '')}")
        lines.append("")

    trail = report.get("audit_trail", {})
    if trail:
        lines.append("## Audit Trail\n")
        lines.append(f"- Duration: {trail.get('duration_seconds', 0):.1f}s")
        lines.append(f"- LLM calls: {trail.get('total_llm_calls', 0)}")
        lines.append(f"- Rules evaluated: {trail.get('total_rules_evaluated', 0)}")
        lines.append(f"- Evaluator model: {trail.get('evaluator_model', 'N/A')}")
        lines.append(f"- Orchestrator model: {trail.get('orchestrator_model', 'N/A')}")

    methodology = report.get("score_methodology", {})
    if methodology:
        lines.append(f"\n*Scoring: {methodology.get('formula', '')}*")

    return "\n".join(lines)


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
