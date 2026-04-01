"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  FileText,
  AlertTriangle,
  BarChart3,
  ArrowRight,
  Loader2,
  PenLine,
  ShieldCheck,
  Sparkles,
  AlertCircle,
  X,
  ChevronDown,
  Trash2,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useDocumentStore } from "@/stores/document-store";
import { ProcessingDashboard } from "@/components/upload/processing-dashboard";
import { DocumentUpload } from "@/components/upload/document-upload";
import { listDocuments, getDocument, deleteDocument } from "@/lib/api";
import { useValidateRestoredDoc } from "@/hooks/useWebSocket";
import { useLocalStorage } from "@/hooks/useLocalStorage";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useHydrated } from "@/hooks/useHydrated";

interface DocSummary {
  doc_id: string;
  filename: string | null;
  status: string;
  total_pages: number;
}

interface ExpandedPageData {
  pageNum: number;
  status: "approved" | "flagged" | "pending";
  confidence: number;
}

const STATS_ICONS = [FileText, Loader2, AlertTriangle, BarChart3];
const STATS_COLORS = [
  "text-primary",
  "text-info",
  "text-warning",
  "text-success",
];

export default function Home() {
  const hydrated = useHydrated();
  const { docId, processingStatus } = useDocumentStore();
  useValidateRestoredDoc();
  const [documents, setDocuments] = useState<DocSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [welcomeDismissed, setWelcomeDismissed] = useLocalStorage("welcome-dismissed", false);
  const [expandedDoc, setExpandedDoc] = useState<string | null>(null);
  const [expandedPages, setExpandedPages] = useState<ExpandedPageData[]>([]);
  const [isExpandPending, startExpandTransition] = useTransition();

  useEffect(() => {
    listDocuments()
      .then((data) => {
        if (!Array.isArray(data.documents)) {
          throw new Error("Invalid response from server");
        }
        setDocuments(data.documents);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to load documents";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (processingStatus === "completed" || processingStatus === "error") {
      listDocuments()
        .then((data) => {
          if (!Array.isArray(data.documents)) return;
          setDocuments(data.documents);
        })
        .catch(() => {});
    }
  }, [processingStatus]);

  const handleRetry = () => {
    setError(null);
    setLoading(true);
    listDocuments()
      .then((data) => {
        if (!Array.isArray(data.documents)) {
          throw new Error("Invalid response from server");
        }
        setDocuments(data.documents);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to load documents";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  };

  const handleExpand = (targetDocId: string) => {
    if (expandedDoc === targetDocId) {
      setExpandedDoc(null);
      setExpandedPages([]);
      return;
    }
    setExpandedDoc(targetDocId);
    setExpandedPages([]);
    startExpandTransition(async () => {
      try {
        const data = await getDocument(targetDocId);
        const extractions = data.results?.extractions || [];
        const decisions = data.results?.hitl_decisions || [];
        const pages: ExpandedPageData[] = extractions.map(
          (ext: Record<string, unknown>, i: number) => {
            const decision = decisions[i] as Record<string, unknown> | undefined;
            const dStatus = decision?.status;
            return {
              pageNum: i + 1,
              status:
                dStatus === "approved" || dStatus === "edited"
                  ? "approved"
                  : dStatus === "flagged"
                  ? "flagged"
                  : "pending",
              confidence:
                typeof ext?.confidence === "number"
                  ? ext.confidence
                  : typeof ext?.quality_score === "number"
                  ? (ext.quality_score as number)
                  : 0,
            };
          },
        );
        setExpandedPages(pages);
      } catch {
        setExpandedPages([]);
      }
    });
  };

  const handleDelete = async (targetDocId: string) => {
    try {
      await deleteDocument(targetDocId);
      setDocuments((prev) => prev.filter((d) => d.doc_id !== targetDocId));
      if (expandedDoc === targetDocId) setExpandedDoc(null);
      if (useDocumentStore.getState().docId === targetDocId) {
        useDocumentStore.getState().reset();
      }
      toast.success("Document deleted");
    } catch {
      toast.error("Failed to delete document");
    }
  };

  const showProcessing = docId && processingStatus !== "idle";

  const isProcessingNow = !!(docId && processingStatus !== "idle" && processingStatus !== "completed" && processingStatus !== "error");
  const activeDocInList = documents.some((d) => d.doc_id === docId);

  const stats = [
    { label: "Total Documents", value: documents.length + (isProcessingNow && !activeDocInList ? 1 : 0) },
    {
      label: "Processing",
      value:
        documents.filter((d) => (d.status === "uploaded" || d.status === "processing") && d.doc_id !== docId).length
        + (isProcessingNow ? 1 : 0),
    },
    { label: "Needs Review", value: documents.filter((d) => d.status === "needs_review" || d.status === "processed").length },
    { label: "Completed", value: documents.filter((d) => d.status === "completed").length },
  ];

  return (
    <div className="p-4 lg:p-6 space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Turn PDFs into review-ready, compliance-ready records with confidence.
        </p>
      </div>

      {/* Welcome banner */}
      {!welcomeDismissed && (
        <motion.div
          initial={hydrated ? { opacity: 0, y: 8 } : false}
          animate={{ opacity: 1, y: 0 }}
        >
          <Card className="bg-gradient-to-br from-primary/5 via-primary/3 to-transparent border-primary/10">
            <CardContent className="flex items-start gap-4 p-5">
              <div className="size-10 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                <Sparkles className="size-5 text-primary" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-semibold text-foreground mb-1">Welcome to AutoTranscript Workspace</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Upload a PDF to begin. The platform extracts, validates, and routes each page with full traceability:
                  Upload → Text Intelligence → Confidence Analysis → Human Validation → Compliance Review.
                </p>
                <div className="flex items-center gap-2 mt-3">
                  <Button size="sm" asChild>
                    <Link href="#upload">Start Upload</Link>
                  </Button>
                  <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => setWelcomeDismissed(true)}>
                    Dismiss
                  </Button>
                </div>
              </div>
              <button onClick={() => setWelcomeDismissed(true)} className="text-muted-foreground hover:text-foreground">
                <X className="size-4" />
              </button>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {/* Error banner */}
      {error && (
        <Card className="border-destructive/20 bg-destructive/5">
          <CardContent className="py-3 px-4 flex items-center gap-3">
            <AlertCircle className="size-4 text-destructive flex-shrink-0" />
            <p className="text-sm text-destructive">{error}</p>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto text-xs"
              onClick={handleRetry}
            >
              Retry
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat, i) => {
          const Icon = STATS_ICONS[i];
          return (
            <motion.div
              key={stat.label}
              initial={hydrated ? { opacity: 0, y: 8 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
            >
              <Card>
                <CardContent className="p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-muted-foreground">{stat.label}</span>
                    <Icon className={`size-4 ${STATS_COLORS[i]}`} />
                  </div>
                  {loading ? (
                    <Skeleton className="h-7 w-12" />
                  ) : (
                    <p className="text-2xl font-semibold text-foreground">{stat.value}</p>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          );
        })}
      </div>

      {/* Upload + Processing */}
      <div id="upload">
        {showProcessing ? (
          <ProcessingDashboard />
        ) : (
          <DocumentUpload />
        )}
      </div>

      {/* Recent documents */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-foreground">Recent Documents</h2>
          <Button variant="ghost" size="sm" asChild className="text-muted-foreground">
            <Link href="/documents">
              View all <ArrowRight className="size-3.5 ml-1" />
            </Link>
          </Button>
        </div>
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="p-4 space-y-3">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="flex items-center gap-3">
                    <Skeleton className="size-9 rounded-lg" />
                    <div className="flex-1">
                      <Skeleton className="h-4 w-40 mb-1" />
                      <Skeleton className="h-3 w-24" />
                    </div>
                  </div>
                ))}
              </div>
            ) : documents.length === 0 ? (
              <div className="p-8 text-center">
                <FileText className="size-8 text-muted-foreground/40 mx-auto mb-2" />
                <p className="text-sm text-muted-foreground">No documents yet. Upload one to get started.</p>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {documents.slice(0, 5).map((doc) => {
                  const isExpanded = expandedDoc === doc.doc_id;
                  return (
                    <div key={doc.doc_id}>
                      <div
                        className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50 transition-colors cursor-pointer"
                        onClick={() => handleExpand(doc.doc_id)}
                      >
                        <div className="size-9 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
                          <FileText className="size-4 text-muted-foreground" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">{doc.filename || "Untitled"}</p>
                          <div className="flex items-center gap-2 mt-0.5">
                            <span className="text-[10px] text-muted-foreground font-mono">{doc.doc_id.slice(0, 8)}</span>
                            {doc.total_pages > 0 && (
                              <>
                                <span className="text-border text-[10px]">·</span>
                                <span className="text-[10px] text-muted-foreground">{doc.total_pages} pages</span>
                              </>
                            )}
                          </div>
                        </div>
                        <Badge variant="outline" className="text-[10px] capitalize hidden sm:inline-flex">
                          {doc.status.replace(/_/g, " ")}
                        </Badge>
                        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <Button variant="outline" size="sm" className="h-7 text-[11px] px-2.5 gap-1" asChild>
                            <Link href={`/review?doc=${doc.doc_id}`}>
                              <PenLine className="size-3" />
                              <span className="hidden sm:inline">Review</span>
                            </Link>
                          </Button>
                          <Button variant="ghost" size="sm" className="h-7 text-[11px] px-2.5 gap-1 text-muted-foreground" asChild>
                            <Link href={`/compliance?doc=${doc.doc_id}`}>
                              <ShieldCheck className="size-3" />
                              <span className="hidden sm:inline">Compliance</span>
                            </Link>
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-muted-foreground hover:text-destructive"
                            onClick={() => handleDelete(doc.doc_id)}
                          >
                            <Trash2 className="size-3" />
                          </Button>
                        </div>
                        <ChevronDown
                          className={cn(
                            "size-4 text-muted-foreground transition-transform duration-200",
                            isExpanded && "rotate-180",
                          )}
                        />
                      </div>

                      {/* Expandable page view */}
                      <AnimatePresence>
                        {isExpanded && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2 }}
                            className="overflow-hidden"
                          >
                            <div className="px-4 pb-3 pt-1 ml-12">
                              {isExpandPending ? (
                                <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                                  <Loader2 className="size-3 animate-spin" />
                                  Loading pages...
                                </div>
                              ) : expandedPages.length === 0 ? (
                                <p className="text-xs text-muted-foreground py-2">No page data available</p>
                              ) : (
                                <div className="space-y-2">
                                  <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                                    <span>
                                      <span className="font-medium text-success">
                                        {expandedPages.filter((p) => p.status === "approved").length}
                                      </span>{" "}
                                      approved
                                    </span>
                                    {expandedPages.filter((p) => p.status === "flagged").length > 0 && (
                                      <span>
                                        <span className="font-medium text-warning">
                                          {expandedPages.filter((p) => p.status === "flagged").length}
                                        </span>{" "}
                                        flagged
                                      </span>
                                    )}
                                    {expandedPages.filter((p) => p.status === "pending").length > 0 && (
                                      <span>
                                        <span className="font-medium text-foreground">
                                          {expandedPages.filter((p) => p.status === "pending").length}
                                        </span>{" "}
                                        pending
                                      </span>
                                    )}
                                  </div>
                                  <div className="flex flex-wrap gap-1.5">
                                    {expandedPages.map((page) => (
                                      <Link
                                        key={page.pageNum}
                                        href={`/review?doc=${doc.doc_id}&page=${page.pageNum}`}
                                        title={`Page ${page.pageNum}: ${page.status}${page.confidence > 0 ? ` (${Math.round(page.confidence * 100)}%)` : ""}`}
                                        className={cn(
                                          "size-7 rounded-md text-[10px] font-medium flex items-center justify-center transition-all duration-150 hover:scale-105 hover:shadow-md active:scale-95",
                                          page.status === "approved"
                                            ? "bg-success/10 text-success border border-success/20"
                                            : page.status === "flagged"
                                            ? "bg-warning/10 text-warning border border-warning/20"
                                            : "bg-muted text-muted-foreground border border-border",
                                        )}
                                      >
                                        {page.pageNum}
                                      </Link>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
