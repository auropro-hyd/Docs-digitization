"use client";

import { useCallback, useState, useEffect } from "react";
import { useDocumentStore } from "@/stores/document-store";
import { uploadDocument, processDocument } from "@/lib/api";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { Upload, FileText, X, Loader2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { formatFileSize } from "@/lib/utils";

const MAX_FILE_SIZE = 100 * 1024 * 1024;

export function DocumentUpload() {
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const { setDocId, setError: setStoreError, setProcessingStatus } = useDocumentStore();

  useEffect(() => {
    if (!isUploading) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isUploading]);

  const validateFile = useCallback((file: File): boolean => {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Invalid file type", { description: "Only PDF documents are supported" });
      return false;
    }
    if (file.size > MAX_FILE_SIZE) {
      toast.error("File too large", { description: "Maximum file size is 100MB" });
      return false;
    }
    return true;
  }, []);

  const handleFile = useCallback(
    async (file: File) => {
      if (!validateFile(file)) return;
      setSelectedFile(file);
    },
    [validateFile],
  );

  const startProcessing = useCallback(async () => {
    if (!selectedFile) return;
    setIsUploading(true);
    setUploadProgress(0);

    try {
      toast.loading("Uploading document...", { id: "upload" });

      const result = await uploadDocument(selectedFile);
      setUploadProgress(100);
      setDocId(result.doc_id, result.filename);

      toast.loading("Starting pipeline...", { id: "upload" });
      await processDocument(result.doc_id);
      setProcessingStatus("ingested");
      toast.success("Pipeline started", { id: "upload", description: "Document is being processed" });
      setSelectedFile(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      setStoreError(msg);
      toast.error("Upload failed", { id: "upload", description: msg });
    } finally {
      setIsUploading(false);
      setUploadProgress(0);
    }
  }, [selectedFile, setDocId, setStoreError, setProcessingStatus]);

  const cancelSelection = () => {
    setSelectedFile(null);
    setIsUploading(false);
    setUploadProgress(0);
  };

  if (selectedFile) {
    return (
      <Card>
        <CardContent className="p-6">
          <div className="flex items-start gap-4">
            <div className="size-12 rounded-xl bg-primary/10 flex items-center justify-center flex-shrink-0">
              <FileText className="size-6 text-primary" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground truncate">{selectedFile.name}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                PDF · {formatFileSize(selectedFile.size)}
              </p>
              {isUploading && (
                <div className="mt-3">
                  <Progress value={uploadProgress} className="h-1.5" />
                  <p className="text-xs text-muted-foreground mt-1">
                    {uploadProgress < 100 ? "Uploading..." : "Processing..."}
                  </p>
                </div>
              )}
            </div>
            {!isUploading && (
              <button
                onClick={cancelSelection}
                className="text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="size-4" />
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 mt-4">
            <Button onClick={startProcessing} disabled={isUploading} className="flex-1 sm:flex-none">
              {isUploading ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  Processing...
                </>
              ) : (
                <>
                  <Upload className="size-4 mr-2" />
                  Process Document
                </>
              )}
            </Button>
            {!isUploading && (
              <Button variant="ghost" onClick={cancelSelection}>
                Cancel
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0">
        <motion.div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            const file = e.dataTransfer.files[0];
            if (file) handleFile(file);
          }}
          className={`relative p-10 text-center transition-all duration-200 border-2 border-dashed rounded-xl m-1 ${
            isDragging
              ? "border-primary bg-primary/5"
              : "border-transparent hover:border-border"
          }`}
        >
          <div className="flex flex-col items-center">
            <div className={`size-12 rounded-xl flex items-center justify-center mb-4 transition-colors duration-200 ${
              isDragging ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
            }`}>
              <Upload className="size-6" />
            </div>

            <p className="text-sm font-medium text-foreground mb-1">
              Drop your PDF here
            </p>
            <p className="text-xs text-muted-foreground mb-4">
              or click to browse · PDF up to 100MB
            </p>

            <label>
              <Button variant="outline" size="sm" className="cursor-pointer" asChild>
                <span>Browse Files</span>
              </Button>
              <input
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFile(file);
                }}
              />
            </label>
          </div>
        </motion.div>
      </CardContent>
    </Card>
  );
}
