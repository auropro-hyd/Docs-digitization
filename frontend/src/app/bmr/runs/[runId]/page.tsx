"use client";

import { use, useEffect, useState } from "react";
import { AlertCircle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { BpcrSectionsPanel } from "@/components/bmr/bpcr-sections-panel";
import { RunStageProgress } from "@/components/bmr/run-stage-progress";
import { useBmrRunEvents } from "@/hooks/useBmrRunEvents";
import { getBmrRun } from "@/lib/api";
import type { RunReport } from "@/types/bmr";

interface PageProps {
  params: Promise<{ runId: string }>;
}

// Minimal BMR run-detail page (Spec 007 follow-up).
//
// First UI surface for the BMR audit pipeline. Kept deliberately
// thin so it can be merged without churn: header card with run
// metadata, the BPCR-sections panel, and a findings summary. A
// fuller findings UI lives in a separate piece of work.
export default function BmrRunDetailPage({ params }: PageProps) {
  const { runId } = use(params);
  const [report, setReport] = useState<RunReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Subscribe to the live events stream so an in-flight run shows
  // stage-by-stage progress instead of just a static "running" badge.
  // The hook returns a small reduced state; on a finished run the
  // socket replays the snapshot at connect and then immediately
  // closes, which is fine — the UI also has the persisted report.
  const stageProgress = useBmrRunEvents(runId);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getBmrRun(runId)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // Re-fetch the report once the live stream sees a terminal event so
  // the findings + bpcr_sections panels show the just-landed data.
  useEffect(() => {
    if (!stageProgress.finished) return;
    getBmrRun(runId)
      .then(setReport)
      .catch(() => {});
  }, [stageProgress.finished, runId]);

  if (error) {
    return (
      <main className="container mx-auto p-6">
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-destructive">
              <AlertCircle className="size-4" /> Failed to load run
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm">{error}</CardContent>
        </Card>
      </main>
    );
  }

  if (!report) {
    return (
      <main className="container mx-auto p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading run {runId}…
        </div>
      </main>
    );
  }

  return (
    <main className="container mx-auto space-y-6 p-6">
      <RunHeader report={report} />
      <RunStageProgress
        progress={stageProgress}
        fallbackStatus={report.status}
      />
      <BpcrSectionsPanel sections={report.bpcr_sections} />
      <FindingsSummary report={report} />
    </main>
  );
}

function RunHeader({ report }: { report: RunReport }) {
  const statusVariant: "default" | "secondary" | "destructive" =
    report.status === "completed"
      ? "default"
      : report.status === "failed"
        ? "destructive"
        : "secondary";
  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="space-y-1">
          <CardTitle className="text-base">Run {report.run_id}</CardTitle>
          <div className="text-xs text-muted-foreground">
            package <code>{report.package_id}</code>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge variant={statusVariant}>{report.status}</Badge>
          <span className="text-xs text-muted-foreground">
            stage: {report.stage}
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
        <Stat label="Rules evaluated" value={report.rules_evaluated} />
        <Stat label="Rules loaded" value={report.rules_loaded} />
        <Stat
          label="Skipped (deprecated)"
          value={report.rules_skipped_deprecated}
        />
        <Stat label="Findings" value={report.summary.total} />
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono text-base">{value}</div>
    </div>
  );
}

function FindingsSummary({ report }: { report: RunReport }) {
  const byStatus = report.summary.by_status;
  const bySeverity = report.summary.by_severity;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Findings summary</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <DistributionRow label="By status" data={byStatus} />
        <DistributionRow label="By severity" data={bySeverity} />
      </CardContent>
    </Card>
  );
}

function DistributionRow({
  label,
  data,
}: {
  label: string;
  data: Record<string, number>;
}) {
  const entries = Object.entries(data).sort(([, a], [, b]) => b - a);
  if (entries.length === 0) {
    return (
      <div className="text-xs text-muted-foreground">{label}: (none)</div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">{label}:</span>
      {entries.map(([key, count]) => (
        <Badge key={key} variant="outline">
          {key}: {count}
        </Badge>
      ))}
    </div>
  );
}
