"use client";

import { useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { addRule, bulkAddRules, addCategory } from "@/lib/api";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

const SEVERITY_OPTIONS = ["critical", "major", "minor", "observation"] as const;

interface RuleEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: "single" | "bulk" | "category";
  agentId: string;
  category?: { id: string; display: string };
  onSuccess: () => void;
}

export function RuleEditorDialog({
  open,
  onOpenChange,
  mode,
  agentId,
  category,
  onSuccess,
}: RuleEditorDialogProps) {
  const [ruleText, setRuleText] = useState("");
  const [severity, setSeverity] = useState<string>("observation");
  const [bulkText, setBulkText] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [saving, setSaving] = useState(false);

  const parsedBulkLines = useMemo(() => {
    if (mode !== "bulk") return [];
    return bulkText
      .split("\n")
      .map((line) => line.replace(/^\d+\.\s*/, "").trim())
      .filter(Boolean);
  }, [bulkText, mode]);

  const handleSubmit = async () => {
    setSaving(true);
    try {
      if (mode === "single") {
        if (!ruleText.trim()) {
          toast.error("Rule text is required");
          return;
        }
        if (!category) {
          toast.error("No category selected");
          return;
        }
        await addRule(agentId, {
          category: category.id,
          category_display: category.display,
          text: ruleText.trim(),
          severity_hint: severity,
        });
        toast.success("Rule added");
        setRuleText("");
        onSuccess();
      } else if (mode === "bulk") {
        if (parsedBulkLines.length === 0) {
          toast.error("No valid rules detected");
          return;
        }
        if (!category) {
          toast.error("No category selected");
          return;
        }
        await bulkAddRules(agentId, {
          category: category.id,
          category_display: category.display,
          texts: parsedBulkLines,
          severity_hint: severity,
        });
        toast.success(`${parsedBulkLines.length} rules added`);
        setBulkText("");
        onSuccess();
      } else if (mode === "category") {
        if (!categoryName.trim()) {
          toast.error("Category name is required");
          return;
        }
        await addCategory(agentId, { display: categoryName.trim() });
        toast.success("Category added");
        setCategoryName("");
        onSuccess();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Operation failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {mode === "single" && (
          <>
            <DialogHeader>
              <DialogTitle>Add Rule</DialogTitle>
              <DialogDescription>
                Add a new rule to <strong>{category?.display}</strong>
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-3">
              <textarea
                placeholder="Enter rule text..."
                value={ruleText}
                onChange={(e) => setRuleText(e.target.value)}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 resize-none bg-background"
                rows={3}
                autoFocus
              />
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Severity:</span>
                <Select value={severity} onValueChange={setSeverity}>
                  <SelectTrigger className="h-8 text-xs w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {SEVERITY_OPTIONS.map((s) => (
                      <SelectItem key={s} value={s} className="text-xs">{s}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </>
        )}

        {mode === "bulk" && (
          <>
            <DialogHeader>
              <DialogTitle>Bulk Import Rules</DialogTitle>
              <DialogDescription>
                Paste rules for <strong>{category?.display}</strong> &mdash; one per line.
                Numbering (e.g. &quot;1. Rule text&quot;) is auto-stripped.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-3">
              <textarea
                placeholder={"1. Each entry has clear initials or signature.\n2. Electronic entries include operator login.\n3. Personnel identity is traceable."}
                value={bulkText}
                onChange={(e) => setBulkText(e.target.value)}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 resize-none font-mono bg-background"
                rows={8}
                autoFocus
              />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Severity:</span>
                  <Select value={severity} onValueChange={setSeverity}>
                    <SelectTrigger className="h-8 text-xs w-32">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SEVERITY_OPTIONS.map((s) => (
                        <SelectItem key={s} value={s} className="text-xs">{s}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Badge variant="secondary" className="text-xs">
                  {parsedBulkLines.length} rule{parsedBulkLines.length !== 1 ? "s" : ""} detected
                </Badge>
              </div>
            </div>
          </>
        )}

        {mode === "category" && (
          <>
            <DialogHeader>
              <DialogTitle>Add Category</DialogTitle>
              <DialogDescription>
                Create a new rule category for this agent
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-2">
              <input
                type="text"
                placeholder="Category name, e.g. Equipment Identification"
                value={categoryName}
                onChange={(e) => setCategoryName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
                className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 bg-background"
                autoFocus
              />
              {categoryName.trim() && (
                <p className="text-xs text-muted-foreground">
                  ID: <code className="bg-muted px-1 rounded">{categoryName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")}</code>
                </p>
              )}
            </div>
          </>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1.5 animate-spin" />}
            {mode === "single" ? "Add Rule" : mode === "bulk" ? `Import ${parsedBulkLines.length} Rules` : "Add Category"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
