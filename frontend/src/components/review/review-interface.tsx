"use client";

import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { rehypeTableFix } from "@/lib/rehype-table-fix";
import Link from "next/link";
import {
  ArrowRight,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Check,
  Flag,
  GripVertical,
  FileText,
  Eye,
  Copy,
  PenTool,
  KeyRound,
  CheckCheck,
  Download,
  FileDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import { PdfViewer } from "@/components/common/pdf-viewer";
import { getDocumentPdfUrl, componentAction, bulkComponentAction, downloadExport, downloadExportAsPdf } from "@/lib/api";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { ReviewableComponent, type ComponentDecision } from "./reviewable-component";

interface SignatureInfo {
  status: string;
  confidence: number;
  label: string;
  component_id?: string;
  decision?: ComponentDecision;
}

interface KVPair {
  key: string;
  value: string;
  confidence: number;
  component_id?: string;
  decision?: ComponentDecision;
}

interface ReviewPage {
  pageNum: number;
  confidence: number;
  markdown: string;
  status: string;
  confidenceTier?: string;
  contentComponentId?: string;
  contentDecision?: ComponentDecision;
  signatures?: SignatureInfo[];
  keyValuePairs?: KVPair[];
  handwrittenCount?: number;
}

interface ReviewInterfaceProps {
  docId: string;
  pages: ReviewPage[];
  fullMarkdown?: string;
  initialPage?: number;
  signatures?: SignatureInfo[];
  keyValuePairs?: KVPair[];
  onApprove: (pageNum: number) => Promise<void>;
  onEdit: (pageNum: number, data?: { markdown: string }) => Promise<void>;
  onFlag: (pageNum: number, reason?: string) => Promise<void>;
}

const PAGE_BREAK_RE = /<!-- PageBreak -->/g;

function splitByPageBreaks(md: string): string[] {
  if (!md) return [];
  return md.split(PAGE_BREAK_RE).map((s) => s.trim()).filter(Boolean);
}

const VALID_TABLE_SECTIONS = ["thead", "tbody", "tfoot", "colgroup", "caption"];

const UNSAFE_TABLE_PROPS = new Set([
  "node",
  "dataSourcepos",
  "sourcePosition",
  "dataSourcePosition",
  "position",
  "index",
  "parent",
]);

function TableWrapper({ children, ...props }: React.ComponentPropsWithoutRef<"table">) {
  const safeProps = Object.fromEntries(
    Object.entries(props).filter(([k]) => !UNSAFE_TABLE_PROPS.has(k))
  );
  const childArray = React.Children.toArray(children);
  const processed: React.ReactNode[] = [];
  let orphanContent: React.ReactNode[] = [];

  const flushOrphans = () => {
    if (orphanContent.length > 0) {
      processed.push(
        <tbody key={`orphan-${processed.length}`}>
          <tr>
            <td colSpan={100}>{orphanContent}</td>
          </tr>
        </tbody>
      );
      orphanContent = [];
    }
  };

  for (let i = 0; i < childArray.length; i++) {
    const child = childArray[i];
    const isElement = typeof child === "object" && child !== null && "type" in child;
    const tagName = isElement && typeof (child as React.ReactElement).type === "string"
      ? ((child as React.ReactElement).type as string)
      : null;

    if (isElement && tagName && VALID_TABLE_SECTIONS.includes(tagName)) {
      flushOrphans();
      processed.push(child);
    } else if (isElement && tagName === "tr") {
      flushOrphans();
      processed.push(<tbody key={`tr-${i}`}>{child}</tbody>);
    } else {
      orphanContent.push(child);
    }
  }
  flushOrphans();

  return (
    <div className="table-wrapper">
      <table {...safeProps}>{processed}</table>
    </div>
  );
}

const mdComponents = {
  table: TableWrapper,
};

export function ReviewInterface({
  docId,
  pages,
  fullMarkdown,
  initialPage,
  onApprove,
  onEdit,
  onFlag,
}: ReviewInterfaceProps) {
  const startIndex = initialPage
    ? Math.max(0, pages.findIndex((p) => p.pageNum === initialPage))
    : 0;
  const [currentIndex, setCurrentIndex] = useState(startIndex);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [flagDialogOpen, setFlagDialogOpen] = useState(false);
  const [flagReason, setFlagReason] = useState("");
  const [flagPageNum, setFlagPageNum] = useState<number | null>(null);
  const [mobileTab, setMobileTab] = useState<string>("digitized");
  const isMobile = useIsMobile();

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const isScrollingProgrammatically = useRef(false);

  const currentPage = pages[currentIndex];
  const totalPages = pages.length;
  const reviewedCount = pages.filter((p) => p.status === "approved" || p.status === "flagged").length;
  const reviewProgress = totalPages > 0 ? Math.round((reviewedCount / totalPages) * 100) : 0;

  const pdfUrl = getDocumentPdfUrl(docId);

  const pageSections = useMemo(() => splitByPageBreaks(fullMarkdown || ""), [fullMarkdown]);
  const hasFullDoc = pageSections.length > 0;

  // ── Scroll-sync: IntersectionObserver watches page sections ──
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || pages.length === 0) return;

    const visiblePages = new Set<number>();

    const observer = new IntersectionObserver(
      (entries) => {
        if (isScrollingProgrammatically.current) return;

        for (const entry of entries) {
          const pageNum = Number(entry.target.getAttribute("data-page"));
          if (isNaN(pageNum)) continue;
          if (entry.isIntersecting) {
            visiblePages.add(pageNum);
          } else {
            visiblePages.delete(pageNum);
          }
        }

        if (visiblePages.size > 0) {
          const topPage = Math.min(...visiblePages);
          const idx = pages.findIndex((p) => p.pageNum === topPage);
          if (idx >= 0) setCurrentIndex(idx);
        }
      },
      {
        root: container,
        rootMargin: "0px 0px -70% 0px",
        threshold: 0,
      },
    );

    pageRefs.current.forEach((el) => observer.observe(el));
    return () => {
      observer.disconnect();
      visiblePages.clear();
    };
  }, [pages]);

  const goToPage = useCallback(
    (idx: number) => {
      isScrollingProgrammatically.current = true;
      setCurrentIndex(idx);
      const pageNum = pages[idx]?.pageNum;
      if (pageNum && pageRefs.current.has(pageNum)) {
        pageRefs.current.get(pageNum)?.scrollIntoView({ behavior: "smooth", block: "start" });
        setTimeout(() => {
          isScrollingProgrammatically.current = false;
        }, 600);
      } else {
        isScrollingProgrammatically.current = false;
      }
    },
    [pages],
  );

  const goNext = useCallback(() => {
    if (currentIndex < totalPages - 1) goToPage(currentIndex + 1);
  }, [currentIndex, totalPages, goToPage]);

  const goPrev = useCallback(() => {
    if (currentIndex > 0) goToPage(currentIndex - 1);
  }, [currentIndex, goToPage]);

  const handleApprove = useCallback(
    async (pageNum: number) => {
      if (actionLoading) return;
      setActionLoading(`approve-${pageNum}`);
      try {
        await onApprove(pageNum);
        toast.success(`Page ${pageNum} approved`);
      } catch {
        /* parent handles */
      } finally {
        setActionLoading(null);
      }
    },
    [actionLoading, onApprove],
  );

  const openFlagDialog = useCallback((pageNum: number) => {
    setFlagPageNum(pageNum);
    setFlagDialogOpen(true);
  }, []);

  const handleFlag = useCallback(async () => {
    if (!flagPageNum || actionLoading) return;
    setActionLoading(`flag-${flagPageNum}`);
    try {
      await onFlag(flagPageNum, flagReason || undefined);
      toast.warning(`Page ${flagPageNum} flagged`);
      setFlagDialogOpen(false);
      setFlagReason("");
      setFlagPageNum(null);
    } catch {
      /* parent handles */
    } finally {
      setActionLoading(null);
    }
  }, [flagPageNum, actionLoading, onFlag, flagReason]);

  const handleComponentApprove = useCallback(
    async (cid: string) => {
      await componentAction(docId, cid, "approve");
      toast.success("Component approved");
    },
    [docId],
  );

  const handleComponentEdit = useCallback(
    async (cid: string, value: string) => {
      await componentAction(docId, cid, "edit", { value });
      toast.success("Component edited");
    },
    [docId],
  );

  const handleComponentFlag = useCallback(
    async (cid: string, reason?: string) => {
      await componentAction(docId, cid, "flag", { reason });
      toast.warning("Component flagged");
    },
    [docId],
  );

  const handleApproveAllComponents = useCallback(
    async (page: ReviewPage) => {
      const cids: string[] = [];
      if (page.contentComponentId) cids.push(page.contentComponentId);
      page.keyValuePairs?.forEach((kv) => {
        if (kv.component_id) cids.push(kv.component_id);
      });
      page.signatures?.forEach((sig) => {
        if (sig.component_id) cids.push(sig.component_id);
      });
      if (cids.length === 0) {
        await onApprove(page.pageNum);
      } else {
        await bulkComponentAction(docId, cids, "approve");
      }
      toast.success(`All components on page ${page.pageNum} approved`);
    },
    [docId, onApprove],
  );

  const copyFullMarkdown = useCallback(() => {
    const text = fullMarkdown || pages.map((p) => p.markdown).join("\n\n---\n\n");
    navigator.clipboard.writeText(text);
    toast.success("Copied full document to clipboard");
  }, [fullMarkdown, pages]);

  const handleDownload = useCallback(
    async (format: "md" | "html" | "pdf") => {
      try {
        if (format === "pdf") {
          await downloadExportAsPdf(docId);
        } else {
          await downloadExport(docId, format);
        }
        toast.success(`${format.toUpperCase()} downloaded`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Download failed";
        toast.error(msg);
      }
    },
    [docId],
  );

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable) return;
      if (e.key === "ArrowRight") goNext();
      if (e.key === "ArrowLeft") goPrev();
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && currentPage) {
        handleApprove(currentPage.pageNum);
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goNext, goPrev, handleApprove, currentPage]);

  // Scroll to initial page on mount
  useEffect(() => {
    if (startIndex > 0) {
      const pageNum = pages[startIndex]?.pageNum;
      if (pageNum) {
        requestAnimationFrame(() => {
          isScrollingProgrammatically.current = true;
          pageRefs.current.get(pageNum)?.scrollIntoView({ behavior: "auto", block: "start" });
          setTimeout(() => {
            isScrollingProgrammatically.current = false;
          }, 300);
        });
      }
    }
  }, []);

  if (!currentPage) return null;

  const pageStatusBadge = (page: ReviewPage) => (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px] capitalize",
        page.status === "approved"
          ? "border-success/30 text-success"
          : page.status === "flagged"
            ? "border-destructive/30 text-destructive"
            : "border-warning/30 text-warning",
      )}
    >
      {page.status.replace("_", " ")}
    </Badge>
  );

  const renderPageComponents = (page: ReviewPage, markdownContent: string) => (
    <div className="space-y-2">
      {/* KV Pairs as reviewable components */}
      {page.keyValuePairs && page.keyValuePairs.length > 0 && (
        <ReviewableComponent
          componentId={page.keyValuePairs[0]?.component_id ?? `p${page.pageNum}-kv-group`}
          label={`Key-Value Fields (${page.keyValuePairs.length})`}
          type="kv"
          confidence={
            page.keyValuePairs.length > 0
              ? page.keyValuePairs.reduce((s, kv) => s + kv.confidence, 0) / page.keyValuePairs.length
              : undefined
          }
          decision={page.keyValuePairs[0]?.decision}
          onApprove={handleComponentApprove}
          onFlag={handleComponentFlag}
        >
          <div className="grid gap-1">
            {page.keyValuePairs.map((kv, i) => (
              <div key={i} className="flex items-baseline gap-2 text-xs">
                <span className="font-medium text-foreground min-w-[100px] shrink-0">{kv.key}:</span>
                <span className="text-muted-foreground">{kv.value || <em className="italic">empty</em>}</span>
                {kv.confidence > 0 && (
                  <span className={cn(
                    "text-[9px] ml-auto shrink-0",
                    kv.confidence >= 0.8 ? "text-success" : kv.confidence >= 0.6 ? "text-warning" : "text-destructive"
                  )}>
                    {Math.round(kv.confidence * 100)}%
                  </span>
                )}
              </div>
            ))}
          </div>
        </ReviewableComponent>
      )}

      {/* Signatures as reviewable components */}
      {page.signatures && page.signatures.filter((s) => s.status === "signed").length > 0 && (
        <ReviewableComponent
          componentId={page.signatures.find((s) => s.component_id)?.component_id ?? `p${page.pageNum}-sig-group`}
          label={`Signatures (${page.signatures.filter((s) => s.status === "signed").length})`}
          type="signature"
          confidence={
            page.signatures.filter((s) => s.status === "signed").length > 0
              ? page.signatures.filter((s) => s.status === "signed").reduce((s, sig) => s + sig.confidence, 0) /
                page.signatures.filter((s) => s.status === "signed").length
              : undefined
          }
          decision={page.signatures.find((s) => s.status === "signed")?.decision}
          onApprove={handleComponentApprove}
          onFlag={handleComponentFlag}
        >
          <div className="space-y-1">
            {page.signatures.filter((s) => s.status === "signed").map((sig, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <PenTool className="size-3 text-info shrink-0" />
                <span>{sig.label || "Signature detected"}</span>
                <span className={cn(
                  "text-[9px] ml-auto",
                  sig.confidence >= 0.8 ? "text-success" : "text-warning"
                )}>
                  {Math.round(sig.confidence * 100)}%
                </span>
              </div>
            ))}
          </div>
        </ReviewableComponent>
      )}

      {/* Markdown content as reviewable component */}
      {markdownContent && (
        <ReviewableComponent
          componentId={page.contentComponentId ?? `p${page.pageNum}-content`}
          label="Extracted Content"
          type="content"
          confidence={page.confidence}
          decision={page.contentDecision}
          editableValue={markdownContent}
          onApprove={handleComponentApprove}
          onEdit={handleComponentEdit}
          onFlag={handleComponentFlag}
        >
          <div className="prose prose-sm prose-slate dark:prose-invert max-w-none text-[13px]">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, rehypeSanitize, rehypeTableFix]} components={mdComponents}>
              {markdownContent}
            </ReactMarkdown>
          </div>
        </ReviewableComponent>
      )}
    </div>
  );

  const renderSections = hasFullDoc
    ? pageSections.map((section, idx) => {
        const pageNum = idx + 1;
        const page = pages.find((p) => p.pageNum === pageNum);
        return (
          <div
            key={pageNum}
            data-page={pageNum}
            ref={(el) => {
              if (el) pageRefs.current.set(pageNum, el);
            }}
            className="mb-2"
          >
            <PageDivider
              pageNum={pageNum}
              page={page}
              isFirst={idx === 0}
              actionLoading={actionLoading}
              onApproveAll={handleApproveAllComponents}
              onFlag={openFlagDialog}
              statusBadge={pageStatusBadge}
            />
            {page ? renderPageComponents(page, section) : (
              <div className="prose prose-sm prose-slate dark:prose-invert max-w-none text-[13px]">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw, rehypeSanitize, rehypeTableFix]} components={mdComponents}>
                  {section}
                </ReactMarkdown>
              </div>
            )}
          </div>
        );
      })
    : pages.map((page) => (
        <div
          key={page.pageNum}
          data-page={page.pageNum}
          ref={(el) => {
            if (el) pageRefs.current.set(page.pageNum, el);
          }}
          className="mb-2"
        >
          <PageDivider
            pageNum={page.pageNum}
            page={page}
            isFirst={page.pageNum === 1}
            actionLoading={actionLoading}
            onApproveAll={handleApproveAllComponents}
            onFlag={openFlagDialog}
            statusBadge={pageStatusBadge}
          />
          {page.markdown ? renderPageComponents(page, page.markdown) : (
            <p className="text-sm text-muted-foreground">No extraction data for this page.</p>
          )}
        </div>
      ));

  const fullDocContent = (
    <div ref={scrollContainerRef} className="h-full overflow-y-auto">
      <div className="p-4 lg:p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-foreground">Digitized Content</h3>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="size-7" onClick={copyFullMarkdown}>
                <Copy className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy full document</TooltipContent>
          </Tooltip>
        </div>
        {renderSections}
      </div>
    </div>
  );

  return (
    <div className="flex flex-col h-[calc(100vh-var(--header-height)-1px)]">
      {/* Review header */}
      <div className="flex items-center justify-between px-4 lg:px-6 py-2.5 border-b border-border bg-card flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Button variant="outline" size="icon" className="size-7" onClick={goPrev} disabled={currentIndex === 0}>
              <ChevronLeft className="size-4" />
            </Button>
            <span className="text-sm font-medium text-foreground min-w-[5rem] text-center">
              Page {currentPage.pageNum} <span className="text-muted-foreground">/ {totalPages}</span>
            </span>
            <Button
              variant="outline"
              size="icon"
              className="size-7"
              onClick={goNext}
              disabled={currentIndex === totalPages - 1}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>

          <ConfidenceBadge score={currentPage.confidence} />
          {pageStatusBadge(currentPage)}

          <div className="hidden lg:flex items-center gap-2 ml-2">
            <Progress value={reviewProgress} className="w-24 h-1.5" />
            <span className="text-[10px] text-muted-foreground">
              {reviewedCount}/{totalPages}
            </span>
          </div>
          {reviewProgress === 100 && (
            <div className="flex items-center gap-2 px-3 py-1.5 bg-success/10 rounded-md ml-2">
              <CheckCircle className="size-3.5 text-success" />
              <span className="text-xs text-success font-medium">All pages reviewed</span>
              <Button size="sm" variant="outline" className="h-6 text-xs ml-auto" asChild>
                <Link href={`/compliance?doc=${docId}`}>
                  Run Compliance <ArrowRight className="size-3 ml-1" />
                </Link>
              </Button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <DropdownMenu>
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="h-8 text-xs">
                    <Download className="size-3.5 mr-1" />
                    <span className="hidden sm:inline">Export</span>
                  </Button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent>Download document</TooltipContent>
            </Tooltip>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleDownload("html")}>
                <FileDown className="size-4 mr-2" /> Download HTML
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleDownload("md")}>
                <FileDown className="size-4 mr-2" /> Download Markdown
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleDownload("pdf")}>
                <FileDown className="size-4 mr-2" /> Print as PDF
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-xs text-destructive hover:text-destructive"
                onClick={() => openFlagDialog(currentPage.pageNum)}
                disabled={!!actionLoading}
              >
                <Flag className="size-3.5 mr-1" />
                <span className="hidden sm:inline">Flag</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Flag this page</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="sm"
                className="h-8 text-xs bg-success hover:bg-success/90 text-success-foreground"
                onClick={() => handleApprove(currentPage.pageNum)}
                disabled={!!actionLoading}
              >
                <Check className="size-3.5 mr-1" />
                <span className="hidden sm:inline">Approve</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Approve (Cmd+Enter)</TooltipContent>
          </Tooltip>
        </div>
      </div>

      {/* Mobile tab switcher */}
      {isMobile && (
        <div className="p-2 border-b border-border bg-card">
          <Tabs value={mobileTab} onValueChange={setMobileTab} className="w-full">
            <TabsList className="w-full grid grid-cols-2">
              <TabsTrigger value="original" className="text-xs">
                <FileText className="size-3.5 mr-1" /> Original
              </TabsTrigger>
              <TabsTrigger value="digitized" className="text-xs">
                <Eye className="size-3.5 mr-1" /> Digitized
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      )}

      {/* Split pane content */}
      <div className="flex-1 overflow-hidden">
        {isMobile ? (
          <div className="h-full">
            {mobileTab === "original" ? (
              <PdfViewer url={pdfUrl} pageNumber={currentPage.pageNum} className="h-full flex flex-col" />
            ) : (
              fullDocContent
            )}
          </div>
        ) : (
          <PanelGroup orientation="horizontal" id="review-split">
            <Panel defaultSize={50} minSize={25}>
              <PdfViewer url={pdfUrl} pageNumber={currentPage.pageNum} className="h-full flex flex-col" />
            </Panel>
            <PanelResizeHandle className="w-1.5 bg-border hover:bg-primary/20 transition-colors flex items-center justify-center">
              <GripVertical className="size-3 text-muted-foreground" />
            </PanelResizeHandle>
            <Panel defaultSize={50} minSize={25}>
              {fullDocContent}
            </Panel>
          </PanelGroup>
        )}
      </div>

      {/* Page thumbnail strip */}
      <div className="border-t border-border bg-card flex-shrink-0 overflow-x-auto">
        <div className="flex items-center gap-1 p-2 min-w-max">
          {pages.map((page, idx) => (
            <button
              key={page.pageNum}
              onClick={() => goToPage(idx)}
              className={cn(
                "size-8 rounded-md text-[10px] font-medium flex items-center justify-center transition-all flex-shrink-0",
                idx === currentIndex
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : page.status === "approved"
                    ? "bg-success/10 text-success border border-success/20 hover:bg-success/20"
                    : page.status === "flagged"
                      ? "bg-destructive/10 text-destructive border border-destructive/20 hover:bg-destructive/20"
                      : "bg-muted text-muted-foreground hover:bg-accent",
              )}
            >
              {page.pageNum}
            </button>
          ))}
        </div>
      </div>

      {/* Keyboard shortcuts */}
      <div className="hidden lg:flex px-4 py-1.5 border-t border-border bg-muted/50 items-center gap-4 text-[10px] text-muted-foreground flex-shrink-0">
        <span>
          <kbd className="px-1 py-0.5 bg-card rounded border border-border text-[10px]">←→</kbd> Navigate
        </span>
        <span>
          <kbd className="px-1 py-0.5 bg-card rounded border border-border text-[10px]">⌘+Enter</kbd> Approve
        </span>
      </div>

      {/* Flag dialog */}
      <AlertDialog open={flagDialogOpen} onOpenChange={setFlagDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Flag page {flagPageNum}?</AlertDialogTitle>
            <AlertDialogDescription>
              Flagging marks this page for further review. You can optionally provide a reason.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <textarea
            value={flagReason}
            onChange={(e) => setFlagReason(e.target.value)}
            placeholder="Reason for flagging (optional)..."
            className="w-full h-20 p-3 rounded-lg border border-input bg-background text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
            autoFocus
          />
          <AlertDialogFooter>
            <AlertDialogCancel
              onClick={() => {
                setFlagReason("");
                setFlagPageNum(null);
              }}
            >
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction onClick={handleFlag} className="bg-destructive text-white hover:bg-destructive/90">
              Flag Page
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PageDivider({
  pageNum,
  page,
  isFirst,
  actionLoading,
  onApproveAll,
  onFlag,
  statusBadge,
}: {
  pageNum: number;
  page?: ReviewPage;
  isFirst: boolean;
  actionLoading: string | null;
  onApproveAll: (page: ReviewPage) => Promise<void>;
  onFlag: (pageNum: number) => void;
  statusBadge: (page: ReviewPage) => React.ReactNode;
}) {
  const pageSigs = page?.signatures?.filter((s) => s.status === "signed") ?? [];
  const pageKvCount = page?.keyValuePairs?.length ?? 0;

  return (
    <div className={cn(isFirst ? "mt-0" : "mt-6")}>
      <div
        className={cn(
          "flex items-center gap-2 py-2 px-3 rounded-lg mb-1 sticky top-0 z-10 backdrop-blur-sm border",
          page?.status === "approved"
            ? "bg-success/5 border-success/20"
            : page?.status === "flagged"
              ? "bg-destructive/5 border-destructive/20"
              : "bg-muted/80 border-border",
        )}
      >
        <span className="text-xs font-semibold text-foreground">Page {pageNum}</span>
        {page && <ConfidenceBadge score={page.confidence} />}
        {page && statusBadge(page)}

        {pageSigs.length > 0 && (
          <Badge variant="outline" className="text-[9px] gap-0.5 border-info/30 text-info">
            <PenTool className="size-2.5" />
            {pageSigs.length} sig
          </Badge>
        )}

        {pageKvCount > 0 && (
          <Badge variant="outline" className="text-[9px] gap-0.5 border-primary/30 text-primary">
            <KeyRound className="size-2.5" />
            {pageKvCount} fields
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1">
          {page && page.status !== "approved" && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] px-2 text-success hover:text-success"
                  onClick={() => onApproveAll(page)}
                  disabled={!!actionLoading}
                >
                  <CheckCheck className="size-3 mr-0.5" /> Approve All
                </Button>
              </TooltipTrigger>
              <TooltipContent>Approve all components on this page</TooltipContent>
            </Tooltip>
          )}
          {page && page.status !== "flagged" && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-[10px] px-2 text-destructive hover:text-destructive"
              onClick={() => onFlag(pageNum)}
              disabled={!!actionLoading}
            >
              <Flag className="size-3 mr-0.5" /> Flag
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
