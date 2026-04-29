// Type definitions matching the backend BMR audit pipeline schema
// (backend/app/bmr/workflow/models.py). Keep in sync — the API
// returns these models verbatim via FastAPI's Pydantic serialization.

export type RunStage =
  | "ingest"
  | "legibility_and_classification"
  | "extraction"
  | "compliance"
  | "report";

export type RunStatus =
  | "pending"
  | "running"
  | "awaiting_legibility_review"
  | "completed"
  | "failed";

export type FindingStatus =
  | "pass"
  | "fail"
  | "unevaluated"
  | "skipped"
  | "internal_error";

export type FindingSource = "rule" | "fallback" | "synthesis";

export interface EvidenceRegion {
  doc_id: string;
  page_index: number;
  field?: string | null;
  value?: unknown;
  page_bbox?: [number, number, number, number] | null;
  note?: string;
  section_id?: string | null;
}

export interface FindingRecord {
  finding_id: string;
  rule_id: string;
  rule_version: string;
  rule_content_hash?: string;
  status: FindingStatus;
  severity: string;
  alcoa_tag?: string | null;
  gmp_category?: string | null;
  source: FindingSource;
  summary: string;
  detail?: string;
  source_finding_ids: string[];
  evidence: EvidenceRegion[];
  tolerance_applied?: Record<string, unknown> | null;
  fields: Record<string, unknown>;
  fallback_applied?: string | null;
  superseded_by?: string | null;
  supersedes?: string | null;
}

export interface RunSummary {
  total: number;
  by_status: Record<string, number>;
  by_severity: Record<string, number>;
  by_source: Record<string, number>;
}

// Per-row shape of RunReport.bpcr_sections — see Spec 007 follow-up
// in backend/app/bmr/workflow/stages.py::_bpcr_section_summary. The
// metadata keys (display_name, confidence, detection_method) are
// absent on rows where the detector inherited or fell back to
// "unsectioned"; the JSON shape is intentionally narrow on those
// rows so consumers don't have to special-case null metadata.
export interface BpcrSectionRow {
  doc_id: string;
  page_index: number;
  section_id: string;
  display_name?: string;
  confidence?: number;
  detection_method?: string;
}

export interface RunListItem {
  run_id: string;
  package_id?: string | null;
  status?: RunStatus | null;
  stage?: RunStage | null;
  started_at?: string | null;
  finished_at?: string | null;
  total_findings?: number | null;
  bpcr_section_count?: number | null;
}

export interface RunListResponse {
  runs: RunListItem[];
}

export interface RunReport {
  run_id: string;
  package_id: string;
  status: RunStatus;
  stage: RunStage;
  rules_evaluated: number;
  rules_loaded: number;
  rules_skipped_deprecated: number;
  findings: FindingRecord[];
  summary: RunSummary;
  error?: string | null;
  started_at: string;
  finished_at?: string | null;
  rules_dir?: string | null;
  aliases_dir?: string | null;
  repo_root?: string | null;
  legibility_reasons: string[];
  legibility_decided_at?: string | null;
  legibility_decision?: string | null;
  legibility_decided_by?: string | null;
  legibility_decision_note?: string | null;
  package_snapshot_hash?: string | null;
  bpcr_sections: BpcrSectionRow[];
}
