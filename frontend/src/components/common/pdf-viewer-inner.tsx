"use client";

import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { Skeleton } from "@/components/ui/skeleton";

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

interface PdfViewerInnerProps {
  url: string;
  pageNumber: number;
  scale?: number;
  width?: number;
  highlightRect?: { x: number; y: number; width: number; height: number } | null;
}

export default function PdfViewerInner({ url, pageNumber, scale, width, highlightRect }: PdfViewerInnerProps) {
  return (
    <Document
      file={url}
      loading={<Skeleton className="w-[400px] h-[560px] rounded-lg" />}
      error={
        <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
          Failed to load PDF
        </div>
      }
    >
      <div className="relative inline-block">
        <Page
          pageNumber={pageNumber}
          {...(scale ? { scale } : width ? { width } : { width: 600 })}
          loading={<Skeleton className="w-[400px] h-[560px] rounded-lg" />}
          className="shadow-lg rounded-lg overflow-hidden"
        />
        {highlightRect && (
          <div
            className="absolute border-2 border-primary bg-primary/15 rounded-sm pointer-events-none animate-pulse"
            style={{
              left: `${highlightRect.x * 100}%`,
              top: `${highlightRect.y * 100}%`,
              width: `${highlightRect.width * 100}%`,
              height: `${highlightRect.height * 100}%`,
            }}
          />
        )}
      </div>
    </Document>
  );
}
