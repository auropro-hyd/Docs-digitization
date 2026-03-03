"use client";

import { cn } from "@/lib/utils";
import type { ProcessingStatus } from "@/stores/document-store";

const STATUS_CONFIG: Record<ProcessingStatus, { label: string; color: string }> = {
  idle: { label: "Ready", color: "bg-gray-400" },
  uploading: { label: "Uploading...", color: "bg-blue-400 animate-pulse" },
  ingested: { label: "Ingested", color: "bg-blue-500" },
  marker_ocr_running: { label: "Running Marker OCR...", color: "bg-indigo-500 animate-pulse" },
  azure_di_running: { label: "Running Azure DI...", color: "bg-indigo-500 animate-pulse" },
  quality_scoring: { label: "Quality Scoring...", color: "bg-purple-500 animate-pulse" },
  merging_results: { label: "Merging Results...", color: "bg-purple-500 animate-pulse" },
  hitl_required: { label: "Review Required", color: "bg-amber-500" },
  auto_approved: { label: "Auto-Approved", color: "bg-green-500" },
  reviewed: { label: "Reviewed", color: "bg-green-500" },
  completed: { label: "Completed", color: "bg-green-600" },
  error: { label: "Error", color: "bg-red-500" },
};

interface StatusIndicatorProps {
  status: ProcessingStatus;
  className?: string;
}

export function StatusIndicator({ status, className }: StatusIndicatorProps) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <span className={cn("h-2.5 w-2.5 rounded-full", config.color)} />
      <span className="text-sm font-medium text-gray-700">{config.label}</span>
    </div>
  );
}
