"use client";

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
import type { BpcrSectionRow } from "@/types/bmr";

interface BpcrSectionsPanelProps {
  sections: BpcrSectionRow[];
}

// Render the per-page BPCR section assignment from a RunReport.
// Empty input is treated as "detection didn't run" (the operator's
// signal that something is mis-wired) — we show an explicit empty
// state rather than just rendering nothing, so a reviewer doesn't
// silently miss the missing data.
export function BpcrSectionsPanel({ sections }: BpcrSectionsPanelProps) {
  if (sections.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">BPCR sections</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          No section assignments on this run. Either the package has no BPCR
          document, section detection was disabled
          (<code>AT_BMR__BPCR_SECTIONS_ENABLED=false</code>), the detector
          failed, or no page carried any content the detector could read.
        </CardContent>
      </Card>
    );
  }

  // Group rows by doc_id so multi-BPCR packages render one mini-table per
  // document. Sort within each doc by page_index ascending.
  const grouped = new Map<string, BpcrSectionRow[]>();
  for (const row of sections) {
    const list = grouped.get(row.doc_id) ?? [];
    list.push(row);
    grouped.set(row.doc_id, list);
  }
  for (const list of grouped.values()) {
    list.sort((a, b) => a.page_index - b.page_index);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          BPCR sections{" "}
          <span className="text-xs font-normal text-muted-foreground">
            ({sections.length} page
            {sections.length === 1 ? "" : "s"} tagged)
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {Array.from(grouped.entries()).map(([docId, rows]) => (
          <div key={docId} className="space-y-2">
            <div className="text-xs text-muted-foreground">
              doc_id: <code>{docId}</code>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-16">Page</TableHead>
                  <TableHead>Section</TableHead>
                  <TableHead className="w-28">Confidence</TableHead>
                  <TableHead>Detection method</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <SectionRow
                    key={`${row.doc_id}:${row.page_index}`}
                    row={row}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SectionRow({ row }: { row: BpcrSectionRow }) {
  const isUnsectioned = row.section_id === "unsectioned";
  return (
    <TableRow>
      <TableCell className="font-mono text-sm">{row.page_index}</TableCell>
      <TableCell>
        {isUnsectioned ? (
          <span className="text-muted-foreground">unsectioned</span>
        ) : (
          <div className="flex flex-col">
            <span className="font-medium">
              {row.display_name ?? row.section_id}
            </span>
            <code className="text-xs text-muted-foreground">
              {row.section_id}
            </code>
          </div>
        )}
      </TableCell>
      <TableCell>
        {typeof row.confidence === "number" ? (
          <ConfidenceBadge value={row.confidence} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-xs">
        {row.detection_method ? (
          <code>{row.detection_method}</code>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}

function ConfidenceBadge({ value }: { value: number }) {
  // Buckets match the detector's _CONF_* constants in
  // backend/app/bmr/capabilities/bpcr_section_detect.py — strong matches
  // (primary regex on top-of-page) hit 1.0; weaker alias-only matches
  // floor at 0.4. We don't try to re-derive the exact thresholds here;
  // bucketing is deliberately coarse so reviewers don't read precision
  // into a heuristic.
  const variant: "default" | "secondary" | "outline" =
    value >= 0.85 ? "default" : value >= 0.6 ? "secondary" : "outline";
  return <Badge variant={variant}>{value.toFixed(2)}</Badge>;
}
