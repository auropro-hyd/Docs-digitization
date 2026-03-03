"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ReviewInterface } from "@/components/review/review-interface";

function ReviewContent() {
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc");

  if (!docId) {
    return (
      <div className="flex h-[calc(100vh-60px)] items-center justify-center text-gray-500">
        Select a document to review
      </div>
    );
  }

  const samplePages = [
    { pageNum: 1, confidence: 0.45, markdown: "Sample extraction for page 1..." },
    { pageNum: 2, confidence: 0.72, markdown: "Sample extraction for page 2..." },
    { pageNum: 3, confidence: 0.95, markdown: "Sample extraction for page 3..." },
  ];

  return (
    <div className="h-[calc(100vh-60px)]">
      <ReviewInterface
        docId={docId}
        pages={samplePages}
        onApprove={(pageNum) => console.log("Approved page", pageNum)}
        onEdit={(pageNum, data) => console.log("Edited page", pageNum, data)}
        onFlag={(pageNum, reason) => console.log("Flagged page", pageNum, reason)}
      />
    </div>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading...</div>}>
      <ReviewContent />
    </Suspense>
  );
}
