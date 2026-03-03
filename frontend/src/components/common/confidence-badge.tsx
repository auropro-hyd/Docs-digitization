"use client";

import { cn, formatConfidence, getConfidenceColor, getConfidenceLabel } from "@/lib/utils";

interface ConfidenceBadgeProps {
  score: number;
  showLabel?: boolean;
  className?: string;
}

export function ConfidenceBadge({ score, showLabel = true, className }: ConfidenceBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        getConfidenceColor(score),
        className
      )}
    >
      {formatConfidence(score)}
      {showLabel && <span className="opacity-75">({getConfidenceLabel(score)})</span>}
    </span>
  );
}
