"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { ReviewInterface } from "@/components/review/review-interface";
import { DocumentContextBar } from "@/components/common/document-context-bar";
import { getDocument, getReviewPages, approvePage, editPage, flagPage } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/common/empty-state";
import { PenLine, FileText, AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

interface ComponentDecision {
  action: string;
  status: string;
  value?: string;
  reason?: string;
}

interface SignatureInfo {
  status: string;
  confidence: number;
  label: string;
  component_id?: string;
  decision?: ComponentDecision | null;
}

interface KVPair {
  key: string;
  value: string;
  confidence: number;
  component_id?: string;
  decision?: ComponentDecision | null;
}

interface ReviewPageData {
  pageNum: number;
  confidence: number;
  markdown: string;
  status: string;
  confidenceTier?: string;
  contentComponentId?: string;
  contentDecision?: ComponentDecision | null;
  signatures?: SignatureInfo[];
  keyValuePairs?: KVPair[];
  handwrittenCount?: number;
}

function ReviewContent() {
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc");
  const initialPageParam = searchParams.get("page");
  const initialPage = initialPageParam ? parseInt(initialPageParam, 10) : undefined;
  const [pages, setPages] = useState<ReviewPageData[]>([]);
  const [loading, setLoading] = useState(!!docId);
  const [error, setError] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [fullMarkdown, setFullMarkdown] = useState("");

  useEffect(() => {
    if (!docId) return;
    getDocument(docId)
      .then((doc) => setFilename(doc.filename ?? null))
      .catch(() => setFilename(null));
  }, [docId]);

  useEffect(() => {
    if (!docId) return;
    getReviewPages(docId)
      .then((data) => {
        if (!Array.isArray(data.pages)) {
          throw new Error("Invalid response: expected pages array");
        }
        /* eslint-disable @typescript-eslint/no-explicit-any */
        const formatted = data.pages.map((p: any) => ({
          pageNum: p.page_num as number,
          confidence: p.confidence as number,
          markdown: (p.markdown as string) ?? "",
          status: p.status as string,
          confidenceTier: p.confidence_tier ?? undefined,
          contentComponentId: p.content_component_id ?? undefined,
          contentDecision: p.content_decision ?? null,
          signatures: (p.signatures ?? []).map((s: any) => ({
            status: s.status ?? "unsigned",
            confidence: s.confidence ?? 0,
            label: s.label ?? "",
            component_id: s.component_id ?? undefined,
            decision: s.decision ?? null,
          })),
          keyValuePairs: (p.key_value_pairs ?? []).map((kv: any) => ({
            key: kv.key ?? "",
            value: kv.value ?? "",
            confidence: kv.confidence ?? 0,
            component_id: kv.component_id ?? undefined,
            decision: kv.decision ?? null,
          })),
          handwrittenCount: p.handwritten_count ?? 0,
        }));
        /* eslint-enable @typescript-eslint/no-explicit-any */
        setPages(formatted);
        setFullMarkdown((data.full_markdown as string) ?? "");
        setError(null);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to load review pages";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  }, [docId]);

  const handleApprove = async (pageNum: number) => {
    if (!docId) throw new Error("No document selected");
    try {
      await approvePage(docId, pageNum);
      setPages((prev) =>
        prev.map((p) => (p.pageNum === pageNum ? { ...p, status: "approved" } : p)),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to approve page";
      toast.error(msg);
      throw err;
    }
  };

  const handleEdit = async (pageNum: number, data?: { markdown: string }) => {
    if (!docId) throw new Error("No document selected");
    try {
      await editPage(docId, pageNum, data?.markdown);
      setPages((prev) =>
        prev.map((p) =>
          p.pageNum === pageNum ? { ...p, status: "edited", markdown: data?.markdown ?? p.markdown } : p,
        ),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to save edits";
      toast.error(msg);
      throw err;
    }
  };

  const handleFlag = async (pageNum: number, reason?: string) => {
    if (!docId) throw new Error("No document selected");
    try {
      await flagPage(docId, pageNum, reason);
      setPages((prev) =>
        prev.map((p) => (p.pageNum === pageNum ? { ...p, status: "flagged" } : p)),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to flag page";
      toast.error(msg);
      throw err;
    }
  };

  if (!docId) {
    return (
      <div className="p-4 lg:p-6">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Page Review</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Review and approve extracted document pages
          </p>
        </div>
        <EmptyState
          icon={<PenLine className="size-7" />}
          title="No document selected"
          description="Choose a document from the list to review its pages"
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

  if (loading) {
    return (
      <div className="flex flex-col h-[calc(100vh-var(--header-height)-1px)]">
        <div className="flex items-center gap-3 px-6 py-3 border-b border-border">
          <Skeleton className="h-7 w-32" />
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
        <div className="flex-1 flex">
          <Skeleton className="flex-1 m-4 rounded-xl" />
          <Skeleton className="flex-1 m-4 rounded-xl" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 lg:p-6">
        <EmptyState
          icon={<AlertCircle className="size-7" />}
          title="Failed to load review data"
          description={error}
          action={
            <Button size="sm" asChild>
              <Link href="/documents">
                <FileText className="size-4 mr-2" /> Back to Documents
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  if (pages.length === 0) {
    return (
      <div className="p-4 lg:p-6">
        <EmptyState
          icon={<PenLine className="size-7" />}
          title="No pages found"
          description="This document has no pages available for review"
          action={
            <Button size="sm" asChild>
              <Link href="/documents">
                <FileText className="size-4 mr-2" /> Back to Documents
              </Link>
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <DocumentContextBar docId={docId} filename={filename} currentPage="review" />
      <ReviewInterface
        docId={docId}
        pages={pages}
        fullMarkdown={fullMarkdown}
        initialPage={initialPage}
        onApprove={handleApprove}
        onEdit={handleEdit}
        onFlag={handleFlag}
      />
    </div>
  );
}

export default function ReviewPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[60vh]">
          <Loader2 className="size-6 text-primary animate-spin" />
        </div>
      }
    >
      <ReviewContent />
    </Suspense>
  );
}
