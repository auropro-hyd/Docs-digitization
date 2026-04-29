"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useDocumentStore, type PageData } from "@/stores/document-store";
import { useDocumentWebSocket, useProcessingPollFallback } from "@/hooks/useWebSocket";
import { StatusIndicator } from "@/components/common/status-indicator";
import { PageDetailModal } from "@/components/upload/page-detail-modal";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  FileText,
  CheckCircle,
  AlertTriangle,
  RotateCcw,
  PenLine,
  ShieldCheck,
  Clock,
  ArrowRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { displayProcessingStatus, pipelineProgressFromStatus } from "@/lib/processing-labels";

const STAGE_ORDER = [
  { key: "upload", label: "Upload" },
  { key: "intake", label: "Intake" },
  { key: "extract", label: "Intake + OCR" },
  { key: "validate", label: "Validate" },
  { key: "finalize", label: "Finalize" },
] as const;

function useElapsedTime(active: boolean) {
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef(0);

  useEffect(() => {
    if (!active) return;
    startRef.current = Date.now();
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [active]);

  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
}

export function ProcessingDashboard() {
  const router = useRouter();
  const { docId, filename, totalPages, processingStatus, ocrProgress, ocrProgressLabel, timeline, pages, error } =
    useDocumentStore();
  useDocumentWebSocket(docId);
  useProcessingPollFallback(docId);
  const [selectedPage, setSelectedPage] = useState<PageData | null>(null);

  const isComplete = processingStatus === "completed";
  const isError = processingStatus === "error";
  const isActive = !isComplete && !isError;

  const elapsedStr = useElapsedTime(isActive);

  const isOcrPhase = processingStatus === "azure_di_running" || processingStatus === "marker_ocr_running";

  // Smooth the OCR phase so progress feels continuous between server ticks.
  // All hooks must run before any conditional early return so React's
  // hook ordering stays stable across renders — the early return sits
  // below this block. The hooks themselves no-op when not in an OCR
  // phase, so their cost is bounded.
  const [smoothedOcrProgress, setSmoothedOcrProgress] = useState(0);
  useEffect(() => {
    if (!isOcrPhase) {
      setSmoothedOcrProgress(0);
      return;
    }
    setSmoothedOcrProgress((prev) => Math.max(prev, ocrProgress));
  }, [isOcrPhase, ocrProgress]);

  useEffect(() => {
    if (!isOcrPhase || isComplete || isError) return;
    const timer = setInterval(() => {
      setSmoothedOcrProgress((prev) => {
        // Keep gently advancing until close to completion, but never fake 100%.
        const floor = Math.max(ocrProgress, 6);
        const nudged = prev < floor ? floor : prev + 0.35;
        return Math.min(97, nudged);
      });
    }, 700);
    return () => clearInterval(timer);
  }, [isOcrPhase, isComplete, isError, ocrProgress]);

  const liveOcrProgress = useMemo(
    () => (isOcrPhase ? Math.max(ocrProgress, smoothedOcrProgress) : 0),
    [isOcrPhase, ocrProgress, smoothedOcrProgress],
  );

  if (!docId) return null;

  const pageArray = Array.from(pages.values());
  const approvedCount = pageArray.filter((p) => p.status === "approved").length;
  const reviewCount = pageArray.filter(
    (p) => p.status === "reviewing" || p.status === "flagged",
  ).length;
  const displayPages = totalPages || pageArray.length;
  const hasRealProgress = isOcrPhase && ocrProgress > 0;

  const statusLabel = hasRealProgress && ocrProgressLabel
    ? ocrProgressLabel
    : displayProcessingStatus(processingStatus);

  const pipelinePercent = hasRealProgress
    ? Math.round(20 + (liveOcrProgress / 100) * 50)
    : pipelineProgressFromStatus(processingStatus);

  const stageIndexByStatus: Record<string, number> = {
    uploading: 0,
    ingested: 1,
    azure_di_running: 2,
    marker_ocr_running: 2,
    quality_scoring: 3,
    merging_results: 4,
    auto_approved: 4,
    hitl_required: 4,
    reviewed: 4,
    completed: 4,
  };
  const currentStage = stageIndexByStatus[processingStatus] ?? 0;

  return (
    <div className="space-y-4">
      <Card
        className={cn(
          "transition-all duration-500 overflow-hidden",
          isComplete && "border-success/30",
          isError && "border-destructive/30",
        )}
      >
        <CardContent className="p-0">
          {/* Document info + status */}
          <div
            className={cn(
              "px-5 pt-4 pb-3 transition-colors duration-300",
              isComplete && "bg-success/5",
              isError && "bg-destructive/5",
            )}
          >
            <div className="flex items-center gap-3">
              <div
                className={cn(
                  "size-10 rounded-lg flex items-center justify-center flex-shrink-0 transition-colors duration-300",
                  isComplete
                    ? "bg-success/10"
                    : isError
                    ? "bg-destructive/10"
                    : "bg-muted",
                )}
              >
                {isComplete ? (
                  <CheckCircle className="size-5 text-success" />
                ) : isError ? (
                  <AlertTriangle className="size-5 text-destructive" />
                ) : (
                  <FileText className="size-5 text-muted-foreground" />
                )}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">
                  {filename}
                </p>
                <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
                  {displayPages > 0 && (
                    <span>
                      {displayPages} page{displayPages !== 1 ? "s" : ""}
                    </span>
                  )}
                  {isActive && (
                    <>
                      {displayPages > 0 && (
                        <span className="text-border">·</span>
                      )}
                      <span className="flex items-center gap-1">
                        <Clock className="size-3" />
                        {elapsedStr}
                      </span>
                    </>
                  )}
                  {isComplete && elapsedStr !== "0s" && (
                    <>
                      <span className="text-border">·</span>
                      <span className="text-success">{elapsedStr}</span>
                    </>
                  )}
                </div>
              </div>
              <StatusIndicator status={processingStatus} />
            </div>
          </div>

          {/* Progress bar (active state only) */}
          {isActive && (
            <div className="px-5 pb-3 space-y-3">
              <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1.5">
                <span className="flex items-center gap-1.5">
                  <span className="relative flex size-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                    <span className="relative inline-flex size-1.5 rounded-full bg-primary" />
                  </span>
                  {statusLabel}
                </span>
                <span>{pipelinePercent}%</span>
              </div>
              <Progress value={pipelinePercent} className="h-1.5" />

              {isOcrPhase && (
                <div className="rounded-md border border-primary/20 bg-primary/5 p-2">
                  <div className="flex items-center justify-between text-[11px] text-primary/90 mb-1">
                    <span>Intake progress</span>
                    <span className="tabular-nums">{Math.round(liveOcrProgress)}%</span>
                  </div>
                  <Progress value={liveOcrProgress} className="h-1.5" />
                </div>
              )}

              <div className="grid grid-cols-5 gap-1.5">
                {STAGE_ORDER.map((stage, idx) => (
                  <div
                    key={stage.key}
                    className={cn(
                      "rounded-md border px-2 py-1 text-[10px] text-center",
                      idx < currentStage
                        ? "border-success/30 bg-success/10 text-success"
                        : idx === currentStage
                        ? "border-primary/30 bg-primary/10 text-primary"
                        : "border-border bg-muted/40 text-muted-foreground",
                    )}
                  >
                    {stage.label}
                  </div>
                ))}
              </div>

              {timeline.length > 0 && (
                <div className="rounded-md border border-border bg-muted/20 p-2">
                  <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Live activity</p>
                  <div className="space-y-1">
                    {timeline.slice(-4).map((e) => (
                      <div key={e.id} className="text-[11px] text-muted-foreground flex items-center justify-between gap-2">
                        <span className="truncate">{e.text}</span>
                        <span className="tabular-nums text-[10px] text-muted-foreground/70">
                          {new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="mx-5 mb-3 p-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive flex items-center gap-2">
              <AlertTriangle className="size-4 flex-shrink-0" />
              {error}
            </div>
          )}

          {/* Actions bar */}
          {(isComplete || isError) && (
            <div className="px-5 pb-3 pt-1 flex flex-wrap items-center gap-2">
              {isComplete && (
                <>
                  <Button
                    size="sm"
                    onClick={() => router.push(`/review?doc=${docId}`)}
                  >
                    <PenLine className="size-3.5 mr-1.5" />
                    Open Review
                    <ArrowRight className="size-3.5 ml-1" />
                  </Button>
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/compliance?doc=${docId}`}>
                      <ShieldCheck className="size-3.5 mr-1.5" />
                      Open Compliance
                    </Link>
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground text-xs"
                    onClick={() => useDocumentStore.getState().reset()}
                  >
                    <RotateCcw className="size-3 mr-1" />
                    Start New File
                  </Button>
                </>
              )}
              {isError && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => useDocumentStore.getState().reset()}
                >
                  <RotateCcw className="size-3.5 mr-1.5" />
                  Restart
                </Button>
              )}
            </div>
          )}

          {/* Page status grid (inline in the same card) */}
          {pageArray.length > 0 && (
            <div className="border-t border-border px-5 py-3">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-4 text-xs">
                  <span className="text-muted-foreground uppercase tracking-wider font-medium text-[10px]">
                    Pages
                  </span>
                  <span className="text-muted-foreground">
                    <span className="font-medium text-success">
                      {approvedCount}
                    </span>{" "}
                    approved
                  </span>
                  {reviewCount > 0 && (
                    <span className="text-muted-foreground">
                      <span className="font-medium text-warning">
                        {reviewCount}
                      </span>{" "}
                      review
                    </span>
                  )}
                </div>
                <p className="text-[10px] text-muted-foreground">
                  Click for details
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {pageArray.map((page) => (
                  <button
                    key={page.pageNum}
                    onClick={() => setSelectedPage(page)}
                    title={`Page ${page.pageNum}: ${page.status} (${Math.round(page.confidence * 100)}%)`}
                    className={cn(
                      "size-8 rounded-md text-[10px] font-medium flex items-center justify-center transition-all duration-150 cursor-pointer hover:scale-105 hover:shadow-md active:scale-95",
                      page.status === "approved"
                        ? "bg-success/10 text-success border border-success/20"
                        : page.status === "reviewing" ||
                          page.status === "flagged"
                        ? "bg-warning/10 text-warning border border-warning/20"
                        : "bg-muted text-muted-foreground border border-border animate-pulse",
                    )}
                  >
                    {page.pageNum}
                  </button>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <PageDetailModal
        page={selectedPage}
        docId={docId}
        onClose={() => setSelectedPage(null)}
      />
    </div>
  );
}
