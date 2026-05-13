"""Compliance review graph.

Orchestrator → Segmentation → Section-aware per-page agents → Cross-page
reconciliation → Report generation → Store.

WebSocket progress is emitted at each phase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.api.websocket import manager as ws_manager
from app.compliance.alcoa import ALCOAAgent
from app.compliance.checklist import ChecklistAgent
from app.compliance.cross_page.agent import ReconciliationAgent
from app.compliance.evaluator import _deduplicate_findings
from app.compliance.gmp import GMPAgent
from app.compliance.models import (
    AGENT_DISPLAY_NAMES,
    AgentReport,
    AuditTrail,
    ComplianceFinding,
    ComplianceReport,
    ExecutiveSummary,
    ScoreMethodology,
    SkippedCategory,
)
from app.compliance.orchestrator import ComplianceOrchestrator
from app.compliance.rules.profiles import normalize_document_type
from app.compliance.rules.registry import get_registry
from app.compliance.segmentation import (
    DocumentSegmenter,
    build_page_to_section,
    enrich_with_bpcr_sub_sections,
    load_segmentation,
    store_segmentation,
)
from app.compliance.sop import SOPAgent
from app.config.container import get_container
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_AGENT_CLASSES = {
    "alcoa": ALCOAAgent,
    "gmp": GMPAgent,
    "checklist": ChecklistAgent,
    "sop": SOPAgent,
}


async def _ws_progress(doc_id: str, data: dict) -> None:
    """Send a compliance_progress WS message."""
    await ws_manager.broadcast(doc_id, {"type": "compliance_progress", **data})


async def run_compliance_pipeline(
    doc_id: str,
    extractions: list[dict],
    filename: str = "",
    total_pages: int = 0,
    key_value_pairs: list[dict] | None = None,
    selected_agents: list[str] | None = None,
) -> ComplianceReport:
    """Execute the full compliance audit pipeline.

    Args:
        selected_agents: If provided, only these agents will run (skipping orchestrator
                         relevance filtering for them). ``None`` means run all applicable.
    """

    settings = get_settings()
    config = settings.compliance
    container = get_container()
    registry = get_registry()

    started_at = datetime.now(UTC)
    llm_call_count = 0

    user_selected = bool(selected_agents)
    doc_dir = Path(settings.storage.base_path) / doc_id

    # Bind a per-run telemetry sink so every ``logger.*()`` call,
    # every explicit ``record_event()``, and every sub-step's
    # observability event during this pipeline lands in a single
    # ``doc_dir/telemetry.json`` for post-run validation. The
    # context manager handles flush-on-exit and exception safety.
    from app.observability.run_telemetry import telemetry_run

    async with _telemetry_async_wrapper(doc_id, doc_dir):
        return await _run_compliance_pipeline_inner(
            doc_id, extractions, filename, total_pages,
            key_value_pairs, selected_agents,
            settings, config, container, registry,
            started_at, user_selected, doc_dir,
        )


@asynccontextmanager
async def _telemetry_async_wrapper(doc_id: str, doc_dir: Path):
    """Async wrapper for the (sync) ``telemetry_run`` context.

    The synchronous ContextVar binding is correct across async
    boundaries because ContextVars propagate through ``await``;
    we just need an async context manager shape so the caller
    can use ``async with``.
    """
    from app.observability.run_telemetry import telemetry_run

    with telemetry_run(doc_id=doc_id, doc_dir=doc_dir):
        yield


async def _run_compliance_pipeline_inner(
    doc_id: str,
    extractions: list[dict],
    filename: str,
    total_pages: int,
    key_value_pairs: list[dict] | None,
    selected_agents: list[str] | None,
    settings,
    config,
    container,
    registry,
    started_at: datetime,
    user_selected: bool,
    doc_dir: Path,
) -> ComplianceReport:
    """Actual pipeline body — extracted so the entrypoint can wrap it
    in a telemetry context without indenting hundreds of lines."""

    llm_call_count = 0

    # ── Phase 1: Orchestrator ─────────────────────────────────
    await _ws_progress(doc_id, {
        "phase": "orchestrator",
        "status": "running",
        "label": "Analyzing document type...",
    })

    orch_llm = container.compliance_orchestrator_llm
    orchestrator = ComplianceOrchestrator(orch_llm)
    orch_result = await orchestrator.analyze(
        filename, total_pages, extractions, key_value_pairs,
    )
    orch_result.document_type = normalize_document_type(orch_result.document_type)
    llm_call_count += 1

    if user_selected:
        applicable = list(selected_agents)  # type: ignore[arg-type]
        skipped_names = set(_AGENT_CLASSES.keys()) - set(applicable)
        skipped = [
            SkippedCategory(category=s, reason="Not selected by user")
            for s in skipped_names
        ]
    else:
        applicable = list(orch_result.applicable_categories)
        skipped = orch_result.skipped_categories

    if config.enable_cross_page and "reconciliation" not in applicable:
        if not user_selected:
            applicable.append("reconciliation")

    await _ws_progress(doc_id, {
        "phase": "orchestrator",
        "status": "complete",
        "document_type": orch_result.document_type,
        "applicable": applicable,
        "skipped": [s.model_dump() if hasattr(s, "model_dump") else {"category": s.category, "reason": s.reason} for s in skipped],
    })

    if not applicable:
        report = _build_empty_report(
            doc_id, filename, total_pages, orch_result, started_at, config,
        )
        _store_report(doc_id, report)
        await _ws_progress(doc_id, {
            "phase": "complete",
            "overall_score": report.overall_score,
            "total_findings": 0,
        })
        return report

    # ── Phase 1.5: Segmentation ──────────────────────────────
    section_map: dict[int, dict] = {}
    segmentation = None

    if config.enable_cross_page:
        await _ws_progress(doc_id, {
            "phase": "segmentation",
            "status": "running",
            "label": "Identifying document sections...",
        })

        segmentation = load_segmentation(doc_dir)
        cache_was_used = segmentation is not None
        if segmentation is None:
            cross_llm = container.compliance_cross_page_llm
            segmenter = DocumentSegmenter(cross_llm)
            segmentation = await segmenter.segment(
                extractions, key_value_pairs, filename, total_pages,
            )
            llm_call_count += 1

        # Spec 007 — drill BPCR-classified sections into their 13
        # canonical sub-sections (cover_page, material_dispensing,
        # yield_calculation, …). Pure post-processing; no extra LLM
        # call. Idempotent: re-running on an already-enriched seg
        # regenerates the same rows from the same input, so it's
        # safe to apply on every load. Without this, a doc whose
        # segmentation.json was cached before this feature shipped
        # would silently keep its empty ``sub_sections`` arrays
        # forever — exactly the symptom Akhilesh hit on his re-run.
        needs_enrichment = any(
            not section.sub_sections
            for section in segmentation.sections
            if section.section_type
            and any(
                hint in section.section_type.lower()
                for hint in ("batch_record", "bpcr", "batch_production")
            )
        )
        if needs_enrichment:
            segmentation = enrich_with_bpcr_sub_sections(segmentation, extractions)
            # Re-persist so subsequent loads see the enriched form
            # even if the rest of the pipeline doesn't trigger a write.
            store_segmentation(doc_dir, segmentation)
        elif not cache_was_used:
            # Fresh segmentation that already had no BPCR sections to
            # enrich — still persist so the run produces a stable
            # cache file.
            store_segmentation(doc_dir, segmentation)

        section_map = build_page_to_section(segmentation)
        
        await _ws_progress(doc_id, {
            "phase": "segmentation",
            "status": "complete",
            "sections_count": len(segmentation.sections),
            "label": f"Identified {len(segmentation.sections)} document sections",
        })

        # Phase 1.5b: Page summarization (load-then-generate-then-store)
        from app.compliance.summarizer import summarize_pages_in_batches  # noqa: PLC0415
        await _ws_progress(doc_id, {
            "phase": "summarization",
            "status": "running",
            "label": f"Generating page summaries ({len(extractions)} pages)...",
        })
        summ_llm = container.compliance_cross_page_llm
        await summarize_pages_in_batches(extractions, section_map, doc_id, summ_llm)
        await _ws_progress(doc_id, {
            "phase": "summarization",
            "status": "complete",
            "label": "Page summaries ready",
        })

    # ── Phase 2: Per-page agent evaluation ────────────────────
    eval_llm = container.compliance_evaluator_llm
    vlm_provider = container.vlm
    agent_reports: list[AgentReport] = []
    agents_executed: list[str] = []
    all_cross_refs: list[dict] = []

    async def _run_agent(agent_name: str) -> AgentReport:
        nonlocal llm_call_count
        cls = _AGENT_CLASSES[agent_name]
        agent = cls(eval_llm, registry, config, vlm=vlm_provider)

        all_rules = registry.get_rules(agent_name)
        batches = registry.get_batches(
            agent_name, config.rule_batch_size, config.batch_by_category,
        )
        total_batches = len(batches) * len(extractions)

        rules_manifest = [
            {"id": r.id, "text": r.text[:120], "category": r.category_display, "severity": r.severity_hint}
            for r in all_rules
        ]

        await _ws_progress(doc_id, {
            "phase": "evaluation",
            "agent": agent_name,
            "status": "running",
            "batches_complete": 0,
            "batches_total": total_batches,
            "percent": 0,
            "label": f"{AGENT_DISPLAY_NAMES.get(agent_name, agent_name)}: Starting evaluation...",
            "rules": rules_manifest,
        })

        rule_statuses: dict[str, dict] = {}

        async def _agent_progress(completed: int, total: int, batch, result_tuple=None):
            nonlocal llm_call_count
            llm_call_count += 1
            pct = int((completed / max(total, 1)) * 100)

            rule_updates: list[dict] = []
            if result_tuple:
                _bid, _pnum, batch_result = result_tuple
                for ev in batch_result.evaluations:
                    prev = rule_statuses.get(ev.rule_id, {})
                    prev_conf = prev.get("confidence", 1.0)
                    new_conf = min(prev_conf, ev.confidence)
                    new_status = ev.status
                    if prev.get("status") == "non_compliant":
                        new_status = "non_compliant"
                    elif prev.get("status") == "uncertain" and new_status == "compliant":
                        new_status = "uncertain"

                    rule_statuses[ev.rule_id] = {
                        "status": new_status,
                        "confidence": new_conf,
                    }
                    rule_updates.append({
                        "rule_id": ev.rule_id,
                        "status": new_status,
                        "confidence": round(new_conf, 2),
                    })

                for cr in batch_result.cross_references:
                    all_cross_refs.append({
                        "ref_type": cr.ref_type,
                        "identifier": cr.identifier,
                        "context": cr.context,
                        "page_num": cr.page_num or _pnum,
                    })

            await _ws_progress(doc_id, {
                "phase": "evaluation",
                "agent": agent_name,
                "status": "running",
                "batches_complete": completed,
                "batches_total": total,
                "percent": pct,
                "label": f"{AGENT_DISPLAY_NAMES.get(agent_name, agent_name)}: Evaluating '{batch.category}' rules...",
                "rule_updates": rule_updates,
            })

        async def _prescreen_progress(pages_done: int, total_pages: int, stats: dict):
            nonlocal llm_call_count
            status = stats.get("status", "screening")
            display = AGENT_DISPLAY_NAMES.get(agent_name, agent_name)

            if status == "started":
                await _ws_progress(doc_id, {
                    "phase": "evaluation",
                    "agent": agent_name,
                    "status": "prescreening",
                    "prescreen_pages_done": 0,
                    "prescreen_pages_total": total_pages,
                    "prescreen_percent": 0,
                    "prescreen_total_rules": stats.get("total_rules", 0),
                    "label": f"{display}: Pre-screening pages...",
                })
            elif status == "screening":
                llm_call_count += 1
                pct = int((pages_done / max(total_pages, 1)) * 100)
                await _ws_progress(doc_id, {
                    "phase": "evaluation",
                    "agent": agent_name,
                    "status": "prescreening",
                    "prescreen_pages_done": pages_done,
                    "prescreen_pages_total": total_pages,
                    "prescreen_percent": pct,
                    "prescreen_total_rules": stats.get("total_rules", 0),
                    "prescreen_applicable_count": stats.get("applicable_count", 0),
                    "label": f"{display}: Pre-screening pages ({pages_done}/{total_pages})...",
                })
            elif status == "complete":
                avg = stats.get("avg_applicable", 0)
                total_rules = stats.get("total_rules", 0)
                await _ws_progress(doc_id, {
                    "phase": "evaluation",
                    "agent": agent_name,
                    "status": "prescreen_complete",
                    "prescreen_pages_done": total_pages,
                    "prescreen_pages_total": total_pages,
                    "prescreen_percent": 100,
                    "prescreen_total_rules": total_rules,
                    "prescreen_avg_applicable": avg,
                    "label": f"{display}: Pre-screen done — avg {avg}/{total_rules} rules/page applicable",
                })

        report = await agent.review_document(
            extractions,
            document_type=orch_result.document_type,
            progress_callback=_agent_progress,
            prescreen_callback=_prescreen_progress,
            section_map=section_map if section_map else None,
            global_kv_pairs=key_value_pairs,
            doc_id=doc_id,
        )

        await _ws_progress(doc_id, {
            "phase": "evaluation",
            "agent": agent_name,
            "status": "complete",
            "findings_count": report.total_findings,
            "needs_review_count": sum(1 for f in report.findings if f.hitl_status == "needs_review"),
        })

        return report

    agent_names_to_run = [a for a in applicable if a in _AGENT_CLASSES]

    # An agent the orchestrator selected as applicable but for which the
    # registry holds zero rules cannot produce a meaningful verdict —
    # running it would build an ``AgentReport`` with the dataclass'
    # default ``score=100.0`` and total_rules=0, which downstream renders
    # as "Full compliance with X" in the executive summary even though
    # the agent never evaluated a single rule. Move those agents into
    # ``skipped`` with an explicit reason so the report makes the gap
    # visible instead of papering over it.
    no_rule_agents = [
        name for name in agent_names_to_run
        if not registry.get_rules(name)
    ]
    if no_rule_agents:
        for name in no_rule_agents:
            skipped.append(SkippedCategory(
                category=name,
                reason="No rules are registered for this agent (rule file empty or archived).",
            ))
            logger.warning(
                "Agent %s was selected as applicable but has no rules in "
                "the registry — moved to skipped_agents to avoid emitting "
                "a fabricated 100/100 report.",
                name,
            )
        agent_names_to_run = [a for a in agent_names_to_run if a not in no_rule_agents]

    # When the user selects only zero-rule agents (e.g. picks
    # Checklist whose rules.md is archived), ``agent_names_to_run``
    # is empty here even though ``applicable`` was non-empty
    # earlier. Without this branch the pipeline still runs
    # through report assembly with empty agent_reports — emitting
    # a confusing "0/0 agents done" report where the user can't
    # tell whether anything ran. Surface an explicit empty
    # report carrying the skip reasons so the UI shows
    # "Checklist: no rules registered" instead of a silent void.
    if not agent_names_to_run:
        logger.warning(
            "All selected agents were filtered out (zero rules registered). "
            "Building empty report with skip reasons attached: %s",
            [s.category for s in skipped],
        )
        report = _build_empty_report(
            doc_id, filename, total_pages, orch_result, started_at, config,
        )
        report.skipped_agents = list(skipped)
        # Replace executive summary so the LLM doesn't confabulate
        # "Full compliance" on a run that evaluated zero rules.
        report.executive_summary = ExecutiveSummary(
            overall_assessment=(
                "No rules were evaluated in this run. "
                + ("Selected agent(s) have no rules registered: "
                   + ", ".join(s.category for s in skipped) + ". "
                   if skipped else "")
                + "Select an agent with a non-empty rule file or contact "
                "your admin to publish rules for this agent."
            ),
            key_risks=[],
            strengths=[],
            priority_actions=[
                "Select an agent with rules registered (e.g. ALCOA+) and "
                "re-run the audit.",
            ],
        )
        _store_report(doc_id, report)
        await _ws_progress(doc_id, {
            "phase": "complete",
            "overall_score": report.overall_score,
            "total_findings": 0,
            "skipped_agents": [
                {"category": s.category, "reason": s.reason} for s in skipped
            ],
        })
        return report

    agent_results = await asyncio.gather(
        *[_run_agent(name) for name in agent_names_to_run],
        return_exceptions=True,
    )
    for name, result in zip(agent_names_to_run, agent_results):
        if isinstance(result, Exception):
            logger.error("Agent %s failed: %s", name, result)
        else:
            agent_reports.append(result)
            agents_executed.append(name)

    # Store dependency tags for reconciliation
    if all_cross_refs:
        dep_path = doc_dir / "dependency_tags.json"
        dep_path.write_text(
            json.dumps(all_cross_refs, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    # ── Phase 2.5: Cross-Page Reconciliation ──────────────────
    if "reconciliation" in applicable and config.enable_cross_page and segmentation:
        try:
            cross_llm = container.compliance_cross_page_llm
            recon_rules = registry.get_rules("reconciliation")

            rules_manifest = [
                {"id": r.id, "text": r.text[:120], "category": r.category_display, "severity": r.severity_hint}
                for r in recon_rules
            ]
            await _ws_progress(doc_id, {
                "phase": "evaluation",
                "agent": "reconciliation",
                "status": "running",
                "batches_complete": 0,
                "batches_total": len(recon_rules),
                "percent": 0,
                "label": "Cross-Page Reconciliation: Starting...",
                "rules": rules_manifest,
            })

            recon_rule_statuses: dict[str, dict] = {}

            async def _recon_progress(completed: int, total: int, batch, result_tuple=None):
                nonlocal llm_call_count
                llm_call_count += 1
                pct = int((completed / max(total, 1)) * 100)

                rule_updates: list[dict] = []
                if result_tuple:
                    _bid, _pnum, batch_result = result_tuple
                    for ev in batch_result.evaluations:
                        recon_rule_statuses[ev.rule_id] = {
                            "status": ev.status,
                            "confidence": ev.confidence,
                        }
                        rule_updates.append({
                            "rule_id": ev.rule_id,
                            "status": ev.status,
                            "confidence": round(ev.confidence, 2),
                        })

                await _ws_progress(doc_id, {
                    "phase": "evaluation",
                    "agent": "reconciliation",
                    "status": "running",
                    "batches_complete": completed,
                    "batches_total": total,
                    "percent": pct,
                    "label": "Cross-Page Reconciliation: Evaluating rules...",
                    "rule_updates": rule_updates,
                })

            dep_tags = all_cross_refs
            if not dep_tags:
                dep_path = doc_dir / "dependency_tags.json"
                if dep_path.exists():
                    dep_tags = json.loads(dep_path.read_text(encoding="utf-8"))

            recon_agent = ReconciliationAgent(
                cross_llm, registry, config, segmentation, dep_tags, doc_dir,
            )
            recon_report = await recon_agent.review_document(
                extractions, progress_callback=_recon_progress,
            )
            agent_reports.append(recon_report)
            agents_executed.append("reconciliation")

            await _ws_progress(doc_id, {
                "phase": "evaluation",
                "agent": "reconciliation",
                "status": "complete",
                "findings_count": recon_report.total_findings,
                "needs_review_count": sum(1 for f in recon_report.findings if f.hitl_status == "needs_review"),
            })
        except Exception as exc:
            logger.error("Reconciliation agent failed: %s", exc, exc_info=True)

    # ── Phase 3: Report generation ────────────────────────────
    await _ws_progress(doc_id, {
        "phase": "report",
        "status": "running",
        "label": "Generating executive summary...",
    })

    all_findings: list[ComplianceFinding] = []
    for ar in agent_reports:
        all_findings.extend(ar.findings)

    # Default to attribution-preserving: two agents that both match the
    # same rule_id keep both findings. Tab badges and filtered lists agree
    # by construction. See research §R7.
    all_findings = _deduplicate_findings(all_findings, mode="cross_agent_preserve")

    scored_reports = [ar for ar in agent_reports if ar.total_rules > 0]
    total_rules_weight = sum(ar.total_rules for ar in scored_reports)
    overall_score = (
        sum(ar.score * ar.total_rules for ar in scored_reports) / max(total_rules_weight, 1)
        if scored_reports else 0.0
    )

    severity_counts: dict[str, int] = {}
    for f in all_findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1

    exec_summary = await _generate_executive_summary(
        orch_llm, all_findings, overall_score, agent_reports,
    )
    llm_call_count += 1

    completed_at = datetime.now(UTC)
    total_rules = sum(ar.total_rules for ar in agent_reports)

    report = ComplianceReport(
        report_id=str(uuid.uuid4()),
        doc_id=doc_id,
        filename=filename,
        total_pages=total_pages,
        document_type=orch_result.document_type,
        generated_at=completed_at,
        model_versions={
            "evaluator": config.evaluator_model,
            "orchestrator": config.orchestrator_model,
        },
        overall_score=round(overall_score, 1),
        model_score=round(overall_score, 1),
        review_adjusted_score=round(overall_score, 1),
        score_decomposition={},
        score_methodology=ScoreMethodology(),
        executive_summary=exec_summary,
        total_findings=len(all_findings),
        severity_counts=severity_counts,
        agent_reports=agent_reports,
        skipped_agents=list(skipped),
        findings=all_findings,
        dedup_mode="cross_agent_preserve",
        audit_trail=AuditTrail(
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round((completed_at - started_at).total_seconds(), 1),
            total_llm_calls=llm_call_count,
            total_rules_evaluated=total_rules,
            rule_batch_size=config.rule_batch_size,
            orchestrator_model=config.orchestrator_model,
            evaluator_model=config.evaluator_model,
            agents_executed=agents_executed,
            agents_skipped=[s.category for s in skipped],
        ),
    )

    _store_report(doc_id, report)

    await _ws_progress(doc_id, {
        "phase": "complete",
        "overall_score": report.overall_score,
        "total_findings": report.total_findings,
    })

    # Observability (FR-005): metric rollups for the finished run.
    try:
        from app.observability.metrics import (
            COMPLIANCE_AGENT_DURATION,
            COMPLIANCE_FINDINGS,
            COMPLIANCE_RUN_DURATION,
            COMPLIANCE_RUNS,
        )

        run_duration = (completed_at - started_at).total_seconds()
        COMPLIANCE_RUNS.labels(status="ok").inc()
        COMPLIANCE_RUN_DURATION.labels(status="ok").observe(run_duration)
        for ar in agent_reports:
            # We don't have per-agent wall time here; use the number of
            # findings as a proxy signal for activity and observe the run
            # duration scoped to the agent row (so rate-of-run per agent
            # is visible even before T2.5's per-agent timing lands).
            COMPLIANCE_AGENT_DURATION.labels(
                agent=ar.agent, status="ok"
            ).observe(run_duration)
        for f in report.findings:
            COMPLIANCE_FINDINGS.labels(
                agent=f.agent,
                status=f.status,
                severity=f.severity,
                hitl_status=f.hitl_status,
            ).inc()
    except Exception:  # pragma: no cover — fail-open
        pass

    return report


async def _generate_executive_summary(
    llm, findings: list[ComplianceFinding], score: float, agent_reports: list[AgentReport],
) -> ExecutiveSummary:
    """Use the orchestrator LLM to generate a structured executive summary."""
    findings_text = "\n".join(
        f"- [{f.severity.upper()}] {f.rule_id}: {f.description}" for f in findings[:30]
    )
    # Belt-and-braces: even though the orchestrator filter upstream
    # already drops zero-rule agents, the LLM prompt should never see
    # an agent with ``total_rules == 0`` — feeding it ``score 100/100``
    # rows for un-evaluated agents was the mechanism by which the May 4
    # run fabricated "Full compliance with GMP/SOP/Checklist" in
    # ``strengths``.
    scored_reports = [ar for ar in agent_reports if ar.total_rules > 0]
    agents_text = "\n".join(
        f"- {ar.agent_display}: score {ar.score}/100, {ar.total_findings} findings"
        for ar in scored_reports
    ) or "(no agents produced rule-level verdicts in this run)"

    prompt = (
        f"Based on the compliance audit results below, generate an executive summary.\n\n"
        f"Overall score: {score}/100\n\n"
        f"Agent results:\n{agents_text}\n\n"
        f"Top findings:\n{findings_text}\n\n"
        f"Provide: overall_assessment (2-3 sentences), key_risks (top 3-5), "
        f"strengths (what the document does well), priority_actions (top corrective actions)."
    )

    try:
        result = await llm.generate_structured(
            prompt, ExecutiveSummary,
            system="You are a pharmaceutical regulatory compliance expert writing an audit executive summary.",
        )
        if not isinstance(result, ExecutiveSummary):
            result = ExecutiveSummary.model_validate(result)
        return result
    except Exception:
        logger.exception("Failed to generate executive summary")
        return ExecutiveSummary(
            overall_assessment=f"Compliance audit completed with a score of {score}/100. {len(findings)} findings identified.",
            key_risks=[f.description[:100] for f in findings[:3] if f.severity in ("critical", "major")],
            strengths=["Audit completed successfully"],
            priority_actions=[f.recommendation[:100] for f in findings[:3]],
        )


def _build_empty_report(doc_id, filename, total_pages, orch_result, started_at, config):
    """Build a report when the document is not relevant for compliance."""
    completed_at = datetime.now(UTC)
    return ComplianceReport(
        report_id=str(uuid.uuid4()),
        doc_id=doc_id,
        filename=filename,
        total_pages=total_pages,
        document_type=orch_result.document_type,
        generated_at=completed_at,
        model_versions={
            "evaluator": config.evaluator_model,
            "orchestrator": config.orchestrator_model,
        },
        overall_score=100.0,
        model_score=100.0,
        review_adjusted_score=100.0,
        score_decomposition={},
        executive_summary=ExecutiveSummary(
            overall_assessment="This document was determined to not be relevant for compliance auditing.",
            key_risks=[],
            strengths=[],
            priority_actions=[],
        ),
        skipped_agents=[
            SkippedCategory(category=s.category, reason=s.reason)
            for s in orch_result.skipped_categories
        ],
        audit_trail=AuditTrail(
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=round((completed_at - started_at).total_seconds(), 1),
            total_llm_calls=1,
            total_rules_evaluated=0,
            rule_batch_size=config.rule_batch_size,
            orchestrator_model=config.orchestrator_model,
            evaluator_model=config.evaluator_model,
            agents_executed=[],
            agents_skipped=[s.category for s in orch_result.skipped_categories],
        ),
    )


def _store_report(doc_id: str, report: ComplianceReport) -> None:
    """Persist the compliance report as JSON."""
    settings = get_settings()
    doc_dir = Path(settings.storage.base_path) / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    out = doc_dir / "compliance_result.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Stored compliance report at %s", out)
