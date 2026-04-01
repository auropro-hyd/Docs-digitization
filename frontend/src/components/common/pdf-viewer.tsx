"use client";

import dynamic from "next/dynamic";
import { useState, useCallback, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { ZoomIn, ZoomOut, Maximize } from "lucide-react";

const PdfViewerInner = dynamic(() => import("./pdf-viewer-inner"), { ssr: false });

interface PdfViewerProps {
  url: string;
  pageNumber: number;
  className?: string;
  focusLabel?: string;
  focusPulseKey?: string;
  highlightRect?: { x: number; y: number; width: number; height: number } | null;
}

export function PdfViewer({ url, pageNumber, className, focusLabel, focusPulseKey, highlightRect }: PdfViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState<number>(0);
  const [manualScale, setManualScale] = useState<number | null>(null);
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect.width;
        if (w > 0) setContainerWidth(w);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const fitWidth = useCallback(() => setManualScale(null), []);
  const zoomIn = useCallback(() => {
    setManualScale((prev) => Math.min((prev ?? 1.0) + 0.25, 3));
  }, []);
  const zoomOut = useCallback(() => {
    setManualScale((prev) => Math.max((prev ?? 1.0) - 0.25, 0.5));
  }, []);

  const fitWidthPx = containerWidth > 32 ? containerWidth - 32 : undefined;
  const displayLabel = manualScale ? `${Math.round(manualScale * 100)}%` : "Fit";

  useEffect(() => {
    if (!focusPulseKey) return;
    setPulse(true);
    const timer = setTimeout(() => setPulse(false), 900);
    return () => clearTimeout(timer);
  }, [focusPulseKey]);

  return (
    <div className={className}>
      <div className="flex items-center gap-1 p-2 border-b border-border bg-muted/50 flex-shrink-0">
        <Button variant="ghost" size="icon" className="size-7" onClick={zoomOut}>
          <ZoomOut className="size-3.5" />
        </Button>
        <span className="text-xs text-muted-foreground min-w-[3rem] text-center">
          {displayLabel}
        </span>
        <Button variant="ghost" size="icon" className="size-7" onClick={zoomIn}>
          <ZoomIn className="size-3.5" />
        </Button>
        <Button variant="ghost" size="icon" className="size-7" onClick={fitWidth} title="Fit to width">
          <Maximize className="size-3.5" />
        </Button>
        {focusLabel && (
          <span className="ml-auto text-[10px] text-muted-foreground truncate max-w-[45%]">
            Focus: {focusLabel}
          </span>
        )}
      </div>

      <div
        ref={containerRef}
        className={`overflow-auto flex-1 min-h-0 flex justify-center p-4 bg-muted/30 transition-shadow ${pulse ? "ring-2 ring-primary/40" : ""}`}
      >
        <PdfViewerInner
          url={url}
          pageNumber={pageNumber}
          scale={manualScale ?? undefined}
          width={manualScale ? undefined : fitWidthPx}
          highlightRect={highlightRect}
        />
      </div>
    </div>
  );
}
