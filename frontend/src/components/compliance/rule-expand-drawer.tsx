"use client";

import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Pencil, ShieldAlert, ThumbsDown, ThumbsUp } from "lucide-react";
import { reviewComplianceFinding } from "@/lib/api";
import { HITLBadge } from "@/components/compliance/hitl-badge";
import { toast } from "sonner";
import type { ComplianceFinding } from "@/types/compliance";

/** Lightweight finding shape mirroring the bits the drawer touches.
 * Lives inline (not pulled from findings-table.tsx) so we can
 * retire that file without dragging type baggage with it. */
export interface DrawerFinding extends ComplianceFinding {
  hitl_note?: string;
  hitl_reviewed_at?: string | null;
}

export interface ScoresUpdate {
  model_score?: number;
  review_adjusted_score?: number;
  overall_score?: number;
  agent_scores?: Array<{
    agent: string;
    model_score?: number;
    review_adjusted_score?: number;
  }>;
}

interface RuleExpandDrawerProps {
  /** Findings belonging to the expanded rule. May be empty for
   * compliant rules (no finding gets emitted for those). */
  findings: DrawerFinding[];
  docId: string;
  onFindingUpdate: (findingId: string, updates: Partial<DrawerFinding>) => void;
  onScoresUpdate?: (scores: ScoresUpdate) => void;
}

/** Expanded section under a ``RuleRow`` — shows per-finding reasoning,
 * evidence, and the HITL approve / reject / modify controls.
 *
 * Reads its findings from the parent (already filtered by
 * ``rule_id``) rather than refetching, so HITL updates surface
 * immediately and the parent's score overlay stays consistent. */
export function RuleExpandDrawer({
  findings,
  docId,
  onFindingUpdate,
  onScoresUpdate,
}: RuleExpandDrawerProps) {
  if (findings.length === 0) {
    return (
      <div className="px-4 py-3 text-xs text-muted-foreground italic">
        No detailed findings recorded for this rule.
      </div>
    );
  }

  return (
    <div className="px-4 py-3 space-y-4 bg-muted/30">
      {findings.map((f) => (
        <FindingDetailBlock
          key={f.finding_id}
          finding={f}
          docId={docId}
          onUpdate={(updates) => onFindingUpdate(f.finding_id, updates)}
          onScores={onScoresUpdate}
        />
      ))}
    </div>
  );
}

