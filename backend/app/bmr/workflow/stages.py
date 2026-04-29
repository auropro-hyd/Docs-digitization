"""The five pipeline stages (Constitution II).

Every stage is a pure function ``(state) -> partial_state``. Stages never
mutate the input dict; LangGraph merges the return value into state.
Errors are expressed by setting ``status=FAILED`` + ``error`` rather than
raising, so the graph can always reach a REPORT node.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.bmr.capabilities.aliases import AliasTable, load_alias_table
from app.bmr.capabilities.evidence import FindingDraft
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.capabilities.rule_eval import (
    cross_doc_rule_eval_v1,
    page_aggregate_eval_v1,
    same_page_eval_v1,
)
from app.bmr.capabilities.synthesise import checklist_synthesise_v1
from app.bmr.ingest.models import DocumentPackage, PackageStatus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.rules.loader import LoadedRule, load_rule_bank
from app.bmr.workflow.extractor import ExtractorPort, SidecarExtractor
from app.bmr.workflow.models import (
    FindingRecord,
    RunReport,
    RunStage,
    RunStatus,
    RunSummary,
    now_utc,
)
from app.bmr.workflow.state import BMRRunState

_ = LoadedRule  # re-exported for type hints referenced as forward strings

logger = logging.getLogger(__name__)

# Structlog logger for context-aware, kwarg-based log lines. The module
# keeps the stdlib ``logger`` too so legacy ``logger.warning("...", arg)``
# calls continue to work unchanged.
from app.observability import get_logger as _get_logger  # noqa: E402

_slog = _get_logger(__name__)


def _observe_stage(stage: RunStage, fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a stage function with stage timing + context binding.

    Cheap per invocation: one metric observe, two log lines, one
    ContextVar set/reset. Exposed so ``build_bmr_graph`` can wrap each
    stage function at composition time.
    """

    from functools import wraps

    @wraps(fn)
    def wrapper(state: BMRRunState, *args: Any, **kwargs: Any) -> Any:
        from app.observability import bind_context, reset_context, span
        from app.observability.metrics import BMR_STAGE_DURATION

        token = bind_context(stage=stage.value)
        started = time.monotonic()
        try:
            with span(f"bmr.stage.{stage.value}"):
                _slog.info("bmr.stage.entered", stage=stage.value)
                result = fn(state, *args, **kwargs)
        finally:
            dur = time.monotonic() - started
            try:
                BMR_STAGE_DURATION.labels(stage=stage.value).observe(dur)
            except Exception:  # pragma: no cover
                pass
            _slog.info(
                "bmr.stage.completed",
                stage=stage.value,
                duration_ms=round(dur * 1_000, 2),
            )
            reset_context(token)
        return result

    return wrapper

RuleEvalFn = Callable[..., list[FindingDraft]]

_DISPATCH: dict[str, RuleEvalFn] = {
    "same_page": same_page_eval_v1,
    "cross_document": cross_doc_rule_eval_v1,
    "page_aggregate": page_aggregate_eval_v1,
}


def _fail(stage: RunStage, message: str) -> dict[str, Any]:
    return {"stage": stage, "status": RunStatus.FAILED, "error": message}


# ── Stage 1: Ingest ──────────────────────────────────────────────────────────


def make_ingest_stage(package_store: PackageStore) -> Callable[[BMRRunState], dict[str, Any]]:
    def ingest_stage(state: BMRRunState) -> dict[str, Any]:
        package_id = state.get("package_id")
        if not package_id:
            return _fail(RunStage.INGEST, "ingest: missing package_id")

        package = package_store.load(package_id)
        if package is None:
            return _fail(RunStage.INGEST, f"ingest: package {package_id!r} not found")
        if package.status == PackageStatus.REJECTED:
            return _fail(
                RunStage.INGEST,
                f"ingest: package {package_id!r} is rejected and cannot be audited",
            )

        return {
            "stage": RunStage.INGEST,
            "status": RunStatus.RUNNING,
            "package": package,
        }

    return ingest_stage


