"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { ComplianceBadge } from "./compliance-badge";
import {
  RuleExpandDrawer,
  type DrawerFinding,
  type ScoresUpdate,
} from "./rule-expand-drawer";
import type { ReportRow as ReportRowT } from "@/types/compliance";

interface RuleRowProps {
  row: ReportRowT;
  /** Findings on this rule, pre-filtered by parent so the drawer
   * doesn't refetch. May be empty for compliant rules. */
  findings: DrawerFinding[];
  docId: string;
  showAgentColumn: boolean;
  onFindingUpdate: (findingId: string, updates: Partial<DrawerFinding>) => void;
  onScoresUpdate?: (scores: ScoresUpdate) => void;
}

/** One rule of the client-aligned rule table. Five cells in
 * spec order (Question | Compliance | Evidence | Detailed | Mitigation),
 * with an expand chevron that swaps in the per-finding drawer.
 *
 * Compliant rows hide the chevron — the spec leaves the pages and
 * mitigation cells empty on those, so there's nothing additional
 * to show. */
export function RuleRow({
  row,
  findings,
  docId,
  showAgentColumn,
  onFindingUpdate,
  onScoresUpdate,
}: RuleRowProps) {
  const [expanded, setExpanded] = useState(false);
  const expandable = row.compliance_kind !== "compliant" && findings.length > 0;

  return (
    <>
      <tr
        className={cn(
          "border-b border-border hover:bg-muted/30 transition-colors",
          expandable && "cursor-pointer",
        )}
        onClick={() => expandable && setExpanded((p) => !p)}
      >
        <td className="py-2 px-3 align-top">
          <div className="flex items-start gap-1.5">
            {expandable ? (
              expanded ? (
                <ChevronDown className="size-3.5 text-muted-foreground flex-shrink-0 mt-0.5" />
              ) : (
                <ChevronRight className="size-3.5 text-muted-foreground flex-shrink-0 mt-0.5" />
              )
            ) : (
              <span className="size-3.5 flex-shrink-0" />
            )}
            <div className="min-w-0">
              <p className="text-xs font-medium text-foreground leading-snug">
                {row.question}
              </p>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                {row.rule_id}
                {showAgentColumn && (
                  <span className="ml-1.5 inline-flex items-center rounded bg-muted px-1.5 py-px text-[10px] text-muted-foreground">
                    {row.agent}
                  </span>
                )}
              </p>
            </div>
          </div>
        </td>
        <td className="py-2 px-3 align-top whitespace-nowrap">
          <ComplianceBadge kind={row.compliance_kind} label={row.compliance_label} />
        </td>
        <td className="py-2 px-3 align-top text-xs text-muted-foreground whitespace-nowrap">
          {row.evidence_pages || <span className="text-muted-foreground/50">—</span>}
        </td>
        <td className="py-2 px-3 align-top text-xs text-muted-foreground">
          <p className="line-clamp-3 leading-snug">{row.detailed_evidence}</p>
        </td>
        <td className="py-2 px-3 align-top text-xs text-muted-foreground">
          <p className="line-clamp-3 leading-snug">{row.mitigation}</p>
        </td>
      </tr>
      {expanded && expandable && (
        <tr className="bg-muted/20">
          <td colSpan={5} className="p-0">
            <RuleExpandDrawer
              findings={findings}
              docId={docId}
              onFindingUpdate={onFindingUpdate}
              onScoresUpdate={onScoresUpdate}
            />
          </td>
        </tr>
      )}
    </>
  );
}
