"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText } from "lucide-react";
import { listDocuments } from "@/lib/api";

interface DocumentSummary {
  doc_id: string;
  filename?: string;
  status?: string;
  total_pages?: number;
}

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listDocuments()
      .then((data) => setDocuments(data.documents || []))
      .catch(() => setDocuments([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="mb-6 text-2xl font-bold text-gray-900">Documents</h1>

      {loading ? (
        <p className="text-gray-500">Loading...</p>
      ) : documents.length === 0 ? (
        <div className="rounded-xl border-2 border-dashed border-gray-300 p-12 text-center">
          <FileText className="mx-auto mb-4 h-12 w-12 text-gray-300" />
          <p className="text-gray-500">No documents yet. Upload one to get started.</p>
          <Link
            href="/"
            className="mt-4 inline-block rounded-lg bg-blue-600 px-6 py-2 text-sm font-medium text-white"
          >
            Upload Document
          </Link>
        </div>
      ) : (
        <div className="space-y-3">
          {documents.map((doc) => (
            <Link
              key={doc.doc_id}
              href={`/review?doc=${doc.doc_id}`}
              className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-4 transition-colors hover:bg-gray-50"
            >
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5 text-gray-400" />
                <div>
                  <p className="font-medium text-gray-900">{doc.filename || doc.doc_id}</p>
                  <p className="text-xs text-gray-500">{doc.total_pages || "?"} pages</p>
                </div>
              </div>
              <span className="text-sm text-gray-400">{doc.status || "unknown"}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
