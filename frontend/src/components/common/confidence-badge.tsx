"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface ConfidenceBadgeProps {
  score: number;
  className?: string;
}

export function ConfidenceBadge({ score, className }: ConfidenceBadgeProps) {
  const pct = Math.round(score * 100);
  const tier = score >= 0.8 ? "high" : score >= 0.6 ? "medium" : "low";

  const styles = {
    high: "border-success/30 bg-success/10 text-success",
    medium: "border-warning/30 bg-warning/10 text-warning",
    low: "border-destructive/30 bg-destructive/10 text-destructive",
  };

  const labels = { high: "High", medium: "Medium", low: "Low" };

  return (
    <Badge variant="outline" className={cn("gap-1.5 text-[11px] font-medium", styles[tier], className)}>
      {labels[tier]} {pct}%
    </Badge>
  );
}
