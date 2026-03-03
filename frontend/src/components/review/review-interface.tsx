"use client";

import { useState, useCallback, useEffect } from "react";
import { Check, Flag, Edit3, ChevronLeft, ChevronRight, Keyboard } from "lucide-react";
import { cn } from "@/lib/utils";
import { ConfidenceBadge } from "@/components/common/confidence-badge";

interface ReviewPage {
  pageNum: number;
  confidence: number;
  markdown: string;
  extraction?: Record<string, unknown>;
}

interface ReviewInterfaceProps {
  docId: string;
  pages: ReviewPage[];
  onApprove: (pageNum: number) => void;
  onEdit: (pageNum: number, data: Record<string, unknown>) => void;
  onFlag: (pageNum: number, reason: string) => void;
}

export function ReviewInterface({ docId, pages, onApprove, onEdit, onFlag }: ReviewInterfaceProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isEditing, setIsEditing] = useState(false);
  const [editedMarkdown, setEditedMarkdown] = useState("");
  const currentPage = pages[currentIndex];

  const goNext = useCallback(() => {
    setCurrentIndex((i) => Math.min(i + 1, pages.length - 1));
    setIsEditing(false);
  }, [pages.length]);

  const goPrev = useCallback(() => {
    setCurrentIndex((i) => Math.max(i - 1, 0));
    setIsEditing(false);
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (isEditing) return;
      switch (e.key) {
        case "Enter":
          if (currentPage) onApprove(currentPage.pageNum);
          goNext();
          break;
        case "f":
        case "F":
          if (currentPage) onFlag(currentPage.pageNum, "Flagged via keyboard");
          goNext();
          break;
        case "e":
        case "E":
          setIsEditing(true);
          break;
        case "ArrowRight":
          goNext();
          break;
        case "ArrowLeft":
          goPrev();
          break;
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [currentPage, isEditing, onApprove, onFlag, goNext, goPrev]);

  if (!currentPage) {
    return (
      <div className="flex h-96 items-center justify-center text-gray-500">
        No pages to review
      </div>
    );
  }

  const reviewed = currentIndex;
  const total = pages.length;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center gap-4">
          <button onClick={goPrev} disabled={currentIndex === 0} className="rounded p-1 hover:bg-gray-100 disabled:opacity-30">
            <ChevronLeft className="h-5 w-5" />
          </button>
          <span className="text-sm font-medium text-gray-700">
            <span className="text-xs text-gray-400">{docId}</span>{" "}
            Page {currentPage.pageNum} ({currentIndex + 1} of {total})
          </span>
          <button onClick={goNext} disabled={currentIndex === total - 1} className="rounded p-1 hover:bg-gray-100 disabled:opacity-30">
            <ChevronRight className="h-5 w-5" />
          </button>
        </div>
        <div className="flex items-center gap-2">
          <ConfidenceBadge score={currentPage.confidence} />
          <span className="text-xs text-gray-400">
            {reviewed} reviewed, {total - reviewed} remaining
          </span>
        </div>
        <div className="flex items-center gap-1 text-xs text-gray-400">
          <Keyboard className="h-3.5 w-3.5" />
          Enter=Approve F=Flag E=Edit Arrows=Navigate
        </div>
      </div>

      {/* Split pane */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Original PDF placeholder */}
        <div className="flex w-1/2 flex-col border-r border-gray-200 bg-gray-50">
          <div className="border-b border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700">
            Original Document
          </div>
          <div className="flex flex-1 items-center justify-center p-4 text-gray-400">
            <div className="text-center">
              <p className="text-sm">PDF Viewer (PDF.js)</p>
              <p className="text-xs">Page {currentPage.pageNum}</p>
            </div>
          </div>
        </div>

        {/* Right: Extracted data */}
        <div className="flex w-1/2 flex-col bg-white">
          <div className="flex items-center justify-between border-b border-gray-200 px-4 py-2 text-sm font-medium text-gray-700">
            <span>Extracted Data</span>
            {isEditing && (
              <button
                onClick={() => {
                  onEdit(currentPage.pageNum, { markdown: editedMarkdown });
                  setIsEditing(false);
                }}
                className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
              >
                Save Changes
              </button>
            )}
          </div>
          <div className="flex-1 overflow-auto p-4">
            {isEditing ? (
              <textarea
                value={editedMarkdown}
                onChange={(e) => setEditedMarkdown(e.target.value)}
                className="h-full w-full resize-none rounded-lg border border-blue-300 bg-blue-50/30 p-4 font-mono text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            ) : (
              <div className="prose prose-sm max-w-none">
                <pre className="whitespace-pre-wrap rounded-lg bg-gray-50 p-4 text-sm">
                  {currentPage.markdown || "No content extracted"}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-between border-t border-gray-200 bg-white px-6 py-3">
        <div className="flex gap-2">
          <button
            onClick={() => {
              onApprove(currentPage.pageNum);
              goNext();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-700"
          >
            <Check className="h-4 w-4" /> Approve
          </button>
          <button
            onClick={() => {
              if (!isEditing && currentPage) {
                setEditedMarkdown(currentPage.markdown || "");
              }
              setIsEditing(!isEditing);
            }}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              isEditing
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-700 hover:bg-gray-200"
            )}
          >
            <Edit3 className="h-4 w-4" /> Edit
          </button>
          <button
            onClick={() => {
              onFlag(currentPage.pageNum, "Flagged for review");
              goNext();
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber-100 px-4 py-2 text-sm font-medium text-amber-700 transition-colors hover:bg-amber-200"
          >
            <Flag className="h-4 w-4" /> Flag
          </button>
        </div>
      </div>
    </div>
  );
}
