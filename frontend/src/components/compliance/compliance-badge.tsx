"use client";

import { AlertCircle, CheckCircle, HelpCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ComplianceKind } from "@/types/compliance";

/** Visual contract for the three-state compliance taxonomy from
 * Spec 008. Mirrors the styling of the exported PDF so the in-app
 * and exported view share the same colour language. */
const BADGE_CONFIG: Record<
  ComplianceKind,
  { icon: typeof CheckCircle; cls: string; label: string }
> = {
  compliant: {
    icon: CheckCircle,
    cls: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-900",
    label: "Compliant",
  },
  action_required: {
    icon: AlertCircle,
    cls: "bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-900",
    label: "Action Required",
  },
  needs_attention: {
    icon: HelpCircle,
    cls: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900",
    label: "Needs Attention",
  },
};

interface ComplianceBadgeProps {
  kind: ComplianceKind;
  /** Optional label override (defaults to the canonical label from
   * the builder map). The backend provides ``compliance_label``
   * verbatim; passing it through here keeps the export and screen
   * views in lock-step even if the wording changes. */
  label?: string;
  className?: string;
}

export function ComplianceBadge({ kind, label, className }: ComplianceBadgeProps) {
  const cfg = BADGE_CONFIG[kind];
  const Icon = cfg.icon;
  return (
    <Badge
      variant="outline"
      className={cn(
        "inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5",
        cfg.cls,
        className,
      )}
    >
      <Icon className="size-3" />
      {label ?? cfg.label}
    </Badge>
  );
}
