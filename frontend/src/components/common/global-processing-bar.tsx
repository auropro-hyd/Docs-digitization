"use client";

import Link from "next/link";
import { useDocumentStore } from "@/stores/document-store";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2 } from "lucide-react";
import { useHydrated } from "@/hooks/useHydrated";
import { displayProcessingStatus } from "@/lib/processing-labels";

const TERMINAL = new Set(["idle", "completed", "error"]);

export function GlobalProcessingBar() {
  const { docId, filename, processingStatus, ocrProgress } = useDocumentStore();
  const hydrated = useHydrated();

  const isActive = hydrated && docId && !TERMINAL.has(processingStatus);

  return (
    <AnimatePresence>
      {isActive && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="overflow-hidden"
          role="status"
          aria-live="polite"
        >
          <Link
            href="/"
            className="flex items-center gap-2 px-4 lg:px-6 py-1.5 bg-primary/5 border-b border-primary/10 text-xs text-primary hover:bg-primary/10 transition-colors"
          >
            <Loader2 className="size-3 animate-spin" />
            <span className="font-medium">
              Processing {filename || "document"}...
            </span>
            <span className="text-primary/60 capitalize">
              {displayProcessingStatus(processingStatus)}
            </span>
            {ocrProgress > 0 && ocrProgress < 100 && (
              <span className="ml-auto text-primary/70 tabular-nums">{Math.round(ocrProgress)}%</span>
            )}
          </Link>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
