"""Compliance review LangGraph subgraph.

Runs ALCOA++, GMP, Checklist, and SOP agents in parallel via Send,
then aggregates findings into a compliance report.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.config.container import get_container
from app.workflow.state import ComplianceState

logger = logging.getLogger(__name__)


async def fan_out_compliance(state: ComplianceState) -> list[Send]:
    """Fan out to all four compliance agents in parallel."""
    return [
        Send("alcoa_review", state),
        Send("gmp_review", state),
        Send("checklist_review", state),
        Send("sop_review", state),
    ]


async def alcoa_review(state: ComplianceState) -> dict:
    from app.compliance.alcoa import ALCOAAgent

    container = get_container()
    agent = ALCOAAgent(container.llm)
    findings = await agent.review_document(state["extractions"])
    return {"alcoa_findings": [_finding_to_dict(f) for f in findings]}


async def gmp_review(state: ComplianceState) -> dict:
    from app.compliance.gmp import GMPAgent

    container = get_container()
    agent = GMPAgent(container.llm)
    findings = await agent.review_document(state["extractions"])
    return {"gmp_findings": [_finding_to_dict(f) for f in findings]}


async def checklist_review(state: ComplianceState) -> dict:
    from app.compliance.checklist import ChecklistAgent

    container = get_container()
    agent = ChecklistAgent(container.llm)
    findings = await agent.review_document(state["extractions"])
    return {"checklist_findings": [_finding_to_dict(f) for f in findings]}


async def sop_review(state: ComplianceState) -> dict:
    from app.compliance.sop import SOPAgent

    container = get_container()
    agent = SOPAgent(container.llm)
    findings = await agent.review_document(state["extractions"])
    return {"sop_findings": [_finding_to_dict(f) for f in findings]}


async def aggregate_findings(state: ComplianceState) -> dict:
    """Aggregate all compliance findings and compute overall score."""
    all_findings = (
        state.get("alcoa_findings", [])
        + state.get("gmp_findings", [])
        + state.get("checklist_findings", [])
        + state.get("sop_findings", [])
    )

    severity_weights = {"critical": 10, "major": 5, "minor": 2, "observation": 1}
    total_deductions = sum(severity_weights.get(f.get("severity", "observation"), 1) for f in all_findings)

    max_score = 100
    score = max(0, max_score - total_deductions)

    return {
        "aggregated_findings": all_findings,
        "compliance_score": score,
    }


def _finding_to_dict(finding) -> dict:
    return {
        "rule_id": finding.rule_id,
        "rule_category": finding.rule_category,
        "severity": finding.severity,
        "page_num": finding.page_num,
        "description": finding.description,
        "recommendation": finding.recommendation,
    }


def build_compliance_graph(checkpointer=None):
    """Build the compliance review LangGraph subgraph."""
    builder = StateGraph(ComplianceState)

    builder.add_node("alcoa_review", alcoa_review)
    builder.add_node("gmp_review", gmp_review)
    builder.add_node("checklist_review", checklist_review)
    builder.add_node("sop_review", sop_review)
    builder.add_node("aggregate_findings", aggregate_findings)

    builder.add_conditional_edges(START, fan_out_compliance)

    builder.add_edge("alcoa_review", "aggregate_findings")
    builder.add_edge("gmp_review", "aggregate_findings")
    builder.add_edge("checklist_review", "aggregate_findings")
    builder.add_edge("sop_review", "aggregate_findings")
    builder.add_edge("aggregate_findings", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)
