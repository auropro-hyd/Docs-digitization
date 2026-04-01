"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmationDialog } from "@/components/common/confirmation-dialog";
import { DocumentContextBar } from "@/components/common/document-context-bar";
import { EmptyState } from "@/components/common/empty-state";
import { ComplianceProgress } from "@/components/compliance/compliance-progress";
import { ComplianceReportView } from "@/components/compliance/compliance-report";
import { RuleManagerSheet } from "@/components/compliance/rule-manager-sheet";
import { NewAgentDialog } from "@/components/compliance/new-agent-dialog";
import { useComplianceStore } from "@/stores/compliance-store";
import { DocumentWebSocket, type WSMessage } from "@/lib/websocket";
import {
  cancelComplianceRun,
  getAgentsWithMeta,
  getComplianceReport,
  getComplianceStatus,
  getDocument,
  runComplianceReview,
} from "@/lib/api";
import type { AgentMeta } from "@/types/compliance";
import { toast } from "sonner";
import {
  ShieldCheck,
  FileText,
  Loader2,
  Play,
  AlertCircle,
  Check,
  ListTree,
  Plus,
} from "lucide-react";

type PageState = "loading" | "pre-run" | "running" | "summary" | "report" | "error";
type ReportFocus = {
  tab?: "all" | string;
  hitlFilter?: "all" | "needs_review" | "reviewed" | "auto";
  severityFilter?: "all" | "critical" | "major" | "minor" | "observation";
};

