export function normalizeEngineTerms(text: string): string {
  if (!text) return "";
  return text
    .replace(/azure\s*di/gi, "OCR")
    .replace(/document\s*intelligence/gi, "OCR")
    .replace(/marker\s*ocr/gi, "OCR")
    .replace(/docling/gi, "quality engine")
    .replace(/ollama/gi, "language engine");
}

const STATUS_LABELS: Record<string, string> = {
  idle: "Ready",
  uploading: "Secure upload in progress",
  ingested: "Workflow started",
  azure_di_running: "Text extraction in progress",
  marker_ocr_running: "Text extraction in progress",
  quality_scoring: "Quality signals validation",
  merging_results: "Building review-ready output",
  hitl_required: "Expert review required",
  auto_approved: "Quality checks passed",
  reviewed: "Validation complete",
  completed: "Document ready",
  error: "Processing issue",
};

const STATUS_PROGRESS: Record<string, number> = {
  uploading: 8,
  ingested: 16,
  azure_di_running: 58,
  marker_ocr_running: 58,
  quality_scoring: 72,
  merging_results: 84,
  hitl_required: 90,
  auto_approved: 95,
  reviewed: 97,
  completed: 100,
};

export function displayProcessingStatus(status: string): string {
  const mapped = STATUS_LABELS[status] ?? status.replace(/_/g, " ");
  return normalizeEngineTerms(mapped);
}

export function pipelineProgressFromStatus(status: string): number {
  return STATUS_PROGRESS[status] ?? 0;
}

