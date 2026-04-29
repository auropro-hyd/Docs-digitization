"""BMR audit run routes.

Narrow v0 surface:

- ``POST /api/bmr/runs`` — start a run against an existing package.
- ``GET  /api/bmr/runs/{run_id}`` — fetch the persisted :class:`RunReport`.
- ``GET  /api/bmr/runs`` — list run ids (dev/debug only).

Runs are orchestrated by a :class:`BMRRunService` that owns the compiled
LangGraph. For v0 the graph runs synchronously on a worker thread so the
HTTP handler doesn't block the event loop.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from app.api.deps import require_actor
from app.bmr.events import get_event_bus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.workflow import BMRRunService, RunReport, RunStore, StartRunSpec
from app.bmr.workflow.section_enrichment import build_default_section_enricher
from app.bmr.workflow.service import LegibilityDecisionError
from app.bmr.workflow.stages import bpcr_sections_enabled
from app.config.settings import get_settings

router = APIRouter()


class StartRunRequest(BaseModel):
    package_id: str = Field(min_length=1)
    rules_dir: str | None = Field(
        default=None,
        description="Absolute or repo-relative path to a rule YAML directory."
        " Defaults to the pilot rule bank.",
    )
    aliases_dir: str | None = Field(
        default=None,
        description="Directory of alias YAML files (optional).",
    )
    extraction_path: str | None = Field(
        default=None,
        description="Explicit path to an extraction.json (optional).",
    )


class RunListItem(BaseModel):
    """Lightweight summary row for the run-list endpoint.

    The detail endpoint already returns the full :class:`RunReport`;
    this row carries only what a list UI needs to render and decide
    which run to drill into. Populated by re-reading each persisted
    report — cheap given a v0 run store backed by per-run JSON files.
    Fields beyond ``run_id`` are optional so an unreadable / corrupt
    report row degrades to "we know the id, nothing else" instead of
    failing the whole list response.
    """

    run_id: str
    package_id: str | None = None
    status: str | None = None
    stage: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    total_findings: int | None = None
    bpcr_section_count: int | None = None


class RunListResponse(BaseModel):
    runs: list[RunListItem]


# ── wiring ───────────────────────────────────────────────────────────────────


def _bmr_base_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path).resolve().parent / "bmr-packages"


def _runs_base_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path).resolve().parent / "bmr-runs"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_rules_dir() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "rules"
        / "pilot"
        / "bank"
    ).resolve()


def _default_aliases_dir() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "config"
        / "rules"
        / "pilot"
        / "aliases"
    ).resolve()


@lru_cache(maxsize=1)
def _service() -> BMRRunService:
    package_store = PackageStore(_bmr_base_dir())
    run_store = RunStore(_runs_base_dir())

    # Spec 007 — wire the BPCR section enricher unless the operator
    # has switched it off via env. ``build_default_section_enricher``
    # may return None (e.g. malformed spec YAML); the workflow stage
    # treats that as "no enricher wired", which is the same behaviour
    # as today's pre-Spec-007 path.
    section_enricher = (
        build_default_section_enricher() if bpcr_sections_enabled() else None
    )

    return BMRRunService(
        package_store=package_store,
        run_store=run_store,
        repo_root=_repo_root(),
        section_enricher=section_enricher,
        event_bus=get_event_bus(),
    )


def _allowed_config_roots() -> list[Path]:
    """Roots a caller may point rules/aliases/extraction at.

    Only the pilot config directory and the package-store directory are
    whitelisted. Additional roots can be promoted via env when a deployment
    ships its own rule bank.
    """

    config_root = (Path(__file__).resolve().parents[3] / "config").resolve()
    return [config_root, _bmr_base_dir()]


def _resolve_user_path(raw: str, *, field: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (_repo_root() / candidate)
    resolved = candidate.resolve()
    roots = _allowed_config_roots()
    if not any(resolved.is_relative_to(root) for root in roots):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field} must resolve inside a configured root "
                f"({', '.join(str(r) for r in roots)})"
            ),
        )
    return resolved


# ── endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=RunReport,
    status_code=status.HTTP_201_CREATED,
)
async def start_run(
    body: StartRunRequest, _actor: str = Depends(require_actor)
) -> RunReport:
    service = _service()

    rules_dir = (
        _resolve_user_path(body.rules_dir, field="rules_dir")
        if body.rules_dir
        else _default_rules_dir()
    )
    aliases_dir = (
        _resolve_user_path(body.aliases_dir, field="aliases_dir")
        if body.aliases_dir
        else _default_aliases_dir()
    )
    extraction_path = (
        _resolve_user_path(body.extraction_path, field="extraction_path")
        if body.extraction_path
        else None
    )

    spec = StartRunSpec(
        package_id=body.package_id,
        rules_dir=rules_dir,
        aliases_dir=aliases_dir,
        extraction_path=extraction_path,
    )
    return await service.start_run_async(spec)


@router.get("/runs/{run_id}", response_model=RunReport)
async def get_run(run_id: str, _actor: str = Depends(require_actor)) -> RunReport:
    report = _service().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return report


@router.get("/runs", response_model=RunListResponse)
async def list_runs(_actor: str = Depends(require_actor)) -> RunListResponse:
    """List BMR runs with enough metadata for a list UI to render.

    Reads each persisted report once. The list is small in v0
    (single-tenant, low run volume) so we accept the I/O cost over
    a separate index file that would need to stay in sync with
    the run store.
    """

    service = _service()
    ids = service.list_run_ids()
    items: list[RunListItem] = []
    for run_id in ids:
        report = service.get_report(run_id)
        if report is None:
            # Report file vanished between list and load — still surface
            # the id so the operator can see something is wrong rather
            # than silently dropping the row.
            items.append(RunListItem(run_id=run_id))
            continue
        items.append(
            RunListItem(
                run_id=report.run_id,
                package_id=report.package_id,
                status=report.status.value,
                stage=report.stage.value,
                started_at=report.started_at.isoformat() if report.started_at else None,
                finished_at=report.finished_at.isoformat() if report.finished_at else None,
                total_findings=report.summary.total,
                bpcr_section_count=len(report.bpcr_sections),
            )
        )
    # Newest runs first so the list is useful by default; ``started_at``
    # is the truthful sort key (run_id is a uuid hex with no time order).
    items.sort(
        key=lambda i: i.started_at or "",
        reverse=True,
    )
    return RunListResponse(runs=items)


# ── Legibility HITL interrupt (follow-up #2) ────────────────────────────────


class LegibilityStatusResponse(BaseModel):
    run_id: str
    status: str
    reasons: list[str] = Field(default_factory=list)
    decision: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None


class LegibilityDecisionRequest(BaseModel):
    action: str = Field(..., description="proceed | reupload")
    note: str | None = None


@router.get("/runs/{run_id}/legibility", response_model=LegibilityStatusResponse)
async def get_legibility(
    run_id: str, _actor: str = Depends(require_actor)
) -> LegibilityStatusResponse:
    report = _service().get_report(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return LegibilityStatusResponse(
        run_id=report.run_id,
        status=report.status.value,
        reasons=list(report.legibility_reasons),
        decision=report.legibility_decision,
        decided_by=report.legibility_decided_by,
        decision_note=report.legibility_decision_note,
    )


@router.post(
    "/runs/{run_id}/legibility/decision",
    response_model=RunReport,
    status_code=status.HTTP_200_OK,
)
async def decide_legibility(
    run_id: str,
    body: LegibilityDecisionRequest,
    actor_id: str = Depends(require_actor),
) -> RunReport:
    service = _service()
    try:
        return await asyncio.to_thread(
            service.resume_after_legibility,
            run_id,
            action=body.action,
            actor_id=actor_id,
            note=body.note,
        )
    except LegibilityDecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ── WebSocket lifecycle events (follow-up #6) ───────────────────────────────


@router.websocket("/runs/{run_id}/events")
async def run_events_ws(websocket: WebSocket, run_id: str) -> None:
    """Streams lifecycle events for a run as JSON envelopes.

    Each message has the shape::

        {
            "schema_version": "1.0",
            "event": "run.started" | "run.completed" | "run.failed"
                     | "run.awaiting_legibility_review"
                     | "run.legibility_decided"
                     | "resolution.recorded"
                     | "correction.started" | "correction.applied" | "correction.failed",
            "run_id": "...",
            "timestamp": "2026-04-16T...Z",
            "payload": { ... }
        }

    The bus is in-process for v0; subscribers receive events emitted
    after they connect. A future Redis/NATS adapter can replace the
    default bus without changing clients.
    """

    await websocket.accept()
    bus = get_event_bus()
    queue = bus.subscribe(run_id)

    # Send a snapshot of the run so fresh subscribers know current state.
    report = _service().get_report(run_id)
    await websocket.send_json(
        {
            "schema_version": "1.0",
            "event": "snapshot",
            "run_id": run_id,
            "payload": (
                {"status": report.status.value, "stage": report.stage.value}
                if report
                else {"status": "unknown"}
            ),
        }
    )

    try:
        while True:
            envelope = await queue.get()
            await websocket.send_json(envelope)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(run_id, queue)


__all__ = ["router"]
