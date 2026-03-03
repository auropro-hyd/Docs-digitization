"use client";

import { cn } from "@/lib/utils";
import { ShieldCheck, AlertTriangle, AlertCircle, Info } from "lucide-react";

interface Finding {
  rule_id: string;
  rule_category: string;
  severity: "critical" | "major" | "minor" | "observation";
  page_num: number | null;
  description: string;
  recommendation: string;
}

interface ComplianceDashboardProps {
  docId: string;
  score: number;
  findings: Finding[];
}

const SEVERITY_CONFIG = {
  critical: { icon: AlertCircle, color: "text-red-600 bg-red-50 border-red-200", label: "Critical" },
  major: { icon: AlertTriangle, color: "text-orange-600 bg-orange-50 border-orange-200", label: "Major" },
  minor: { icon: Info, color: "text-amber-600 bg-amber-50 border-amber-200", label: "Minor" },
  observation: { icon: Info, color: "text-blue-600 bg-blue-50 border-blue-200", label: "Observation" },
};

const CATEGORY_LABELS: Record<string, string> = {
  attributable: "Attributable",
  legible: "Legible",
  contemporaneous: "Contemporaneous",
  original: "Original",
  accurate: "Accurate",
  complete: "Complete",
  consistent: "Consistent",
  enduring: "Enduring",
  available: "Available",
  gmp: "GMP",
  checklist: "Checklist",
  sop: "SOP",
};

export function ComplianceDashboard({ docId, score, findings }: ComplianceDashboardProps) {
  const criticalCount = findings.filter((f) => f.severity === "critical").length;
  const majorCount = findings.filter((f) => f.severity === "major").length;
  const minorCount = findings.filter((f) => f.severity === "minor").length;
  const obsCount = findings.filter((f) => f.severity === "observation").length;

  const categories = [...new Set(findings.map((f) => f.rule_category))];

  return (
    <div className="space-y-6">
      {/* Score card */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-4">
          <div
            className={cn(
              "flex h-20 w-20 items-center justify-center rounded-full text-2xl font-bold",
              score >= 80
                ? "bg-green-50 text-green-700"
                : score >= 60
                  ? "bg-amber-50 text-amber-700"
                  : "bg-red-50 text-red-700"
            )}
          >
            {score}
          </div>
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Compliance Score</h2>
            <p className="text-xs font-medium text-gray-400">Document {docId}</p>
            <p className="text-sm text-gray-500">
              {findings.length} findings across {categories.length} categories
            </p>
          </div>
        </div>
      </div>

      {/* Severity breakdown */}
      <div className="grid grid-cols-4 gap-4">
        <SeverityCard severity="critical" count={criticalCount} />
        <SeverityCard severity="major" count={majorCount} />
        <SeverityCard severity="minor" count={minorCount} />
        <SeverityCard severity="observation" count={obsCount} />
      </div>

      {/* Category breakdown */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-semibold text-gray-900">Findings by Category</h3>
        <div className="space-y-2">
          {categories.map((cat) => {
            const catFindings = findings.filter((f) => f.rule_category === cat);
            return (
              <div key={cat} className="flex items-center justify-between rounded-lg bg-gray-50 px-4 py-2">
                <span className="text-sm font-medium text-gray-700">
                  {CATEGORY_LABELS[cat] || cat}
                </span>
                <span className="text-sm text-gray-500">{catFindings.length} findings</span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Findings list */}
      <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h3 className="mb-4 text-sm font-semibold text-gray-900">All Findings</h3>
        <div className="space-y-3">
          {findings.map((finding, idx) => (
            <FindingCard key={`${finding.rule_id}-${idx}`} finding={finding} />
          ))}
          {findings.length === 0 && (
            <div className="flex items-center gap-2 rounded-lg bg-green-50 p-4 text-green-700">
              <ShieldCheck className="h-5 w-5" />
              <span className="text-sm font-medium">No compliance issues found</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SeverityCard({ severity, count }: { severity: string; count: number }) {
  const config = SEVERITY_CONFIG[severity as keyof typeof SEVERITY_CONFIG];
  const Icon = config.icon;

  return (
    <div className={cn("rounded-lg border p-4", config.color)}>
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4" />
        <span className="text-xs font-medium">{config.label}</span>
      </div>
      <span className="mt-1 block text-2xl font-bold">{count}</span>
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const config = SEVERITY_CONFIG[finding.severity];
  const Icon = config.icon;

  return (
    <div className={cn("rounded-lg border p-4", config.color)}>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4" />
          <span className="text-xs font-semibold">{finding.rule_id}</span>
          <span className="rounded bg-white/50 px-1.5 py-0.5 text-xs">
            {CATEGORY_LABELS[finding.rule_category] || finding.rule_category}
          </span>
        </div>
        {finding.page_num && (
          <span className="text-xs opacity-75">Page {finding.page_num}</span>
        )}
      </div>
      <p className="mb-1 text-sm">{finding.description}</p>
      <p className="text-xs opacity-75">Recommendation: {finding.recommendation}</p>
    </div>
  );
}
