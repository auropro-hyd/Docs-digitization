"""Orchestration service that ties the 5-stage graph to persistence.

Lifecycle:

1. :meth:`BMRRunService.start_run` — create a ``run_id`` + initial
   :class:`RunReport` stub (status=PENDING) and persist it.
2. Invoke the compiled LangGraph synchronously. When the legibility
   stage detects that the package is in ``NEEDS_REVIEW`` the graph
   short-circuits to a terminal ``AWAITING_LEGIBILITY_REVIEW`` report so
   a reviewer can decide whether to proceed or re-upload.
3. :meth:`BMRRunService.resume_after_legibility` accepts the reviewer's
   decision and either resumes the run (skipping the legibility gate)
   or terminates it with a ``FAILED`` status carrying the reviewer note.

An async variant (``start_run_async``) is provided so the HTTP route can
run the graph off the request thread without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.bmr.events import EventBus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.workflow.extractor import ExtractorPort
from app.bmr.workflow.graph import build_bmr_graph
from app.bmr.workflow.models import RunReport, RunStage, RunStatus, now_utc
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.state import BMRRunState


def _package_snapshot_hash(
    package_store: PackageStore, package_id: str
) -> str | None:
    """Stable hash of the package.json body at the moment of capture.

    Used to detect drift between the first pass (which paused at
    legibility review) and the resume call — if the document set has
    been edited or re-uploaded, compliance would silently run against a
    different package than the reviewer approved.
    """

    pkg = package_store.load(package_id)
    if pkg is None:
        return None
    canonical = pkg.model_dump_json()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

EventPublisher = Callable[[str, str, dict[str, Any]], None]


def _noop_publisher(_event: str, _run_id: str, _payload: dict[str, Any]) -> None:
    return None

logger = logging.getLogger(__name__)


class LegibilityDecisionError(ValueError):
    """Raised when a legibility decision cannot be applied."""


@dataclass(frozen=True)
class StartRunSpec:
    """Inputs required to kick off a BMR audit run."""

    package_id: str
    rules_dir: Path
    aliases_dir: Path | None = None
    extraction_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class BMRRunService:
    """Synchronous + async entry points around the compiled graph."""

    def __init__(
        self,
        *,
        package_store: PackageStore,
        run_store: RunStore,
        repo_root: Path,
        extractor: ExtractorPort | None = None,
        section_enricher: Any | None = None,
        event_bus: EventBus | None = None,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._package_store = package_store
        self._run_store = run_store
        self._repo_root = Path(repo_root).resolve()
        self._graph = build_bmr_graph(
            package_store=package_store,
            repo_root=self._repo_root,
            extractor=extractor,
            section_enricher=section_enricher,
        )
        if event_publisher is not None and event_bus is not None:
            raise ValueError(
                "provide either event_bus or event_publisher, not both"
            )
        if event_publisher is not None:
            self._publish: EventPublisher = event_publisher
        elif event_bus is not None:
            self._publish = event_bus.publish
        else:
            self._publish = _noop_publisher

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    def start_run(self, spec: StartRunSpec) -> RunReport:
        run_id = self._run_store.new_run_id()
        started_at = now_utc()
        snapshot_hash = _package_snapshot_hash(
            self._package_store, spec.package_id
        )
        # Bind business context for the remainder of this call — log lines
        # emitted by every downstream stage inherit run_id + doc_id.
        from app.observability import bind_context, reset_context
        from app.observability.metrics import BMR_RUN_DURATION, BMR_RUNS, BMR_RUNS_IN_FLIGHT

        scope_token = bind_context(run_id=run_id, doc_id=spec.package_id)
        BMR_RUNS_IN_FLIGHT.inc()
        run_started = time.monotonic()

        pending = RunReport(
            run_id=run_id,
            package_id=spec.package_id,
            status=RunStatus.PENDING,
            stage=RunStage.INGEST,
            started_at=started_at,
            rules_dir=str(spec.rules_dir),
            aliases_dir=str(spec.aliases_dir) if spec.aliases_dir else None,
            repo_root=str(self._repo_root),
            package_snapshot_hash=snapshot_hash,
        )
        self._run_store.save(pending)
        self._publish(
            "run.started",
            run_id,
            {
                "package_id": spec.package_id,
                "rules_dir": str(spec.rules_dir),
            },
        )

        initial_state = self._build_initial_state(
            run_id=run_id,
            spec=spec,
            started_at=started_at,
            legibility_override=False,
        )
        try:
            report = self._invoke_and_persist(
                run_id=run_id,
                spec=spec,
                started_at=started_at,
                initial_state=initial_state,
            )
        finally:
            BMR_RUNS_IN_FLIGHT.dec()
            duration = time.monotonic() - run_started
            # Resolve terminal status from the persisted report if available,
            # otherwise fall back to a generic "failed" bucket.
            try:
                terminal = report.status.value  # type: ignore[union-attr]
            except Exception:
                terminal = "failed"
            try:
                BMR_RUNS.labels(status=terminal).inc()
                BMR_RUN_DURATION.labels(status=terminal).observe(duration)
            except Exception:  # pragma: no cover — fail-open
                pass
            reset_context(scope_token)
        return report

    def resume_after_legibility(
        self,
        run_id: str,
        *,
        action: str,
        actor_id: str,
        note: str | None = None,
    ) -> RunReport:
        """Apply the reviewer's legibility decision and finalize the run.

        ``action`` is either ``"proceed"`` (continue the pipeline,
        skipping the legibility gate) or ``"reupload"`` (abort — the
        reviewer will re-ingest a cleaner package).
        """

        if action not in {"proceed", "reupload"}:
            raise LegibilityDecisionError(
                f"unknown legibility action {action!r}; expected 'proceed' or 'reupload'"
            )

        report = self._run_store.load(run_id)
        if report is None:
            raise LegibilityDecisionError(f"run {run_id!r} not found")
        if report.status is not RunStatus.AWAITING_LEGIBILITY_REVIEW:
            raise LegibilityDecisionError(
                f"run {run_id!r} is {report.status.value}, not awaiting legibility review"
            )

        if action == "proceed" and report.package_snapshot_hash is not None:
            current_hash = _package_snapshot_hash(
                self._package_store, report.package_id
            )
            if current_hash != report.package_snapshot_hash:
                raise LegibilityDecisionError(
                    f"package {report.package_id!r} changed between pause and "
                    "resume; abort and start a new run",
                )

        decided_at = now_utc()
        report.legibility_decision = action
        report.legibility_decided_at = decided_at
        report.legibility_decided_by = actor_id
        report.legibility_decision_note = note

        self._publish(
            "run.legibility_decided",
            run_id,
            {"action": action, "actor_id": actor_id, "note": note},
        )

        if action == "reupload":
            report.status = RunStatus.FAILED
            report.stage = RunStage.LEGIBILITY_AND_CLASSIFICATION
            report.error = (
                f"legibility review rejected by {actor_id}; reviewer note: "
                f"{note or '(none)'}"
            )
            report.finished_at = decided_at
            self._run_store.save(report)
            self._publish(
                "run.failed",
                run_id,
                {"reason": "legibility_reupload", "error": report.error},
            )
            return report

        # proceed → re-invoke the graph with legibility_override=True so
        # the gate is bypassed on the resume pass.
        spec = StartRunSpec(
            package_id=report.package_id,
            rules_dir=Path(report.rules_dir) if report.rules_dir else self._repo_root,
            aliases_dir=Path(report.aliases_dir) if report.aliases_dir else None,
        )
        initial_state = self._build_initial_state(
            run_id=run_id,
            spec=spec,
            started_at=report.started_at,
            legibility_override=True,
        )
        resumed = self._invoke_and_persist(
            run_id=run_id,
            spec=spec,
            started_at=report.started_at,
            initial_state=initial_state,
        )
        resumed.legibility_decision = action
        resumed.legibility_decided_at = decided_at
        resumed.legibility_decided_by = actor_id
        resumed.legibility_decision_note = note
        resumed.legibility_reasons = list(report.legibility_reasons)
        self._run_store.save(resumed)
        return resumed

    def _build_initial_state(
        self,
        *,
        run_id: str,
        spec: StartRunSpec,
        started_at: Any,
        legibility_override: bool,
    ) -> BMRRunState:
        initial_state: BMRRunState = {
            "run_id": run_id,
            "package_id": spec.package_id,
            "rules_dir": str(spec.rules_dir),
            "started_at": started_at,
            "status": RunStatus.RUNNING,
            "stage": RunStage.INGEST,
            "findings": [],
            "repo_root": str(self._repo_root),
            "legibility_override": legibility_override,
        }
        if spec.aliases_dir is not None:
            initial_state["aliases_dir"] = str(spec.aliases_dir)
        if spec.extraction_path is not None:
            initial_state["extraction_path"] = str(spec.extraction_path)
        return initial_state

    def _invoke_and_persist(
        self,
        *,
        run_id: str,
        spec: StartRunSpec,
        started_at: Any,
        initial_state: BMRRunState,
    ) -> RunReport:
        try:
            final_state: BMRRunState = self._graph.invoke(initial_state)
        except Exception as exc:
            logger.exception("BMR run %s crashed", run_id)
            failed = RunReport(
                run_id=run_id,
                package_id=spec.package_id,
                status=RunStatus.FAILED,
                stage=RunStage.INGEST,
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                finished_at=now_utc(),
            )
            self._run_store.save(failed)
            return failed

        report = final_state.get("report")
        if report is None:
            report = RunReport(
                run_id=run_id,
                package_id=spec.package_id,
                status=RunStatus.FAILED,
                stage=final_state.get("stage", RunStage.INGEST),
                error=final_state.get("error") or "graph produced no report",
                started_at=started_at,
                finished_at=now_utc(),
            )
        self._run_store.save(report)
        self._publish_terminal(report)
        return report

    def _publish_terminal(self, report: RunReport) -> None:
        if report.status is RunStatus.COMPLETED:
            self._publish(
                "run.completed",
                report.run_id,
                {
                    "rules_evaluated": report.rules_evaluated,
                    "finding_count": len(report.findings),
                    "summary": report.summary.model_dump(),
                },
            )
        elif report.status is RunStatus.FAILED:
            self._publish(
                "run.failed",
                report.run_id,
                {"error": report.error, "stage": report.stage.value},
            )
        elif report.status is RunStatus.AWAITING_LEGIBILITY_REVIEW:
            self._publish(
                "run.awaiting_legibility_review",
                report.run_id,
                {"reasons": list(report.legibility_reasons)},
            )

    async def start_run_async(self, spec: StartRunSpec) -> RunReport:
        return await asyncio.to_thread(self.start_run, spec)

    def get_report(self, run_id: str) -> RunReport | None:
        return self._run_store.load(run_id)

    def list_run_ids(self) -> list[str]:
        return self._run_store.list_ids()


__all__ = ["BMRRunService", "LegibilityDecisionError", "StartRunSpec"]
