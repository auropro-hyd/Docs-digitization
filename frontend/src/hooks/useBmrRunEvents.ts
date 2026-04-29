"use client";

import { useEffect, useState } from "react";

import { API_BASE } from "@/lib/api";
import type { RunStage } from "@/types/bmr";

// Mirror of backend ``app/bmr/events/__init__.py::_EVENT_SCHEMA_VERSION``
// — every envelope carries this version so a forward-incompatible
// schema change can be detected client-side.
export const BMR_EVENT_SCHEMA_VERSION = "1.0";

export interface BmrEventEnvelope {
  schema_version: string;
  event: string;
  run_id: string;
  timestamp?: string;
  trace_id?: string | null;
  span_id?: string | null;
  payload?: Record<string, unknown>;
}

export interface StageProgress {
  // Last stage the backend told us about, regardless of whether it
  // entered or completed. The UI uses this to highlight the
  // currently-active stage in the timeline.
  current: RunStage | null;
  // 1-indexed position within the 5-stage pipeline. ``0`` until the
  // first ``stage.entered`` event lands.
  index: number;
  total: number;
  // True once the pipeline emitted a terminal lifecycle event
  // (``run.completed`` / ``run.failed``). Used to stop the pulse
  // animation on the current-stage row.
  finished: boolean;
}

const INITIAL: StageProgress = { current: null, index: 0, total: 5, finished: false };

// Subscribes to the ``/api/bmr/runs/{runId}/events`` WebSocket and
// distills the per-stage entered/completed events into a single
// ``StageProgress`` snapshot the run-detail page can render. Reconnect
// on close/error with a small backoff so a transient network blip
// doesn't permanently freeze the UI on a stale stage; on reconnect the
// server replays the run's current snapshot via the ``snapshot`` event
// so the UI catches up without further work.
export function useBmrRunEvents(runId: string | null): StageProgress {
  const [progress, setProgress] = useState<StageProgress>(INITIAL);

  useEffect(() => {
    if (!runId) return;

    let cancelled = false;
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const url = API_BASE.replace(/^http/, "ws") + `/api/bmr/runs/${runId}/events`;
      ws = new WebSocket(url);

      ws.onmessage = (e) => {
        if (cancelled) return;
        let envelope: BmrEventEnvelope;
        try {
          envelope = JSON.parse(e.data);
        } catch {
          return;
        }
        setProgress((prev) => reduceEnvelope(prev, envelope));
      };

      ws.onclose = () => {
        if (cancelled) return;
        // 2s reconnect — keeps the run-detail page resilient to a
        // transient WS drop without hammering the server during a
        // longer outage.
        reconnectTimer = setTimeout(connect, 2000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, [runId]);

  return progress;
}

function reduceEnvelope(
  prev: StageProgress,
  envelope: BmrEventEnvelope,
): StageProgress {
  const payload = (envelope.payload ?? {}) as Record<string, unknown>;
  switch (envelope.event) {
    case "snapshot": {
      const stage = payload.stage as RunStage | undefined;
      const status = payload.status as string | undefined;
      return {
        ...prev,
        current: stage ?? prev.current,
        finished: status === "completed" || status === "failed",
      };
    }
    case "bmr.stage.entered":
    case "bmr.stage.completed": {
      const stage = payload.stage as RunStage | undefined;
      const idx = (payload.stage_index as number | undefined) ?? prev.index;
      const total = (payload.total_stages as number | undefined) ?? prev.total;
      // Monotone-only: don't snap backwards if a stale event for an
      // earlier stage races a later one across the wire.
      const nextIndex = Math.max(prev.index, idx);
      return {
        ...prev,
        current: stage ?? prev.current,
        index: nextIndex,
        total,
      };
    }
    case "run.completed":
    case "run.failed":
      return { ...prev, finished: true };
    default:
      return prev;
  }
}
