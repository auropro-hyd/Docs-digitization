"use client";

import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
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

function ElapsedTimer({ startedAt }: { startedAt: number | null }) {
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef(0);

  useEffect(() => {
    if (!startedAt) return;
    timerRef.current = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => clearInterval(timerRef.current);
  }, [startedAt]);

  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
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

function AgentCard({ progress }: { progress: AgentProgress }) {
  const [expanded, setExpanded] = useState(false);
  const display = AGENT_DISPLAY_NAMES[progress.agent] || progress.agent;
  const color = AGENT_COLORS[progress.agent] || "bg-primary";
  const hasRules = progress.rules.length > 0;
  const isPrescreening = progress.status === "prescreening";

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

          {isPrescreening && (
            <PrescreenIndicator prescreen={progress.prescreen} />
          )}

          {progress.status === "running" && (
            <div className="space-y-1.5">
              {progress.prescreen.status === "complete" && (
                <div className="flex items-center gap-1.5 mb-1">
                  <CheckCircle className="size-3 text-cyan-600 dark:text-cyan-400 flex-shrink-0" />
                  <span className="text-[10px] text-cyan-700 dark:text-cyan-300">
                    Pre-screened — avg {progress.prescreen.avgApplicable ?? "?"}/{progress.prescreen.totalRules} rules/page
                  </span>
                </div>
              )}
              <Progress value={progress.percent} className="h-1.5" />
              <p className="text-[11px] text-muted-foreground truncate">{progress.label}</p>
              <RuleStats rules={progress.rules} />
            </div>
          )}

          {progress.status === "complete" && (
            <div>
              {progress.prescreen.status === "complete" && (
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
  filename,
  onCancel,
}: {
  filename?: string | null;
  onCancel?: () => void;
}) {
  const {
    phase, overallPercent, label, agents, totalFindings, startedAt,
    segmentationLabel, segmentationSections,
  } = useComplianceStore();
  const agentList = Object.values(agents);

  const findingsSoFar = agentList.reduce((s, a) => s + a.findingsCount, 0);
  const needsReview = agentList.reduce((s, a) => s + a.needsReviewCount, 0);
  const completedAgents = agentList.filter((a) => a.status === "complete").length;
  const applicableAgents = agentList.filter((a) => a.status !== "skipped").length;

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
                  <span>{completedAgents}/{applicableAgents} agents done</span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              <Clock className="size-3.5 text-muted-foreground" />
              <ElapsedTimer startedAt={startedAt} />
              {onCancel && (
                <Button variant="ghost" size="sm" className="text-xs text-muted-foreground h-7 px-2" onClick={onCancel}>
                  Cancel
                </Button>
              )}
            </div>
          </div>

          <Progress value={overallPercent} className="h-2" />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        <AnimatePresence mode="popLayout">
          {agentList.map((a) => (
            <AgentCard key={a.agent} progress={a} />
          ))}
        </AnimatePresence>
      </div>

      {(findingsSoFar > 0 || needsReview > 0 || phase === "report") && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex items-center justify-center gap-6 text-sm text-muted-foreground"
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