function FindingDetailBlock({
  finding,
  docId,
  onUpdate,
  onScores,
}: {
  finding: DrawerFinding;
  docId: string;
  onUpdate: (updates: Partial<DrawerFinding>) => void;
  onScores?: (scores: ScoresUpdate) => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [reviewNote, setReviewNote] = useState(finding.hitl_note ?? "");
  const [modSeverity, setModSeverity] = useState(finding.severity);
  const [showModify, setShowModify] = useState(false);

  const handleReview = useCallback(
    async (action: "approve" | "reject" | "modify") => {
      setSubmitting(true);
      try {
        const result = await reviewComplianceFinding(docId, finding.finding_id, {
          action,
          note: reviewNote,
          modified_severity: action === "modify" ? modSeverity : undefined,
        });
        onUpdate({
          hitl_status: result.hitl_status,
          hitl_note: result.hitl_note,
          hitl_reviewed_at: result.hitl_reviewed_at,
          severity: result.severity,
          resolved: result.resolved,
        });
        onScores?.({
          model_score: result.model_score,
          review_adjusted_score: result.review_adjusted_score,
          overall_score: result.overall_score,
          agent_scores: result.agent_scores,
        });
        toast.success(
          action === "approve"
            ? "Finding approved"
            : action === "reject"
              ? "Finding rejected as false positive"
              : "Finding modified",
        );
      } catch {
        toast.error("Review failed");
      } finally {
        setSubmitting(false);
      }
    },
    [docId, finding.finding_id, reviewNote, modSeverity, onUpdate, onScores],
  );

  const handleReset = useCallback(async () => {
    setSubmitting(true);
    try {
      const result = await reviewComplianceFinding(docId, finding.finding_id, { action: "reset" });
      onUpdate({
        hitl_status: result.hitl_status,
        hitl_note: result.hitl_note ?? "",
        hitl_reviewed_at: result.hitl_reviewed_at ?? null,
        resolved: result.resolved ?? false,
      });
      onScores?.({
        model_score: result.model_score,
        review_adjusted_score: result.review_adjusted_score,
        overall_score: result.overall_score,
        agent_scores: result.agent_scores,
      });
      toast.success("Review reset");
    } catch {
      toast.error("Failed to reset review");
    } finally {
      setSubmitting(false);
    }
  }, [docId, finding.finding_id, onUpdate, onScores]);

  const alreadyReviewed = ["user_approved", "user_rejected", "user_modified"].includes(finding.hitl_status);

  return (
    <div className="rounded-md border border-border bg-background p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium text-muted-foreground">
          Finding {finding.finding_id}
        </span>
        <Badge variant="outline" className="text-[10px] capitalize">
          {finding.severity}
        </Badge>
        {finding.hitl_status && <HITLBadge status={finding.hitl_status} />}
        {finding.page_numbers?.length > 0 && (
          <span className="text-[10px] text-muted-foreground">
            Pages {finding.page_numbers.join(", ")}
          </span>
        )}
      </div>

      {finding.reasoning && (
        <div className="text-xs text-foreground">
          <p className="font-medium text-[11px] mb-1">Reasoning</p>
          <p className="text-muted-foreground whitespace-pre-wrap">{finding.reasoning}</p>
        </div>
      )}
      {finding.evidence && (
        <div className="text-xs text-foreground">
          <p className="font-medium text-[11px] mb-1">Evidence</p>
          <p className="text-muted-foreground italic whitespace-pre-wrap">
            &ldquo;{finding.evidence}&rdquo;
          </p>
        </div>
      )}
      {finding.recommendation && (
        <div className="text-xs text-foreground">
          <p className="font-medium text-[11px] mb-1">Author Recommendation</p>
          <p className="text-muted-foreground whitespace-pre-wrap">{finding.recommendation}</p>
        </div>
      )}

      <div className="space-y-2 pt-2 border-t border-dashed">
        <div className="flex items-center gap-2">
          <ShieldAlert className="size-3.5 text-warning" />
          <span className="text-xs font-medium">
            {alreadyReviewed ? "Review Decision" : "Human Review Required"}
          </span>
        </div>

        {finding.hitl_note && alreadyReviewed && (
          <p className="text-xs text-muted-foreground italic pl-5">
            Note: {finding.hitl_note}
          </p>
        )}

        {!alreadyReviewed && (
          <>
            <Textarea
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              placeholder="Add review note (optional)..."
              className="h-14 text-xs resize-none"
            />

            {showModify && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Override severity:</span>
                <Select value={modSeverity} onValueChange={setModSeverity}>
                  <SelectTrigger className="h-7 w-28 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="critical">Critical</SelectItem>
                    <SelectItem value="major">Major</SelectItem>
                    <SelectItem value="minor">Minor</SelectItem>
                    <SelectItem value="observation">Observation</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button
                size="sm"
                className="h-7 text-xs"
                disabled={submitting}
                onClick={() => handleReview("approve")}
              >
                <ThumbsUp className="size-3 mr-1" /> Confirm
              </Button>
              <Button
                size="sm"
                variant="destructive"
                className="h-7 text-xs"
                disabled={submitting}
                onClick={() => handleReview("reject")}
              >
                <ThumbsDown className="size-3 mr-1" /> False Positive
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                disabled={submitting}
                onClick={() => {
                  if (showModify) handleReview("modify");
                  else setShowModify(true);
                }}
              >
                <Pencil className="size-3 mr-1" /> {showModify ? "Save" : "Modify"}
              </Button>
            </div>
          </>
        )}

        {alreadyReviewed && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 text-[10px] text-muted-foreground"
            disabled={submitting}
            onClick={() => handleReset()}
          >
            Reset review
          </Button>
        )}
      </div>
    </div>
  );
}