# ── Stage 2: Legibility & classification ─────────────────────────────────────


def _legibility_reasons(package: DocumentPackage) -> list[str]:
    """Human-readable reasons the package needs a legibility/classification review.

    Currently the package status + ``issues`` list is the authoritative
    signal (populated by the ingest classifier). When the OCR pipeline
    lands (follow-up #1) it can append per-page legibility warnings.
    """

    reasons: list[str] = []
    for issue in package.issues:
        reasons.append(f"{issue.kind.value}: {issue.message}")
    return reasons


def legibility_and_classification_stage(state: BMRRunState) -> dict[str, Any]:
    package: DocumentPackage | None = state.get("package")
    if package is None:
        return _fail(
            RunStage.LEGIBILITY_AND_CLASSIFICATION,
            "legibility_and_classification: no package loaded",
        )

    if not package.documents:
        return _fail(
            RunStage.LEGIBILITY_AND_CLASSIFICATION,
            "legibility_and_classification: package has no documents",
        )

    if package.status == PackageStatus.NEEDS_REVIEW and not state.get(
        "legibility_override"
    ):
        reasons = _legibility_reasons(package)
        logger.info(
            "package %s needs_review — pausing BMR run %s for HITL (%d reasons)",
            package.package_id,
            state.get("run_id"),
            len(reasons),
        )
        return {
            "stage": RunStage.LEGIBILITY_AND_CLASSIFICATION,
            "status": RunStatus.AWAITING_LEGIBILITY_REVIEW,
            "legibility_reasons": reasons,
        }

    return {"stage": RunStage.LEGIBILITY_AND_CLASSIFICATION}


# ── Stage 3: Extraction ──────────────────────────────────────────────────────


SectionEnricher = Callable[[ExtractedPackage, "DocumentPackage", Path], ExtractedPackage]
"""Optional Spec 007 hook. Receives the just-extracted package, the
source :class:`DocumentPackage`, and the package dir on disk; returns
a (possibly new) :class:`ExtractedPackage` with ``section_id`` stamped
on every BPCR page that the detector could place. Default is a no-op
so existing runs are unaffected (FR-012)."""


