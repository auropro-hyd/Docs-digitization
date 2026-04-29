"""Compliance audit Pydantic schemas.

Two layers:
  Layer 1 – LLM Output Schemas: enforced per LLM call via ``response_format``.
  Layer 2 – Report Schemas: assembled deterministically from LLM outputs.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════
#  Enums
# ═══════════════════════════════════════════════════════════════


class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    OBSERVATION = "observation"


SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 10,
    "major": 5,
    "minor": 2,
    "observation": 1,
}


class ComplianceStatus(str, Enum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NOT_APPLICABLE = "not_applicable"
    UNCERTAIN = "uncertain"


class AuditCategory(str, Enum):
    ALCOA = "alcoa"
    GMP = "gmp"
    CHECKLIST = "checklist"
    SOP = "sop"


AGENT_DISPLAY_NAMES: dict[str, str] = {
    "alcoa": "ALCOA+",
    "gmp": "GMP Validation",
    "checklist": "Checklist Review",
    "sop": "SOP Compliance",
    "reconciliation": "Cross-Page Reconciliation",
}


class DocumentType(str, Enum):
    BATCH_RECORD = "batch_record"
    SOP = "sop"
    PROTOCOL = "protocol"
    CERTIFICATE = "certificate"
    LOGBOOK = "logbook"
    OTHER = "other"


# ═══════════════════════════════════════════════════════════════
#  Vision Models (VLM structured output)
# ═══════════════════════════════════════════════════════════════


class VisualRegion(BaseModel):
    """Bounding region for a visual finding (normalized 0-1 coordinates)."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    label: str = ""


class VisualCheckResult(BaseModel):
    """Result of a single visual check on a page image."""

    check_id: str = ""
    detected: bool = False
    classification: str = ""
    confidence: float = 0.0
    description: str = ""
    regions: list[VisualRegion] = Field(default_factory=list)


class VisionBatchResult(BaseModel):
    """VLM output: visual check results for a batch of rules on one page."""

    page_num: int = 0
    checks: list[VisualCheckResult] = Field(default_factory=list)
    rule_evaluations: list[RuleEvaluation] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  Layer 1: LLM Output Schemas
# ═══════════════════════════════════════════════════════════════


class RuleEvaluation(BaseModel):
    """Result for a single rule evaluated against a single page."""

    rule_id: str
    status: str = "compliant"
    severity: str | None = None
    confidence: float = 1.0
    reasoning: str = ""
    evidence: str = ""
    description: str = ""
    recommendation: str = ""
    applicability_trace: list[str] = Field(default_factory=list)


class CrossReference(BaseModel):
    """Dependency cue tagged during per-page evaluation."""

    ref_type: str = ""
    identifier: str = ""
    context: str = ""
    page_num: int = 0


class RuleBatchResult(BaseModel):
    """Output of one LLM call: N rules evaluated against 1 page."""

    evaluations: list[RuleEvaluation] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)


class ApplicabilityScreenResult(BaseModel):
    """Lightweight LLM pre-screen: identifies which rules apply to a page.

    Used by the hybrid applicability gate (Tier 2) to replace brittle
    static keyword/page-type filters with content-aware classification.
    """

    applicable_rule_ids: list[str] = Field(default_factory=list)
    reasoning: str = ""


class SkippedCategory(BaseModel):
    category: str
    reason: str


class OrchestratorResult(BaseModel):
    """Orchestrator decision: relevance and routing."""

    is_relevant: bool = True
    confidence: float = 0.8
    document_type: str = "batch_record"
    document_type_reasoning: str = ""
    applicable_categories: list[str] = Field(default_factory=list)
    skipped_categories: list[SkippedCategory] = Field(default_factory=list)


class DocumentSection(BaseModel):
    """A distinct sub-document identified during segmentation."""

    section_id: str = ""
    name: str = ""
    section_type: str = ""
    document_type: str = ""  # sub-document classifier; normalized in build_page_to_section
    start_page: int = 0
    end_page: int = 0
    description: str = ""


class DocumentSegmentation(BaseModel):
    """LLM-identified document structure — stored as ``segmentation.json``."""

    sections: list[DocumentSection] = Field(default_factory=list)
    document_type: str = ""
    confidence: float = 0.0


class SectionResolution(BaseModel):
    """Mapping of one cross-page rule to matched document sections."""

    rule_id: str = ""
    matched_section_ids: list[str] = Field(default_factory=list)
    applicable: bool = True
    reason: str = ""


class SectionResolutionResult(BaseModel):
    """LLM output: all rule-to-section mappings."""

    resolutions: list[SectionResolution] = Field(default_factory=list)


class DiscoveredRule(BaseModel):
    """An auto-discovered cross-page check persisted for reproducibility."""

    description: str = ""
    sections_semantic: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    reasoning: str = ""
    priority: str = "medium"
    discovered_at: str = ""
    promoted: bool = False


class ExecutiveSummary(BaseModel):
    """LLM-generated but schema-constrained summary."""

    overall_assessment: str = ""
    key_risks: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    priority_actions: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
#  Layer 2: Report Schemas (deterministic assembly)
# ═══════════════════════════════════════════════════════════════


class HITLStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"
    NEEDS_REVIEW = "needs_review"
    USER_APPROVED = "user_approved"
    USER_REJECTED = "user_rejected"
    USER_MODIFIED = "user_modified"


class SectionRef(BaseModel):
    """Reference to a document section in a cross-page finding."""

    section_id: str = ""
    section_name: str = ""
    pages: list[int] = Field(default_factory=list)


class ComplianceFinding(BaseModel):
    """A single non-compliant or uncertain finding."""

    finding_id: str
    rule_id: str
    rule_text: str = ""
    rule_category: str
    rule_category_display: str = ""
    agent: str
    severity: str
    status: str = "non_compliant"
    confidence: float = 1.0
    page_numbers: list[int] = Field(default_factory=list)
    reasoning: str = ""
    evidence: str = ""
    description: str = ""
    recommendation: str = ""
    applicability_trace: list[str] = Field(default_factory=list)
    resolved: bool = False
    hitl_status: str = "auto_approved"
    hitl_note: str = ""
    hitl_reviewed_at: str | None = None
    source: str = "predefined"
    section_refs: list[SectionRef] = Field(default_factory=list)
    # Vision evaluation extensions
    evaluation_channels: list[str] = Field(default_factory=list)
    visual_evidence: str = ""
    visual_regions: list[VisualRegion] = Field(default_factory=list)


class CategoryScore(BaseModel):
    """Score for one rule category within an agent."""

    category_id: str
    category_display: str = ""
    agent: str
    score: float = 100.0
    total_rules: int = 0
    compliant: int = 0
    non_compliant: int = 0
    not_applicable: int = 0
    uncertain: int = 0
    finding_ids: list[str] = Field(default_factory=list)


class RuleResult(BaseModel):
    """Full evaluation result for one rule (pass or fail) — audit trail."""

    rule_id: str
    rule_text: str = ""
    rule_category: str = ""
    agent: str = ""
    status: str = "compliant"
    confidence: float = 1.0
    reasoning: str = ""
    evidence: str = ""
    applicability_trace: list[str] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)


class AgentReport(BaseModel):
    """Results from one compliance agent."""

    agent: str
    agent_display: str = ""
    score: float = 100.0
    model_score: float = 100.0
    review_adjusted_score: float | None = None
    score_decomposition: dict = Field(default_factory=dict)
    total_rules: int = 0
    total_findings: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)
    category_scores: list[CategoryScore] = Field(default_factory=list)
    findings: list[ComplianceFinding] = Field(default_factory=list)
    all_evaluations: list[RuleResult] = Field(default_factory=list)
    pages_reviewed: list[int] = Field(default_factory=list)


class ScoreMethodology(BaseModel):
    starting_score: float = 100.0
    deduction_weights: dict[str, int] = Field(
        default_factory=lambda: dict(SEVERITY_WEIGHTS),
    )
    formula: str = (
        "agent_score = 100 * (compliant_rules / applicable_rules); "
        "overall_score = mean(agent_scores)"
    )
    review_adjusted_formula: str = (
        "review_adjusted_score = max(0, 100 - sum(finding penalties)); "
        "user_rejected findings contribute 0 penalty"
    )
    policy: dict[str, str] = Field(default_factory=lambda: {
        "not_applicable": "excluded from denominator",
        "uncertain": "counted as non-compliant in model score",
        "retry_exhausted_or_error": "excluded from denominator",
        "review_adjustment": "severity-weight penalties from non-rejected findings",
    })


class AuditTrail(BaseModel):
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    total_llm_calls: int = 0
    total_rules_evaluated: int = 0
    rule_batch_size: int = 7
    orchestrator_model: str = ""
    evaluator_model: str = ""
    agents_executed: list[str] = Field(default_factory=list)
    agents_skipped: list[str] = Field(default_factory=list)


class ComplianceReport(BaseModel):
    """Top-level compliance report — stored as ``compliance_result.json``."""

    report_id: str
    doc_id: str
    filename: str = ""
    total_pages: int = 0
    document_type: str = "batch_record"
    generated_at: datetime
    model_versions: dict[str, str] = Field(default_factory=dict)

    overall_score: float = 100.0
    model_score: float = 100.0
    review_adjusted_score: float | None = None
    score_decomposition: dict = Field(default_factory=dict)
    score_methodology: ScoreMethodology = Field(default_factory=ScoreMethodology)

    executive_summary: ExecutiveSummary = Field(default_factory=ExecutiveSummary)

    total_findings: int = 0
    severity_counts: dict[str, int] = Field(default_factory=dict)

    agent_reports: list[AgentReport] = Field(default_factory=list)
    skipped_agents: list[SkippedCategory] = Field(default_factory=list)

    findings: list[ComplianceFinding] = Field(default_factory=list)

    # Optional: how the global findings list was constructed.
    # New reports record the explicit mode; legacy reports default to
    # ``"cross_agent_collapse"`` for read-path compatibility (see spec
    # 006 / FR-014).
    dedup_mode: str | None = None

    audit_trail: AuditTrail | None = None
