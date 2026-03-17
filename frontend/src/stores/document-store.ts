import { create } from "zustand";

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

interface DocumentState {
  docId: string | null;
  filename: string | null;
  totalPages: number;
  processingStatus: ProcessingStatus;
  ocrProgress: number;
  ocrProgressLabel: string;
  pages: Map<number, PageData>;
  error: string | null;

  setDocId: (docId: string, filename: string) => void;
  setProcessingStatus: (status: ProcessingStatus) => void;
  setOcrProgress: (percent: number, label: string) => void;
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
  pages: new Map(),
  error: null,

  setDocId: (docId, filename) => {
    persistDoc(docId, filename);
    set({ docId, filename, processingStatus: "uploading", error: null, pages: new Map(), totalPages: 0, ocrProgress: 0, ocrProgressLabel: "" });
  },

  setProcessingStatus: (status) => {
    if (TERMINAL_STATUSES.has(status)) {
      persistDoc(null, null);
    }
    set({ processingStatus: status });
  },

  setOcrProgress: (percent, label) => set({ ocrProgress: percent, ocrProgressLabel: label }),

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
      pages: new Map(),
      error: null,
    });
  },
}));
