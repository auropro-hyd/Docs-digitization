const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

export async function getComplianceReport(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/report`);
  if (!response.ok) throw new Error(`Failed to fetch compliance report: ${response.statusText}`);
  return response.json();
}

export async function runComplianceReview(docId: string) {
  const response = await fetch(`${API_BASE}/api/compliance/${docId}/run`, {
    method: "POST",
  });
  if (!response.ok) throw new Error(`Failed to run compliance review: ${response.statusText}`);
  return response.json();
}
