"use client";

import { Activity, Check, Circle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import type { RunStage, RunStatus } from "@/types/bmr";
import type { StageProgress } from "@/hooks/useBmrRunEvents";

// Pipeline order mirrors backend ``app/bmr/workflow/stages.py::_STAGE_ORDER``.
// Tested via the wire contract (``stage_index`` field on each event)
// rather than coupled at the type level so a backend renaming would
// not silently shift the UI's display.
const STAGE_LABELS: { id: RunStage; label: string }[] = [
  { id: "ingest", label: "Ingest" },
  { id: "legibility_and_classification", label: "Legibility & classify" },
  { id: "extraction", label: "Extraction" },
  { id: "compliance", label: "Compliance" },
  { id: "report", label: "Report" },
];

interface RunStageProgressProps {
  progress: StageProgress;
  // Falls back to the report's persisted status when the live event
  // stream hasn't seen a terminal lifecycle event yet (typical when a
  // user opens a long-finished run — there are no live events to
  // replay, only the snapshot).
  fallbackStatus: RunStatus | null;
}

// Live stage progress driven by the events WebSocket. Renders a 5-step
// timeline plus a coarse percent bar so the user knows the pipeline is
// alive even when an individual stage is taking a while. Replaces what
// previously was just a single status badge on the detail page.
export function RunStageProgress({ progress, fallbackStatus }: RunStageProgressProps) {
  const finished =
    progress.finished ||
    fallbackStatus === "completed" ||
    fallbackStatus === "failed";
  const liveIndex = progress.index;
  // When the run is already finished and the live stream gave us no
  // index (snapshot doesn't carry one), assume all stages finished
  // so the timeline doesn't render every row as "pending".
  const effectiveIndex =
    liveIndex === 0 && finished ? STAGE_LABELS.length : liveIndex;
  const percent = Math.round((effectiveIndex / STAGE_LABELS.length) * 100);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between text-base">
          <span>Pipeline progress</span>
          <span className="text-xs font-normal text-muted-foreground tabular-nums">
            {percent}%
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Progress value={percent} className="h-1.5" />
        <ol className="space-y-1.5">
          {STAGE_LABELS.map((s, i) => {
            const stageNum = i + 1;
            const status = stageNum < effectiveIndex
              ? "done"
              : stageNum === effectiveIndex
                ? finished ? "done" : "active"
                : "pending";
            return (
              <li
                key={s.id}
                className={cn(
                  "flex items-center gap-2 text-sm",
                  status === "pending" && "text-muted-foreground/70",
                  status === "active" && "text-foreground",
                  status === "done" && "text-muted-foreground",
                )}
              >
                <StageIcon status={status} />
                <span className={status === "active" ? "font-medium" : undefined}>
                  {s.label}
                </span>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

function StageIcon({ status }: { status: "done" | "active" | "pending" }) {
  if (status === "done") {
    return <Check className="size-4 text-success" />;
  }
  if (status === "active") {
    return <Activity className="size-4 animate-pulse text-primary" />;
  }
  return <Circle className="size-4 text-muted-foreground/50" />;
}
