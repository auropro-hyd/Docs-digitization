"use client";

import { DocumentUpload } from "@/components/upload/document-upload";
import { ProcessingDashboard } from "@/components/upload/processing-dashboard";
import { useDocumentStore } from "@/stores/document-store";

export default function HomePage() {
  const { docId, processingStatus } = useDocumentStore();

  return (
    <div className="mx-auto max-w-4xl px-6 py-12">
      <div className="mb-8 text-center">
        <h1 className="mb-2 text-3xl font-bold text-gray-900">Document Digitalization</h1>
        <p className="text-gray-500">
          Upload pharmaceutical documents for automated extraction, quality scoring, and compliance review.
        </p>
      </div>

      {!docId && <DocumentUpload />}

      {docId && <ProcessingDashboard />}

      {processingStatus === "completed" && (
        <div className="mt-6 flex justify-center gap-4">
          <a
            href={`/review?doc=${docId}`}
            className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
          >
            Review Extractions
          </a>
          <a
            href={`/compliance?doc=${docId}`}
            className="rounded-lg bg-gray-100 px-6 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-200"
          >
            Compliance Report
          </a>
        </div>
      )}
    </div>
  );
}
