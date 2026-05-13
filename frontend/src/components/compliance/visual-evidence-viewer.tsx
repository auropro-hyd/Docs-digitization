"use client";

import React, { useState, useRef, useCallback } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Eye, ZoomIn, ZoomOut, RotateCcw } from "lucide-react";
import { getPageImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { VisualRegion } from "@/types/compliance";

interface VisualEvidenceViewerProps {
  docId: string;
  pageNum: number;
  visualEvidence?: string;
  visualRegions?: VisualRegion[];
  evaluationChannels?: string[];
  ruleId: string;
  trigger?: React.ReactNode;
}

export function VisualEvidenceViewer({
  docId,
  pageNum,
  visualEvidence,
  visualRegions,
  evaluationChannels,
  ruleId,
  trigger,
}: VisualEvidenceViewerProps) {
  const [zoom, setZoom] = useState(1);
  const [imageLoaded, setImageLoaded] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);

  const handleZoomIn = useCallback(() => setZoom((z) => Math.min(z + 0.25, 3)), []);
  const handleZoomOut = useCallback(() => setZoom((z) => Math.max(z - 0.25, 0.5)), []);
  const handleReset = useCallback(() => setZoom(1), []);

  const imageUrl = getPageImageUrl(docId, pageNum);

  return (
    <Dialog onOpenChange={() => { setZoom(1); setImageLoaded(false); }}>
      <DialogTrigger asChild>
        {trigger || (
          <Button
            variant="outline"
            size="sm"
            className="h-6 text-[10px] border-violet-300 dark:border-violet-700 text-violet-600 dark:text-violet-400"
          >
            <Eye className="size-3 mr-1" /> View visual evidence
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="sm:max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Eye className="size-4 text-violet-600 dark:text-violet-400" />
            Visual Evidence — {ruleId} (Page {pageNum})
            {evaluationChannels?.map((ch) => (
              <Badge
                key={ch}
                variant="outline"
                className={cn(
                  "text-[9px] px-1.5 py-0",
                  ch === "vision"
                    ? "border-violet-300 dark:border-violet-700 text-violet-600 dark:text-violet-400"
                    : "border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400",
                )}
              >
                {ch}
              </Badge>
            ))}
          </DialogTitle>
          <DialogDescription className="sr-only">
            VLM-detected regions overlaid on the original page image for rule {ruleId}.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2 pb-2 border-b">
          <Button variant="outline" size="icon" className="size-7" onClick={handleZoomOut} disabled={zoom <= 0.5}>
            <ZoomOut className="size-3.5" />
          </Button>
          <span className="text-xs tabular-nums text-muted-foreground w-12 text-center">{Math.round(zoom * 100)}%</span>
          <Button variant="outline" size="icon" className="size-7" onClick={handleZoomIn} disabled={zoom >= 3}>
            <ZoomIn className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" className="size-7" onClick={handleReset}>
            <RotateCcw className="size-3.5" />
          </Button>
        </div>

        <div className="flex-1 overflow-auto min-h-0">
          <div className="flex gap-4">
            <div className="flex-1 overflow-auto rounded-lg border bg-muted/30 relative">
              <div
                style={{ transform: `scale(${zoom})`, transformOrigin: "top left" }}
                className="relative inline-block transition-transform duration-150"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  ref={imageRef}
                  src={imageUrl}
                  alt={`Page ${pageNum}`}
                  className="block max-w-none"
                  onLoad={() => setImageLoaded(true)}
                />
                {imageLoaded && visualRegions?.map((region, idx) => (
                  <div
                    key={idx}
                    className="absolute border-2 border-violet-500/70 bg-violet-500/15 rounded-sm pointer-events-none"
                    style={{
                      left: `${region.x * 100}%`,
                      top: `${region.y * 100}%`,
                      width: `${region.width * 100}%`,
                      height: `${region.height * 100}%`,
                    }}
                    title={region.label}
                  >
                    <span className="absolute -top-5 left-0 text-[9px] bg-violet-600 text-white px-1 py-0.5 rounded whitespace-nowrap">
                      {region.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {visualEvidence && (
              <div className="w-64 flex-shrink-0 space-y-3">
                <div className="p-3 rounded-lg bg-violet-50 dark:bg-violet-900/10 border border-violet-200 dark:border-violet-800">
                  <p className="text-xs font-medium text-violet-700 dark:text-violet-300 mb-1.5">Visual Analysis</p>
                  <p className="text-xs text-muted-foreground leading-relaxed">{visualEvidence}</p>
                </div>
                {visualRegions && visualRegions.length > 0 && (
                  <div className="p-3 rounded-lg border">
                    <p className="text-xs font-medium mb-1.5">Detected Regions ({visualRegions.length})</p>
                    <div className="space-y-1">
                      {visualRegions.map((r, i) => (
                        <div key={i} className="flex items-center gap-1.5">
                          <div className="size-2 rounded-full bg-violet-500 flex-shrink-0" />
                          <span className="text-[11px] text-muted-foreground">{r.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
