/** Centralized display names for compliance agents. */
export const AGENT_DISPLAY_NAMES: Record<string, string> = {
  alcoa: "ALCOA+",
  gmp: "GMP",
  checklist: "Checklist",
  sop: "SOP",
  reconciliation: "Cross-Page",
};

export interface AgentMeta {
  id: string;
  label: string;
  description: string;
  rule_count: number;
  category_count: number;
  categories: string[];
}

export interface Rule {
  id: string;
  number: number;
  text: string;
  severity_hint: string;
}

export interface RuleCategory {
  id: string;
  display: string;
  rules: Rule[];
}

export interface AgentRulesResponse {
  agent: string;
  label: string;
  description: string;
  categories: RuleCategory[];
  total_rules: number;
}

export interface VisualRegion {
  x: number;
  y: number;
  width: number;
  height: number;
  label: string;
}

// ── Spec 008 — client-aligned rule-table shape ─────────────

/** The three-state compliance taxonomy from Spec 008.
 *
 * Maps from raw rule status: ``compliant`` → ``compliant``;
 * ``non_compliant`` → ``action_required``;
 * ``uncertain`` / ``error`` / ``needs_review`` → ``needs_attention``;
 * ``not_applicable`` is excluded entirely (no row rendered). */
export type ComplianceKind = "compliant" | "action_required" | "needs_attention";

export interface ReportRow {
  rule_id: string;
  /** Pre-formatted agent display name from the backend builder
   * (``Checklist``, ``GMP``, ``ALCOA+``, etc.). */
  agent: string;
  question: string;
  compliance_label: string;
  compliance_kind: ComplianceKind;
  /** Pre-formatted page-range string, e.g. ``"PAGE:36 to 42"`` or
   * ``"PAGE:6, 9, 31"``. Empty for compliant rows. */
  evidence_pages: string;
  /** Cross-page summary for compliant rows; concatenated finding
   * reasoning for non-compliant / uncertain rows. */
  detailed_evidence: string;
  /** ``"Not Applicable"`` for compliant rows; rule-author
   * recommendation or LLM-synthesised mitigation otherwise. */
  mitigation: string;
}

export interface ReportHeader {
  product_name: string;
  title: string;
  is_draft: boolean;
  metadata_rows: [string, string][];
  logo_path: string | null;
}

export interface ReportFooter {
  operator_name: string;
  generated_at: string;
  disclaimer: string;
}

export interface ReportStats {
  row_count: number;
  compliant_count: number;
  action_required_count: number;
  needs_attention_count: number;
  excluded_not_applicable_count: number;
}

export interface ReportDocument {
  header: ReportHeader;
  rows: ReportRow[];
  footer: ReportFooter;
  stats: ReportStats;
}

export interface ComplianceFinding {
  finding_id: string;
  rule_id: string;
  rule_text: string;
  rule_category: string;
  rule_category_display: string;
  agent: string;
  severity: string;
  status: string;
  confidence: number;
  page_numbers: number[];
  reasoning: string;
  evidence: string;
  description: string;
  recommendation: string;
  hitl_status: string;
  resolved: boolean;
  applicability_trace?: string[];
  user_comment?: string;
  evaluation_channels?: string[];
  visual_evidence?: string;
  visual_regions?: VisualRegion[];
}