function ComplianceContent() {
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc");

  const [pageState, setPageState] = useState<PageState>("loading");
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [reportReady, setReportReady] = useState(false);
  const [reportFocus, setReportFocus] = useState<ReportFocus>({ tab: "all", hitlFilter: "all", severityFilter: "all" });
  const [errorMsg, setErrorMsg] = useState("");
  const [filename, setFilename] = useState<string | null>(null);

  const [agents, setAgents] = useState<AgentMeta[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [selectedAgents, setSelectedAgents] = useState<Set<string>>(new Set());

  const [sheetAgent, setSheetAgent] = useState<string | null>(null);
  const [showNewAgent, setShowNewAgent] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  const { phase, startRun, handleProgress, hydrateFromReport, reset } = useComplianceStore();
  const wsRef = useRef<DocumentWebSocket | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      setAgentsLoading(true);
      const data: AgentMeta[] = await getAgentsWithMeta();
      setAgents(data);
      setSelectedAgents((prev) => {
        if (prev.size === 0) return new Set(data.map((a) => a.id));
        const valid = new Set(data.map((a) => a.id));
        const next = new Set([...prev].filter((id) => valid.has(id)));
        return next.size > 0 ? next : new Set(data.map((a) => a.id));
      });
    } catch {
      toast.error("Failed to load compliance agents");
    } finally {
      setAgentsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  useEffect(() => {
    if (!docId) return;
    getDocument(docId)
      .then((doc) => setFilename(doc.filename ?? null))
      .catch(() => setFilename(null));
  }, [docId]);

  useEffect(() => {
    if (!docId) {
      setPageState("pre-run");
      return;
    }

    let cancelled = false;

    async function load() {
      try {
        const status = await getComplianceStatus(docId!);
        if (cancelled) return;

        if (status.status === "complete") {
          const data = await getComplianceReport(docId!);
          if (!cancelled) {
            setReport(data);
            setReportReady(true);
            hydrateFromReport(data as Record<string, unknown>);
            setFilename(((data as Record<string, unknown>).filename as string) ?? null);
            setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
            setPageState("summary");
          }
        } else if (status.status === "running") {
          startRun();
          setPageState("running");
          connectWS();
        } else {
          setPageState("pre-run");
        }
      } catch {
        if (!cancelled) {
          try {
            const data = await getComplianceReport(docId!);
            if (!cancelled) {
              setReport(data);
              setReportReady(true);
              hydrateFromReport(data as Record<string, unknown>);
              setFilename(((data as Record<string, unknown>).filename as string) ?? null);
              setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
              setPageState("summary");
            }
          } catch {
            if (!cancelled) setPageState("pre-run");
          }
        }
      }
    }

    load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const connectWS = useCallback(() => {
    if (!docId || wsRef.current) return;

    const ws = new DocumentWebSocket(docId);
    wsRef.current = ws;

    ws.subscribe((msg: WSMessage) => {
      if (msg.type === "compliance_progress") {
        handleProgress(msg as Record<string, unknown>);
      }
    });

    ws.connect();
  }, [docId, handleProgress]);

  useEffect(() => {
    if (phase === "complete" && docId) {
      const timer = setTimeout(async () => {
        try {
          const data = await getComplianceReport(docId);
          setReport(data);
          setReportReady(true);
          hydrateFromReport(data as Record<string, unknown>);
          setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
          setPageState("summary");
          toast.success("Compliance audit complete. Review and export when ready.");
        } catch {
          setErrorMsg("Audit completed but failed to load report");
          setPageState("error");
        } finally {
          wsRef.current?.disconnect();
          wsRef.current = null;
        }
      }, 500);
      return () => clearTimeout(timer);
    }
    if (phase === "error") {
      setErrorMsg("Compliance audit failed. Please try again.");
      setPageState("error");
      wsRef.current?.disconnect();
      wsRef.current = null;
    }
  }, [phase, docId]);

  // Periodically re-check status while in "running" state to detect stale/completed runs
  useEffect(() => {
    if (pageState !== "running" || !docId) return;
    const interval = setInterval(async () => {
      try {
        const status = await getComplianceStatus(docId);
        if (status.status === "complete") {
          const data = await getComplianceReport(docId);
          setReport(data);
          setReportReady(true);
          hydrateFromReport(data as Record<string, unknown>);
          setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
          setPageState("summary");
          wsRef.current?.disconnect();
          wsRef.current = null;
          toast.success("Compliance audit complete. Open report when ready.");
        } else if (status.status === "idle") {
          wsRef.current?.disconnect();
          wsRef.current = null;
          reset();
          setPageState("pre-run");
          toast.info("Previous compliance run ended. You can start a new one.");
        }
      } catch {
        // ignore transient errors
      }
    }, 30_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageState, docId]);

  useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
      wsRef.current = null;
      reset();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleAgent = useCallback((agentId: string) => {
    setSelectedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) {
        if (next.size > 1) next.delete(agentId);
      } else {
        next.add(agentId);
      }
      return next;
    });
  }, []);

  const handleStartRun = async () => {
    if (!docId) return;
    if (selectedAgents.size === 0) {
      toast.error("Select at least one compliance agent");
      return;
    }
    try {
      startRun();
      setReportReady(false);
      setReport(null);
      setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
      setPageState("running");
      connectWS();
      await runComplianceReview(docId, Array.from(selectedAgents));
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to start compliance review";
      toast.error(msg);
      setErrorMsg(msg);
      setPageState("error");
    }
  };

  const handleReRun = () => {
    reset();
    setReport(null);
    setReportReady(false);
    setReportFocus({ tab: "all", hitlFilter: "all", severityFilter: "all" });
    setPageState("pre-run");
  };

  const handleCancelRun = async () => {
    if (!docId) return;
    try {
      await cancelComplianceRun(docId);
      wsRef.current?.disconnect();
      wsRef.current = null;
      reset();
      setReportReady(false);
      setPageState("pre-run");
      setShowCancelConfirm(false);
      toast.success("Compliance run cancelled");
    } catch {
      toast.error("Failed to cancel run");
    }
  };

  if (!docId) {
    return (
      <div className="p-4 lg:p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Compliance Review</h1>
          <p className="text-sm text-muted-foreground mt-1">
            ALCOA+, GMP, Checklist, and SOP compliance analysis
          </p>
        </div>
        <EmptyState
          icon={<ShieldCheck className="size-7" />}
          title="No document selected"
          description="Select a document to run compliance review"
          action={
            <Button size="sm" asChild>
              <Link href="/documents">
                <FileText className="size-4 mr-2" /> Browse Documents
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <DocumentContextBar docId={docId} filename={filename} currentPage="compliance" />
      <div className="p-4 lg:p-6 space-y-6">
      {pageState === "loading" && (
        <div className="space-y-6">
          <Skeleton className="h-7 w-48 mb-2" />
          <Skeleton className="h-4 w-64" />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Skeleton className="h-64 rounded-xl" />
            <Skeleton className="h-64 rounded-xl lg:col-span-2" />
          </div>
        </div>
      )}

      {pageState === "pre-run" && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-6"
        >
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              Compliance Review{filename ? ` — ${filename}` : ""}
            </h1>
            <p className="text-sm text-muted-foreground mt-1">
              Select compliance standards and manage rules, then start the audit
            </p>
          </div>

          <Card>
            <CardContent className="p-8 flex flex-col items-center text-center gap-5">
              <div className="size-16 rounded-2xl bg-primary/10 flex items-center justify-center">
                <ShieldCheck className="size-8 text-primary" />
              </div>
              <div>
                <h2 className="text-lg font-semibold">Run Compliance Audit</h2>
                <p className="text-sm text-muted-foreground mt-1 max-w-md">
                  Select which compliance standards to evaluate, then start the audit.
                </p>
              </div>

              {agentsLoading ? (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 w-full max-w-2xl">
                  {[1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} className="h-36 rounded-xl" />
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 w-full max-w-2xl">
                  {agents.map((agent) => {
                    const active = selectedAgents.has(agent.id);
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        onClick={() => toggleAgent(agent.id)}
                        className={`
                          relative flex flex-col items-center gap-1.5 rounded-xl border-2 px-3 py-4
                          transition-all duration-150 cursor-pointer text-center
                          ${active
                            ? "border-primary bg-primary/5 shadow-sm"
                            : "border-muted bg-muted/30 opacity-60 hover:opacity-80 hover:border-muted-foreground/30"
                          }
                        `}
                      >
                        {/* View rules button */}
                        <span
                          role="button"
                          tabIndex={0}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSheetAgent(agent.id);
                          }}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.stopPropagation(); setSheetAgent(agent.id); } }}
                          className="absolute top-2 left-2 size-6 rounded-md bg-muted/80 hover:bg-muted flex items-center justify-center transition-colors"
                          title="View & manage rules"
                        >
                          <ListTree className="size-3.5 text-muted-foreground" />
                        </span>

                        {active && (
                          <span className="absolute top-2 right-2 size-5 rounded-full bg-primary flex items-center justify-center">
                            <Check className="size-3 text-primary-foreground" />
                          </span>
                        )}

                        <span className={`text-sm font-semibold mt-2 ${active ? "text-foreground" : "text-muted-foreground"}`}>
                          {agent.label}
                        </span>
                        <span className="text-[11px] leading-tight text-muted-foreground line-clamp-2">
                          {agent.description}
                        </span>

                        <div className="flex items-center gap-1.5 mt-auto pt-1.5">
                          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
                            {agent.rule_count} rules
                          </Badge>
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                            {agent.category_count} cat.
                          </Badge>
                        </div>
                      </button>
                    );
                  })}

                  {/* New agent card */}
                  <button
                    type="button"
                    onClick={() => setShowNewAgent(true)}
                    className="flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-muted px-3 py-4 text-muted-foreground hover:border-primary/40 hover:text-primary transition-colors cursor-pointer"
                  >
                    <Plus className="size-5" />
                    <span className="text-xs font-medium">New Agent</span>
                  </button>
                </div>
              )}

              <div className="flex items-center gap-3">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-xs text-muted-foreground"
                  onClick={() => setSelectedAgents(new Set(agents.map((a) => a.id)))}
                >
                  Select All
                </Button>
                <Button size="lg" onClick={handleStartRun} disabled={selectedAgents.size === 0}>
                  <Play className="size-4 mr-2" />
                  Start Audit ({selectedAgents.size} agent{selectedAgents.size !== 1 ? "s" : ""})
                </Button>
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {(pageState === "running" || pageState === "summary") && (
          <ComplianceProgress
          docId={docId}
          filename={filename}
          reportReady={reportReady}
          onViewReport={(focus) => {
            setReportFocus(focus || { tab: "all", hitlFilter: "all", severityFilter: "all" });
            setPageState("report");
          }}
          onCancel={pageState === "running" ? () => setShowCancelConfirm(true) : undefined}
        />
      )}

      <ConfirmationDialog
        open={showCancelConfirm}
        onOpenChange={setShowCancelConfirm}
        title="Cancel compliance audit?"
        description="This will stop the current audit. Progress will be lost and you will need to start over."
        confirmLabel="Cancel audit"
        cancelLabel="Keep running"
        variant="destructive"
        onConfirm={handleCancelRun}
      />

      {pageState === "report" && report && (
        <ComplianceReportView
          report={report}
          docId={docId}
          onReRun={handleReRun}
          initialFocus={reportFocus}
        />
      )}

      {pageState === "error" && (
        <Card className="border-destructive/20 bg-destructive/5">
          <CardContent className="py-6 px-5 flex items-start gap-3">
            <AlertCircle className="size-5 text-destructive flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-destructive">Compliance Audit Failed</p>
              <p className="text-xs text-destructive/80 mt-0.5">{errorMsg}</p>
              <Button
                variant="outline"
                size="sm"
                className="mt-3 text-xs"
                onClick={handleReRun}
              >
                <Loader2 className="size-3 mr-1" /> Retry
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Rule Manager Sheet */}
      <RuleManagerSheet
        agentId={sheetAgent}
        open={sheetAgent !== null}
        onOpenChange={(open) => { if (!open) { setSheetAgent(null); fetchAgents(); } }}
      />

      {/* New Agent Dialog */}
      <NewAgentDialog
        open={showNewAgent}
        onOpenChange={(open) => { if (!open) { setShowNewAgent(false); fetchAgents(); } }}
        onCreated={(agentId) => {
          setShowNewAgent(false);
          fetchAgents();
          setSheetAgent(agentId);
        }}
      />
      </div>
    </div>
  );
}

export default function CompliancePage() {
  return (
    <Suspense
      fallback={
        <div className="p-4 lg:p-6 space-y-6">
          <div>
            <Skeleton className="h-7 w-48 mb-2" />
            <Skeleton className="h-4 w-64" />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Skeleton className="h-64 rounded-xl" />
            <Skeleton className="h-64 rounded-xl lg:col-span-2" />
          </div>
          <Skeleton className="h-96 rounded-xl" />
        </div>
      }
    >
      <ComplianceContent />
    </Suspense>
  );
}
