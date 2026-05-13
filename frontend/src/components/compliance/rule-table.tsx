"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertCircle, CheckCircle, HelpCircle, Loader2, RefreshCw } from "lucide-react";
import { getComplianceReportRows } from "@/lib/api";
import { RuleRow } from "./rule-row";
import type {
  DrawerFinding,
  ScoresUpdate,
} from "./rule-expand-drawer";
import type {
  ComplianceKind,
  ReportDocument,
  ReportRow as ReportRowT,
} from "@/types/compliance";

interface RuleTableProps {
  docId: string;
  /** Per-finding state lifted from the parent so HITL reviews
   * propagate back through the same overlay the legacy table used. */
  findings: DrawerFinding[];
  /** When non-null, scopes the table (and the underlying /report-rows
   * request) to a single agent. */
  agent?: string;
  onFindingUpdate: (findingId: string, updates: Partial<DrawerFinding>) => void;
  onScoresUpdate?: (scores: ScoresUpdate) => void;
}

const KIND_LABEL: Record<ComplianceKind, string> = {
  compliant: "Compliant",
  action_required: "Action Required",
  needs_attention: "Needs Attention",
};

const KIND_ICON: Record<ComplianceKind, typeof CheckCircle> = {
  compliant: CheckCircle,
  action_required: AlertCircle,
  needs_attention: HelpCircle,
};

/** Top-level rule table for the on-screen compliance view (Spec 008).
 * Same source-of-truth as the exported PDF — the backend's
 * ``build_report_document()`` derives both shapes from the stored
 * ``ComplianceReport``. The on-screen and exported tables therefore
 * stay in lock-step by construction. */
export function RuleTable({
  docId,
  findings,
  agent,
  onFindingUpdate,
  onScoresUpdate,
}: RuleTableProps) {
  const [doc, setDoc] = useState<ReportDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<"all" | ComplianceKind>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await getComplianceReportRows(docId, { agent });
      setDoc(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report rows");
    } finally {
      setLoading(false);
    }
  }, [docId, agent]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Refetch when a HITL review changes finding state — the row
  // bucket may flip (e.g. user-approves a non-compliant finding
  // and the row moves from Action Required to Compliant).
  // findings.length doesn't change; we key on a content hash.
  const findingsKey = useMemo(
    () => findings.map((f) => `${f.finding_id}:${f.hitl_status}:${f.resolved}`).join("|"),
    [findings],
  );
  useEffect(() => {
    if (!doc) return;
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findingsKey]);

  const findingsByRule = useMemo(() => {
    const m = new Map<string, DrawerFinding[]>();
    for (const f of findings) {
      const list = m.get(f.rule_id) ?? [];
      list.push(f);
      m.set(f.rule_id, list);
    }
    return m;
  }, [findings]);

  const filteredRows: ReportRowT[] = useMemo(() => {
    if (!doc) return [];
    if (kindFilter === "all") return doc.rows;
    return doc.rows.filter((r) => r.compliance_kind === kindFilter);
  }, [doc, kindFilter]);

  const showAgentColumn = useMemo(() => {
    if (!doc) return false;
    return new Set(doc.rows.map((r) => r.agent)).size > 1;
  }, [doc]);

  if (loading && !doc) {
    return (
      <Card>
        <CardContent className="py-8 flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading rule table…
        </CardContent>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <CardContent className="py-6 text-sm text-destructive">
          {error}{" "}
          <Button variant="link" size="sm" onClick={() => void refresh()} className="text-xs">
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }
  if (!doc) return null;

  return (
    <Card>
      {/* Metadata block — mirrors the exported PDF's header band. */}
      <CardContent className="px-5 py-4 border-b space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              {doc.header.product_name}
            </p>
            <h3 className="text-base font-semibold text-foreground">{doc.header.title}</h3>
            {doc.header.is_draft && (
              <Badge variant="outline" className="mt-1 text-[10px] border-warning/40 text-warning">
                Document is Draft
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs"
              onClick={() => void refresh()}
              disabled={loading}
            >
              <RefreshCw className={`size-3.5 mr-1 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </div>

        {doc.header.metadata_rows.length > 0 && (
          <dl className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1 text-xs">
            {doc.header.metadata_rows.map(([label, value]) => (
              <div key={label} className="flex gap-2">
                <dt className="text-muted-foreground">{label}:</dt>
                <dd className="text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>

      {/* Filter strip */}
      <CardContent className="px-5 py-3 border-b flex flex-wrap items-center gap-3 text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground">Compliance:</span>
          <Select
            value={kindFilter}
            onValueChange={(v) => setKindFilter(v as typeof kindFilter)}
          >
            <SelectTrigger className="h-7 w-44 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All rows</SelectItem>
              <SelectItem value="action_required">Action Required</SelectItem>
              <SelectItem value="needs_attention">Needs Attention</SelectItem>
              <SelectItem value="compliant">Compliant</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          {(["action_required", "needs_attention", "compliant"] as ComplianceKind[]).map((k) => {
            const Icon = KIND_ICON[k];
            const count =
              k === "action_required"
                ? doc.stats.action_required_count
                : k === "needs_attention"
                  ? doc.stats.needs_attention_count
                  : doc.stats.compliant_count;
            return (
              <Badge key={k} variant="outline" className="text-[10px] gap-1">
                <Icon className="size-3" /> {count} {KIND_LABEL[k]}
              </Badge>
            );
          })}
        </div>
      </CardContent>

      {/* Table */}
      {filteredRows.length === 0 ? (
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          No rules match the current filter.
        </CardContent>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted/40 text-left">
                <th className="py-2 px-3 text-[11px] font-medium text-muted-foreground">Question</th>
                <th className="py-2 px-3 text-[11px] font-medium text-muted-foreground">Compliance</th>
                <th className="py-2 px-3 text-[11px] font-medium text-muted-foreground">Evidence From Document</th>
                <th className="py-2 px-3 text-[11px] font-medium text-muted-foreground">Detailed Evidence</th>
                <th className="py-2 px-3 text-[11px] font-medium text-muted-foreground">Mitigation</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => (
                <RuleRow
                  key={`${row.agent}:${row.rule_id}`}
                  row={row}
                  findings={findingsByRule.get(row.rule_id) ?? []}
                  docId={docId}
                  showAgentColumn={showAgentColumn}
                  onFindingUpdate={onFindingUpdate}
                  onScoresUpdate={onScoresUpdate}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
