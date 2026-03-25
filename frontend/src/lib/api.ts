export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100";

export async function uploadDocument(file: File): Promise<{ doc_id: string; filename: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/api/documents/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) throw new Error(`Upload failed: ${response.statusText}`);
  return response.json();
}

export async function processDocument(docId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/documents/${docId}/process`, {
    method: "POST",
  });

  if (!response.ok) throw new Error(`Processing failed: ${response.statusText}`);
}

export async function getDocument(docId: string) {
  const response = await fetch(`${API_BASE}/api/documents/${docId}`);
  if (!response.ok) throw new Error(`Failed to fetch document: ${response.statusText}`);
  return response.json();
}

export async function listDocuments() {
  const response = await fetch(`${API_BASE}/api/documents/`);
  if (!response.ok) throw new Error(`Failed to list documents: ${response.statusText}`);
  return response.json();
}

export async function deleteDocument(docId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/documents/${docId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Failed to delete document: ${response.statusText}`);
}

export function getDocumentPdfUrl(docId: string): string {
  return `${API_BASE}/api/documents/${docId}/pdf`;
}

export async function getReviewPages(docId: string) {
  const response = await fetch(`${API_BASE}/api/review/${docId}/pages`);
  if (!response.ok) throw new Error(`Failed to fetch review pages: ${response.statusText}`);
  return response.json();
}

export async function approvePage(docId: string, pageNum: number) {
  const response = await fetch(`${API_BASE}/api/review/${docId}/pages/${pageNum}/approve`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Failed to approve page: ${response.statusText}`);
  return response.json();
}

export async function editPage(docId: string, pageNum: number, markdown?: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/review/${docId}/pages/${pageNum}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown }),
  });
  if (!response.ok) throw new Error(`Failed to edit page: ${response.statusText}`);
  return response.json();
}

export async function flagPage(docId: string, pageNum: number, reason?: string): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE}/api/review/${docId}/pages/${pageNum}/flag`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!response.ok) throw new Error(`Failed to flag page: ${response.statusText}`);
  return response.json();
}

export async function getComplianceReport(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/report`);
  if (!response.ok) throw new Error(`Failed to fetch compliance report: ${response.statusText}`);
  return response.json();
}

export async function runComplianceReview(docId: string, agents?: string[]) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agents: agents?.length ? agents : undefined }),
  });
  if (!response.ok) throw new Error(`Failed to run compliance review: ${response.statusText}`);
  return response.json();
}

export async function getComplianceStatus(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/status`);
  if (!response.ok) throw new Error(`Failed to fetch compliance status: ${response.statusText}`);
  return response.json();
}

export async function cancelComplianceRun(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/run`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Failed to cancel compliance run: ${response.statusText}`);
  return response.json();
}

export async function resolveComplianceFinding(docId: string, findingId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/findings/${findingId}/resolve`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Failed to resolve finding: ${response.statusText}`);
  return response.json();
}

export async function reviewComplianceFinding(
  docId: string,
  findingId: string,
  body: {
    action: "approve" | "reject" | "modify" | "reset";
    note?: string;
    modified_severity?: string;
    modified_description?: string;
  },
) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/findings/${findingId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Failed to review finding: ${response.statusText}`);
  return response.json();
}

export async function getComplianceHITLSummary(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/hitl-summary`);
  if (!response.ok) throw new Error(`Failed to fetch HITL summary: ${response.statusText}`);
  return response.json();
}

export function getComplianceExportUrl(
  docId: string,
  format: "md" | "html",
  options?: { agent?: string },
): string {
  const params = new URLSearchParams({ format });
  if (options?.agent) {
    params.set("agent", options.agent);
  }
  return `${API_BASE}/api/compliance/${docId}/export?${params.toString()}`;
}

export async function downloadComplianceExport(
  docId: string,
  format: "md" | "html",
  options?: { agent?: string },
): Promise<void> {
  const url = getComplianceExportUrl(docId, format, options);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Export failed: ${response.statusText}`);

  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const filenameMatch = disposition.match(/filename="([^"]+)"/);
  const filename = filenameMatch?.[1] ?? `compliance_report.${format}`;

  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

// ── Export / Download ───────────────────────────────────────

export function getExportUrl(docId: string, format: "md" | "html"): string {
  return `${API_BASE}/api/review/${docId}/export?format=${format}`;
}

export async function downloadExport(docId: string, format: "md" | "html"): Promise<void> {
  const url = getExportUrl(docId, format);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Export failed: ${response.statusText}`);

  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const filenameMatch = disposition.match(/filename="([^"]+)"/);
  const filename = filenameMatch?.[1] ?? `document.${format}`;

  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

