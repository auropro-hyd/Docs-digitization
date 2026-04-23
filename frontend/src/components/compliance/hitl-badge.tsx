"use client";

/**
 * HITLBadge — the single place that maps a finding's HITL wire value to a
 * label + palette + icon + tooltip.
 *
 * Satisfies spec 006 FR-011 / FR-012 / FR-013:
 *  - ``auto_approved`` is displayed as "System-confirmed" with a neutral
 *    palette (never success-green). Severity owns the "is this bad?" colour.
 *  - ``unknown`` is an explicit state; the fallback for an unrecognised wire
 *    value is ``unknown``, NEVER ``auto_approved``.
 *
 * Contract: ``specs/006-observability-and-finding-semantics/contracts/hitl-display-contract.md``.
 */

import {
  CircleHelp,
  Eye,
  Pencil,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export type HitlWireValue =
  | "auto_approved"
  | "system_confirmed"
  | "needs_review"
  | "user_approved"
  | "user_rejected"
  | "user_modified"
  | "unknown";

export type HitlPalette =
  | "success"
  | "warning"
  | "destructive"
  | "info"
  | "neutral";

interface Config {
  label: string;
  palette: HitlPalette;
  classes: string;
  icon: LucideIcon;
  tooltip: string;
}

const NEUTRAL_CLASSES =
  "text-muted-foreground border-muted-foreground/20 bg-muted/40";
const SUCCESS_CLASSES = "text-success border-success/30 bg-success/10";
const WARNING_CLASSES = "text-warning border-warning/20 bg-warning/5";
const DESTRUCTIVE_CLASSES =
  "text-destructive border-destructive/20 bg-destructive/5";
const INFO_CLASSES =
  "text-blue-600 border-blue-300 bg-blue-50 dark:text-blue-400 dark:border-blue-800 dark:bg-blue-900/10";

export const HITL_CONFIG: Record<HitlWireValue, Config> = {
  auto_approved: {
    label: "System-confirmed",
    palette: "neutral",
    classes: NEUTRAL_CLASSES,
    icon: ShieldCheck,
    tooltip:
      "Model-only review — high confidence, no reviewer needed. This is still a non-compliance finding.",
  },
  system_confirmed: {
    label: "System-confirmed",
    palette: "neutral",
    classes: NEUTRAL_CLASSES,
    icon: ShieldCheck,
    tooltip:
      "Model-only review — high confidence, no reviewer needed. This is still a non-compliance finding.",
  },
  needs_review: {
    label: "Needs review",
    palette: "warning",
    classes: WARNING_CLASSES,
    icon: Eye,
    tooltip: "Awaiting reviewer confirmation.",
  },
  user_approved: {
    label: "Reviewer-approved",
    palette: "success",
    classes: SUCCESS_CLASSES,
    icon: ThumbsUp,
    tooltip: "Reviewer confirmed as a valid finding.",
  },
  user_rejected: {
    label: "Reviewer-rejected",
    palette: "destructive",
    classes: DESTRUCTIVE_CLASSES,
    icon: ThumbsDown,
    tooltip: "Reviewer rejected as spurious; excluded from scoring.",
  },
  user_modified: {
    label: "Reviewer-modified",
    palette: "info",
    classes: INFO_CLASSES,
    icon: Pencil,
    tooltip: "Reviewer edited severity / description.",
  },
  unknown: {
    label: "Unknown",
    palette: "neutral",
    classes: NEUTRAL_CLASSES,
    icon: CircleHelp,
    tooltip: "HITL state missing — data integrity issue.",
  },
};

export function normalizeHitlStatus(raw: unknown): HitlWireValue {
  if (typeof raw === "string" && raw in HITL_CONFIG) {
    return raw as HitlWireValue;
  }
  return "unknown";
}

interface HITLBadgeProps {
  status: string | undefined | null;
  className?: string;
}

export function HITLBadge({ status, className }: HITLBadgeProps) {
  const wire = normalizeHitlStatus(status);
  const config = HITL_CONFIG[wire];
  const Icon = config.icon;
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px] px-1.5 py-0 gap-0.5",
        config.classes,
        className,
      )}
      title={config.tooltip}
      data-hitl-wire={wire}
      data-hitl-palette={config.palette}
    >
      <Icon className="size-2.5" />
      {config.label}
    </Badge>
  );
}

export default HITLBadge;
