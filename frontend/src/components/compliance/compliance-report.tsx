"use client";

import { useState, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn, formatPageRanges } from "@/lib/utils";
import {
  Download,
  RefreshCw,
  SkipForward,
  ChevronDown,
  ChevronRight,
  Info,
  Eye,
  ShieldCheck as ShieldCheckIcon,
  CheckCircle,
  AlertCircle,
  HelpCircle,
  MinusCircle,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  ListChecks,
  Route,
  FileText,
} from "lucide-react";
import { ExecutiveSummary } from "./executive-summary";
import { AgentScorecard } from "./agent-scorecard";
import { FindingsTable } from "./findings-table";
import { SegmentationEditor } from "./segmentation-editor";
import { DiscoveredRulesPanel } from "./discovered-rules-panel";
import { downloadComplianceExport } from "@/lib/api";
import { toast } from "sonner";

interface ComplianceReportProps {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  report: Record<string, any>;
  docId: string;
  onReRun?: () => void;
  initialFocus?: {
    tab?: "all" | string;
    hitlFilter?: "all" | "needs_review" | "reviewed" | "auto";
    severityFilter?: "all" | "critical" | "major" | "minor" | "observation";
  };
}

import { AGENT_DISPLAY_NAMES, type ComplianceFinding as BaseFinding } from "@/types/compliance";

interface Finding extends BaseFinding {
  hitl_note: string;
  hitl_reviewed_at: string | null;
}

interface RuleResult {
  rule_id: string;
  rule_text: string;
  rule_category: string;
  agent: string;
  status: string;
  confidence: number;
  reasoning: string;
  evidence: string;
  applicability_trace?: string[];
  page_numbers: number[];
}

const STATUS_ICON: Record<string, { icon: typeof CheckCircle; cls: string; label: string }> = {
  compliant: { icon: CheckCircle, cls: "text-success", label: "Compliant" },
  non_compliant: { icon: AlertCircle, cls: "text-destructive", label: "Non-Compliant" },
  uncertain: { icon: HelpCircle, cls: "text-warning", label: "Uncertain" },
  not_applicable: { icon: MinusCircle, cls: "text-muted-foreground", label: "N/A" },
};

function PageRangeLabel({ pages, className }: { pages: number[]; className?: string }) {
  const { display, full } = formatPageRanges(pages);
  if (!display) return null;
  return (
    <span className={cn("text-muted-foreground truncate", className)} title={full}>
      {display}
    </span>
  );
}

