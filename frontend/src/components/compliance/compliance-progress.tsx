"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { downloadComplianceExport } from "@/lib/api";
import { toast } from "sonner";
import {
  CheckCircle,
  Loader2,
  Clock,
  SkipForward,
  AlertTriangle,
  ShieldCheck,
  ChevronDown,
  ChevronRight,
  XCircle,
  MinusCircle,
  HelpCircle,
  AlertCircle,
  Layers,
  Download,
} from "lucide-react";
import { type AgentProgress, type PrescreenProgress, type RuleProgress, useComplianceStore } from "@/stores/compliance-store";
import { AGENT_DISPLAY_NAMES } from "@/types/compliance";

const AGENT_COLORS: Record<string, string> = {
  alcoa: "bg-blue-500",
  gmp: "bg-emerald-500",
  checklist: "bg-amber-500",
  sop: "bg-violet-500",
  reconciliation: "bg-rose-500",
};

function ElapsedTimer({
  startedAt,
  endedAt,
  finalDurationSec,
}: {
  startedAt: number | null;
  endedAt: number | null;
  finalDurationSec: number | null;
}) {
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(0);

  const frozenElapsed =
    typeof finalDurationSec === "number" && finalDurationSec >= 0
      ? finalDurationSec
      : startedAt && endedAt
      ? Math.max(0, Math.floor((endedAt - startedAt) / 1000))
      : null;

  useEffect(() => {
    if (!startedAt || frozenElapsed !== null) return;
    timerRef.current = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timerRef.current);
  }, [startedAt, frozenElapsed]);

  const displaySeconds = frozenElapsed ?? elapsed;
  const mins = Math.floor(displaySeconds / 60);
  const secs = displaySeconds % 60;
  return (
    <span className="tabular-nums text-sm text-muted-foreground">
      {mins}:{secs.toString().padStart(2, "0")}
    </span>
  );
}

function RuleStatusIcon({ status }: { status: RuleProgress["status"] }) {
  switch (status) {
    case "compliant":
      return <CheckCircle className="size-3.5 text-success flex-shrink-0" />;
    case "non_compliant":
      return <XCircle className="size-3.5 text-destructive flex-shrink-0" />;
    case "uncertain":
      return <HelpCircle className="size-3.5 text-warning flex-shrink-0" />;
    case "not_applicable":
      return <MinusCircle className="size-3.5 text-muted-foreground/50 flex-shrink-0" />;
    case "evaluating":
      return <Loader2 className="size-3.5 text-primary animate-spin flex-shrink-0" />;
    default:
      return <div className="size-3.5 rounded-full border-2 border-muted-foreground/20 flex-shrink-0" />;
  }
}

function ConfidenceDot({ confidence }: { confidence: number }) {
  if (confidence >= 0.8) return null;
  const color = confidence >= 0.6 ? "bg-warning" : "bg-destructive";
  return (
    <span
      className={cn("size-1.5 rounded-full flex-shrink-0", color)}
      title={`Confidence: ${Math.round(confidence * 100)}%`}
    />
  );
}

