"""HITL service: resolutions, projections, gate, and export.

Owns the lifecycle:

1. ``record_resolution`` — validate + persist a :class:`StructuredResolution`,
   then synchronously seed a :class:`FeedbackSample` via
   :func:`feedback_seed_v1` (Spec 004 cross-entity rule §3).
2. ``project_report`` — compute the grouped projection at read time via
   :func:`report_project_v1`.
3. ``export_report`` — enforce the gate, render PDF + bundle, persist an
   :class:`AuditReportRevision`.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.bmr.ingest.package_store import PackageStore

from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)
from app.bmr.capabilities.feedback_seed import feedback_seed_v1
from app.bmr.capabilities.report_project import report_project_v1
from app.bmr.capabilities.rerun_plan import RerunPlan, plan_selective_rerun_v1
from app.bmr.capabilities.rule_eval import (
    cross_doc_rule_eval_v1,
    page_aggregate_eval_v1,
    same_page_eval_v1,
)
from app.bmr.capabilities.synthesise import checklist_synthesise_v1
from app.bmr.hitl.models import (
    AuditReportRevision,
    CorrectionStatus,
    CorrectionWorkflow,
    DismissReasonType,
    ExportGateStatus,
    FeedbackSample,
    GroupedReport,
    ResolutionAction,
    StructuredResolution,
    now_utc,
)
from app.bmr.hitl.renderer import (
    ReportRenderer,
    WeasyPrintRenderer,
    render_bundle_json,
)
from app.bmr.hitl.reporting_config import ReportingConfig
from app.bmr.hitl.stores import (
    CorrectionStore,
    FeedbackStore,
    ResolutionStore,
    RevisionStore,
)
from app.bmr.hitl.validation import (
    CorrectionDraft,
    ResolutionDraft,
    ResolutionValidationError,
    validate_resolution_payload,
)
from app.bmr.rules.loader import load_rule_bank
from app.bmr.workflow.extraction import load_extracted_package
from app.bmr.workflow.models import (
    FindingRecord,
    RunReport,
    RunSummary,
)
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.stages import _load_alias_tables

_LEAF_DISPATCH = {
    "same_page": same_page_eval_v1,
    "cross_document": cross_doc_rule_eval_v1,
    "page_aggregate": page_aggregate_eval_v1,
}


class RunNotFoundError(LookupError):
    pass


class FindingNotFoundError(LookupError):
    pass


class CorrectionNotApplicableError(RuntimeError):
    """Raised when a CORRECT cannot be applied (missing field, no rules_dir, etc)."""


class ExportGateBlockedError(RuntimeError):
    def __init__(self, status: ExportGateStatus, pending: int) -> None:
        super().__init__(f"export blocked: {status.value} (pending={pending})")
        self.status = status
        self.pending = pending


@dataclass(frozen=True)
class ResolveFindingResult:
    resolution: StructuredResolution
    feedback_sample: FeedbackSample | None


@dataclass(frozen=True)
class ExportResult:
    revision: AuditReportRevision
    pdf_bytes: bytes
    bundle_bytes: bytes


@dataclass(frozen=True)
class CorrectionResult:
    workflow: CorrectionWorkflow
    resolution: StructuredResolution
    feedback_sample: FeedbackSample | None
    plan: RerunPlan
    new_findings: list[FindingRecord]
    superseded_finding_ids: list[str]
    run_report: RunReport


class HITLService:
    def __init__(
        self,
        *,
        run_store: RunStore,
        resolution_store: ResolutionStore,
        feedback_store: FeedbackStore,
        revision_store: RevisionStore,
        correction_store: CorrectionStore | None = None,
        package_store: PackageStore | None = None,
        renderer: ReportRenderer | None = None,
        reporting_config: ReportingConfig | None = None,
        event_emitter: Callable[[str, str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._run_store = run_store
        self._resolution_store = resolution_store
        self._feedback_store = feedback_store
        self._revision_store = revision_store
        self._correction_store = correction_store
        self._package_store = package_store
        self._renderer: ReportRenderer = renderer or WeasyPrintRenderer()
        self._reporting_config = reporting_config or ReportingConfig.default()
        self._event_emitter = event_emitter or (lambda *_args, **_kw: None)
        # Per-run mutex map for HITL writes. Serializes concurrent
        # resolution / correction / export calls against the same run so
        # the load → mutate → save sequence is not interleaved. Acquired
        # by ``_run_lock``; the registry itself is guarded by ``_locks_lock``.
        self._run_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    @contextmanager
    def _run_lock(self, run_id: str):
        with self._locks_lock:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = threading.Lock()
                self._run_locks[run_id] = lock
        with lock:
            yield

    @property
    def reporting_config(self) -> ReportingConfig:
        return self._reporting_config

    # ── resolutions ──────────────────────────────────────────────────────────

    def record_resolution(
        self,
        *,
        run_id: str,
        finding_id: str,
        draft: ResolutionDraft,
        actor_id: str,
    ) -> ResolveFindingResult:
        with self._run_lock(run_id):
            return self._record_resolution_locked(
                run_id=run_id,
                finding_id=finding_id,
                draft=draft,
                actor_id=actor_id,
            )

    def _record_resolution_locked(
        self,
        *,
        run_id: str,
        finding_id: str,
        draft: ResolutionDraft,
        actor_id: str,
    ) -> ResolveFindingResult:
        report = self._require_report(run_id)
        finding = self._require_finding(report, finding_id)

        # DISMISS/DUPLICATE_FINDING carries a pointer to the primary
        # finding — enforce that it actually exists in this run and that
        # it is not the finding being dismissed itself. Missing
        # references would produce dangling audit records that look
        # resolved but point nowhere.
        if draft.duplicate_of_finding_id is not None:
            if draft.duplicate_of_finding_id == finding_id:
                raise ResolutionValidationError(
                    "duplicate_of_finding_id cannot reference the finding being dismissed"
                )
            self._require_finding(report, draft.duplicate_of_finding_id)

        resolution = StructuredResolution(
            resolution_id=f"res_{uuid.uuid4().hex}",
            run_id=run_id,
            finding_id=finding_id,
            action=draft.action,
            reason_type=draft.reason_type,
            observed_value_on_document=draft.observed_value_on_document,
            system_extracted_value=self._snapshot_system_value(finding),
            reason_comment=draft.reason_comment,
            duplicate_of_finding_id=draft.duplicate_of_finding_id,
            actor_id=actor_id,
            created_at=now_utc(),
        )
        self._resolution_store.save(resolution)

        feedback: FeedbackSample | None = None
        if draft.action in (ResolutionAction.DISMISS, ResolutionAction.CORRECT):
            feedback = feedback_seed_v1(resolution=resolution, finding=finding)
            self._feedback_store.save(feedback)

        self._event_emitter(
            "resolution.recorded",
            run_id,
            {
                "finding_id": finding_id,
                "resolution_id": resolution.resolution_id,
                "action": resolution.action.value,
                "actor_id": actor_id,
                "feedback_sample_id": feedback.sample_id if feedback else None,
            },
        )

        return ResolveFindingResult(resolution=resolution, feedback_sample=feedback)

    # ── corrections ──────────────────────────────────────────────────────────

    def record_correction(
        self,
        *,
        run_id: str,
        finding_id: str,
        draft: CorrectionDraft,
        actor_id: str,
    ) -> CorrectionResult:
        with self._run_lock(run_id):
            return self._record_correction_locked(
                run_id=run_id,
                finding_id=finding_id,
                draft=draft,
                actor_id=actor_id,
            )

    def _record_correction_locked(
        self,
        *,
        run_id: str,
        finding_id: str,
        draft: CorrectionDraft,
        actor_id: str,
    ) -> CorrectionResult:
        if self._correction_store is None:
            raise CorrectionNotApplicableError(
                "HITLService was instantiated without a CorrectionStore; cannot "
                "apply corrections"
            )
        if self._package_store is None:
            raise CorrectionNotApplicableError(
                "HITLService was instantiated without a PackageStore; cannot "
                "re-evaluate rules after a correction"
            )

        report = self._require_report(run_id)
        finding = self._require_finding(report, finding_id)
        target_region = _find_evidence_region(finding, draft.field)
        if target_region is None:
            raise CorrectionNotApplicableError(
                f"finding {finding_id!r} has no evidence region for field "
                f"{draft.field!r}; CORRECT needs a concrete page anchor"
            )
        if not report.rules_dir:
            raise CorrectionNotApplicableError(
                "run report does not record rules_dir; corrections require a "
                "re-runnable rule bank. Re-run the audit to capture it."
            )

        previous_value = target_region.value

        self._event_emitter(
            "correction.started",
            run_id,
            {
                "finding_id": finding_id,
                "field": draft.field,
                "doc_id": target_region.doc_id,
                "page_index": target_region.page_index,
            },
        )

        workflow_id = f"cwf_{uuid.uuid4().hex}"
        resolution = StructuredResolution(
            resolution_id=f"res_{uuid.uuid4().hex}",
            run_id=run_id,
            finding_id=finding_id,
            action=ResolutionAction.CORRECT,
            reason_type=None,
            observed_value_on_document=draft.observed_value_on_document,
            system_extracted_value=previous_value,
            reason_comment=draft.reason_comment,
            duplicate_of_finding_id=None,
            actor_id=actor_id,
            created_at=now_utc(),
        )
        self._resolution_store.save(resolution)

        feedback = feedback_seed_v1(resolution=resolution, finding=finding)
        self._feedback_store.save(feedback)

        workflow = CorrectionWorkflow(
            workflow_id=workflow_id,
            run_id=run_id,
            finding_id=finding_id,
            rule_id=finding.rule_id,
            field=draft.field,
            doc_id=target_region.doc_id,
            page_index=target_region.page_index,
            previous_value=previous_value,
            corrected_value=draft.corrected_value,
            reason_comment=draft.reason_comment,
            resolution_id=resolution.resolution_id,
            actor_id=actor_id,
            status=CorrectionStatus.PENDING,
            created_at=now_utc(),
        )
        self._correction_store.save(workflow)

        try:
            plan, new_findings, superseded_ids, updated_report = (
                self._reevaluate_with_correction(
                    report=report,
                    field=draft.field,
                    corrected_value=draft.corrected_value,
                    target_region=target_region,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            workflow.status = CorrectionStatus.FAILED
            workflow.error = f"{type(exc).__name__}: {exc}"
            workflow.applied_at = now_utc()
            self._correction_store.save(workflow)
            self._event_emitter(
                "correction.failed", run_id, {"workflow_id": workflow_id, "error": workflow.error}
            )
            raise

        workflow.status = CorrectionStatus.APPLIED
        workflow.applied_at = now_utc()
        workflow.affected_rule_ids = list(
            plan.affected_rule_ids + plan.affected_synthesis_rule_ids
        )
        workflow.superseded_finding_ids = superseded_ids
        workflow.new_finding_ids = [f.finding_id for f in new_findings]
        self._correction_store.save(workflow)

        self._event_emitter(
            "correction.applied",
            run_id,
            {
                "workflow_id": workflow_id,
                "affected_rules": workflow.affected_rule_ids,
                "new_findings": workflow.new_finding_ids,
                "superseded_findings": workflow.superseded_finding_ids,
            },
        )

        return CorrectionResult(
            workflow=workflow,
            resolution=resolution,
            feedback_sample=feedback,
            plan=plan,
            new_findings=new_findings,
            superseded_finding_ids=superseded_ids,
            run_report=updated_report,
        )

    def _reevaluate_with_correction(
        self,
        *,
        report: RunReport,
        field: str,
        corrected_value: Any,
        target_region,
    ) -> tuple[RerunPlan, list[FindingRecord], list[str], RunReport]:
        assert report.rules_dir is not None  # guarded by caller
        assert self._package_store is not None

        package_dir = self._package_store.base_path / report.package_id
        extracted = load_extracted_package(
            report.package_id, package_dir=package_dir
        )
        corrected = _apply_field_overlay(
            extracted,
            doc_id=target_region.doc_id,
            page_index=target_region.page_index,
            field=field,
            new_value=corrected_value,
        )

        bank = load_rule_bank(Path(report.rules_dir))
        if not bank.ok:
            raise CorrectionNotApplicableError(
                "cannot re-evaluate: rule bank has validation errors"
            )

        repo_root = Path(report.repo_root) if report.repo_root else Path(report.rules_dir).parent
        aliases_dir = Path(report.aliases_dir) if report.aliases_dir else None
        alias_tables = _load_alias_tables(
            bank.rules, repo_root=repo_root, aliases_dir=aliases_dir
        )

        plan = plan_selective_rerun_v1(
            loaded_rules=[r.rule for r in bank.rules],
            corrected_field=field,
        )

        affected = set(plan.affected_rule_ids)
        affected_synth = set(plan.affected_synthesis_rule_ids)

        # Re-run leaf rules whose scope touches the corrected field.
        new_drafts = []
        for loaded in bank.rules:
            if loaded.scope == "checklist_synthesis":
                continue
            if loaded.id not in affected:
                continue
            evaluator = _LEAF_DISPATCH.get(loaded.scope)
            if evaluator is None:
                continue
            new_drafts.extend(
                evaluator(
                    rule=loaded.rule,
                    extracted=corrected,
                    alias_tables=alias_tables,
                )
            )

        # Synthesis roll-ups re-run across the union of existing + new drafts
        # for the affected synthesis rules so roll-up verdicts stay coherent.
        if affected_synth:
            leaf_findings_for_synthesis = _drafts_from_records(
                [
                    f
                    for f in report.findings
                    if f.rule_id not in affected and f.source.value != "checklist_synthesis"
                ]
            )
            leaf_findings_for_synthesis.extend(new_drafts)
            for loaded in bank.rules:
                if loaded.scope != "checklist_synthesis":
                    continue
                if loaded.id not in affected_synth:
                    continue
                new_drafts.extend(
                    checklist_synthesise_v1(
                        rule=loaded.rule, findings=leaf_findings_for_synthesis
                    )
                )

        new_records: list[FindingRecord] = []
        for draft in new_drafts:
            record = FindingRecord(
                finding_id=uuid.uuid4().hex,
                rule_id=draft.rule_id,
                rule_version=draft.rule_version,
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
            )
            new_records.append(record)

        superseded_ids: list[str] = []
        new_by_rule: dict[str, FindingRecord] = {}
        for record in new_records:
            new_by_rule.setdefault(record.rule_id, record)

        for existing in report.findings:
            if existing.rule_id in (affected | affected_synth) and existing.superseded_by is None:
                replacement = new_by_rule.get(existing.rule_id)
                if replacement is not None:
                    existing.superseded_by = replacement.finding_id
                    replacement.supersedes = existing.finding_id
                    superseded_ids.append(existing.finding_id)

        report.findings.extend(new_records)
        report.rules_evaluated = len(bank.rules)
        report.summary = _recompute_summary(report.findings)
        report.finished_at = now_utc()
        self._run_store.save(report)
        return plan, new_records, superseded_ids, report

    # ── projection + gate ────────────────────────────────────────────────────

    def project_report(
        self, run_id: str, *, view: str = "grouped"
    ) -> tuple[RunReport, GroupedReport]:
        run_report = self._require_report(run_id)
        resolutions = self._resolution_store.list_for_run(run_id)
        active = list(self._resolution_store.list_active_by_finding(run_id).values())
        grouped = report_project_v1(
            run_report=run_report,
            resolutions=active,
            severity_config=self._reporting_config.severity,
            sections_config=self._reporting_config.sections,
            view=view,
        )
        del resolutions  # keep the closure shape explicit; tests rely on active set
        return run_report, grouped

    # ── export ───────────────────────────────────────────────────────────────

    def export_report(
        self, run_id: str, *, actor_id: str
    ) -> ExportResult:
        with self._run_lock(run_id):
            return self._export_report_locked(run_id, actor_id=actor_id)

    def _export_report_locked(
        self, run_id: str, *, actor_id: str
    ) -> ExportResult:
        # Snapshot everything we need to build the revision under the
        # per-run lock *before* deciding on the gate, so the gate and
        # the subsequent render + save all observe the same state. The
        # previous shape re-read the stores three times; even under the
        # lock this made the relationship between "gate said READY" and
        # "bundle written" harder to reason about.
        run_report = self._require_report(run_id)
        resolutions = self._resolution_store.list_for_run(run_id)
        active_map = self._resolution_store.list_active_by_finding(run_id)
        feedback_samples = self._feedback_store.list_for_run(run_id)

        grouped = report_project_v1(
            run_report=run_report,
            resolutions=list(active_map.values()),
            severity_config=self._reporting_config.severity,
            sections_config=self._reporting_config.sections,
        )
        if grouped.export_gate is not ExportGateStatus.READY:
            raise ExportGateBlockedError(
                status=grouped.export_gate,
                pending=grouped.pending_blocking_count,
            )
        # Defence-in-depth: if any resolution — active or historical —
        # still flags needs_re_action, hard-block regardless of gate.
        # Catches bugs where a stale resolution was somehow marked
        # "active" despite carrying the re-action flag.
        if any(r.needs_re_action for r in active_map.values()):
            raise ExportGateBlockedError(
                status=ExportGateStatus.BLOCKED_BY_STALE_RESOLUTIONS,
                pending=sum(
                    1 for r in active_map.values() if r.needs_re_action
                ),
            )
        active_by_finding = {
            finding_id: res.resolution_id for finding_id, res in active_map.items()
        }

        html_body = self._renderer.render_html(
            run_report=run_report,
            grouped_report=grouped,
            resolutions=resolutions,
        )
        pdf_bytes = self._renderer.render_pdf(html_body) or b""
        bundle_bytes = render_bundle_json(
            run_report=run_report,
            grouped_report=grouped,
            resolutions=resolutions,
            feedback_samples=feedback_samples,
        )

        revision_number = self._revision_store.next_revision_number(run_id)
        predecessors = self._revision_store.list_for_run(run_id)
        predecessor_id = predecessors[-1].revision_id if predecessors else None
        revision_id = f"rev_{uuid.uuid4().hex}"
        revision = AuditReportRevision(
            revision_id=revision_id,
            run_id=run_id,
            revision_number=revision_number,
            predecessor_id=predecessor_id,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            bundle_sha256=hashlib.sha256(bundle_bytes).hexdigest(),
            pdf_bytes_stored_path=str(
                Path(self._revision_store.base_path)
                / run_id
                / revision_id
                / "report.pdf"
            ),
            bundle_stored_path=str(
                Path(self._revision_store.base_path)
                / run_id
                / revision_id
                / "bundle.json"
            ),
            exported_by=actor_id,
            exported_at=now_utc(),
            findings_snapshot=[
                {
                    "finding_id": f.finding_id,
                    "rule_id": f.rule_id,
                    "status": f.status.value,
                    "severity": f.severity,
                    "source": f.source.value,
                    "active_resolution_id": active_by_finding.get(f.finding_id),
                }
                for f in run_report.findings
            ],
        )
        self._revision_store.save(revision, pdf_bytes=pdf_bytes, bundle_bytes=bundle_bytes)
        return ExportResult(
            revision=revision,
            pdf_bytes=pdf_bytes,
            bundle_bytes=bundle_bytes,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _require_report(self, run_id: str) -> RunReport:
        report = self._run_store.load(run_id)
        if report is None:
            raise RunNotFoundError(f"run {run_id!r} not found")
        return report

    @staticmethod
    def _require_finding(report: RunReport, finding_id: str) -> FindingRecord:
        for finding in report.findings:
            if finding.finding_id == finding_id:
                return finding
        raise FindingNotFoundError(
            f"finding {finding_id!r} not found in run {report.run_id!r}"
        )

    @staticmethod
    def _snapshot_system_value(finding: FindingRecord):  # type: ignore[no-untyped-def]
        # Prefer the source-field value if present; otherwise the first
        # evidence value; otherwise None. Kept conservative for v0.
        if "source_value" in finding.fields:
            return finding.fields["source_value"]
        if finding.evidence and finding.evidence[0].value is not None:
            return finding.evidence[0].value
        return None


def _find_evidence_region(finding: FindingRecord, field: str):
    for region in finding.evidence:
        if region.field == field:
            return region
    # Fall back to the first region when the rule's source doesn't carry a
    # field name (e.g. page-aggregate "_synthesised") but still targets a
    # concrete page anchor.
    if finding.evidence and finding.evidence[0].field is None:
        return finding.evidence[0]
    return None


def _apply_field_overlay(
    extracted: ExtractedPackage,
    *,
    doc_id: str,
    page_index: int,
    field: str,
    new_value: Any,
) -> ExtractedPackage:
    new_pages: list[ExtractedPage] = []
    for page in extracted.pages:
        if page.doc_id != doc_id or page.page_index != page_index:
            new_pages.append(page)
            continue
        new_fields: list[FieldValue] = []
        replaced = False
        for fv in page.fields:
            if fv.field == field and not replaced:
                new_fields.append(fv.model_copy(update={"value": new_value}))
                replaced = True
            else:
                new_fields.append(fv)
        if not replaced:
            # Rule expected a field that didn't exist on the page; add one
            # so the re-run has something to evaluate.
            new_fields.append(
                FieldValue(
                    field=field,
                    value=new_value,
                    source_doc_id=doc_id,
                    source_page_index=page_index,
                )
            )
        new_pages.append(page.model_copy(update={"fields": new_fields}))
    return extracted.model_copy(update={"pages": new_pages})


def _drafts_from_records(records: list[FindingRecord]):
    from app.bmr.capabilities.evidence import FindingDraft

    drafts = []
    for r in records:
        drafts.append(
            FindingDraft(
                rule_id=r.rule_id,
                rule_version=r.rule_version,
                status=r.status,
                severity=r.severity,
                alcoa_tag=r.alcoa_tag,
                gmp_category=r.gmp_category,
                summary=r.summary,
                detail=r.detail,
                source=r.source,
                source_finding_ids=list(r.source_finding_ids),
                evidence=list(r.evidence),
                tolerance_applied=r.tolerance_applied,
                fields=dict(r.fields),
                fallback_applied=r.fallback_applied,
            )
        )
    return drafts


def _recompute_summary(findings: list[FindingRecord]) -> RunSummary:
    summary = RunSummary(total=len(findings), by_status={}, by_severity={}, by_source={})
    for f in findings:
        # Exclude superseded findings from active status counts.
        key = "superseded" if f.superseded_by is not None else f.status.value
        summary.by_status[key] = summary.by_status.get(key, 0) + 1
        summary.by_severity[f.severity] = summary.by_severity.get(f.severity, 0) + 1
        src = f.source.value
        summary.by_source[src] = summary.by_source.get(src, 0) + 1
    return summary


__all__ = [
    "CorrectionNotApplicableError",
    "CorrectionResult",
    "ExportGateBlockedError",
    "ExportResult",
    "FindingNotFoundError",
    "HITLService",
    "ResolutionValidationError",
    "ResolveFindingResult",
    "RunNotFoundError",
    "validate_resolution_payload",
    "DismissReasonType",
    "ResolutionAction",
]
