"use client";

import { useEffect, useRef } from "react";
import { DocumentWebSocket, type WSMessage } from "@/lib/websocket";
import { useDocumentStore, type ProcessingStatus } from "@/stores/document-store";

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

function isProcessingStatus(value: string): value is ProcessingStatus {
  return VALID_STATUSES.has(value);
}

export function useDocumentWebSocket(docId: string | null): void {
  const wsRef = useRef<DocumentWebSocket | null>(null);
  const { setProcessingStatus, setTotalPages, setError } = useDocumentStore();

  useEffect(() => {
    if (!docId) return;

    const ws = new DocumentWebSocket(docId);
    wsRef.current = ws;

    const unsubscribe = ws.subscribe((msg: WSMessage) => {
      if (msg.type === "status" && msg.status) {
        if (isProcessingStatus(msg.status)) {
          setProcessingStatus(msg.status);
        }
        if (msg.total_pages) {
          setTotalPages(msg.total_pages);
        }
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
  }, [docId, setProcessingStatus, setTotalPages, setError]);
}