function RuleChecklist({ rules, expanded }: { rules: RuleProgress[]; expanded: boolean }) {
  if (!expanded || rules.length === 0) return null;

  const categories = new Map<string, RuleProgress[]>();
  for (const r of rules) {
    const list = categories.get(r.category) || [];
    list.push(r);
    categories.set(r.category, list);
  }

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      className="overflow-hidden"
    >
      <div className="border-t mt-3 pt-3 space-y-3 max-h-64 overflow-y-auto">
        {[...categories.entries()].map(([cat, catRules]) => (
          <div key={cat}>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5 px-1">
              {cat}
            </p>
            <div className="space-y-0.5">
              {catRules.map((rule) => (
                <div
                  key={rule.id}
                  className={cn(
                    "flex items-center gap-2 px-2 py-1 rounded text-[11px] transition-colors",
                    rule.status === "non_compliant" && "bg-destructive/5",
                    rule.status === "uncertain" && "bg-warning/5",
                    rule.status === "compliant" && "bg-success/5",
                    rule.status === "pending" && "opacity-50",
                  )}
                >
                  <RuleStatusIcon status={rule.status} />
                  <span className="flex-1 min-w-0 truncate text-muted-foreground">
                    <span className="font-medium text-foreground/70">{rule.id}</span>
                    {" "}
                    {rule.text}
                  </span>
                  <ConfidenceDot confidence={rule.confidence} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

function RuleStats({ rules }: { rules: RuleProgress[] }) {
  if (rules.length === 0) return null;

  const compliant = rules.filter((r) => r.status === "compliant").length;
  const nonCompliant = rules.filter((r) => r.status === "non_compliant").length;
  const uncertain = rules.filter((r) => r.status === "uncertain").length;
  const evaluated = rules.filter((r) => r.status !== "pending" && r.status !== "evaluating").length;
  const lowConfidence = rules.filter((r) => r.confidence < 0.8 && r.status !== "pending").length;

  return (
    <div className="flex items-center gap-2 flex-wrap mt-1.5">
      <span className="text-[10px] text-muted-foreground">{evaluated}/{rules.length}</span>
      {compliant > 0 && (
        <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 text-success border-success/20 bg-success/5">
          {compliant} pass
        </Badge>
      )}
      {nonCompliant > 0 && (
        <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 text-destructive border-destructive/20 bg-destructive/5">
          {nonCompliant} fail
        </Badge>
      )}
      {uncertain > 0 && (
        <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 text-warning border-warning/20 bg-warning/5">
          {uncertain} uncertain
        </Badge>
      )}
      {lowConfidence > 0 && (
        <Badge variant="outline" className="text-[9px] px-1 py-0 h-4 text-amber-600 dark:text-amber-400 border-amber-300 bg-amber-50 dark:bg-amber-900/10">
          {lowConfidence} needs review
        </Badge>
      )}
    </div>
  );
}

function PrescreenIndicator({ prescreen }: { prescreen: PrescreenProgress }) {
  if (prescreen.status === "idle") return null;

  const isComplete = prescreen.status === "complete";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        {isComplete ? (
          <CheckCircle className="size-3.5 text-cyan-600 dark:text-cyan-400 flex-shrink-0" />
        ) : (
          <Loader2 className="size-3.5 text-cyan-600 dark:text-cyan-400 animate-spin flex-shrink-0" />
        )}
        <span className="text-[11px] font-medium text-cyan-700 dark:text-cyan-300">
          {isComplete ? "Pre-screen complete" : "Pre-screening pages..."}
        </span>
      </div>
      <Progress value={prescreen.percent} className="h-1" />
      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
        <span>{prescreen.pagesDone}/{prescreen.pagesTotal} pages</span>
        {isComplete && prescreen.avgApplicable !== null ? (
          <span>
            avg {prescreen.avgApplicable}/{prescreen.totalRules} rules/page
          </span>
        ) : (
          <span>{prescreen.totalRules} rules to screen</span>
        )}
      </div>
    </div>
  );
}

function AgentCard({
  progress,
  docId,
}: {
  progress: AgentProgress;
  docId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [exporting, setExporting] = useState<"html" | "md" | null>(null);
  const display = AGENT_DISPLAY_NAMES[progress.agent] || progress.agent;
  const color = AGENT_COLORS[progress.agent] || "bg-primary";
  const hasRules = progress.rules.length > 0;
  const isPrescreening = progress.status === "prescreening";
  const hasPrescreen =
    progress.prescreen.status !== "idle" ||
    progress.prescreen.totalRules > 0 ||
    progress.prescreen.pagesTotal > 0;
  const canExportThisAgent = progress.status === "complete";

  const exportAgent = async (format: "html" | "md") => {
    if (!canExportThisAgent) return;
    try {
      setExporting(format);
      await downloadComplianceExport(docId, format, { agent: progress.agent });
      toast.success(`Exported ${display} as ${format.toUpperCase()}`);
    } catch {
      toast.error("Agent export is not ready yet");
    } finally {
      setExporting(null);
    }
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <Card
        className={cn(
          "transition-all duration-300",
          isPrescreening && "ring-2 ring-cyan-500/30",
          progress.status === "running" && "ring-2 ring-primary/30",
          progress.status === "complete" && "ring-2 ring-success/30",
          progress.status === "skipped" && "opacity-60",
        )}
      >
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <div className={cn("size-2.5 rounded-full", color)} />
              <span className="text-sm font-medium">{display}</span>
              {hasRules && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                  {progress.rules.length} rules
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              {hasRules && (progress.status === "running" || progress.status === "complete") && (
                <button
                  onClick={() => setExpanded(!expanded)}
                  className="size-6 rounded-md hover:bg-muted flex items-center justify-center transition-colors"
                  title={expanded ? "Collapse rules" : "Expand rules"}
                >
                  {expanded ? (
                    <ChevronDown className="size-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="size-3.5 text-muted-foreground" />
                  )}
                </button>
              )}
              <AgentStatusIcon status={progress.status} />
            </div>
          </div>

          {isPrescreening && hasPrescreen && (
            <div className="space-y-2">
              <div>
                <div className="flex items-center justify-between text-[10px] text-cyan-700 dark:text-cyan-300 mb-1">
                  <span>Stage 1/2: Pre-screen</span>
                  <span>{progress.prescreen.percent}%</span>
                </div>
                <Progress value={progress.prescreen.percent} className="h-1.5" />
              </div>
              <div>
                <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                  <span>Stage 2/2: Rule evaluation</span>
                  <span>0%</span>
                </div>
                <Progress value={0} className="h-1.5" />
              </div>
              <PrescreenIndicator prescreen={progress.prescreen} />
            </div>
          )}

          {progress.status === "running" && hasPrescreen && (
            <div className="space-y-1.5">
              <div>
                <div className="flex items-center justify-between text-[10px] text-cyan-700 dark:text-cyan-300 mb-1">
                  <span>Stage 1/2: Pre-screen</span>
                  <span>{progress.prescreen.status === "complete" ? 100 : progress.prescreen.percent}%</span>
                </div>
                <Progress value={progress.prescreen.status === "complete" ? 100 : progress.prescreen.percent} className="h-1.5" />
              </div>
              <div>
                <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                  <span>Stage 2/2: Rule evaluation</span>
                  <span>{progress.percent}%</span>
                </div>
                <Progress value={progress.percent} className="h-1.5" />
              </div>
              <p className="text-[11px] text-muted-foreground truncate">{progress.label}</p>
              <RuleStats rules={progress.rules} />
            </div>
          )}

          {progress.status === "running" && !hasPrescreen && (
            <div className="space-y-1.5">
              {(() => {
                const l = progress.label.toLowerCase();
                const stageTitle = l.includes("discover")
                  ? "Cross-page discovery"
                  : l.includes("section")
                  ? "Section mapping"
                  : l.includes("recon")
                  ? "Reconciliation"
                  : "Cross-section evaluation";
                return (
                  <div>
                    <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                      <span>{stageTitle}</span>
                      <span>{progress.percent}%</span>
                    </div>
                    <Progress value={progress.percent} className="h-1.5" />
                  </div>
                );
              })()}
              <p className="text-[11px] text-muted-foreground truncate">{progress.label}</p>
              <RuleStats rules={progress.rules} />
            </div>
          )}

          {progress.status === "complete" && (
            <div>
              {progress.prescreen.status === "complete" && hasPrescreen && (
                <div className="flex items-center gap-1.5 mb-1.5">
                  <CheckCircle className="size-3 text-cyan-600 dark:text-cyan-400 flex-shrink-0" />
                  <span className="text-[10px] text-cyan-700 dark:text-cyan-300">
                    Pre-screened — avg {progress.prescreen.avgApplicable ?? "?"}/{progress.prescreen.totalRules} rules/page
                  </span>
                </div>
              )}
              <div className="flex items-center gap-2">
                <p className="text-xs text-muted-foreground">
                  {progress.findingsCount} finding{progress.findingsCount !== 1 ? "s" : ""}
                </p>
                {progress.needsReviewCount > 0 && (
                  <Badge variant="outline" className="text-[10px] px-1.5 py-0 text-warning border-warning/30">
                    <AlertCircle className="size-2.5 mr-0.5" />
                    {progress.needsReviewCount} to review
                  </Badge>
                )}
              </div>
              <RuleStats rules={progress.rules} />
            </div>
          )}

          {progress.status === "skipped" && (
            <p className="text-[11px] text-muted-foreground truncate">
              {progress.skipReason || "Not applicable"}
            </p>
          )}

          {progress.status === "pending" && (
            <p className="text-[11px] text-muted-foreground">Waiting...</p>
          )}

          {progress.status !== "skipped" && (
            <div className="flex items-center gap-2 mt-2">
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[11px]"
                disabled={!canExportThisAgent || exporting !== null}
                onClick={() => exportAgent("html")}
                title={
                  canExportThisAgent
                    ? "Export this agent report as HTML"
                    : "Enabled once this agent is complete"
                }
              >
                <Download className="size-3 mr-1" />
                {exporting === "html" ? "Exporting..." : "Export HTML"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-[11px]"
                disabled={!canExportThisAgent || exporting !== null}
                onClick={() => exportAgent("md")}
                title={
                  canExportThisAgent
                    ? "Export this agent report as Markdown"
                    : "Enabled once this agent is complete"
                }
              >
                <Download className="size-3 mr-1" />
                {exporting === "md" ? "Exporting..." : "Export MD"}
              </Button>
            </div>
          )}

          <AnimatePresence>
            <RuleChecklist rules={progress.rules} expanded={expanded} />
          </AnimatePresence>
        </CardContent>
      </Card>
    </motion.div>
  );
}

function AgentStatusIcon({ status }: { status: string }) {
  switch (status) {
    case "prescreening":
      return <Loader2 className="size-4 text-cyan-600 dark:text-cyan-400 animate-spin" />;
    case "running":
      return <Loader2 className="size-4 text-primary animate-spin" />;
    case "complete":
      return <CheckCircle className="size-4 text-success" />;
    case "skipped":
      return <SkipForward className="size-4 text-muted-foreground" />;
    default:
      return <Clock className="size-4 text-muted-foreground" />;
  }
}

export function ComplianceProgress({
  docId,
  filename,
  onCancel,
  onViewReport,
  reportReady,
}: {
  docId: string;
  filename?: string | null;
  onCancel?: () => void;
  onViewReport?: (focus?: {
    tab?: "all" | string;
    hitlFilter?: "all" | "needs_review" | "reviewed" | "auto";
    severityFilter?: "all" | "critical" | "major" | "minor" | "observation";
  }) => void;
  reportReady?: boolean;
}) {
  const {
    phase, overallPercent, label, agents, totalFindings, startedAt, endedAt, finalDurationSec,
    segmentationLabel, segmentationSections, applicableAgents, skippedAgents,
  } = useComplianceStore();
  const visibleAgentIds = new Set([
    ...applicableAgents,
    ...skippedAgents.map((s) => s.category),
  ]);
  const allAgents = Object.values(agents);
  const agentList =
    visibleAgentIds.size > 0
      ? allAgents.filter((a) => visibleAgentIds.has(a.agent))
      : allAgents.filter((a) => a.status !== "pending");

  const findingsSoFar = agentList.reduce((s, a) => s + a.findingsCount, 0);
  const needsReview = agentList.reduce((s, a) => s + a.needsReviewCount, 0);
  const completedAgents = agentList.filter((a) => a.status === "complete").length;
  const applicableAgentCount = agentList.filter((a) => a.status !== "skipped").length;
  const lowConfidenceRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status !== "pending" && r.confidence < 0.8).length,
    0,
  );
  const evaluatedRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status !== "pending" && r.status !== "evaluating").length,
    0,
  );
  const failedRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status === "non_compliant").length,
    0,
  );
  const criticalRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status === "non_compliant" && r.severity === "critical").length,
    0,
  );
  const majorRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status === "non_compliant" && r.severity === "major").length,
    0,
  );
  const uncertainRules = agentList.reduce(
    (sum, a) => sum + a.rules.filter((r) => r.status === "uncertain").length,
    0,
  );

  const isSegmenting = phase === "segmentation";
  const segDone = segmentationSections > 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4"
    >
      <Card>
        <CardContent className="p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3 min-w-0">
              <ShieldCheck className="size-5 text-primary flex-shrink-0" />
              <div className="min-w-0">
                <h2 className="text-base font-semibold truncate">
                  Compliance Audit{filename ? ` — ${filename}` : ""}
                </h2>
                <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                  {isSegmenting ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="size-3 animate-spin text-cyan-500" />
                      {segmentationLabel || "Identifying sections..."}
                    </span>
                  ) : segDone ? (
                    <span className="flex items-center gap-1.5">
                      <Layers className="size-3 text-cyan-500" />
                      {segmentationSections} sections
                    </span>
                  ) : null}
                  <span>{completedAgents}/{applicableAgentCount} agents done</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              {onCancel && (
                <Button variant="ghost" size="sm" className="text-xs text-muted-foreground h-7 px-2" onClick={onCancel}>
                  Cancel
                </Button>
              )}
              {(phase === "complete" || reportReady) && reportReady && onViewReport && (
                <Button size="sm" className="h-7 text-xs" onClick={() => onViewReport({ tab: "all", hitlFilter: "all" })}>
                  View report
                </Button>
              )}
            </div>
          </div>

          <Progress value={overallPercent} className="h-2" />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="text-[11px]" title="Total elapsed run time">
              Elapsed: <span className="ml-1 font-medium"><ElapsedTimer startedAt={startedAt} endedAt={endedAt} finalDurationSec={finalDurationSec} /></span>
            </Badge>
            <Badge variant="outline" className="text-[11px]" title="Completed agents out of applicable agents">
              Agents complete: <span className="ml-1 font-medium">{completedAgents}/{applicableAgentCount}</span>
            </Badge>
            <Badge variant="outline" className="text-[11px]" title="Total findings identified so far">
              Findings: <span className="ml-1 font-medium">{findingsSoFar}</span>
            </Badge>
            <Badge variant="outline" className="text-[11px]" title="Findings requiring human decision">
              Review queue: <span className="ml-1 font-medium">{needsReview}</span>
            </Badge>
            <Badge variant="outline" className="text-[11px]" title="Rules currently marked non-compliant">
              Non-compliant: <span className="ml-1 font-medium">{failedRules}</span>
            </Badge>
            <Badge variant="outline" className="text-[11px]" title="Rules with confidence below 80%">
              Low confidence: <span className="ml-1 font-medium">{lowConfidenceRules}</span>
            </Badge>
            {evaluatedRules > 0 && (
              <Badge variant="outline" className="text-[11px]" title="Rules already evaluated (excluding pending)">
                Evaluated rules: <span className="ml-1 font-medium">{evaluatedRules}</span>
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        <AnimatePresence mode="popLayout">
          {agentList.map((a) => (
            <AgentCard
              key={a.agent}
              progress={a}
              docId={docId}
            />
          ))}
        </AnimatePresence>
      </div>

      {(findingsSoFar > 0 || needsReview > 0 || phase === "report" || reportReady) && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-wrap items-center justify-center gap-4 text-sm text-muted-foreground"
        >
          {findingsSoFar > 0 && (
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-warning" />
              <span>
                Findings so far: <strong className="text-foreground">{findingsSoFar}</strong>
              </span>
            </div>
          )}
          {needsReview > 0 && (
            <div className="flex items-center gap-2">
              <AlertCircle className="size-4 text-amber-500" />
              <span>
                Needs review: <strong className="text-foreground">{needsReview}</strong>
              </span>
            </div>
          )}
          {onViewReport && (
            <>
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => onViewReport({ tab: "all", hitlFilter: "all" })}>
                Open findings
              </Button>
              {needsReview > 0 && (
                <Button
                  size="sm"
                  className="h-7 text-xs"
                  title="Jump directly to findings that need review"
                  onClick={() => onViewReport({ tab: "all", hitlFilter: "needs_review" })}
                >
                  Start review ({needsReview})
                </Button>
              )}
              {criticalRules > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs border-destructive/40 text-destructive"
                  title="Open critical severity findings"
                  onClick={() => onViewReport({ tab: "all", hitlFilter: "all", severityFilter: "critical" })}
                >
                  Critical risk ({criticalRules})
                </Button>
              )}
              {majorRules > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs border-warning/40 text-warning"
                  title="Open major severity findings"
                  onClick={() => onViewReport({ tab: "all", hitlFilter: "all", severityFilter: "major" })}
                >
                  Major risk ({majorRules})
                </Button>
              )}
              {uncertainRules > 0 && (
                <Badge variant="outline" className="text-xs" title="Findings requiring clarification due to low certainty">
                  Uncertain findings: {uncertainRules}
                </Badge>
              )}
            </>
          )}
          {phase === "report" && (
            <Badge variant="outline" className="text-xs animate-pulse">
              Generating report...
            </Badge>
          )}
        </motion.div>
      )}
    </motion.div>
  );
}
