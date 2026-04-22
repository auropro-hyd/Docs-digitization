"""BMR finding-level HITL routes (Spec 004 v0).

Endpoints (all mounted under ``/api/bmr``):

- ``GET  /runs/{run_id}/report``                         — grouped projection
- ``GET  /runs/{run_id}/findings/{finding_id}``          — finding detail
- ``POST /runs/{run_id}/findings/{finding_id}/resolutions``  — CONFIRM/DISMISS
- ``POST /runs/{run_id}/findings/{finding_id}/corrections``  — 501 (v0)
- ``GET  /runs/{run_id}/export-gate``                    — gate status
- ``POST /runs/{run_id}/reports:export``                 — produce a revision
- ``GET  /reports/revisions/{revision_id}/pdf``          — download PDF
- ``GET  /reports/revisions/{revision_id}/bundle``       — download JSON bundle
- ``GET  /feedback/samples``                             — query feedback corpus
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import require_actor
from app.api.routes.bmr_runs import _runs_base_dir
from app.bmr.events import get_event_bus
from app.bmr.hitl.models import (
    AuditReportRevision,
    CorrectionWorkflow,
    ExportGateStatus,
    FeedbackSample,
    GroupedReport,
    StructuredResolution,
)
from app.bmr.hitl.reporting_config import ReportingConfig, load_reporting_config
from app.bmr.hitl.service import (
    CorrectionNotApplicableError,
    ExportGateBlockedError,
    FindingNotFoundError,
    HITLService,
    RunNotFoundError,
)
from app.bmr.hitl.stores import (
    CorrectionStore,
    FeedbackStore,
    ResolutionStore,
    RevisionStore,
)
from app.bmr.hitl.validation import (
    CorrectionValidationError,
    CorrectNotSupportedError,
    ResolutionValidationError,
    validate_correction_payload,
    validate_resolution_payload,
)
from app.bmr.ingest.package_store import PackageStore
from app.bmr.workflow.models import FindingRecord
from app.bmr.workflow.run_store import RunStore
from app.config.settings import get_settings

router = APIRouter()


# ── request / response schemas ───────────────────────────────────────────────


class ResolutionRequest(BaseModel):
    action: str = Field(..., description="CONFIRM | DISMISS | CORRECT")
    reason_type: str | None = None
    observed_value_on_document: str | None = None
    reason_comment: str | None = None
    duplicate_of_finding_id: str | None = None
    note: str | None = None  # free-text alias for reason_comment on CONFIRM


class ResolutionResponse(BaseModel):
    resolution: StructuredResolution
    feedback_sample_id: str | None = None


class ExportGateResponse(BaseModel):
    status: ExportGateStatus
    pending_blocking_count: int


class ExportRevisionResponse(BaseModel):
    revision: AuditReportRevision
    pdf_url: str
    bundle_url: str


class FindingDetail(BaseModel):
    finding: FindingRecord
    current_resolution: StructuredResolution | None = None


class FeedbackListResponse(BaseModel):
    items: list[FeedbackSample]


# ── wiring ───────────────────────────────────────────────────────────────────


def _hitl_base_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path).resolve().parent / "bmr-hitl"


def _packages_base_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path).resolve().parent / "bmr-packages"


@lru_cache(maxsize=1)
def _reporting_config() -> ReportingConfig:
    settings = get_settings()
    bundle_dir = (
        Path(settings.compliance.rules_root).resolve() / "pilot"
        if getattr(settings, "compliance", None)
        else Path(__file__).resolve().parents[3] / "config" / "rules" / "pilot"
    )
    try:
        return load_reporting_config(bundle_dir)
    except Exception:  # pragma: no cover - defensive; defaults are safe
        return ReportingConfig.default()


@lru_cache(maxsize=1)
def _service() -> HITLService:
    run_store = RunStore(_runs_base_dir())
    base = _hitl_base_dir()
    bus = get_event_bus()
    return HITLService(
        run_store=run_store,
        resolution_store=ResolutionStore(base),
        feedback_store=FeedbackStore(base),
        revision_store=RevisionStore(base),
        correction_store=CorrectionStore(base),
        package_store=PackageStore(_packages_base_dir()),
        reporting_config=_reporting_config(),
        event_emitter=bus.publish,
    )


# ── endpoints ────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/report", response_model=GroupedReport)
async def get_report(
    run_id: str,
    view: str = Query("grouped", pattern="^(grouped|flat)$"),
    _actor: str = Depends(require_actor),
) -> GroupedReport:
    try:
        _, grouped = _service().project_report(run_id, view=view)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return grouped


@router.get("/runs/{run_id}/findings/{finding_id}", response_model=FindingDetail)
async def get_finding(
    run_id: str, finding_id: str, _actor: str = Depends(require_actor)
) -> FindingDetail:
    service = _service()
    try:
        run_report, _ = service.project_report(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finding = next(
        (f for f in run_report.findings if f.finding_id == finding_id),
        None,
    )
    if finding is None:
        raise HTTPException(
            status_code=404,
            detail=f"finding {finding_id} not found in run {run_id}",
        )
    active = service._resolution_store.list_active_by_finding(run_id)  # noqa: SLF001 — thin accessor
    return FindingDetail(finding=finding, current_resolution=active.get(finding_id))


@router.post(
    "/runs/{run_id}/findings/{finding_id}/resolutions",
    response_model=ResolutionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_resolution(
    run_id: str,
    finding_id: str,
    body: ResolutionRequest,
    actor_id: str = Depends(require_actor),
) -> ResolutionResponse:
    service = _service()
    reason_comment = body.reason_comment or body.note
    try:
        draft = validate_resolution_payload(
            action=body.action,
            reason_type=body.reason_type,
            observed_value_on_document=body.observed_value_on_document,
            reason_comment=reason_comment,
            duplicate_of_finding_id=body.duplicate_of_finding_id,
        )
    except CorrectNotSupportedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ResolutionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = service.record_resolution(
            run_id=run_id,
            finding_id=finding_id,
            draft=draft,
            actor_id=actor_id,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FindingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ResolutionResponse(
        resolution=result.resolution,
        feedback_sample_id=(
            result.feedback_sample.sample_id if result.feedback_sample else None
        ),
    )


class CorrectionRequest(BaseModel):
    field: str = Field(..., description="Dotted path of the field to overwrite")
    corrected_value: Any = Field(..., description="Reviewer-supplied value")
    reason_comment: str = Field(..., description="Why the correction is being made")
    observed_value_on_document: str | None = None


class CorrectionResponse(BaseModel):
    workflow: CorrectionWorkflow
    resolution: StructuredResolution
    new_finding_ids: list[str] = Field(default_factory=list)
    superseded_finding_ids: list[str] = Field(default_factory=list)


@router.post(
    "/runs/{run_id}/findings/{finding_id}/corrections",
    response_model=CorrectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_correction(
    run_id: str,
    finding_id: str,
    body: CorrectionRequest,
    actor_id: str = Depends(require_actor),
) -> CorrectionResponse:
    service = _service()
    try:
        draft = validate_correction_payload(
            field=body.field,
            corrected_value=body.corrected_value,
            reason_comment=body.reason_comment,
            observed_value_on_document=body.observed_value_on_document,
        )
    except CorrectionValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = service.record_correction(
            run_id=run_id,
            finding_id=finding_id,
            draft=draft,
            actor_id=actor_id,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FindingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CorrectionNotApplicableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return CorrectionResponse(
        workflow=result.workflow,
        resolution=result.resolution,
        new_finding_ids=list(result.workflow.new_finding_ids),
        superseded_finding_ids=list(result.workflow.superseded_finding_ids),
    )


@router.get("/runs/{run_id}/export-gate", response_model=ExportGateResponse)
async def get_export_gate(
    run_id: str, _actor: str = Depends(require_actor)
) -> ExportGateResponse:
    try:
        _, grouped = _service().project_report(run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ExportGateResponse(
        status=grouped.export_gate,
        pending_blocking_count=grouped.pending_blocking_count,
    )


@router.post(
    "/runs/{run_id}/reports:export", response_model=ExportRevisionResponse
)
async def export_report(
    run_id: str, actor_id: str = Depends(require_actor)
) -> ExportRevisionResponse:
    service = _service()
    try:
        result = service.export_report(run_id, actor_id=actor_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExportGateBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "export_blocked",
                "status": exc.status.value,
                "pending": exc.pending,
            },
        ) from exc
    revision = result.revision
    return ExportRevisionResponse(
        revision=revision,
        pdf_url=f"/api/bmr/reports/revisions/{revision.revision_id}/pdf",
        bundle_url=f"/api/bmr/reports/revisions/{revision.revision_id}/bundle",
    )


@router.get("/reports/revisions/{revision_id}/pdf")
async def get_revision_pdf(
    revision_id: str, _actor: str = Depends(require_actor)
) -> Response:
    store = _service()._revision_store  # noqa: SLF001
    payload = store.read_pdf(revision_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"revision {revision_id} not found")
    # If WeasyPrint is absent the renderer returns HTML bytes; keep the
    # endpoint honest by sniffing the prefix.
    media_type = "application/pdf" if payload.startswith(b"%PDF") else "text/html"
    return Response(content=payload, media_type=media_type)


@router.get("/reports/revisions/{revision_id}/bundle")
async def get_revision_bundle(
    revision_id: str, _actor: str = Depends(require_actor)
) -> Response:
    store = _service()._revision_store  # noqa: SLF001
    payload = store.read_bundle(revision_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"revision {revision_id} not found")
    return Response(content=payload, media_type="application/json")


@router.get("/feedback/samples", response_model=FeedbackListResponse)
async def list_feedback_samples(
    run_id: str | None = None,
    rule_id: str | None = None,
    reason_type: str | None = None,
    _actor: str = Depends(require_actor),
) -> FeedbackListResponse:
    store = _service()._feedback_store  # noqa: SLF001
    samples = store.list_for_run(run_id) if run_id else store.list_all()
    if rule_id:
        samples = [s for s in samples if s.rule_id == rule_id]
    if reason_type:
        samples = [
            s
            for s in samples
            if s.reason_type is not None and s.reason_type.value == reason_type
        ]
    return FeedbackListResponse(items=samples)


__all__ = ["router"]
