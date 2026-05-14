"""Reconciliation agent: orchestrates the full cross-page validation pipeline.

Steps:
  1. Load segmentation + dependency tags
  2. Resolve rule section requirements via LLM
  3. Evaluate cross-page rules (batched by section set)
  4. Auto-discover additional checks (optional)
  5. Evaluate discovered checks
  6. Assemble AgentReport via the shared assembly function
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.compliance.cross_page.discovery import (
    AutoDiscovery,
    checks_to_discovered_rules,
    load_discovered_rules,
    store_discovered_rules,
)
from app.compliance.cross_page.evaluator import (
    CrossPageEvaluator,
    gather_section_content,
    group_rules_by_sections,
)
from app.compliance.cross_page.interface import resolve_requirement
from app.compliance.cross_page.resolver import SectionResolver
from app.compliance.evaluator import assemble_agent_report, synthesize_rule_evidence
from app.compliance.models import (
    AgentReport,
    DocumentSegmentation,
    RuleBatchResult,
    RuleEvaluation,
    SectionResolution,
    SectionRef,
)
from app.compliance.rules.registry import AuditRule, RuleBatch, RuleRegistry
from app.config.settings import ComplianceConfig
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

AGENT_NAME = "reconciliation"
_CONFIDENCE_PENALTY = 0.85


class ReconciliationAgent:
    """Orchestrates cross-page reconciliation."""

    def __init__(
        self,
        llm: LLMProvider,
        registry: RuleRegistry,
        config: ComplianceConfig,
        segmentation: DocumentSegmentation,
        dependency_tags: list[dict],
        doc_dir: Path,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._config = config
        self._segmentation = segmentation
        self._dep_tags = dependency_tags
        self._doc_dir = doc_dir

    async def review_document(
        self,
        extractions: list[dict],
        progress_callback=None,
    ) -> AgentReport:
        all_rules = self._registry.get_rules(AGENT_NAME)
        if not all_rules:
            return AgentReport(agent=AGENT_NAME, agent_display="Cross-Page Reconciliation")

        resolver = SectionResolver(self._llm)
        evaluator = CrossPageEvaluator(self._llm)

        # Step 1: Resolve which sections each rule needs
        rules_with_sections = [r for r in all_rules if r.context_sections]
        wildcard_rules = [r for r in all_rules if not r.context_sections]

        resolution = await resolver.resolve(rules_with_sections, self._segmentation)

        # Deterministic cross-section requirement resolver (config-driven).
        existing_resolved_ids = {r.rule_id for r in resolution.resolutions}
        for rule in all_rules:
            if rule.id in existing_resolved_ids or not rule.cross_section_requirements:
                continue
            for req_spec in rule.cross_section_requirements:
                req_resolution = resolve_requirement(self._segmentation, req_spec)
                matched_ids = sorted(set(
                    list(req_resolution.evidence.source_section_ids) +
                    list(req_resolution.evidence.target_section_ids)
                ))
                resolution.resolutions.append(SectionResolution(
                    rule_id=rule.id,
                    matched_section_ids=matched_ids,
                    applicable=req_resolution.applicable,
                    # Use the resolver's normalized requirement_id —
                    # it formats inline dicts as "doc.section" rather
                    # than dumping the raw repr into the trace.
                    reason=f"{req_resolution.requirement_id}: {req_resolution.reason}",
                ))

        all_section_ids = [s.section_id for s in self._segmentation.sections]
        for r in wildcard_rules:
            resolution.resolutions.append(SectionResolution(
                rule_id=r.id,
                matched_section_ids=all_section_ids,
                applicable=True,
                reason="No section constraint (wildcard)",
            ))

        # Step 2: Group rules by resolved section set
        groups = group_rules_by_sections(all_rules, resolution.resolutions)

        batch_results: list[tuple[str, int, RuleBatchResult]] = []
        total_groups = len(groups)
        completed = 0

        for section_set, rules_group in groups.items():
            content, meta = gather_section_content(
                section_set, extractions, self._segmentation,
            )
            result = await evaluator.evaluate(
                rules_group, content, meta, self._dep_tags,
            )
            batch_id = f"recon-{'-'.join(section_set)}"
            batch_results.append((batch_id, 0, result))
            completed += 1

            if progress_callback:
                batch = RuleBatch(
                    batch_id=batch_id,
                    category=rules_group[0].category if rules_group else "cross_page",
                    agent=AGENT_NAME,
                    rules=rules_group,
                )
                await progress_callback(
                    completed, total_groups, batch, (batch_id, 0, result),
                )

        # Step 3: Handle not-applicable rules
        resolved_ids = {
            r.rule_id
            for r in resolution.resolutions
            if r.applicable
        }
        for rule in all_rules:
            if rule.id not in resolved_ids:
                na_result = RuleBatchResult(evaluations=[
                    RuleEvaluation(rule_id=rule.id, status="not_applicable"),
                ])
                batch_results.append((f"recon-na-{rule.id}", 0, na_result))

        # Step 4: Auto-discovery (optional)
        if self._config.auto_discover_checks:
            discovered = await self._run_auto_discovery(
                all_rules, evaluator, extractions, batch_results,
                progress_callback, completed, total_groups,
            )
            batch_results.extend(discovered)

        # Step 5: Assemble report
        pages = sorted({ext.get("page_num", 0) for ext in extractions})
        if self._config.evidence_synthesis_enabled:
            batch_results = await synthesize_rule_evidence(
                batch_results,
                self._llm,
                threshold=self._config.evidence_synthesis_threshold,
                batch_size=self._config.evidence_synthesis_batch_size,
            )
        report = assemble_agent_report(AGENT_NAME, all_rules, batch_results, pages)

        # Step 6: Post-process findings with section_refs
        self._enrich_findings_with_section_refs(report, resolution)

        return report

    async def _run_auto_discovery(
        self,
        predefined_rules: list[AuditRule],
        evaluator: CrossPageEvaluator,
        extractions: list[dict],
        current_results: list[tuple[str, int, RuleBatchResult]],
        progress_callback,
        completed: int,
        total_groups: int,
    ) -> list[tuple[str, int, RuleBatchResult]]:
        discovery = AutoDiscovery(self._llm)

        evaluated_summary = "\n".join(
            f"- {r.id}: {r.text}" for r in predefined_rules
        )

        existing_discovered = load_discovered_rules(self._doc_dir)
        new_checks = await discovery.discover(
            self._segmentation, evaluated_summary, self._dep_tags,
        )
        all_discovered = checks_to_discovered_rules(new_checks, existing_discovered)
        store_discovered_rules(self._doc_dir, all_discovered)

        additional_results: list[tuple[str, int, RuleBatchResult]] = []
        for i, dr in enumerate(all_discovered):
            if dr.promoted:
                continue

            section_ids = dr.section_ids or [
                s.section_id for s in self._segmentation.sections
            ]
            content, meta = gather_section_content(
                tuple(section_ids), extractions, self._segmentation,
            )

            temp_rule = AuditRule(
                id=f"DISC-{i + 1}",
                number=i + 1,
                category="auto_discovered",
                category_display="Auto-Discovered",
                agent=AGENT_NAME,
                text=dr.description,
                severity_hint="observation",
            )

            result = await evaluator.evaluate(
                [temp_rule], content, meta, self._dep_tags,
            )

            for ev in result.evaluations:
                ev.confidence = round(ev.confidence * _CONFIDENCE_PENALTY, 3)

            batch_id = f"recon-disc-{i}"
            additional_results.append((batch_id, 0, result))

        return additional_results

    def _enrich_findings_with_section_refs(self, report: AgentReport, resolution) -> None:
        """Add section_refs to cross-page findings for structured UI display."""
        res_map = {r.rule_id: r for r in resolution.resolutions}
        sec_map = {s.section_id: s for s in self._segmentation.sections}

        for finding in report.findings:
            finding.source = "auto_discovered" if finding.rule_id.startswith("DISC-") else "predefined"
            res = res_map.get(finding.rule_id)
            if res:
                for sid in res.matched_section_ids:
                    sec = sec_map.get(sid)
                    if sec:
                        finding.section_refs.append(SectionRef(
                            section_id=sid,
                            section_name=sec.name,
                            pages=list(range(sec.start_page, sec.end_page + 1)),
                        ))
                if not finding.page_numbers:
                    finding.page_numbers = sorted({
                        p for ref in finding.section_refs for p in ref.pages
                    })