def bpcr_sections_enabled() -> bool:
    """Read ``AT_BMR__BPCR_SECTIONS_ENABLED`` (defaults to true).

    Public Spec 007 helper — the workflow stage and the API service
    factory both read the same flag, so they share this helper rather
    than re-deriving the truthiness rules in two places.
    """

    raw = os.environ.get("AT_BMR__BPCR_SECTIONS_ENABLED", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


# Backwards-compat alias — older code imported the underscored name.
_bpcr_sections_enabled = bpcr_sections_enabled


def make_extraction_stage(
    package_store: PackageStore,
    *,
    extractor: ExtractorPort | None = None,
    section_enricher: SectionEnricher | None = None,
) -> Callable[[BMRRunState], dict[str, Any]]:
    # Default to the sidecar loader so runs without a configured OCR
    # pipeline keep working exactly as before (Constitution VII — no
    # regression while migrating to the real pipeline).
    chosen = extractor or SidecarExtractor()

    def extraction_stage(state: BMRRunState) -> dict[str, Any]:
        package: DocumentPackage | None = state.get("package")
        if package is None:
            return _fail(RunStage.EXTRACTION, "extraction: no package loaded")

        package_dir = package_store.base_path / package.package_id
        extraction_path_str = state.get("extraction_path")
        extraction_path = Path(extraction_path_str) if extraction_path_str else None

        try:
            extracted = chosen.extract(
                package,
                package_dir=package_dir,
                extraction_path=extraction_path,
            )
        except Exception as exc:  # pragma: no cover - adapters guard internally
            logger.exception("extraction failed for package %s", package.package_id)
            return _fail(RunStage.EXTRACTION, f"extraction: {exc}")

        # Spec 007 — post-extract BPCR section enrichment. Runs only
        # when wired AND the env flag is on; on any failure the run
        # continues with the un-enriched package (FR-013 fail-open).
        if section_enricher is not None and bpcr_sections_enabled():
            try:
                extracted = section_enricher(extracted, package, package_dir)
            except Exception:  # noqa: BLE001 — fail-open per FR-013
                logger.exception(
                    "bpcr section enrichment failed for package %s; "
                    "continuing with un-enriched extraction",
                    package.package_id,
                )

        return {"stage": RunStage.EXTRACTION, "extracted": extracted}

    return extraction_stage


# ── Stage 4: Compliance ──────────────────────────────────────────────────────


def _load_alias_tables(
    rules: list[LoadedRule],
    *,
    repo_root: Path,
    aliases_dir: Path | None,
) -> dict[str, AliasTable]:
    """Resolve every ``aliases_file`` referenced by the rule bank.

    ``aliases_file`` values in rule YAMLs are relative to the repo root
    (see the pilot rule for an example). We also accept a filename-only
    lookup inside ``aliases_dir`` as a convenience for tests.
    """

    tables: dict[str, AliasTable] = {}
    for loaded in rules:
        entity_match = (loaded.rule.get("context_object", {}) or {}).get("entity_match", {}) or {}
        rel = entity_match.get("aliases_file")
        if not rel:
            continue
        key = str(rel)
        if key in tables:
            continue
        candidate_paths: list[Path] = [repo_root / rel]
        if aliases_dir is not None:
            candidate_paths.append(aliases_dir / Path(rel).name)
        for candidate in candidate_paths:
            if candidate.is_file():
                tables[key] = load_alias_table(candidate)
                break
    return tables


_COMPLIANCE_MAX_WORKERS_ENV = "BMR_COMPLIANCE_MAX_WORKERS"


def _compliance_max_workers(requested: int | None) -> int:
    if requested is not None:
        return max(1, requested)
    raw = os.environ.get(_COMPLIANCE_MAX_WORKERS_ENV)
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(4, (os.cpu_count() or 2))


def _stamp_content_hash(
    findings: list[FindingDraft], *, content_hash: str
) -> list[FindingDraft]:
    """Mutate drafts in-place to carry the loader's content hash.

    Capabilities are deliberately unaware of the loader (Constitution
    III), so we stamp the hash here rather than plumb it through every
    evaluator. Mutation is safe because findings are fresh objects the
    capability just built — nothing else holds a reference yet.
    """

    for draft in findings:
        draft.rule_content_hash = content_hash
    return findings


def _failure_findings_for_rule(
    loaded: LoadedRule, *, stage: str
) -> list[FindingDraft]:
    """Emit an INDETERMINATE finding in place of findings lost to a crash.

    Silently dropping findings is the worst failure mode for a compliance
    audit — reviewers cannot tell passing evaluations from evaluations
    that never completed. We substitute a single marker finding so the
    rule appears in the report with an unmistakable status.
    """

    from app.bmr.capabilities.evidence import FindingDraft, FindingSource, FindingStatus

    return [
        FindingDraft(
            rule_id=loaded.id,
            rule_version=loaded.version,
            rule_content_hash=loaded.content_hash,
            status=FindingStatus.INDETERMINATE,
            severity=str(loaded.rule.get("severity", "major")),
            summary=(
                f"rule {loaded.id} could not be evaluated during {stage} "
                "(see server logs for the traceback)"
            ),
            source=FindingSource.ALCOA,
        )
    ]


def _evaluate_leaf_rule(
    loaded: LoadedRule,
    *,
    extracted: ExtractedPackage,
    alias_tables: dict[str, AliasTable],
) -> list[FindingDraft]:
    evaluator = _DISPATCH.get(loaded.scope)
    if evaluator is None:
        logger.warning("no evaluator for scope %s on rule %s", loaded.scope, loaded.id)
        return []
    findings = evaluator(
        rule=loaded.rule, extracted=extracted, alias_tables=alias_tables
    )
    return _stamp_content_hash(findings, content_hash=loaded.content_hash)


def make_compliance_stage(
    *,
    repo_root: Path,
    max_workers: int | None = None,
) -> Callable[[BMRRunState], dict[str, Any]]:
    def compliance_stage(state: BMRRunState) -> dict[str, Any]:
        rules_dir_str = state.get("rules_dir")
        if not rules_dir_str:
            return _fail(RunStage.COMPLIANCE, "compliance: missing rules_dir")
        rules_dir = Path(rules_dir_str)
        if not rules_dir.exists():
            return _fail(
                RunStage.COMPLIANCE,
                f"compliance: rules_dir {rules_dir!s} does not exist",
            )

        bank = load_rule_bank(rules_dir)
        if not bank.ok:
            first = bank.errors[0] if bank.errors else None
            detail = f"{first.path}: {first.message}" if first else "unknown"
            return _fail(
                RunStage.COMPLIANCE,
                f"compliance: rule bank has validation errors ({detail})",
            )

        aliases_dir_str = state.get("aliases_dir")
        aliases_dir = Path(aliases_dir_str) if aliases_dir_str else None
        alias_tables = _load_alias_tables(
            bank.rules, repo_root=repo_root, aliases_dir=aliases_dir
        )

        extracted: ExtractedPackage = state.get(
            "extracted", ExtractedPackage(package_id=state.get("package_id", "unknown"))
        )

        # Spec 005 FR-013: deprecated rules are loaded (so the bank
        # contains them for introspection and prior-run replay) but
        # intentionally skipped here. Log each skip so the run log
        # carries the reason reviewers would otherwise ask us about.
        active_rules: list[LoadedRule] = []
        skipped_rules: list[LoadedRule] = []
        for loaded in bank.rules:
            if getattr(loaded, "deprecated", False):
                skipped_rules.append(loaded)
                logger.info(
                    "skipping deprecated rule %s (superseded_by=%s)",
                    loaded.id,
                    getattr(loaded, "superseded_by", None),
                )
                continue
            active_rules.append(loaded)

        leaf_rules = [r for r in active_rules if r.scope != "checklist_synthesis"]
        synthesis_rules = [
            r for r in active_rules if r.scope == "checklist_synthesis"
        ]

        worker_count = min(_compliance_max_workers(max_workers), max(1, len(leaf_rules)))
        findings: list[FindingDraft] = []
        if worker_count > 1 and len(leaf_rules) > 1:
            from app.observability.tracing import submit_with_context

            with ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="bmr-compliance"
            ) as executor:
                # submit in bank order; results aligned by the same index so
                # findings_by_rule preserves deterministic ordering regardless
                # of thread completion order. ``submit_with_context`` copies
                # the current contextvars into the worker thread so trace +
                # scope survive the handoff (FR-002).
                futures = [
                    submit_with_context(
                        executor,
                        _evaluate_leaf_rule,
                        loaded,
                        extracted=extracted,
                        alias_tables=alias_tables,
                    )
                    for loaded in leaf_rules
                ]
                # Collect every future — a crashed evaluator must not orphan
                # the findings from siblings that finished before it.
                for loaded, future in zip(leaf_rules, futures, strict=True):
                    try:
                        findings.extend(future.result())
                    except Exception:
                        logger.exception(
                            "leaf rule %s evaluation crashed", loaded.id
                        )
                        findings.extend(
                            _failure_findings_for_rule(loaded, stage="compliance")
                        )
        else:
            for loaded in leaf_rules:
                try:
                    findings.extend(
                        _evaluate_leaf_rule(
                            loaded, extracted=extracted, alias_tables=alias_tables
                        )
                    )
                except Exception:
                    logger.exception(
                        "leaf rule %s evaluation crashed", loaded.id
                    )
                    findings.extend(
                        _failure_findings_for_rule(loaded, stage="compliance")
                    )

        for loaded in synthesis_rules:
            synthesised = checklist_synthesise_v1(
                rule=loaded.rule, findings=findings
            )
            findings.extend(
                _stamp_content_hash(synthesised, content_hash=loaded.content_hash)
            )

        return {
            "stage": RunStage.COMPLIANCE,
            "rules_evaluated": len(active_rules),
            "rules_loaded": len(bank.rules),
            "rules_skipped_deprecated": len(skipped_rules),
            "findings": findings,
        }

    return compliance_stage