function RuleEvaluationsList({ evaluations }: { evaluations: RuleResult[] }) {
  const [open, setOpen] = useState(false);
  const [expandedRule, setExpandedRule] = useState<string | null>(null);

  if (!evaluations || evaluations.length === 0) return null;

  const grouped: Record<string, RuleResult[]> = {};
  for (const ev of evaluations) {
    const key = ev.status;
    (grouped[key] ??= []).push(ev);
  }
  const statusOrder = ["compliant", "non_compliant", "uncertain", "not_applicable"];

  return (
    <Card>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-5 py-3 text-left hover:bg-muted/50 transition-colors"
      >
        {open ? <ChevronDown className="size-4 text-muted-foreground" /> : <ChevronRight className="size-4 text-muted-foreground" />}
        <ListChecks className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">All Rule Evaluations</span>
        <span className="text-xs text-muted-foreground ml-1">({evaluations.length} rules)</span>
        <div className="flex gap-1.5 ml-auto">
          {statusOrder.map((s) => {
            const count = grouped[s]?.length || 0;
            if (!count) return null;
            const cfg = STATUS_ICON[s] || STATUS_ICON.compliant;
            return (
              <Badge key={s} variant="outline" className={cn("text-[10px] px-1.5 py-0", cfg.cls)}>
                {count} {cfg.label.toLowerCase()}
              </Badge>
            );
          })}
        </div>
      </button>

      {open && (
        <CardContent className="pt-0 pb-4">
          <div className="space-y-1">
            {evaluations.map((ev) => {
              const cfg = STATUS_ICON[ev.status] || STATUS_ICON.compliant;
              const Icon = cfg.icon;
              const isExpanded = expandedRule === ev.rule_id;

              return (
                <div key={ev.rule_id} className="border rounded-md">
                  <button
                    onClick={() => setExpandedRule(isExpanded ? null : ev.rule_id)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/30 transition-colors"
                  >
                    {isExpanded ? <ChevronDown className="size-3 text-muted-foreground" /> : <ChevronRight className="size-3 text-muted-foreground" />}
                    <Icon className={cn("size-3.5", cfg.cls)} />
                    <span className="text-xs font-medium">{ev.rule_id}</span>
                    <Badge variant="outline" className={cn("text-[10px] px-1 py-0", cfg.cls)}>
                      {cfg.label}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate flex-1 ml-1">{ev.rule_text}</span>
                    {ev.page_numbers.length > 0 && (
                      <PageRangeLabel pages={ev.page_numbers} className="text-[10px] max-w-[140px] flex-shrink-0" />
                    )}
                  </button>
                  {isExpanded && (
                    <div className="px-3 pb-3 pt-1 ml-8 space-y-2 border-t">
                      {ev.reasoning && (
                        <div className="p-2 rounded bg-blue-50 dark:bg-blue-900/10 border-l-2 border-blue-400">
                          <p className="text-[11px] font-medium text-foreground mb-0.5">Reasoning</p>
                          <p className="text-[11px] text-muted-foreground">{ev.reasoning}</p>
                        </div>
                      )}
                      {ev.evidence && (
                        <div className="p-2 rounded bg-muted border-l-2 border-muted-foreground/30">
                          <p className="text-[11px] font-medium text-foreground mb-0.5">Evidence</p>
                          <p className="text-[11px] text-muted-foreground whitespace-pre-wrap italic">&ldquo;{ev.evidence}&rdquo;</p>
                        </div>
                      )}
                      {ev.applicability_trace && ev.applicability_trace.length > 0 && (
                        <div className="p-2 rounded bg-emerald-50 dark:bg-emerald-900/10 border-l-2 border-emerald-400">
                          <p className="text-[11px] font-medium text-foreground mb-1">Why this rule applied</p>
                          <ul className="space-y-0.5 list-disc pl-4">
                            {ev.applicability_trace.map((step, idx) => (
                              <li key={`${ev.rule_id}-trace-${idx}`} className="text-[11px] text-muted-foreground">
                                {step}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {!ev.reasoning && !ev.evidence && (
                        <p className="text-[11px] text-muted-foreground italic">No reasoning or evidence recorded.</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      )}
    </Card>
  );
}

export function ComplianceReportView({ report, docId, onReRun, initialFocus }: ComplianceReportProps) {
  const modelScore = Number((report.model_score as number | undefined) ?? report.overall_score ?? 0);
  const reviewAdjustedScore = report.review_adjusted_score as number | undefined;

  const [activeTab, setActiveTab] = useState(initialFocus?.tab || "all");
  const [highlightFinding, setHighlightFinding] = useState<string | null>(null);
  const [findings, setFindings] = useState<Finding[]>((report.findings || []) as Finding[]);
  const [viewHitlFilter, setViewHitlFilter] = useState<"all" | "needs_review" | "reviewed" | "auto">(initialFocus?.hitlFilter || "all");
  const [viewSeverityFilter, setViewSeverityFilter] = useState<"all" | "critical" | "major" | "minor" | "observation">(initialFocus?.severityFilter || "all");

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const agentReports = (report.agent_reports || []) as Array<Record<string, any>>;
  const skippedAgents = (report.skipped_agents || []) as Array<{ category: string; reason: string }>;

  const hitlCounts = {
    needsReview: findings.filter((f) => f.hitl_status === "needs_review").length,
    // System-confirmed groups both legacy (auto_approved) and new
    // (system_confirmed) wire values — they are the same display state.
    autoApproved: findings.filter(
      (f) => f.hitl_status === "auto_approved" || f.hitl_status === "system_confirmed",
    ).length,
    userApproved: findings.filter((f) => f.hitl_status === "user_approved").length,
    userRejected: findings.filter((f) => f.hitl_status === "user_rejected").length,
    userModified: findings.filter((f) => f.hitl_status === "user_modified").length,
    unknown: findings.filter(
      (f) =>
        f.hitl_status !== "auto_approved" &&
        f.hitl_status !== "system_confirmed" &&
        f.hitl_status !== "needs_review" &&
        f.hitl_status !== "user_approved" &&
        f.hitl_status !== "user_rejected" &&
        f.hitl_status !== "user_modified",
    ).length,
  };

  const handleFindingUpdate = useCallback((findingId: string, updates: Partial<Finding>) => {
    setFindings((prev) =>
      prev.map((f) => (f.finding_id === findingId ? { ...f, ...updates } : f)),
    );
  }, []);

  useEffect(() => {
    if (initialFocus?.tab) setActiveTab(initialFocus.tab);
    if (initialFocus?.hitlFilter) setViewHitlFilter(initialFocus.hitlFilter);
    if (initialFocus?.severityFilter) setViewSeverityFilter(initialFocus.severityFilter);
  }, [initialFocus?.tab, initialFocus?.hitlFilter, initialFocus?.severityFilter]);

  useEffect(() => {
    if (viewHitlFilter !== "needs_review") return;
    const firstPending = findings.find((f) => f.hitl_status === "needs_review");
    if (firstPending) {
      setHighlightFinding(firstPending.finding_id);
    }
  }, [viewHitlFilter, findings]);

  const activeAgentReport =
    activeTab === "all"
      ? null
      : agentReports.find((ar) => (ar.agent as string) === activeTab) ?? null;
  const activeAgentId = (activeAgentReport?.agent as string | undefined) ?? null;
  const activeAgentLabel =
    (activeAgentReport?.agent_display as string | undefined) ||
    (activeAgentId ? AGENT_DISPLAY_NAMES[activeAgentId] || activeAgentId : null);
  const activeAgentFindings = activeAgentId
    ? findings.filter((f) => f.agent === activeAgentId)
    : findings;

  const handleExport = async (format: "html" | "md", scope: "all" | "agent" = "all") => {
    try {
      const agent = scope === "agent" ? activeAgentId ?? undefined : undefined;
      await downloadComplianceExport(docId, format, { agent });
      if (scope === "agent" && activeAgentLabel) {
        toast.success(`Exported ${activeAgentLabel} as ${format.toUpperCase()}`);
      } else {
        toast.success(`Exported full report as ${format.toUpperCase()}`);
      }
    } catch {
      toast.error("Export failed");
    }
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const methodology = report.score_methodology as Record<string, any> | undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const trail = report.audit_trail as Record<string, any> | undefined;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Compliance Report</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {(report.filename as string) || "Document"} — Model score: {modelScore}/100
            {typeof reviewAdjustedScore === "number" ? ` • Review-adjusted: ${reviewAdjustedScore}/100` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Download className="size-4 mr-2" /> Export <ChevronDown className="size-3 ml-1" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleExport("html", "all")}>
                Export complete report (HTML)
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleExport("md", "all")}>
                Export complete report (Markdown)
              </DropdownMenuItem>
              {activeAgentId && activeAgentLabel && (
                <>
                  <DropdownMenuItem onClick={() => handleExport("html", "agent")}>
                    Export selected agent: {activeAgentLabel} (HTML)
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => handleExport("md", "agent")}>
                    Export selected agent: {activeAgentLabel} (Markdown)
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          {onReRun && (
            <Button variant="outline" size="sm" onClick={onReRun}>
              <RefreshCw className="size-4 mr-2" /> Run again
            </Button>
          )}
        </div>
      </div>

      {/* Flow guidance */}
      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="py-3 px-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-foreground flex items-center gap-1.5">
              <Route className="size-3.5 text-primary" /> Decision workflow
            </span>
            <Badge variant="outline" className="text-[10px]">1. Executive Summary</Badge>
            <ChevronRight className="size-3 text-muted-foreground" />
            <Badge variant="outline" className="text-[10px]">2. Agent Analysis</Badge>
            <ChevronRight className="size-3 text-muted-foreground" />
            <Badge variant="outline" className="text-[10px]">3. Export Scope</Badge>
          </div>
          <p className="text-xs text-muted-foreground mt-2">
            {activeAgentId
              ? `Currently focused: ${activeAgentLabel} (${activeAgentFindings.length} findings). Exports from this tab can be agent-specific.`
              : `Currently focused: combined cross-agent findings (${findings.length} total). Use agent tabs for detailed drill-down.`}
          </p>
        </CardContent>
      </Card>

      {/* Reviewer impact guidance */}
      <Card className="border-emerald-300/40 bg-emerald-50/40 dark:bg-emerald-900/10">
        <CardContent className="py-3 px-4">
          <p className="text-xs text-foreground">
            Reviewer scoring impact: <strong>Confirm</strong> keeps finding penalty, <strong>False Positive</strong> removes penalty,
            <strong> Modify</strong> recalculates penalty based on updated severity. Model score stays fixed; review-adjusted score updates.
          </p>
        </CardContent>
      </Card>

      {/* Executive summary */}
      <ExecutiveSummary report={report as Parameters<typeof ExecutiveSummary>[0]["report"]} />

      {/* HITL Review Summary */}
      {hitlCounts.needsReview > 0 && (
        <Card className="border-warning/30 bg-warning/[0.03]">
          <CardContent className="py-4 px-5">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div className="flex items-center gap-3">
                <Eye className="size-5 text-warning" />
                <div>
                  <p className="text-sm font-medium">Human Validation Queue</p>
                  <p className="text-xs text-muted-foreground">
                    {hitlCounts.needsReview} finding{hitlCounts.needsReview !== 1 ? "s" : ""} currently require reviewer confirmation
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs text-muted-foreground">
                {hitlCounts.autoApproved > 0 && (
                  <span className="flex items-center gap-1">
                    <ShieldCheckIcon className="size-3 text-success" /> {hitlCounts.autoApproved} auto
                  </span>
                )}
                {hitlCounts.userApproved > 0 && (
                  <span className="flex items-center gap-1">
                    <ThumbsUp className="size-3 text-success" /> {hitlCounts.userApproved} approved
                  </span>
                )}
                {hitlCounts.userRejected > 0 && (
                  <span className="flex items-center gap-1">
                    <ThumbsDown className="size-3 text-destructive" /> {hitlCounts.userRejected} rejected
                  </span>
                )}
                {hitlCounts.userModified > 0 && (
                  <span className="flex items-center gap-1">
                    <Pencil className="size-3 text-blue-500" /> {hitlCounts.userModified} modified
                  </span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Segmentation & Auto-Discovered Rules */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SegmentationEditor docId={docId} />
        <DiscoveredRulesPanel docId={docId} />
      </div>

      {/* Agent tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="flex flex-wrap h-auto gap-1 bg-transparent p-0 mb-4">
          <TabsTrigger value="all" className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground text-xs">
            All Findings
          </TabsTrigger>
          {agentReports.map((ar) => {
            const agent = ar.agent as string;
            return (
              <TabsTrigger
                key={agent}
                value={agent}
                className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground text-xs"
              >
                {AGENT_DISPLAY_NAMES[agent] || agent}
                <Badge variant="secondary" className="ml-1.5 text-[10px] px-1.5">
                  {String(ar.total_findings)}
                </Badge>
              </TabsTrigger>
            );
          })}
          {skippedAgents.map((s) => (
            <TooltipProvider key={s.category}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="inline-flex items-center gap-1 px-3 py-1.5 text-xs text-muted-foreground/50 cursor-not-allowed">
                    <SkipForward className="size-3" />
                    {AGENT_DISPLAY_NAMES[s.category] || s.category}
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <p className="text-xs">{s.reason}</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ))}
        </TabsList>

        <TabsContent value="all">
          <Card className="border-muted">
            <CardContent className="py-3 px-4 flex flex-wrap items-center gap-2">
              <FileText className="size-4 text-primary" />
              <span className="text-sm font-medium">Portfolio View</span>
              <Badge variant="secondary" className="text-[10px]">
                {agentReports.length} agents
              </Badge>
              <Badge variant="secondary" className="text-[10px]">
                {findings.length} findings
              </Badge>
              <span className="text-xs text-muted-foreground">
                Use tabs to move to agent-specific detail and targeted exports.
              </span>
            </CardContent>
          </Card>
          <FindingsTable
            findings={findings}
            docId={docId}
            onFindingUpdate={handleFindingUpdate}
            highlightId={highlightFinding || undefined}
            initialHitlFilter={viewHitlFilter}
            initialSeverityFilter={viewSeverityFilter}
          />
        </TabsContent>

        {agentReports.map((ar) => {
          const agent = ar.agent as string;
          const agentFindings = findings.filter((f) => f.agent === agent);
          const allEvals = (ar.all_evaluations || []) as RuleResult[];
          const pagesReviewed = Array.isArray(ar.pages_reviewed)
            ? (ar.pages_reviewed as unknown[]).length
            : Number(ar.pages_reviewed || 0);
          const agentLabel = (ar.agent_display as string) || AGENT_DISPLAY_NAMES[agent] || agent;
          return (
            <TabsContent key={agent} value={agent} className="space-y-4">
              <Card className="border-primary/20">
                <CardContent className="py-3 px-4 flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{agentLabel} Report</span>
                  <Badge variant="secondary" className="text-[10px]">
                    Model {String((ar.model_score as number | undefined) ?? ar.score)}/100
                  </Badge>
                  {typeof ar.review_adjusted_score === "number" && (
                    <Badge variant="outline" className="text-[10px] border-emerald-300 text-emerald-700 dark:text-emerald-400">
                      Review {String(ar.review_adjusted_score)}/100
                    </Badge>
                  )}
                  <Badge variant="secondary" className="text-[10px]">
                    {agentFindings.length} findings
                  </Badge>
                  <Badge variant="secondary" className="text-[10px]">
                    {allEvals.length} rule evaluations
                  </Badge>
                  {pagesReviewed > 0 && (
                    <Badge variant="outline" className="text-[10px]">
                      {pagesReviewed} pages reviewed
                    </Badge>
                  )}
                  <div className="ml-auto flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs"
                      onClick={() => handleExport("html", "agent")}
                    >
                      Export {agentLabel} (HTML)
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 text-xs"
                      onClick={() => handleExport("md", "agent")}
                    >
                      Export {agentLabel} (MD)
                    </Button>
                  </div>
                </CardContent>
              </Card>
              <AgentScorecard
                report={ar as Parameters<typeof AgentScorecard>[0]["report"]}
                onFindingClick={(fid) => setHighlightFinding(fid)}
              />
              <FindingsTable
                findings={agentFindings}
                docId={docId}
                onFindingUpdate={handleFindingUpdate}
                highlightId={highlightFinding || undefined}
                initialHitlFilter={viewHitlFilter}
                initialAgentFilter={agent}
                initialSeverityFilter={viewSeverityFilter}
              />
              <RuleEvaluationsList evaluations={allEvals} />
            </TabsContent>
          );
        })}
      </Tabs>

      {/* Skipped agents */}
      {skippedAgents.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <SkipForward className="size-4 text-muted-foreground" /> Skipped Agents
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0 space-y-2">
            {skippedAgents.map((s) => (
              <div key={s.category} className="flex items-start gap-2 text-sm">
                <Badge variant="outline" className="text-xs">
                  {AGENT_DISPLAY_NAMES[s.category] || s.category}
                </Badge>
                <span className="text-muted-foreground text-xs">{s.reason}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Audit trail footer */}
      {trail && (
        <Card className="bg-muted/30">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs text-muted-foreground flex items-center gap-2">
              <Info className="size-3" /> Audit Trail
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
              <span>Duration: {String(trail.duration_seconds)}s</span>
              <span>LLM calls: {String(trail.total_llm_calls)}</span>
              <span>Rules: {String(trail.total_rules_evaluated)}</span>
              <span>Batch size: {String(trail.rule_batch_size)}</span>
              <span>Evaluator: {String(trail.evaluator_model)}</span>
              <span>Orchestrator: {String(trail.orchestrator_model)}</span>
            </div>
            {methodology && (
              <div className="mt-1.5 space-y-1">
                <p className="text-[11px] text-muted-foreground/70">
                  Model score formula: {String(methodology.formula)}
                </p>
                {methodology.review_adjusted_formula && (
                  <p className="text-[11px] text-muted-foreground/70">
                    Review-adjusted formula: {String(methodology.review_adjusted_formula)}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </motion.div>
  );
}
