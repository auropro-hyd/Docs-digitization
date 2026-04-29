import { create } from "zustand";
import { displayProcessingStatus, normalizeEngineTerms } from "@/lib/processing-labels";

export type PageStatus =
  | "queued"
  | "extracting"
  | "scoring"
  | "reviewing"
  | "approved"
  | "flagged"
  | "error";

export type ProcessingStatus =
  | "idle"
  | "uploading"
  | "ingested"
  | "marker_ocr_running"
  | "azure_di_running"
  | "quality_scoring"
  | "merging_results"
  | "hitl_required"
  | "auto_approved"
  | "reviewed"
  | "completed"
  | "error";

export interface PageData {
  pageNum: number;
  confidence: number;
  status: PageStatus;
  markdown?: string;
  extraction?: Record<string, unknown>;
}

export interface TimelineEvent {
  id: string;
  ts: number;
  text: string;
}

const STORAGE_KEY = "processing-doc";

const TERMINAL_STATUSES = new Set(["idle", "completed", "error"]);

function loadPersistedDoc(): { docId: string | null; filename: string | null } {
  if (typeof window === "undefined") return { docId: null, filename: null };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { docId: parsed.docId || null, filename: parsed.filename || null };
    }
  } catch {}
  return { docId: null, filename: null };
}

function persistDoc(docId: string | null, filename: string | null) {
  try {
    if (docId) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ docId, filename }));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {}
}

// Backend mirrors this enum in app/workflow/nodes.py — stays in sync via the
// WS payload's ``phase`` field. ``submit`` is the brief pre-analysis window;
// ``analyzing`` is the long middle (where the heartbeat label is the
// user's signal that the engine is still working); ``done`` is 100%.
export type OcrPhase = "submit" | "analyzing" | "done" | null;

interface DocumentState {
  docId: string | null;
  filename: string | null;
  totalPages: number;
  processingStatus: ProcessingStatus;
  ocrProgress: number;
  ocrProgressLabel: string;
  ocrPhase: OcrPhase;
  timeline: TimelineEvent[];
  pages: Map<number, PageData>;
  error: string | null;

  setDocId: (docId: string, filename: string) => void;
  setProcessingStatus: (status: ProcessingStatus) => void;
  setOcrProgress: (percent: number, label: string, phase?: OcrPhase) => void;
  addTimelineEvent: (text: string) => void;
  setTotalPages: (count: number) => void;
  updatePage: (pageNum: number, data: Partial<PageData>) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const persisted = loadPersistedDoc();

export const useDocumentStore = create<DocumentState>((set) => ({
  docId: persisted.docId,
  filename: persisted.filename,
  totalPages: 0,
  processingStatus: persisted.docId ? "ingested" : "idle",
  ocrProgress: 0,
  ocrProgressLabel: "",
  ocrPhase: null,
  timeline: [],
  pages: new Map(),
  error: null,

  setDocId: (docId, filename) => {
    persistDoc(docId, filename);
    const now = Date.now();
    set({
      docId,
      filename,
      processingStatus: "uploading",
      error: null,
      pages: new Map(),
      totalPages: 0,
      ocrProgress: 0,
      ocrProgressLabel: "",
      ocrPhase: null,
      timeline: [{ id: `${now}-upload`, ts: now, text: "File selected for secure upload" }],
    });
  },

  setProcessingStatus: (status) =>
    set((state) => {
      if (state.processingStatus === status) return state;
    if (TERMINAL_STATUSES.has(status)) {
      persistDoc(null, null);
    }
      const now = Date.now();
      const event: TimelineEvent = {
        id: `${now}-${status}`,
        ts: now,
        text: displayProcessingStatus(status),
      };
      return { processingStatus: status, timeline: [...state.timeline.slice(-7), event] };
    }),

  setOcrProgress: (percent, label, phase) =>
    set((state) => {
      // Strict monotone-only invariant — the bar never snaps backwards
      // within a single run. Concurrent OCR chunks emit a heartbeat
      // every poll interval whose percent is the chunk's baseline
      // (often 0 while the first chunk is still in flight); without
      // this gate, those heartbeats would clobber an established
      // higher percent (e.g. an already-broadcast "Completed chunk
      // 1/8 → 11%") back to 0 once a still-running chunk's heartbeat
      // landed in the next tick. Resetting the bar on a fresh upload
      // is handled explicitly by ``setDocId`` / ``reset``; the
      // reducer doesn't need to second-guess them with a ``percent
      // === 0`` escape hatch.
      //
      // The label always refreshes — that's the whole point of a
      // heartbeat: keep the user informed even when the bar can't
      // honestly move. Phase tag follows the same liberal rule.
      const nextProgress =
        percent >= state.ocrProgress ? percent : state.ocrProgress;
      return {
        ocrProgress: nextProgress,
        ocrProgressLabel: normalizeEngineTerms(label),
        ocrPhase: phase ?? state.ocrPhase,
        timeline:
          percent === 0 || percent === 100
            ? [...state.timeline.slice(-7), { id: `${Date.now()}-ocr`, ts: Date.now(), text: normalizeEngineTerms(label) }]
            : state.timeline,
      };
    }),

  addTimelineEvent: (text) =>
    set((state) => ({
      timeline: [...state.timeline.slice(-7), { id: `${Date.now()}-evt`, ts: Date.now(), text: normalizeEngineTerms(text) }],
    })),

  setTotalPages: (count) =>
    set((state) => {
      const pages = new Map(state.pages);
      for (let i = 1; i <= count; i++) {
        if (!pages.has(i)) {
          pages.set(i, { pageNum: i, confidence: 0, status: "queued" });
        }
      }
      return { totalPages: count, pages };
    }),

  updatePage: (pageNum, data) =>
    set((state) => {
      const pages = new Map(state.pages);
      const existing = pages.get(pageNum) || { pageNum, confidence: 0, status: "queued" as PageStatus };
      pages.set(pageNum, { ...existing, ...data });
      return { pages };
    }),

  setError: (error) => {
    persistDoc(null, null);
    set({ error, processingStatus: "error" });
  },

  reset: () => {
    persistDoc(null, null);
    set({
      docId: null,
      filename: null,
      totalPages: 0,
      processingStatus: "idle",
      ocrProgress: 0,
      ocrProgressLabel: "",
      ocrPhase: null,
      timeline: [],
      pages: new Map(),
      error: null,
    });
  },
}));
