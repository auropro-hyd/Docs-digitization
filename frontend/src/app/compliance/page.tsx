"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ComplianceDashboard } from "@/components/compliance/compliance-dashboard";

function ComplianceContent() {
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc");

  if (!docId) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-12 text-center text-gray-500">
        Select a document to view compliance report
      </div>
    );
  }

  const sampleFindings = [
    {
      rule_id: "ALCOA-1",
      rule_category: "attributable",
      severity: "major" as const,
      page_num: 5,
      description: "Entry on page 5 lacks signature and date attribution",
      recommendation: "Ensure all entries are signed and dated by the responsible person",
    },
    {
      rule_id: "GMP-P12",
      rule_category: "gmp",
      severity: "minor" as const,
      page_num: 12,
      description: "Equipment ID format inconsistent with standard format",
      recommendation: "Verify equipment ID follows SOP naming convention",
    },
  ];

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="mb-6 text-2xl font-bold text-gray-900">Compliance Report</h1>
      <ComplianceDashboard docId={docId} score={85} findings={sampleFindings} />
    </div>
  );
}

export default function CompliancePage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading...</div>}>
      <ComplianceContent />
    </Suspense>
  );
}
