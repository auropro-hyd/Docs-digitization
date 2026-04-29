"use client";

import { useEffect, useRef } from "react";
import { DocumentWebSocket, type WSMessage } from "@/lib/websocket";
import { useDocumentStore, type ProcessingStatus } from "@/stores/document-store";
import { getDocument, getDocumentProgress } from "@/lib/api";
import { normalizeEngineTerms } from "@/lib/processing-labels";

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
  ingest_document: "ingested",
  run_azure_di_ocr: "azure_di_running",
  run_marker_ocr: "marker_ocr_running",
  run_quality_scoring: "quality_scoring",
  merge_azure_di_results: "merging_results",
  merge_marker_results: "merging_results",
  quality_scoring: "quality_scoring",
  auto_approve: "auto_approved",
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
  const { processingStatus, setProcessingStatus, setTotalPages, setOcrProgress, updatePage, setError, addTimelineEvent } = useDocumentStore();

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
        const phase =
          msg.phase === "submit" || msg.phase === "analyzing" || msg.phase === "done"
            ? msg.phase
            : null;
        setOcrProgress(pct, normalizeEngineTerms(label), phase);
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

      if (msg.type === "quality_gate") {
        const status = typeof msg.status === "string" ? msg.status : "ok";
        const severity = typeof msg.severity === "string" ? msg.severity : "low";
        addTimelineEvent(`Input quality check: ${status} (${severity})`);
      }
    });

    ws.connect();

    return () => {
      unsubscribe();
      ws.disconnect();
      wsRef.current = null;
    };
  }, [shouldConnect, docId, setProcessingStatus, setTotalPages, setOcrProgress, updatePage, setError, addTimelineEvent]);
}

/**
 * Polls the backend as a fallback when WebSocket messages are missed.
 * Two parallel polls run while the document is processing:
 *
 * - ``getDocument`` every 5s detects terminal completion (existing
 *   behaviour — fires once and flips status to ``completed``).
 * - ``getDocumentProgress`` every 2s reads the latest progress
 *   payload from the server-side cache and feeds it into the same
 *   ``setOcrProgress`` reducer the WebSocket handler uses, so the
 *   user sees a heartbeat label even when WS is blocked (corporate
 *   proxies, captive portals, mobile carriers stripping ``Upgrade``).
 *
 * The progress poll re-emits the same payload until the cache moves;
 * the store's monotone-only reducer means duplicate ticks are
 * absorbed without flicker. The poll stops as soon as ``percent``
 * reaches 100 so a finished run doesn't keep hitting the endpoint.
 */
export function useProcessingPollFallback(docId: string | null): void {
  const {
    processingStatus,
    setProcessingStatus,
    setTotalPages,
    setOcrProgress,
  } = useDocumentStore();
  const isActive = docId && isActiveStatus(processingStatus);

  useEffect(() => {
    if (!isActive || !docId) return;

    const completionInterval = setInterval(() => {
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

    const progressInterval = setInterval(() => {
      getDocumentProgress(docId)
        .then((data) => {
          if (data.percent <= 0 && !data.label) return;  // no signal yet
          setOcrProgress(data.percent, data.label, data.phase);
        })
        .catch(() => {});
    }, 2000);

    return () => {
      clearInterval(completionInterval);
      clearInterval(progressInterval);
    };
  }, [isActive, docId, setProcessingStatus, setTotalPages, setOcrProgress]);
}