export async function downloadExportAsPdf(docId: string): Promise<void> {
  const url = getExportUrl(docId, "html");
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Export failed: ${response.statusText}`);

  const html = await response.text();
  const printWindow = window.open("", "_blank");
  if (!printWindow) throw new Error("Pop-up blocked — please allow pop-ups for PDF export");
  printWindow.document.write(html);
  printWindow.document.close();
  printWindow.onload = () => {
    printWindow.print();
  };
}

// ── Rules Management ────────────────────────────────────────

export async function getAgentsWithMeta() {
  const response = await fetch(`${API_BASE}/api/rules/agents`);
  if (!response.ok) throw new Error(`Failed to fetch agents: ${response.statusText}`);
  return response.json();
}

export async function getAgentRules(agentId: string) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}`);
  if (!response.ok) throw new Error(`Failed to fetch agent rules: ${response.statusText}`);
  return response.json();
}

export async function createAgent(payload: { id: string; label: string; description: string }) {
  const response = await fetch(`${API_BASE}/api/rules/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to create agent: ${response.statusText}`);
  }
  return response.json();
}

export async function updateAgent(agentId: string, payload: { label?: string; description?: string }) {
  const response = await fetch(`${API_BASE}/api/rules/agents/${agentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Failed to update agent: ${response.statusText}`);
  return response.json();
}

export async function deleteAgent(agentId: string) {
  const response = await fetch(`${API_BASE}/api/rules/agents/${agentId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete agent: ${response.statusText}`);
  }
  return response.json();
}

export async function addRule(
  agentId: string,
  payload: { category: string; category_display: string; text: string; severity_hint: string },
) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}/rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Failed to add rule: ${response.statusText}`);
  return response.json();
}

export async function bulkAddRules(
  agentId: string,
  payload: { category: string; category_display: string; texts: string[]; severity_hint: string },
) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}/rules/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Failed to bulk add rules: ${response.statusText}`);
  return response.json();
}

export async function updateRule(
  agentId: string,
  ruleId: string,
  payload: { text?: string; severity_hint?: string },
) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}/rules/${ruleId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Failed to update rule: ${response.statusText}`);
  return response.json();
}

export async function deleteRule(agentId: string, ruleId: string) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}/rules/${ruleId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw new Error(`Failed to delete rule: ${response.statusText}`);
  return response.json();
}

export async function addCategory(agentId: string, payload: { display: string }) {
  const response = await fetch(`${API_BASE}/api/rules/${agentId}/categories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Failed to add category: ${response.statusText}`);
  return response.json();
}

// ── Segmentation ────────────────────────────────────────────

export async function getSegmentation(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/segmentation`);
  if (!response.ok) throw new Error(`Failed to fetch segmentation: ${response.statusText}`);
  return response.json();
}

export async function updateSegmentation(docId: string, body: Record<string, unknown>) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/segmentation`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`Failed to update segmentation: ${response.statusText}`);
  return response.json();
}

export async function triggerSegmentation(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/segment`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Failed to trigger segmentation: ${response.statusText}`);
  return response.json();
}

// ── Discovered Rules ────────────────────────────────────────

export async function getDiscoveredRules(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/discovered-rules`);
  if (!response.ok) throw new Error(`Failed to fetch discovered rules: ${response.statusText}`);
  return response.json();
}

export async function promoteDiscoveredRule(docId: string, index: number) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/discovered-rules/${index}/promote`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Failed to promote rule: ${response.statusText}`);
  return response.json();
}

// ── Component-level HITL ────────────────────────────────────

export async function componentAction(
  docId: string,
  componentId: string,
  action: "approve" | "edit" | "flag",
  opts?: { value?: string; reason?: string },
): Promise<{ component_id: string; action: string; status: string }> {
  const response = await fetch(`${API_BASE}/api/review/${docId}/components/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      component_id: componentId,
      action,
      value: opts?.value,
      reason: opts?.reason,
    }),
  });
  if (!response.ok) throw new Error(`Component action failed: ${response.statusText}`);
  return response.json();
}

export async function bulkComponentAction(
  docId: string,
  componentIds: string[],
  action: "approve" | "flag",
): Promise<{ component_ids: string[]; action: string; status: string }> {
  const response = await fetch(`${API_BASE}/api/review/${docId}/components/bulk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ component_ids: componentIds, action }),
  });
  if (!response.ok) throw new Error(`Bulk component action failed: ${response.statusText}`);
  return response.json();
}
