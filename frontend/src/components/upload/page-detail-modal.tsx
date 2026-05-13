"use client";

import React, { useEffect, useState, useTransition } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { rehypeTableFix } from "@/lib/rehype-table-fix";
import type { PageData } from "@/stores/document-store";
import { API_BASE, getDocument } from "@/lib/api";
import { ConfidenceBadge } from "@/components/common/confidence-badge";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

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

/** Build the ReactMarkdown component map for a given document.
 *
 * Datalab OCR emits image crops as ``![alt](HASH_img.jpg)``. The
 * frontend renders these via ReactMarkdown, where a bare relative
 * URL resolves against the frontend origin (e.g.
 * ``http://localhost:3100/HASH_img.jpg``) and 404s. The backend
 * serves the crops at
 * ``/api/documents/{doc_id}/images/{filename}`` — this custom
 * ``img`` renderer rewrites Datalab's relative refs to point at
 * that endpoint. Absolute / data URIs pass through unchanged. */
function buildMdComponents(docId: string) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ImgRenderer = (props: any) => {
    const { src, alt, ...rest } = props;
    const rawSrc = typeof src === "string" ? src : "";
    let resolved = rawSrc;
    if (rawSrc && !/^(https?:|data:|\/)/i.test(rawSrc)) {
      resolved = `${API_BASE}/api/documents/${docId}/images/${rawSrc}`;
    }
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={resolved} alt={alt ?? ""} {...rest} />;
  };
  return {
    table: TableWrapper,
    img: ImgRenderer,
  };
}

interface PageDetailModalProps {
  page: PageData | null;
  docId: string;
  onClose: () => void;
}

export function PageDetailModal({ page, docId, onClose }: PageDetailModalProps) {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    if (!page || !docId) return;
    startTransition(async () => {
      try {
        const data = await getDocument(docId);
        const raw = data.results?.raw_markdown?.[String(page.pageNum)];
        const extraction = data.results?.extractions?.find(
          (e: Record<string, unknown>) => e.page_num === page.pageNum,
        );
        setMarkdown(raw || extraction?.content || null);
        setError(null);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to load page data";
        toast.error(msg);
        setError(msg);
      }
    });
  }, [page, docId]);

  return (
    <Sheet open={!!page} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full sm:max-w-lg p-0 flex flex-col">
        <SheetHeader className="p-5 pb-0">
          <div className="flex items-center justify-between">
            <SheetTitle className="text-sm">
              Page {page?.pageNum}
            </SheetTitle>
            <div className="flex items-center gap-2">
              {page && <ConfidenceBadge score={page.confidence} />}
              {page && (
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
                  {page.status}
                </Badge>
              )}
            </div>
          </div>
        </SheetHeader>

        <div className="px-5 pt-3 pb-1">
          <p className="text-xs font-medium text-muted-foreground">Extracted Content</p>
        </div>

        <ScrollArea className="flex-1 px-5 pb-5">
          {isPending ? (
            <div className="space-y-2 pt-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-4 w-full" style={{ width: `${100 - i * 10}%` }} />
              ))}
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-sm text-destructive font-medium">Failed to load page data</p>
              <p className="text-xs text-destructive/70 mt-1">{error}</p>
            </div>
          ) : markdown ? (
            <div className="prose prose-sm prose-slate dark:prose-invert max-w-none text-[13px] pt-2">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw, rehypeSanitize, rehypeTableFix]}
                components={buildMdComponents(docId)}
              >
                {markdown}
              </ReactMarkdown>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <p className="text-sm text-muted-foreground">No extraction data available</p>
              <p className="text-xs text-muted-foreground/60 mt-1">
                Content will appear after processing completes
              </p>
            </div>
          )}
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}
