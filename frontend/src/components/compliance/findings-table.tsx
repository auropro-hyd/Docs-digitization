"use client";

import React, { useState, useMemo, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { cn, formatPageRanges } from "@/lib/utils";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ExternalLink,
  Filter,
  Info,
  ShieldCheck,
  ToggleLeft,
  ToggleRight,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  Eye,
  ShieldAlert,
  Gauge,
} from "lucide-react";
import { reviewComplianceFinding, resolveComplianceFinding, getPageImageUrl } from "@/lib/api";
import { VisualEvidenceViewer } from "@/components/compliance/visual-evidence-viewer";
import { HITLBadge, normalizeHitlStatus } from "@/components/compliance/hitl-badge";
import { toast } from "sonner";

interface SectionRef {
  section_id: string;
  section_name: string;
  pages: number[];
}

import type { ComplianceFinding as BaseFinding } from "@/types/compliance";

interface Finding extends BaseFinding {
  hitl_note: string;
  hitl_reviewed_at: string | null;
  source?: string;
  section_refs?: SectionRef[];
  applicability_trace?: string[];
}

interface ReportScoresUpdate {
  model_score?: number;
  review_adjusted_score?: number;
  overall_score?: number;
  agent_scores?: Array<{
    agent: string;
    model_score?: number;
    review_adjusted_score?: number;
  }>;
}

interface FindingsTableProps {
  findings: Finding[];
  docId: string;
  onFindingUpdate?: (findingId: string, updates: Partial<Finding>) => void;
  // Fires after each HITL review so the parent can refresh its scorecards
  // without a full report refetch (the "score-not-improving" UX bug fix).
  onScoresUpdate?: (scores: ReportScoresUpdate) => void;
  highlightId?: string;
  initialHitlFilter?: "all" | "needs_review" | "reviewed" | "auto";
  initialAgentFilter?: string;
  initialSeverityFilter?: "all" | "critical" | "major" | "minor" | "observation";
}

const SEVERITY_CONFIG: Record<string, { label: string; icon: typeof AlertCircle; cls: string; badgeCls: string }> = {
  critical: { label: "Critical", icon: AlertCircle, cls: "text-destructive", badgeCls: "bg-destructive/10 text-destructive border-destructive/20" },
  major: { label: "Major", icon: AlertTriangle, cls: "text-warning", badgeCls: "bg-warning/10 text-warning border-warning/20" },
  minor: { label: "Minor", icon: Info, cls: "text-amber-600 dark:text-amber-400", badgeCls: "bg-amber-100 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 border-amber-200" },
  observation: { label: "Observation", icon: CheckCircle, cls: "text-muted-foreground", badgeCls: "bg-muted text-muted-foreground" },
};

import { AGENT_DISPLAY_NAMES } from "@/types/compliance";

type SortKey = "severity" | "rule_id" | "agent" | "page" | "confidence" | "hitl";
const SEVERITY_ORDER: Record<string, number> = { critical: 0, major: 1, minor: 2, observation: 3 };
const HITL_ORDER: Record<string, number> = {
  needs_review: 0,
  user_modified: 1,
  auto_approved: 2,
  system_confirmed: 2,
  user_approved: 3,
  user_rejected: 4,
  unknown: 5,
};

function ConfidenceBadge({ confidence }: { confidence: number }) {
  const pct = Math.round(confidence * 100);
  let cls = "text-success border-success/20 bg-success/5";
  if (pct < 60) {
    cls = "text-destructive border-destructive/20 bg-destructive/5";
  } else if (pct < 80) {
    cls = "text-warning border-warning/20 bg-warning/5";
  }

  return (
    <Badge variant="outline" className={cn("text-[10px] px-1.5 py-0 gap-0.5", cls)}>
      <Gauge className="size-2.5" />
      {pct}%
    </Badge>
  );
}