# ── Stage 5: Report ──────────────────────────────────────────────────────────


def _bump(counter: dict[str, int], key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _project_findings(findings: list[FindingDraft]) -> tuple[list[FindingRecord], RunSummary]:
    records: list[FindingRecord] = []
    summary = RunSummary(total=len(findings), by_status={}, by_severity={}, by_source={})
    for draft in findings:
        record = FindingRecord(
            finding_id=uuid.uuid4().hex,
            rule_id=draft.rule_id,
            rule_version=draft.rule_version,
            rule_content_hash=draft.rule_content_hash,
            status=draft.status,
            severity=draft.severity,
            alcoa_tag=draft.alcoa_tag,
            gmp_category=draft.gmp_category,
            source=draft.source,
            summary=draft.summary,
            detail=draft.detail,
            source_finding_ids=list(draft.source_finding_ids),
            evidence=list(draft.evidence),
            tolerance_applied=draft.tolerance_applied,
            fields=dict(draft.fields),
            fallback_applied=draft.fallback_applied,
        )
        records.append(record)
        _bump(summary.by_status, record.status.value)
        _bump(summary.by_severity, record.severity)
        _bump(summary.by_source, record.source.value)
    return records, summary


def _bpcr_section_summary(extracted: ExtractedPackage | None) -> list[dict[str, Any]]:
    """Project the BPCR pages' ``section_id`` into a flat reviewer summary.

    Empty when no BPCR pages exist or none carry a section_id. The
    section enricher tags every BPCR page (including ``unsectioned``)
    when it runs, so an empty list here is the operator's signal that
    detection didn't run end-to-end.
    """

    if extracted is None:
        return []
    summary: list[dict[str, Any]] = []
    for page in extracted.pages:
        if page.document_role != "BPCR":
            continue
        if page.section_id is None:
            continue
        summary.append(
            {
                "doc_id": page.doc_id,
                "page_index": page.page_index,
                "section_id": page.section_id,
            }
        )
    return summary


def report_stage(state: BMRRunState) -> dict[str, Any]:
    run_id = state.get("run_id") or uuid.uuid4().hex
    package_id = state.get("package_id", "unknown")
    findings = state.get("findings", [])
    records, summary = _project_findings(findings)
    extracted = state.get("extracted")

    report = RunReport(
        run_id=run_id,
        package_id=package_id,
        status=RunStatus.COMPLETED,
        stage=RunStage.REPORT,
        rules_evaluated=int(state.get("rules_evaluated", 0)),
        rules_loaded=int(state.get("rules_loaded", state.get("rules_evaluated", 0))),
        rules_skipped_deprecated=int(state.get("rules_skipped_deprecated", 0)),
        findings=records,
        summary=summary,
        started_at=state.get("started_at") or now_utc(),
        finished_at=now_utc(),
        rules_dir=state.get("rules_dir"),
        aliases_dir=state.get("aliases_dir"),
        repo_root=state.get("repo_root"),
        bpcr_sections=_bpcr_section_summary(extracted),
    )
    return {
        "stage": RunStage.REPORT,
        "status": RunStatus.COMPLETED,
        "report": report,
    }


__all__ = [
    "legibility_and_classification_stage",
    "make_compliance_stage",
    "make_extraction_stage",
    "make_ingest_stage",
    "report_stage",
]
