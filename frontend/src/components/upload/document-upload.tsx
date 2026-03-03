"use client";

import { useCallback, useState } from "react";
import { Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { uploadDocument } from "@/lib/api";
import { useDocumentStore } from "@/stores/document-store";

export function DocumentUpload() {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const { setDocId, setError } = useDocumentStore();

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setError("Only PDF files are supported");
        return;
      }

      setIsUploading(true);
      try {
        const result = await uploadDocument(file);
        setDocId(result.doc_id, result.filename);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setIsUploading(false);
      }
    },
    [setDocId, setError]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={handleDrop}
      className={cn(
        "relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 transition-colors",
        isDragging
          ? "border-blue-500 bg-blue-50"
          : "border-gray-300 bg-gray-50 hover:border-gray-400 hover:bg-gray-100",
        isUploading && "pointer-events-none opacity-60"
      )}
    >
      <div className="mb-4 rounded-full bg-white p-4 shadow-sm">
        {isUploading ? (
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-blue-500 border-t-transparent" />
        ) : (
          <Upload className="h-10 w-10 text-gray-400" />
        )}
      </div>

      <p className="mb-2 text-lg font-semibold text-gray-700">
        {isUploading ? "Uploading..." : "Drop your document here"}
      </p>
      <p className="mb-4 text-sm text-gray-500">or click to browse. PDF files only.</p>

      <label className="cursor-pointer rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700">
        Browse Files
        <input
          type="file"
          accept=".pdf"
          onChange={handleInputChange}
          className="hidden"
          disabled={isUploading}
        />
      </label>
    </div>
  );
}
