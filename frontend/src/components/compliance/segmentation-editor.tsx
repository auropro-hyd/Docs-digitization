"use client";

import { useState, useEffect, useCallback } from "react";
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
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface Section {
  section_id: string;
  name: string;
  section_type: string;
  start_page: number;
  end_page: number;
  description: string;
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
              {seg.sections.map((section, idx) => (
                <tr
                  key={section.section_id}
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
                      <td className="py-2 px-3 font-medium">{section.name}</td>
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
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
