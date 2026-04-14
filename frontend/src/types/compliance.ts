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
