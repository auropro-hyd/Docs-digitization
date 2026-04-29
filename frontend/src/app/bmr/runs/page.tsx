"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, ArrowRight, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listBmrRuns } from "@/lib/api";
import type { RunListItem, RunStatus } from "@/types/bmr";

// BMR run-list landing page. Lists every persisted run with enough
// metadata to drive a click-through into the detail view at
// ``/bmr/runs/[runId]`` — newest first. Backend pre-sorts by
// started_at so the UI doesn't need its own sort step.
export default function BmrRunsListPage() {
  const [items, setItems] = useState<RunListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    listBmrRuns()
      .then((r) => {
        if (!cancelled) setItems(r.runs);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <main className="container mx-auto p-6">
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-destructive">
              <AlertCircle className="size-4" /> Failed to load runs
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm">{error}</CardContent>
        </Card>
      </main>
    );
  }

  if (items === null) {
    return (
      <main className="container mx-auto p-6">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading runs…
        </div>
      </main>
    );
  }

  if (items.length === 0) {
    return (
      <main className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">BMR runs</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            No runs yet. Trigger one via{" "}
            <code>POST /api/bmr/runs</code>.
          </CardContent>
        </Card>
      </main>
    );
  }

  return (
    <main className="container mx-auto space-y-4 p-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            BMR runs{" "}
            <span className="text-xs font-normal text-muted-foreground">
              ({items.length})
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Run</TableHead>
                <TableHead>Package</TableHead>
                <TableHead className="w-32">Status</TableHead>
                <TableHead className="w-24 text-right">Findings</TableHead>
                <TableHead className="w-28 text-right">Sections</TableHead>
                <TableHead>Started</TableHead>
                <TableHead className="w-10"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.run_id} className="hover:bg-muted/50">
                  <TableCell>
                    <Link
                      href={`/bmr/runs/${row.run_id}`}
                      className="font-mono text-xs hover:underline"
                    >
                      {row.run_id.slice(0, 12)}…
                    </Link>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {row.package_id?.slice(0, 12) ?? "—"}…
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={row.status ?? null} />
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.total_findings ?? "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {row.bpcr_section_count ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatStartedAt(row.started_at)}
                  </TableCell>
                  <TableCell>
                    <Link
                      href={`/bmr/runs/${row.run_id}`}
                      className="text-muted-foreground hover:text-foreground"
                      aria-label={`Open run ${row.run_id}`}
                    >
                      <ArrowRight className="size-4" />
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </main>
  );
}

function StatusBadge({ status }: { status: RunStatus | null }) {
  if (!status) {
    return <Badge variant="outline">unknown</Badge>;
  }
  const variant: "default" | "secondary" | "destructive" | "outline" =
    status === "completed"
      ? "default"
      : status === "failed"
        ? "destructive"
        : status === "running" || status === "pending"
          ? "secondary"
          : "outline";
  return <Badge variant={variant}>{status}</Badge>;
}

function formatStartedAt(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    // Show date + HH:MM in the viewer's locale; the run_id detail page
    // shows the full ISO string for absolute precision.
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