function HITLReviewActions({
  finding,
  docId,
  onUpdate,
  onScores,
}: {
  finding: Finding;
  docId: string;
  onUpdate: (updates: Partial<Finding>) => void;
  onScores?: (scores: ReportScoresUpdate) => void;
}) {
  const [reviewNote, setReviewNote] = useState(finding.hitl_note || "");
  const [modSeverity, setModSeverity] = useState(finding.severity);
  const [submitting, setSubmitting] = useState(false);
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
        // Lift the recomputed scores to the parent so the agent scorecard
        // and the toolbar refresh without a full report refetch — without
        // this the displayed score doesn't budge after a reject.
        if (onScores) {
          onScores({
            model_score: result.model_score,
            review_adjusted_score: result.review_adjusted_score,
            overall_score: result.overall_score,
            agent_scores: result.agent_scores,
          });
        }
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
      if (onScores) {
        onScores({
          model_score: result.model_score,
          review_adjusted_score: result.review_adjusted_score,
          overall_score: result.overall_score,
          agent_scores: result.agent_scores,
        });
      }
      toast.success("Review reset");
    } catch {
      toast.error("Failed to reset review");
    } finally {
      setSubmitting(false);
    }
  }, [docId, finding.finding_id, onUpdate, onScores]);

  const alreadyReviewed = ["user_approved", "user_rejected", "user_modified"].includes(finding.hitl_status);

  return (
    <div className="space-y-3 pt-2 border-t border-dashed">
      <div className="flex items-center gap-2">
        <ShieldAlert className="size-3.5 text-warning" />
        <span className="text-xs font-medium">
          {alreadyReviewed ? "Review Decision" : "Human Review Required"}
        </span>
        <ConfidenceBadge confidence={finding.confidence} />
        {alreadyReviewed && <HITLBadge status={finding.hitl_status} />}
      </div>

      {finding.hitl_note && alreadyReviewed && (
        <p className="text-xs text-muted-foreground italic pl-5">
          Note: {finding.hitl_note}
        </p>
      )}

      {!alreadyReviewed && (
        <>
          <div className="text-[11px] text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
            Score impact: Confirm keeps this finding penalty, False Positive removes it, Modify recalculates it from updated severity.
          </div>
          <Textarea
            value={reviewNote}
            onChange={(e) => setReviewNote(e.target.value)}
            placeholder="Add review note (optional)..."
            className="h-16 text-xs resize-none"
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
              <ThumbsUp className="size-3 mr-1" /> Confirm Finding
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
              <Pencil className="size-3 mr-1" /> {showModify ? "Save Changes" : "Modify"}
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
  );
}

function PageRangeLabel({ pages, className }: { pages: number[]; className?: string }) {
  const { display, full } = formatPageRanges(pages);
  if (!display) return null;
  return (
    <span className={cn("text-muted-foreground truncate", className)} title={full}>
      {display}
    </span>
  );
}

function FilterSelects({
  hitlFilter, setHitlFilter,
  severityFilter, setSeverityFilter,
  agentFilter, setAgentFilter,
  resolvedFilter, setResolvedFilter,
  sortKey, setSortKey,
  agents, vertical,
}: {
  hitlFilter: string; setHitlFilter: (v: string) => void;
  severityFilter: string; setSeverityFilter: (v: string) => void;
  agentFilter: string; setAgentFilter: (v: string) => void;
  resolvedFilter: string; setResolvedFilter: (v: string) => void;
  sortKey: SortKey; setSortKey: (v: SortKey) => void;
  agents: string[]; vertical?: boolean;
}) {
  const w = vertical ? "w-full" : "w-28";
  const wWide = vertical ? "w-full" : "w-32";
  return (
    <>
      <Select value={hitlFilter} onValueChange={setHitlFilter}>
        <SelectTrigger className={cn("h-8 text-xs", wWide)}><SelectValue placeholder="Review" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All review states</SelectItem>
          <SelectItem value="needs_review">Needs action</SelectItem>
          <SelectItem value="reviewed">Reviewed by user</SelectItem>
          <SelectItem value="auto">Auto-cleared</SelectItem>
        </SelectContent>
      </Select>
      <Select value={severityFilter} onValueChange={setSeverityFilter}>
        <SelectTrigger className={cn("h-8 text-xs", w)}><SelectValue placeholder="Severity" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All severity</SelectItem>
          <SelectItem value="critical">Critical</SelectItem>
          <SelectItem value="major">Major</SelectItem>
          <SelectItem value="minor">Minor</SelectItem>
          <SelectItem value="observation">Observation</SelectItem>
        </SelectContent>
      </Select>
      <Select value={agentFilter} onValueChange={setAgentFilter}>
        <SelectTrigger className={cn("h-8 text-xs", w)}><SelectValue placeholder="Agent" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All agents</SelectItem>
          {agents.map((a) => (
            <SelectItem key={a} value={a}>{AGENT_DISPLAY_NAMES[a] || a}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={resolvedFilter} onValueChange={setResolvedFilter}>
        <SelectTrigger className={cn("h-8 text-xs", w)}><SelectValue placeholder="Status" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All</SelectItem>
          <SelectItem value="unresolved">Open</SelectItem>
          <SelectItem value="resolved">Resolved</SelectItem>
        </SelectContent>
      </Select>
      <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
        <SelectTrigger className={cn("h-8 text-xs", w)}><SelectValue placeholder="Sort" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="severity">Severity</SelectItem>
          <SelectItem value="confidence">Confidence</SelectItem>
          <SelectItem value="hitl">Review status</SelectItem>
          <SelectItem value="rule_id">Rule ID</SelectItem>
          <SelectItem value="agent">Agent</SelectItem>
          <SelectItem value="page">Page</SelectItem>
        </SelectContent>
      </Select>
    </>
  );
}

const PAGE_SIZE = 25;

export function FindingsTable({
  findings: initialFindings,
  docId,
  onFindingUpdate,
  onScoresUpdate,
  highlightId,
  initialHitlFilter = "all",
  initialAgentFilter = "all",
  initialSeverityFilter = "all",
}: FindingsTableProps) {
  const [findings, setFindings] = useState<Finding[]>(initialFindings);
  const [severityFilter, setSeverityFilter] = useState<string>(initialSeverityFilter);
  const [agentFilter, setAgentFilter] = useState<string>(initialAgentFilter);
  const [resolvedFilter, setResolvedFilter] = useState<string>("all");
  const [hitlFilter, setHitlFilter] = useState<string>(initialHitlFilter);
  const [sortKey, setSortKey] = useState<SortKey>("severity");
  const [expandedId, setExpandedId] = useState<string | null>(highlightId || null);
  const [currentPage, setCurrentPage] = useState(1);
  const isSmall = useMediaQuery("(max-width: 639px)");

  // Sync local state when parent passes updated findings (e.g. after resolve)
  React.useEffect(() => {
    setFindings(initialFindings);
  }, [initialFindings]);
  React.useEffect(() => {
    setSeverityFilter(initialSeverityFilter);
  }, [initialSeverityFilter]);
  React.useEffect(() => {
    setHitlFilter(initialHitlFilter);
  }, [initialHitlFilter]);
  React.useEffect(() => {
    setAgentFilter(initialAgentFilter);
  }, [initialAgentFilter]);

  const agents = useMemo(() => [...new Set(findings.map((f) => f.agent))], [findings]);
  const needsReviewCount = useMemo(() => findings.filter((f) => f.hitl_status === "needs_review").length, [findings]);

  const handleFindingUpdate = useCallback(
    (findingId: string, updates: Partial<Finding>) => {
      setFindings((prev) =>
        prev.map((f) => (f.finding_id === findingId ? { ...f, ...updates } : f)),
      );
      onFindingUpdate?.(findingId, updates);
    },
    [onFindingUpdate],
  );

  const filtered = useMemo(() => {
    let result = [...findings];
    if (severityFilter !== "all") result = result.filter((f) => f.severity === severityFilter);
    if (agentFilter !== "all") result = result.filter((f) => f.agent === agentFilter);
    if (resolvedFilter === "resolved") result = result.filter((f) => f.resolved);
    if (resolvedFilter === "unresolved") result = result.filter((f) => !f.resolved);
    if (hitlFilter === "needs_review") result = result.filter((f) => f.hitl_status === "needs_review");
    if (hitlFilter === "reviewed") result = result.filter((f) => ["user_approved", "user_rejected", "user_modified"].includes(f.hitl_status));
    if (hitlFilter === "auto")
      result = result.filter(
        (f) =>
          normalizeHitlStatus(f.hitl_status) === "auto_approved" ||
          normalizeHitlStatus(f.hitl_status) === "system_confirmed",
      );

    result.sort((a, b) => {
      if (sortKey === "severity") return (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9);
      if (sortKey === "rule_id") return a.rule_id.localeCompare(b.rule_id);
      if (sortKey === "agent") return a.agent.localeCompare(b.agent);
      if (sortKey === "page") return (a.page_numbers[0] ?? 999) - (b.page_numbers[0] ?? 999);
      if (sortKey === "confidence") return a.confidence - b.confidence;
      if (sortKey === "hitl") return (HITL_ORDER[a.hitl_status] ?? 9) - (HITL_ORDER[b.hitl_status] ?? 9);
      return 0;
    });
    return result;
  }, [findings, severityFilter, agentFilter, resolvedFilter, hitlFilter, sortKey]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(currentPage, totalPages);
  const paginated = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, safePage]);

  // Reset to page 1 when any filter/sort changes
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional reset on filter change
  React.useEffect(() => { setCurrentPage(1); }, [severityFilter, agentFilter, resolvedFilter, hitlFilter, sortKey]);

  // When highlightId changes, jump to the correct page and scroll into view
  useEffect(() => {
    if (!highlightId) return;
    const index = filtered.findIndex((f) => f.finding_id === highlightId);
    if (index < 0) return;
    const targetPage = Math.ceil((index + 1) / PAGE_SIZE);
    setCurrentPage(targetPage);
    setExpandedId(highlightId);
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-finding-id="${highlightId}"]`);
      el?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 100);
    return () => clearTimeout(timer);
  }, [highlightId, filtered]);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <CardTitle className="text-sm">
              Findings ({filtered.length}/{findings.length})
            </CardTitle>
            {needsReviewCount > 0 && (
              <Badge
                variant="outline"
                className="text-[10px] px-1.5 py-0 text-warning border-warning/30 bg-warning/5 cursor-pointer"
                onClick={() => {
                  setHitlFilter(hitlFilter === "needs_review" ? "all" : "needs_review");
                }}
              >
                <Eye className="size-2.5 mr-0.5" />
                {needsReviewCount} in review queue
              </Badge>
            )}
          </div>
          {isSmall ? (
            <Sheet>
              <SheetTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 text-xs gap-1.5">
                  <Filter className="size-3" /> Filters & views
                  {(severityFilter !== "all" || agentFilter !== "all" || resolvedFilter !== "all" || hitlFilter !== "all") && (
                    <Badge variant="secondary" className="text-[10px] px-1 py-0 ml-1">Active</Badge>
                  )}
                </Button>
              </SheetTrigger>
              <SheetContent side="bottom" className="pb-8">
                <SheetHeader><SheetTitle className="text-sm">Filters, views, and sort</SheetTitle></SheetHeader>
                <div className="grid grid-cols-2 gap-3 mt-4">
                  <FilterSelects
                    hitlFilter={hitlFilter} setHitlFilter={setHitlFilter}
                    severityFilter={severityFilter} setSeverityFilter={setSeverityFilter}
                    agentFilter={agentFilter} setAgentFilter={setAgentFilter}
                    resolvedFilter={resolvedFilter} setResolvedFilter={setResolvedFilter}
                    sortKey={sortKey} setSortKey={setSortKey}
                    agents={agents} vertical
                  />
                </div>
              </SheetContent>
            </Sheet>
          ) : (
            <div className="flex items-center gap-2 flex-wrap">
              <FilterSelects
                hitlFilter={hitlFilter} setHitlFilter={setHitlFilter}
                severityFilter={severityFilter} setSeverityFilter={setSeverityFilter}
                agentFilter={agentFilter} setAgentFilter={setAgentFilter}
                resolvedFilter={resolvedFilter} setResolvedFilter={setResolvedFilter}
                sortKey={sortKey} setSortKey={setSortKey}
                agents={agents}
              />
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center py-8 text-center">
            <ShieldCheck className="size-8 text-success mb-2" />
            <p className="text-sm font-medium">No findings match filters</p>
            <p className="text-xs text-muted-foreground">Try adjusting your filters</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {totalPages > 1 && (
              <div className="flex items-center justify-between text-xs text-muted-foreground pb-2">
                <span>
                  Showing {(safePage - 1) * PAGE_SIZE + 1}–{Math.min(safePage * PAGE_SIZE, filtered.length)} of {filtered.length}
                </span>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage <= 1} onClick={() => setCurrentPage(1)}>
                    <ChevronsLeft className="size-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage <= 1} onClick={() => setCurrentPage((p) => p - 1)}>
                    <ChevronLeft className="size-3.5" />
                  </Button>
                  <span className="px-2 tabular-nums">{safePage}/{totalPages}</span>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage >= totalPages} onClick={() => setCurrentPage((p) => p + 1)}>
                    <ChevronRight className="size-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage >= totalPages} onClick={() => setCurrentPage(totalPages)}>
                    <ChevronsRight className="size-3.5" />
                  </Button>
                </div>
              </div>
            )}
            <AnimatePresence mode="popLayout">
              {paginated.map((finding, i) => {
                const config = SEVERITY_CONFIG[finding.severity] || SEVERITY_CONFIG.observation;
                const Icon = config.icon;
                const isExpanded = expandedId === finding.finding_id;
                const isNeedsReview = finding.hitl_status === "needs_review";

                return (
                  <motion.div
                    key={finding.finding_id}
                    layout
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ delay: i * 0.015 }}
                    className={cn(
                      "border rounded-lg transition-colors",
                      finding.resolved && "opacity-60",
                      isNeedsReview && "border-warning/40 bg-warning/[0.02]",
                      highlightId === finding.finding_id && "ring-2 ring-primary",
                    )}
                    data-finding-id={finding.finding_id}
                  >
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : finding.finding_id)}
                      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/50 transition-colors"
                    >
                      {isExpanded ? (
                        <ChevronDown className="size-4 text-muted-foreground flex-shrink-0" />
                      ) : (
                        <ChevronRight className="size-4 text-muted-foreground flex-shrink-0" />
                      )}
                      <Icon className={cn("size-4 flex-shrink-0 mt-0.5", config.cls)} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">{finding.rule_id}</span>
                          <Badge variant="outline" className={cn("text-[10px]", config.badgeCls)}>
                            {config.label}
                          </Badge>
                          {finding.resolved && (
                            <Badge variant="outline" className="text-[10px] text-success border-success/20">
                              Resolved
                            </Badge>
                          )}
                          {finding.evaluation_channels && finding.evaluation_channels.length > 0 && (
                            <>
                              {finding.evaluation_channels.includes("vision") && (
                                <Badge variant="outline" className="text-[10px] border-violet-300 dark:border-violet-700 text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-900/10">
                                  <Eye className="size-2.5 mr-0.5" /> VLM
                                </Badge>
                              )}
                              {finding.evaluation_channels.includes("text") && !finding.evaluation_channels.includes("vision") && (
                                <Badge variant="outline" className="text-[10px] border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/10">
                                  TEXT
                                </Badge>
                              )}
                              {finding.evaluation_channels.includes("text") && finding.evaluation_channels.includes("vision") && (
                                <Badge variant="outline" className="text-[10px] border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/10">
                                  TEXT+VLM
                                </Badge>
                              )}
                            </>
                          )}
                          <p className="text-xs text-muted-foreground truncate flex-1 ml-1">
                            {finding.description}
                          </p>
                        </div>
                        <div className="flex items-center gap-2 mt-1 flex-wrap">
                          <Badge variant="outline" className="text-[10px]">
                            {AGENT_DISPLAY_NAMES[finding.agent] || finding.agent}
                          </Badge>
                          <ConfidenceBadge confidence={finding.confidence} />
                          <HITLBadge status={finding.hitl_status} />
                          {finding.page_numbers.length > 0 && (
                            <PageRangeLabel pages={finding.page_numbers} className="text-[10px] max-w-[200px]" />
                          )}
                        </div>
                      </div>
                    </button>

                    <AnimatePresence>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          className="overflow-hidden"
                        >
                          <div className="px-4 pb-4 space-y-3 border-t pt-3 ml-11">
                            {finding.rule_text && (
                              <div>
                                <p className="text-xs font-medium text-foreground mb-0.5">Rule</p>
                                <p className="text-xs text-muted-foreground">{finding.rule_text}</p>
                              </div>
                            )}
                            {finding.reasoning && (
                              <div className="p-2.5 rounded bg-blue-50 dark:bg-blue-900/10 border-l-2 border-blue-400 max-h-36 overflow-y-auto">
                                <p className="text-xs font-medium text-foreground mb-0.5">Reasoning</p>
                                <p className="text-xs text-muted-foreground">{finding.reasoning}</p>
                              </div>
                            )}
                            {finding.applicability_trace && finding.applicability_trace.length > 0 && (
                              <div className="p-2.5 rounded bg-emerald-50 dark:bg-emerald-900/10 border-l-2 border-emerald-400">
                                <p className="text-xs font-medium text-foreground mb-1">Why this rule applied</p>
                                <ul className="list-disc pl-4 space-y-0.5">
                                  {finding.applicability_trace.map((step, idx) => (
                                    <li key={`${finding.finding_id}-trace-${idx}`} className="text-xs text-muted-foreground">
                                      {step}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            {finding.description && (
                              <div>
                                <p className="text-xs font-medium text-foreground mb-0.5">Description</p>
                                <p className="text-xs text-muted-foreground">{finding.description}</p>
                              </div>
                            )}
                            {finding.evidence && (
                              <div className="p-2.5 rounded bg-muted border-l-2 border-warning max-h-48 overflow-y-auto">
                                <p className="text-xs font-medium text-foreground mb-0.5">Evidence</p>
                                <p className="text-xs text-muted-foreground whitespace-pre-wrap italic">&ldquo;{finding.evidence}&rdquo;</p>
                              </div>
                            )}
                            {finding.evaluation_channels && finding.evaluation_channels.includes("vision") && (
                              <div className="p-2.5 rounded bg-violet-50 dark:bg-violet-900/10 border-l-2 border-violet-400 space-y-2">
                                <div className="flex items-center gap-2">
                                  <Eye className="size-3.5 text-violet-600 dark:text-violet-400" />
                                  <p className="text-xs font-medium text-violet-700 dark:text-violet-300">Visual Analysis</p>
                                  <div className="flex gap-1">
                                    {finding.evaluation_channels.map((ch) => (
                                      <Badge
                                        key={ch}
                                        variant="outline"
                                        className={cn(
                                          "text-[9px] px-1.5 py-0",
                                          ch === "vision"
                                            ? "border-violet-300 dark:border-violet-700 text-violet-600 dark:text-violet-400"
                                            : "border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400"
                                        )}
                                      >
                                        {ch}
                                      </Badge>
                                    ))}
                                  </div>
                                </div>
                                {finding.visual_evidence && (
                                  <p className="text-xs text-muted-foreground">{finding.visual_evidence}</p>
                                )}
                                {finding.page_numbers.length > 0 && (
                                  <div className="flex items-center gap-3">
                                    <VisualEvidenceViewer
                                      docId={docId}
                                      pageNum={finding.page_numbers[0]}
                                      visualEvidence={finding.visual_evidence}
                                      visualRegions={finding.visual_regions}
                                      evaluationChannels={finding.evaluation_channels}
                                      ruleId={finding.rule_id}
                                    />
                                    {/* Thumbnail preview */}
                                    <VisualEvidenceViewer
                                      docId={docId}
                                      pageNum={finding.page_numbers[0]}
                                      visualEvidence={finding.visual_evidence}
                                      visualRegions={finding.visual_regions}
                                      evaluationChannels={finding.evaluation_channels}
                                      ruleId={finding.rule_id}
                                      trigger={
                                        <button className="rounded border border-violet-200 dark:border-violet-800 overflow-hidden hover:ring-2 hover:ring-violet-400 transition-all flex-shrink-0">
                                          {/* eslint-disable-next-line @next/next/no-img-element */}
                                          <img
                                            src={getPageImageUrl(docId, finding.page_numbers[0])}
                                            alt={`Page ${finding.page_numbers[0]} thumbnail`}
                                            className="h-16 w-auto object-cover"
                                          />
                                        </button>
                                      }
                                    />
                                  </div>
                                )}
                              </div>
                            )}
                            {finding.section_refs && finding.section_refs.length > 0 && (
                              <div className="p-2.5 rounded bg-rose-50 dark:bg-rose-900/10 border-l-2 border-rose-400">
                                <p className="text-xs font-medium text-foreground mb-1">Cross-Page Sections</p>
                                <div className="flex flex-wrap gap-1.5">
                                  {finding.section_refs.map((ref) => (
                                    <Badge
                                      key={ref.section_id}
                                      variant="outline"
                                      className="text-[10px] px-1.5 py-0 border-rose-300 dark:border-rose-700"
                                    >
                                      {ref.section_name} (p{ref.pages[0]}–p{ref.pages[ref.pages.length - 1]})
                                    </Badge>
                                  ))}
                                </div>
                                {finding.source === "auto_discovered" && (
                                  <Badge variant="outline" className="mt-1.5 text-[9px] px-1 py-0 border-amber-300 bg-amber-50 dark:bg-amber-900/10 text-amber-700 dark:text-amber-400">
                                    Auto-discovered
                                  </Badge>
                                )}
                              </div>
                            )}
                            {finding.recommendation && (
                              <div className="p-2.5 rounded bg-primary/5">
                                <p className="text-xs font-medium text-foreground mb-0.5">Recommendation</p>
                                <p className="text-xs text-muted-foreground">{finding.recommendation}</p>
                              </div>
                            )}

                            <div className="flex items-center gap-2 pt-1">
                              {finding.page_numbers.length > 0 && (
                                <Button
                                  variant="outline"
                                  size="sm"
                                  className="h-7 text-xs"
                                  onClick={() => window.open(`/review?doc=${docId}&page=${finding.page_numbers[0]}`, "_blank")}
                                >
                                  <ExternalLink className="size-3 mr-1" /> Go to page {finding.page_numbers[0]}
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 text-xs"
                                onClick={async () => {
                                  try {
                                    const result = await resolveComplianceFinding(docId, finding.finding_id);
                                    handleFindingUpdate(finding.finding_id, { resolved: result.resolved });
                                    toast.success(result.resolved ? "Finding marked as resolved" : "Finding reopened");
                                  } catch {
                                    toast.error("Failed to update finding");
                                  }
                                }}
                              >
                                {finding.resolved ? (
                                  <><ToggleRight className="size-3 mr-1" /> Unresolve</>
                                ) : (
                                  <><ToggleLeft className="size-3 mr-1" /> Mark Resolved</>
                                )}
                              </Button>
                            </div>

                            <HITLReviewActions
                              finding={finding}
                              docId={docId}
                              onUpdate={(updates) => handleFindingUpdate(finding.finding_id, updates)}
                              onScores={onScoresUpdate}
                            />
                          </div>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </motion.div>
                );
              })}
            </AnimatePresence>
            {totalPages > 1 && (
              <div className="flex items-center justify-between text-xs text-muted-foreground pt-3 border-t">
                <span>
                  Showing {(safePage - 1) * PAGE_SIZE + 1}–{Math.min(safePage * PAGE_SIZE, filtered.length)} of {filtered.length}
                </span>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage <= 1} onClick={() => setCurrentPage(1)}>
                    <ChevronsLeft className="size-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage <= 1} onClick={() => setCurrentPage((p) => p - 1)}>
                    <ChevronLeft className="size-3.5" />
                  </Button>
                  <span className="px-2 tabular-nums">{safePage}/{totalPages}</span>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage >= totalPages} onClick={() => setCurrentPage((p) => p + 1)}>
                    <ChevronRight className="size-3.5" />
                  </Button>
                  <Button variant="ghost" size="icon" className="size-7" disabled={safePage >= totalPages} onClick={() => setCurrentPage(totalPages)}>
                    <ChevronsRight className="size-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
