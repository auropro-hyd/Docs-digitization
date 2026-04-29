"""LangGraph composition for the 5-stage BMR audit pipeline.

Topology (Constitution II)::

    START → ingest → legibility_and_classification → extraction → compliance → report → END

If any stage sets ``status=FAILED``, subsequent stages short-circuit: they
pass through without doing work, the report stage still runs and records
the error so the caller always gets a terminal :class:`RunReport`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.bmr.ingest.package_store import PackageStore
from app.bmr.workflow.extractor import ExtractorPort
from app.bmr.workflow.models import RunStage, RunStatus, now_utc
from app.bmr.workflow.stages import (
    _observe_stage,
    legibility_and_classification_stage,
    make_compliance_stage,
    make_extraction_stage,
    make_ingest_stage,
    report_stage,
)
from app.bmr.workflow.state import BMRRunState

_TERMINAL_STATUSES = frozenset(
    {RunStatus.FAILED, RunStatus.AWAITING_LEGIBILITY_REVIEW}
)


def _route_after(stage_name: str) -> Any:
    def router(state: BMRRunState) -> str:
        if state.get("status") in _TERMINAL_STATUSES:
            return "report_stage"
        return stage_name

    return router


def _report_on_failure(state: BMRRunState) -> dict[str, Any]:
    """Force the final report to reflect an existing non-completed state.

    When an earlier stage set ``status=FAILED`` (hard error) or
    ``status=AWAITING_LEGIBILITY_REVIEW`` (legibility HITL interrupt),
    we still want a terminal :class:`RunReport` so the caller always has
    something to persist.
    """

    status = state.get("status")
    if status not in _TERMINAL_STATUSES:
        return report_stage(state)

    from app.bmr.workflow.models import RunReport

    run_id = state.get("run_id", "unknown")
    report = RunReport(
        run_id=run_id,
        package_id=state.get("package_id", "unknown"),
        status=status,
        stage=state.get("stage", RunStage.INGEST),
        rules_evaluated=int(state.get("rules_evaluated", 0)),
        findings=[],
        error=state.get("error") if status is RunStatus.FAILED else None,
        started_at=state.get("started_at") or now_utc(),
        finished_at=now_utc() if status is RunStatus.FAILED else None,
        rules_dir=state.get("rules_dir"),
        aliases_dir=state.get("aliases_dir"),
        repo_root=state.get("repo_root"),
        legibility_reasons=list(state.get("legibility_reasons") or []),
    )
    return {"stage": RunStage.REPORT, "status": status, "report": report}


def build_bmr_graph(
    *,
    package_store: PackageStore,
    repo_root: Path,
    extractor: ExtractorPort | None = None,
    section_enricher: Any | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Compile and return the BMR audit LangGraph.

    ``extractor`` selects the Stage 3 adapter. The default
    :class:`~app.bmr.workflow.extractor.SidecarExtractor` keeps v0
    behaviour (``extraction.json`` sidecar); production wiring passes
    :class:`~app.bmr.workflow.extractor.OCRBackedExtractor` via the
    service constructor.

    ``section_enricher`` is the Spec 007 post-extraction hook. None
    (default) means: section detection is wired off and existing
    behaviour is preserved exactly. Production composes a real
    enricher via the service constructor.
    """

    builder: StateGraph = StateGraph(BMRRunState)

    ingest_stage = make_ingest_stage(package_store)
    extraction_stage = make_extraction_stage(
        package_store,
        extractor=extractor,
        section_enricher=section_enricher,
    )
    compliance_stage = make_compliance_stage(repo_root=repo_root)

    builder.add_node(
        "ingest_stage", _observe_stage(RunStage.INGEST, ingest_stage)
    )
    builder.add_node(
        "legibility_and_classification_stage",
        _observe_stage(
            RunStage.LEGIBILITY_AND_CLASSIFICATION,
            legibility_and_classification_stage,
        ),
    )
    builder.add_node(
        "extraction_stage",
        _observe_stage(RunStage.EXTRACTION, extraction_stage),
    )
    builder.add_node(
        "compliance_stage",
        _observe_stage(RunStage.COMPLIANCE, compliance_stage),
    )
    builder.add_node(
        "report_stage",
        _observe_stage(RunStage.REPORT, _report_on_failure),
    )

    builder.add_edge(START, "ingest_stage")
    builder.add_conditional_edges(
        "ingest_stage",
        _route_after("legibility_and_classification_stage"),
        {
            "legibility_and_classification_stage": "legibility_and_classification_stage",
            "report_stage": "report_stage",
        },
    )
    builder.add_conditional_edges(
        "legibility_and_classification_stage",
        _route_after("extraction_stage"),
        {"extraction_stage": "extraction_stage", "report_stage": "report_stage"},
    )
    builder.add_conditional_edges(
        "extraction_stage",
        _route_after("compliance_stage"),
        {"compliance_stage": "compliance_stage", "report_stage": "report_stage"},
    )
    builder.add_conditional_edges(
        "compliance_stage",
        _route_after("report_stage"),
        {"report_stage": "report_stage"},
    )
    builder.add_edge("report_stage", END)

    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_bmr_graph"]
