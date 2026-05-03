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

interface Segmentation {
  sections: Section[];
  document_type: string;
  confidence: number;
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
                return (
                <Fragment key={section.section_id}>
                <tr
                  className={cn(
                    "border-t transition-colors",
                    editingIdx === idx && "bg-primary/5",
                    idx % 2 === 0 && editingIdx !== idx && "bg-muted/20",
                  )}
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
