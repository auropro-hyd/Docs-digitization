"use client";

import type { ProcessingStatus } from "@/stores/document-store";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS_CONFIG: Record<
  ProcessingStatus,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline"; className: string; pulse?: boolean }
> = {
  idle: { label: "Ready", variant: "secondary", className: "bg-muted text-muted-foreground" },
  uploading: { label: "Secure Upload", variant: "outline", className: "border-info text-info", pulse: true },
  ingested: { label: "Workflow Queued", variant: "outline", className: "border-info text-info", pulse: true },
  marker_ocr_running: { label: "Text Intelligence", variant: "outline", className: "border-chart-2 text-chart-2", pulse: true },
  azure_di_running: { label: "Text Intelligence", variant: "outline", className: "border-chart-1 text-chart-1", pulse: true },
  quality_scoring: { label: "Confidence Analysis", variant: "outline", className: "border-warning text-warning", pulse: true },
  merging_results: { label: "Result Assembly", variant: "outline", className: "border-chart-1 text-chart-1", pulse: true },
  hitl_required: { label: "Action Needed", variant: "outline", className: "border-warning text-warning" },
  auto_approved: { label: "Quality Confirmed", variant: "outline", className: "border-success text-success" },
  reviewed: { label: "Validation Complete", variant: "outline", className: "border-success text-success" },
  completed: { label: "Ready", variant: "outline", className: "border-success text-success" },
  error: { label: "Attention", variant: "destructive", className: "" },
};

interface StatusIndicatorProps {
  status: ProcessingStatus;
  className?: string;
}

export function StatusIndicator({ status, className }: StatusIndicatorProps) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;

  return (
    <Badge variant={config.variant} className={cn("gap-1.5 text-[11px] font-medium", config.className, className)}>
      <span
        className={cn("size-1.5 rounded-full bg-current", config.pulse && "animate-pulse")}
      />
      {config.label}
    </Badge>
  );
}
