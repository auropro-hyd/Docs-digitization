"use client";

import { Fragment, useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  getSegmentation,
  updateSegmentation,
  triggerSegmentation,
} from "@/lib/api";
import {
  Loader2,
  FileStack,
  RefreshCw,
  Save,
  Pencil,
  X,
  Check,
  ChevronDown,
  ChevronRight,
  Layers,
  AlertTriangle,
  AlertCircle,
  Info,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

// One row inside a BPCR section's nested ``sub_sections`` array.
// Shape mirrors backend ``app/compliance/models.py::BpcrSubSection``
// (PR #22). Optional fields stay optional because the detector
// emits empty strings / zeros for unsectioned spans rather than
// fabricating display data.
interface BpcrSubSection {
  section_id: string;
  display_name: string;
  page_index: number;
  confidence: number;
  detection_method: string;
}

interface Section {
  section_id: string;
  name: string;
  section_type: string;
  start_page: number;
  end_page: number;
  description: string;
  // Spec 007 — populated by the BPCR detector for sections whose
  // ``section_type`` matches a BPCR hint. Empty for non-BPCR
  // sections (raw_material_request_and_issue, sample_set_method,
  // …) and for BPCRs where detection produced no hits.
  sub_sections?: BpcrSubSection[];
}

// Mirrors backend ``SegmentationIssueDict`` shape from Spec 011 /
// FR-014. Each issue carries ``kind`` + ``message`` plus the
// affected section IDs / page range so the UI can scroll or
// highlight the matching row.
interface ValidationIssue {
  kind: string;
  message: string;
  section_ids: string[];
  page_range: [number, number] | null;
}

interface Segmentation {
  sections: Section[];
  document_type: string;
  confidence: number;
  // Optional so segmentations written before Spec 011 still load
  // — the new field defaults to ``[]`` server-side.
  validation_issues?: ValidationIssue[];
}

// Visual taxonomy for the issue kinds — derived from
// ``segmentation.<kind>`` events emitted by the backend
// post-processes. Severity controls icon + colour; the badge in
// the summary header counts each bucket.
type IssueSeverity = "error" | "warning" | "info";

const ISSUE_TAXONOMY: Record<
  string,
  { severity: IssueSeverity; label: string }
> = {
  // Coverage / geometry — high severity (compliance pipeline may
  // silently skip pages otherwise).
  overlap: { severity: "warning", label: "Overlap" },
  gap: { severity: "warning", label: "Coverage gap" },
  output_truncated: { severity: "error", label: "LLM output truncated" },
  retry_exhausted: { severity: "error", label: "Retry exhausted" },
  // Structural minimums — high severity for batch_record etc.
  missing_required_section: {
    severity: "error",
    label: "Missing required section",
  },
  // Vocabulary / type drift — info.
  unknown_document_type: { severity: "warning", label: "Unknown document type" },
  unknown_section_type: { severity: "warning", label: "Unknown section type" },
  type_mismatch: { severity: "warning", label: "Type mismatch" },
  no_kv_evidence: { severity: "info", label: "No KV evidence" },
  // Page-header boundary reconciliation — info (LLM was helped).
  header_boundary_merged: { severity: "info", label: "Boundary merged" },
  header_boundary_split: { severity: "info", label: "Boundary split" },
  header_low_confidence: { severity: "info", label: "Header low confidence" },
  boundary_conflict: { severity: "warning", label: "Header conflict" },
  // HITL preservation — warning.
  override_orphaned: { severity: "warning", label: "Override orphaned" },
};

function _issueConfig(kind: string): { severity: IssueSeverity; label: string } {
  return (
    ISSUE_TAXONOMY[kind] || {
      severity: "info",
      label: kind.replace(/_/g, " "),
    }
  );
}

interface SegmentationEditorProps {
  docId: string;
}

export function SegmentationEditor({ docId }: SegmentationEditorProps) {
  const [seg, setSeg] = useState<Segmentation | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resegmenting, setResegmenting] = useState(false);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Section | null>(null);
  // Track which BPCR section rows have their sub-section breakdown
  // expanded. Default: any section with non-empty ``sub_sections`` is
  // collapsed on first render so the table stays compact for
  // multi-document packets (the user's real doc has 15 top-level
  // sections, only 1 of which is the BPCR with sub-sections).
  const [expandedSubSections, setExpandedSubSections] = useState<Set<number>>(
    () => new Set(),
  );

  const toggleSubSections = useCallback((idx: number) => {
    setExpandedSubSections((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getSegmentation(docId);
      setSeg(data);
    } catch {
      setSeg(null);
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => { load(); }, [load]);

  const handleSave = async () => {
    if (!seg) return;
    setSaving(true);
    try {
      await updateSegmentation(docId, seg as unknown as Record<string, unknown>);
      toast.success("Segmentation saved");
    } catch {
      toast.error("Failed to save segmentation");
    } finally {
      setSaving(false);
    }
  };

  const handleResegment = async () => {
    setResegmenting(true);
    try {
      await triggerSegmentation(docId);
      toast.info("Re-segmentation started...");
      setTimeout(load, 5000);
    } catch {
      toast.error("Failed to trigger re-segmentation");
    } finally {
      setResegmenting(false);
    }
  };

  const startEdit = (idx: number) => {
    if (!seg) return;
    setEditingIdx(idx);
    setEditDraft({ ...seg.sections[idx] });
  };

  const cancelEdit = () => {
    setEditingIdx(null);
    setEditDraft(null);
  };

  const saveEdit = () => {
    if (editingIdx === null || !editDraft || !seg) return;
    const updated = [...seg.sections];
    updated[editingIdx] = editDraft;
    setSeg({ ...seg, sections: updated });
    setEditingIdx(null);
    setEditDraft(null);
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-6 flex items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading segmentation...
        </CardContent>
      </Card>
    );
  }

  if (!seg) {
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <FileStack className="size-8 mx-auto text-muted-foreground mb-2" />
          <p className="text-sm text-muted-foreground mb-3">
            No segmentation available. Run a compliance audit to auto-detect document sections.
          </p>
          <Button size="sm" variant="outline" onClick={handleResegment} disabled={resegmenting}>
            {resegmenting ? <Loader2 className="size-3 mr-1 animate-spin" /> : <RefreshCw className="size-3 mr-1" />}
            Run Segmentation
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">Document Sections</CardTitle>
            <Badge variant="secondary" className="text-[10px]">
              {seg.sections.length} sections
            </Badge>
            {seg.confidence > 0 && (
              <Badge variant="outline" className="text-[10px]">
                {Math.round(seg.confidence * 100)}% confidence
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant="ghost" onClick={handleResegment} disabled={resegmenting}>
              {resegmenting ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />}
            </Button>
            <Button size="sm" variant="outline" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="size-3 mr-1 animate-spin" /> : <Save className="size-3 mr-1" />}
              Save
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <ValidationIssuesPanel
          issues={seg.validation_issues || []}
          onSectionSelect={(id) => {
            // Scroll to the matching section row when the user
            // clicks a chip in the issues panel. Falls back to no-op
            // when the id is empty / not rendered.
            if (!id) return;
            const el = document.getElementById(`seg-row-${id}`);
            if (el) {
              el.scrollIntoView({ behavior: "smooth", block: "center" });
              el.classList.add("ring-2", "ring-warning");
              window.setTimeout(
                () => el.classList.remove("ring-2", "ring-warning"),
                1500,
              );
            }
          }}
        />
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted/50 text-xs text-muted-foreground">
                <th className="text-left py-2 px-3 font-medium">Section</th>
                <th className="text-left py-2 px-3 font-medium">Type</th>
                <th className="text-left py-2 px-3 font-medium">Pages</th>
                <th className="text-left py-2 px-3 font-medium hidden md:table-cell">Description</th>
                <th className="w-16 py-2 px-3"></th>
              </tr>
            </thead>
            <tbody>
              {seg.sections.map((section, idx) => {
                const subCount = section.sub_sections?.length ?? 0;
                const isExpanded = expandedSubSections.has(idx);
                // Highlight rows that one of the post-process
                // validators flagged — operator's first cue that
                // the segmentation needs review.
                const issueKinds = (seg.validation_issues || []).filter(
                  (i) => i.section_ids.includes(section.section_id),
                );
                const rowSeverity = issueKinds.length > 0
                  ? issueKinds.reduce<IssueSeverity>(
                      (acc, i) => {
                        const sev = _issueConfig(i.kind).severity;
                        if (acc === "error" || sev === "error") return "error";
                        if (acc === "warning" || sev === "warning") return "warning";
                        return "info";
                      },
                      "info",
                    )
                  : null;
                return (
                // Section IDs can repeat when the same canonical
                // section spans non-contiguous page ranges; suffix
                // the index so React's reconciler doesn't fold the
                // duplicates and drop edits across re-renders.
                <Fragment key={`${section.section_id}-${idx}`}>
                <tr
                  id={`seg-row-${section.section_id}`}
                  className={cn(
                    "border-t transition-colors",
                    editingIdx === idx && "bg-primary/5",
                    idx % 2 === 0 && editingIdx !== idx && "bg-muted/20",
                    rowSeverity === "error" && "ring-1 ring-inset ring-destructive/30",
                    rowSeverity === "warning" && "ring-1 ring-inset ring-warning/30",
                  )}
                  title={
                    issueKinds.length > 0
                      ? `${issueKinds.length} validation issue${issueKinds.length === 1 ? "" : "s"} — see panel above`
                      : undefined
                  }
                >
                  {editingIdx === idx && editDraft ? (
                    <>
                      <td className="py-2 px-3">
                        <Input
                          value={editDraft.name}
                          onChange={(e) => setEditDraft({ ...editDraft, name: e.target.value })}
                          className="h-7 text-xs"
                        />
                      </td>
                      <td className="py-2 px-3">
                        <Input
                          value={editDraft.section_type}
                          onChange={(e) => setEditDraft({ ...editDraft, section_type: e.target.value })}
                          className="h-7 text-xs"
                        />
                      </td>
                      <td className="py-2 px-3">
                        <div className="flex items-center gap-1">
                          <Input
                            type="number"
                            value={editDraft.start_page}
                            onChange={(e) => setEditDraft({ ...editDraft, start_page: Number(e.target.value) })}
                            className="h-7 text-xs w-14"
                          />
                          <span className="text-muted-foreground">–</span>
                          <Input
                            type="number"
                            value={editDraft.end_page}
                            onChange={(e) => setEditDraft({ ...editDraft, end_page: Number(e.target.value) })}
                            className="h-7 text-xs w-14"
                          />
                        </div>
                      </td>
                      <td className="py-2 px-3 hidden md:table-cell">
                        <Input
                          value={editDraft.description}
                          onChange={(e) => setEditDraft({ ...editDraft, description: e.target.value })}
                          className="h-7 text-xs"
                        />
                      </td>
                      <td className="py-2 px-3">
                        <div className="flex items-center gap-1">
                          <button onClick={saveEdit} className="size-6 rounded hover:bg-success/10 flex items-center justify-center">
                            <Check className="size-3.5 text-success" />
                          </button>
                          <button onClick={cancelEdit} className="size-6 rounded hover:bg-destructive/10 flex items-center justify-center">
                            <X className="size-3.5 text-destructive" />
                          </button>
                        </div>
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="py-2 px-3 font-medium">
                        <div className="flex items-center gap-1.5">
                          {subCount > 0 && (
                            <button
                              onClick={() => toggleSubSections(idx)}
                              className="size-5 rounded hover:bg-muted flex items-center justify-center -ml-1"
                              aria-label={isExpanded ? "Collapse sub-sections" : "Expand sub-sections"}
                            >
                              {isExpanded ? (
                                <ChevronDown className="size-3.5 text-muted-foreground" />
                              ) : (
                                <ChevronRight className="size-3.5 text-muted-foreground" />
                              )}
                            </button>
                          )}
                          <span>{section.name}</span>
                          {subCount > 0 && (
                            <Badge
                              variant="outline"
                              className="text-[9px] px-1 py-0 h-4 gap-0.5 cursor-pointer"
                              onClick={() => toggleSubSections(idx)}
                            >
                              <Layers className="size-2.5" />
                              {distinctSubSectionCount(section.sub_sections)} sub
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="py-2 px-3">
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0 font-mono">
                          {section.section_type}
                        </Badge>
                      </td>
                      <td className="py-2 px-3 tabular-nums text-muted-foreground">
                        {section.start_page}–{section.end_page}
                      </td>
                      <td className="py-2 px-3 text-muted-foreground text-xs truncate max-w-[200px] hidden md:table-cell">
                        {section.description || "—"}
                      </td>
                      <td className="py-2 px-3">
                        <button
                          onClick={() => startEdit(idx)}
                          className="size-6 rounded hover:bg-muted flex items-center justify-center"
                        >
                          <Pencil className="size-3 text-muted-foreground" />
                        </button>
                      </td>
                    </>
                  )}
                </tr>
                {isExpanded && section.sub_sections && section.sub_sections.length > 0 && (
                  <tr className="border-t bg-muted/10">
                    <td colSpan={5} className="py-2 px-3">
                      <SubSectionBreakdown
                        subSections={section.sub_sections}
                        parentRange={[section.start_page, section.end_page]}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}


// ── BPCR sub-section breakdown ────────────────────────────────────────────
//
// The detector emits one ``BpcrSubSection`` per page covered by a span,
// so a 35-page BPCR has 35 rows. Reviewers don't want a flat 35-row
// dump; they want to see the *distinct* sections (cover_page,
// material_dispensing, yield_calculation, …) with their page ranges.
// This component groups by ``section_id`` and renders one row per
// distinct sub-section, preserving the order the detector emitted
// them (which mirrors document order).

function distinctSubSectionCount(sub: BpcrSubSection[] | undefined): number {
  if (!sub || sub.length === 0) return 0;
  const ids = new Set<string>();
  for (const s of sub) {
    if (s.section_id && s.section_id !== "unsectioned") {
      ids.add(s.section_id);
    }
  }
  return ids.size;
}


interface SubSectionGroup {
  section_id: string;
  display_name: string;
  start_page: number;
  end_page: number;
  page_count: number;
  best_confidence: number;
  detection_method: string;
}


function groupSubSections(sub: BpcrSubSection[]): SubSectionGroup[] {
  // Walk the rows in order and emit one group per contiguous run of
  // the same section_id. The detector already merges adjacent spans
  // so this is mostly a one-row-per-distinct-section operation, but
  // doing it client-side guarantees correct rendering even when the
  // backend emits a non-merged list (e.g. legacy data, hand edits).
  const groups: SubSectionGroup[] = [];
  for (const row of sub) {
    const last = groups[groups.length - 1];
    if (last && last.section_id === row.section_id && last.end_page + 1 === row.page_index) {
      last.end_page = row.page_index;
      last.page_count += 1;
      last.best_confidence = Math.max(last.best_confidence, row.confidence);
      continue;
    }
    groups.push({
      section_id: row.section_id,
      display_name: row.display_name || row.section_id,
      start_page: row.page_index,
      end_page: row.page_index,
      page_count: 1,
      best_confidence: row.confidence,
      detection_method: row.detection_method,
    });
  }
  return groups;
}


function SubSectionBreakdown({
  subSections,
  parentRange,
}: {
  subSections: BpcrSubSection[];
  parentRange: [number, number];
}) {
  const groups = groupSubSections(subSections);
  // Filter out the "unsectioned" sentinel from the visible list — it's
  // useful as a debugging signal but not informative to a reviewer.
  // Surface a single "X pages unsectioned" footer instead.
  const real = groups.filter((g) => g.section_id !== "unsectioned");
  const unsectionedPages = groups
    .filter((g) => g.section_id === "unsectioned")
    .reduce((sum, g) => sum + g.page_count, 0);
  const [parentStart, parentEnd] = parentRange;

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
        BPCR sub-sections (pages {parentStart}–{parentEnd})
      </div>
      {real.length === 0 ? (
        <div className="text-xs text-muted-foreground italic">
          Detector ran but produced no canonical sub-section matches.
          The 13-section spec at <code>config/bmr/pilot/bpcr-section-spec.yaml</code>{" "}
          may need an alias for this document&apos;s heading wording.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
          {real.map((g) => (
            <div
              key={`${g.section_id}-${g.start_page}`}
              className="border rounded px-2 py-1.5 bg-background flex items-center gap-2"
            >
              <Badge
                variant="outline"
                className="text-[9px] px-1 py-0 h-4 font-mono shrink-0"
              >
                {g.section_id}
              </Badge>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium truncate">
                  {g.display_name}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  pp. {g.start_page}
                  {g.end_page !== g.start_page ? `–${g.end_page}` : ""}
                  {" · "}
                  {Math.round(g.best_confidence * 100)}% conf
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      {unsectionedPages > 0 && (
        <div className="text-[10px] text-muted-foreground">
          + {unsectionedPages} page
          {unsectionedPages === 1 ? "" : "s"} not assigned to a canonical
          sub-section (carried by inheritance or fallthrough)
        </div>
      )}
    </div>
  );
}


// ── ValidationIssuesPanel ───────────────────────────────────────


/** Render the post-process validators' output from
 * ``DocumentSegmentation.validation_issues`` (Spec 011 / FR-014).
 *
 * Collapsed by default — the editor's main job is showing the
 * sections; the panel is a complement, not the headline. Clicking
 * a section chip scrolls to that section row and flashes it. */
function ValidationIssuesPanel({
  issues,
  onSectionSelect,
}: {
  issues: ValidationIssue[];
  onSectionSelect: (sectionId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  if (issues.length === 0) return null;

  // Group by kind so the header shows ``2 overlap, 1 type_mismatch``
  // rather than a flat count. Stable sort keeps order deterministic
  // across renders.
  const byKind = new Map<string, ValidationIssue[]>();
  for (const issue of issues) {
    const list = byKind.get(issue.kind) || [];
    list.push(issue);
    byKind.set(issue.kind, list);
  }
  const kindEntries = [...byKind.entries()].sort((a, b) => {
    // Severity-first sort: errors → warnings → info; within a tier
    // alpha by kind for stability.
    const aSev = _issueConfig(a[0]).severity;
    const bSev = _issueConfig(b[0]).severity;
    const sevOrder: Record<IssueSeverity, number> = {
      error: 0,
      warning: 1,
      info: 2,
    };
    return sevOrder[aSev] - sevOrder[bSev] || a[0].localeCompare(b[0]);
  });

  // Worst severity drives the panel's overall colour.
  const worstSeverity: IssueSeverity = issues.reduce<IssueSeverity>(
    (acc, i) => {
      const sev = _issueConfig(i.kind).severity;
      if (acc === "error" || sev === "error") return "error";
      if (acc === "warning" || sev === "warning") return "warning";
      return "info";
    },
    "info",
  );

  const SeverityIcon =
    worstSeverity === "error"
      ? AlertCircle
      : worstSeverity === "warning"
        ? AlertTriangle
        : Info;

  const containerCls =
    worstSeverity === "error"
      ? "border-destructive/30 bg-destructive/5"
      : worstSeverity === "warning"
        ? "border-warning/30 bg-warning/5"
        : "border-blue-300/30 bg-blue-50/40 dark:bg-blue-900/10";

  const iconCls =
    worstSeverity === "error"
      ? "text-destructive"
      : worstSeverity === "warning"
        ? "text-warning"
        : "text-blue-600 dark:text-blue-400";

  return (
    <div className={cn("mb-3 rounded-lg border", containerCls)}>
      <button
        onClick={() => setExpanded((p) => !p)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left text-xs"
      >
        {expanded ? (
          <ChevronDown className="size-3.5 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground flex-shrink-0" />
        )}
        <SeverityIcon className={cn("size-3.5 flex-shrink-0", iconCls)} />
        <span className="font-medium">
          {issues.length} validation issue
          {issues.length === 1 ? "" : "s"}
        </span>
        <div className="flex flex-wrap gap-1 ml-2 flex-1">
          {kindEntries.map(([kind, group]) => {
            const cfg = _issueConfig(kind);
            return (
              <Badge
                key={kind}
                variant="outline"
                className={cn(
                  "text-[10px] px-1.5 py-0",
                  cfg.severity === "error" &&
                    "border-destructive/30 text-destructive",
                  cfg.severity === "warning" &&
                    "border-warning/30 text-warning",
                  cfg.severity === "info" &&
                    "border-blue-300 text-blue-600 dark:text-blue-400",
                )}
              >
                {group.length} {cfg.label}
              </Badge>
            );
          })}
        </div>
      </button>
      {expanded && (
        <ul className="px-3 pb-3 space-y-2">
          {kindEntries.flatMap(([kind, group]) =>
            group.map((issue, idx) => {
              const cfg = _issueConfig(kind);
              const KindIcon =
                cfg.severity === "error"
                  ? AlertCircle
                  : cfg.severity === "warning"
                    ? AlertTriangle
                    : Info;
              return (
                <li
                  key={`${kind}-${idx}`}
                  className="text-[11px] flex items-start gap-2 pl-1"
                >
                  <KindIcon
                    className={cn(
                      "size-3 flex-shrink-0 mt-0.5",
                      cfg.severity === "error" && "text-destructive",
                      cfg.severity === "warning" && "text-warning",
                      cfg.severity === "info" &&
                        "text-blue-600 dark:text-blue-400",
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-muted-foreground leading-snug">
                      {issue.message}
                    </p>
                    {(issue.section_ids.length > 0 || issue.page_range) && (
                      <div className="flex flex-wrap items-center gap-1 mt-1">
                        {issue.section_ids.map((sid) => (
                          <button
                            key={sid}
                            onClick={() => onSectionSelect(sid)}
                            className="text-[10px] px-1.5 py-0.5 rounded bg-muted hover:bg-muted-foreground/10 text-muted-foreground font-mono truncate max-w-[200px]"
                            title="Jump to this section"
                          >
                            {sid}
                          </button>
                        ))}
                        {issue.page_range && (
                          <span className="text-[10px] text-muted-foreground/70">
                            pages {issue.page_range[0]}
                            {issue.page_range[1] !== issue.page_range[0]
                              ? `–${issue.page_range[1]}`
                              : ""}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </li>
              );
            }),
          )}
        </ul>
      )}
    </div>
  );
}
