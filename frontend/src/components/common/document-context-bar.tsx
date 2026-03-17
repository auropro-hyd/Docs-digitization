"use client";

import Link from "next/link";
import { ChevronLeft, PenLine, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

interface DocumentContextBarProps {
  docId: string;
  filename?: string | null;
  currentPage: "review" | "compliance";
  className?: string;
}

export function DocumentContextBar({
  docId,
  filename,
  currentPage,
  className,
}: DocumentContextBarProps) {
  const displayName = filename || `Document ${docId.slice(0, 8)}...`;

  return (
    <div
      className={cn(
        "flex items-center gap-3 px-4 py-2 border-b border-border bg-muted/30 text-sm",
        className,
      )}
      role="navigation"
      aria-label="Document context"
    >
      <Link
        href="/documents"
        className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronLeft className="size-4" />
        <span>Documents</span>
      </Link>
      <span className="text-muted-foreground">|</span>
      <span className="font-medium truncate max-w-[200px] sm:max-w-[280px]" title={filename || docId}>
        {displayName}
      </span>
      <span className="text-muted-foreground flex-1" />
      <div className="flex items-center gap-1">
        <Link
          href={`/review?doc=${docId}`}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
            currentPage === "review"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-muted",
          )}
        >
          <PenLine className="size-3" />
          Page Review
        </Link>
        <Link
          href={`/compliance?doc=${docId}`}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
            currentPage === "compliance"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-muted",
          )}
        >
          <ShieldCheck className="size-3" />
          Compliance
        </Link>
      </div>
    </div>
  );
}
