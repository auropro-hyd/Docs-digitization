"use client";

import { useDocumentStore } from "@/stores/document-store";
import { useDocumentWebSocket } from "@/hooks/useWebSocket";
import { StatusIndicator } from "@/components/common/status-indicator";
import { FileText, CheckCircle, AlertTriangle, Clock } from "lucide-react";

export function ProcessingDashboard() {
  const { docId, filename, totalPages, processingStatus, pages, error } = useDocumentStore();

  useDocumentWebSocket(docId);

  if (!docId) return null;

  const pageArray = Array.from(pages.values());
  const approvedCount = pageArray.filter((p) => p.status === "approved").length;
  const reviewCount = pageArray.filter((p) => p.status === "reviewing").length;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
      <div className="mb-6 flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-blue-50 p-2">
            <FileText className="h-5 w-5 text-blue-600" />
          </div>
          <div>
            <h3 className="font-semibold text-gray-900">{filename}</h3>
            <p className="text-sm text-gray-500">{totalPages} pages</p>
          </div>
        </div>
        <StatusIndicator status={processingStatus} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          <AlertTriangle className="mr-2 inline h-4 w-4" />
          {error}
        </div>
      )}

      <div className="mb-6 grid grid-cols-3 gap-4">
        <StatCard
          icon={<Clock className="h-4 w-4 text-blue-500" />}
          label="Total Pages"
          value={totalPages}
        />
        <StatCard
          icon={<CheckCircle className="h-4 w-4 text-green-500" />}
          label="Approved"
          value={approvedCount}
        />
        <StatCard
          icon={<AlertTriangle className="h-4 w-4 text-amber-500" />}
          label="Needs Review"
          value={reviewCount}
        />
      </div>

      {totalPages > 0 && (
        <div>
          <h4 className="mb-3 text-sm font-medium text-gray-700">Page Progress</h4>
          <div className="flex flex-wrap gap-1">
            {pageArray.map((page) => (
              <PageDot key={page.pageNum} page={page} />
            ))}
          </div>
        </div>
      )}

      {processingStatus !== "idle" && processingStatus !== "completed" && (
        <div className="mt-4">
          <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-blue-500 transition-all duration-500"
              style={{
                width: `${totalPages > 0 ? (approvedCount / totalPages) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
}) {
  return (
    <div className="rounded-lg bg-gray-50 p-3">
      <div className="mb-1 flex items-center gap-1.5">
        {icon}
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <span className="text-xl font-bold text-gray-900">{value}</span>
    </div>
  );
}

function PageDot({ page }: { page: { pageNum: number; confidence: number; status: string } }) {
  const colorMap: Record<string, string> = {
    queued: "bg-gray-200",
    extracting: "bg-blue-300 animate-pulse",
    scoring: "bg-purple-300 animate-pulse",
    reviewing: "bg-amber-400",
    approved: "bg-green-400",
    flagged: "bg-red-400",
    error: "bg-red-600",
  };

  return (
    <div
      className={`h-3 w-3 rounded-sm ${colorMap[page.status] || "bg-gray-200"}`}
      title={`Page ${page.pageNum}: ${page.status} (${(page.confidence * 100).toFixed(0)}%)`}
    />
  );
}
