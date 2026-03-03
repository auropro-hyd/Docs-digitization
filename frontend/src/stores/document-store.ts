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

interface PageData {
  pageNum: number;
  confidence: number;
  status: PageStatus;
  markdown?: string;
  extraction?: Record<string, unknown>;
}

interface DocumentState {
  docId: string | null;
  filename: string | null;
  totalPages: number;
  processingStatus: ProcessingStatus;
  pages: Map<number, PageData>;
  error: string | null;

  setDocId: (docId: string, filename: string) => void;
  setProcessingStatus: (status: ProcessingStatus) => void;
  setTotalPages: (count: number) => void;
  updatePage: (pageNum: number, data: Partial<PageData>) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  docId: null,
  filename: null,
  totalPages: 0,
  processingStatus: "idle",
  pages: new Map(),
  error: null,

  setDocId: (docId, filename) => set({ docId, filename, processingStatus: "uploading" }),

  setProcessingStatus: (status) => set({ processingStatus: status }),

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

  setError: (error) => set({ error, processingStatus: "error" }),

  reset: () =>
    set({
      docId: null,
      filename: null,
      totalPages: 0,
      processingStatus: "idle",
      pages: new Map(),
      error: null,
    }),
}));
