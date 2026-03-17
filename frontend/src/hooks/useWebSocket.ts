"use client";

import { useEffect, useRef } from "react";
import { DocumentWebSocket, type WSMessage } from "@/lib/websocket";
import { useDocumentStore, type ProcessingStatus } from "@/stores/document-store";
import { getDocument } from "@/lib/api";

const VALID_STATUSES: Set<string> = new Set<string>([
  "idle",
  "uploading",
  "ingested",
  "marker_ocr_running",
  "azure_di_running",
  "quality_scoring",
  "merging_results",
  "hitl_required",
  "auto_approved",
  "reviewed",
  "completed",
  "error",
]);

const TERMINAL_STATUSES = new Set(["idle", "completed", "error"]);

function isActiveStatus(status: string): boolean {
  return !!status && !TERMINAL_STATUSES.has(status);
}

const NODE_TO_STATUS: Record<string, ProcessingStatus> = {
  ingest_document: "azure_di_running",
  run_azure_di_ocr: "merging_results",
  run_marker_ocr: "merging_results",
  merge_azure_di_results: "auto_approved",
  merge_results: "auto_approved",
  quality_scoring: "auto_approved",
  auto_approve: "completed",
  store_results: "completed",
  completed: "completed",
};

function resolveStatus(value: string): ProcessingStatus | null {
  if (VALID_STATUSES.has(value)) return value as ProcessingStatus;
  return NODE_TO_STATUS[value] ?? null;
}

/**
 * Runs ONCE on mount. If there's a docId restored from localStorage
 * (processingStatus defaults to "ingested"), validate it against the
 * backend. Does NOT fire for freshly uploaded docs in the current session.
 */
export function useValidateRestoredDoc(): void {
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    const { docId, processingStatus, setProcessingStatus, setTotalPages, reset } =
      useDocumentStore.getState();

    if (!docId || processingStatus !== "ingested") return;

    getDocument(docId)
      .then((data) => {
        if (data.has_results) {
          const total = data.results?.extractions?.length ?? 0;
          setTotalPages(total);
          setProcessingStatus("completed");
        } else {
          reset();
        }
      })
      .catch(() => {
        reset();
      });
  }, []);
}

export function useDocumentWebSocket(docId: string | null): void {
  const wsRef = useRef<DocumentWebSocket | null>(null);
  const { processingStatus, setProcessingStatus, setTotalPages, setOcrProgress, updatePage, setError } = useDocumentStore();

  const shouldConnect = docId && isActiveStatus(processingStatus);

  useEffect(() => {
    if (!shouldConnect || !docId) return;

    const ws = new DocumentWebSocket(docId);
    wsRef.current = ws;

    const unsubscribe = ws.subscribe((msg: WSMessage) => {
      if (msg.type === "status" && msg.status) {
        const resolved = resolveStatus(msg.status);
        if (resolved) {
          setProcessingStatus(resolved);
        }
        if (msg.total_pages && msg.total_pages > 0) {
          setTotalPages(msg.total_pages);
        }
      }

      if (msg.type === "progress") {
        const pct = typeof msg.percent === "number" ? msg.percent : 0;
        const label = typeof msg.label === "string" ? msg.label : "";
        setOcrProgress(pct, label);
      }

      if (msg.type === "page_update" && msg.page_num) {
        updatePage(msg.page_num, {
          confidence: msg.confidence ?? 0,
          status: msg.status === "approved" ? "approved" : msg.status === "needs_review" ? "reviewing" : "scoring",
        });
      }

      if (msg.type === "error") {
        setError(msg.error || "Unknown error");
      }

      if (msg.type === "hitl_required") {
        setProcessingStatus("hitl_required");
      }
    });

    ws.connect();

    return () => {
      unsubscribe();
      ws.disconnect();
      wsRef.current = null;
    };
  }, [shouldConnect, docId, setProcessingStatus, setTotalPages, setOcrProgress, updatePage, setError]);
}

/**
 * Polls the backend as a fallback to detect completion when WebSocket
 * messages are missed. Runs every 5s while the document is processing.
 */
export function useProcessingPollFallback(docId: string | null): void {
  const { processingStatus, setProcessingStatus, setTotalPages } = useDocumentStore();
  const isActive = docId && isActiveStatus(processingStatus);

  useEffect(() => {
    if (!isActive || !docId) return;

    const interval = setInterval(() => {
      getDocument(docId)
        .then((data) => {
          if (data.has_results) {
            const total = data.results?.extractions?.length ?? 0;
            setTotalPages(total);
            setProcessingStatus("completed");
          }
        })
        .catch(() => {});
    }, 5000);

    return () => clearInterval(interval);
  }, [isActive, docId, setProcessingStatus, setTotalPages]);
}
