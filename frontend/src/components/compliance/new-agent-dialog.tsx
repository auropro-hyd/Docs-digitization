"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { createAgent } from "@/lib/api";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

interface NewAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (agentId: string) => void;
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function NewAgentDialog({ open, onOpenChange, onCreated }: NewAgentDialogProps) {
  const [label, setLabel] = useState("");
  const [agentId, setAgentId] = useState("");
  const [idManual, setIdManual] = useState(false);
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!idManual && label) {
      setAgentId(slugify(label));
    }
  }, [label, idManual]);

  useEffect(() => {
    if (open) {
      setLabel("");
      setAgentId("");
      setIdManual(false);
      setDescription("");
      setError("");
    }
  }, [open]);

  const handleSubmit = async () => {
    setError("");

    if (!label.trim()) {
      setError("Label is required");
      return;
    }
    if (!agentId.trim() || !/^[a-z][a-z0-9_-]*$/.test(agentId)) {
      setError("ID must start with a letter and contain only lowercase letters, numbers, hyphens, or underscores");
      return;
    }
    if (!description.trim()) {
      setError("Description is required");
      return;
    }

    setSaving(true);
    try {
      await createAgent({ id: agentId.trim(), label: label.trim(), description: description.trim() });
      toast.success(`Agent "${label.trim()}" created`);
      onCreated?.(agentId.trim());
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create agent";
      setError(msg);
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create New Compliance Agent</DialogTitle>
          <DialogDescription>
            Define a new compliance standard. You can add rules after creation.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Display Label</label>
            <input
              type="text"
              placeholder="e.g. ISO 13485"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 bg-background"
              autoFocus
            />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">
              Agent ID
              {!idManual && (
                <button
                  type="button"
                  className="ml-2 text-primary/70 hover:text-primary underline"
                  onClick={() => setIdManual(true)}
                >
                  customize
                </button>
              )}
            </label>
            <input
              type="text"
              placeholder="auto-generated-from-label"
              value={agentId}
              onChange={(e) => { setIdManual(true); setAgentId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "")); }}
              disabled={!idManual}
              className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 bg-background font-mono disabled:opacity-60"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Description</label>
            <input
              type="text"
              placeholder="e.g. Quality management system for medical devices"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
              className="w-full text-sm border rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-primary/20 bg-background"
            />
          </div>

          {error && (
            <p className="text-xs text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={saving}>
            {saving && <Loader2 className="size-4 mr-1.5 animate-spin" />}
            Create Agent
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
