"use client";

import { useState, useCallback } from "react";
import { Check, Flag, Pencil, X, Save, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type ComponentDecision = {
  action: string;
  status: string;
  value?: string;
  reason?: string;
} | null;

interface ReviewableComponentProps {
  componentId: string;
  label: string;
  type: "content" | "kv" | "signature" | "table";
  confidence?: number;
  decision?: ComponentDecision;
  children: React.ReactNode;
  editableValue?: string;
  onApprove: (componentId: string) => Promise<void>;
  onEdit?: (componentId: string, value: string) => Promise<void>;
  onFlag: (componentId: string, reason?: string) => Promise<void>;
  className?: string;
}

const TYPE_STYLES = {
  content: "border-border",
  kv: "border-primary/20",
  signature: "border-info/20",
  table: "border-warning/20",
} as const;

const STATUS_COLORS = {
  approved: "bg-success/5 border-success/30",
  edited: "bg-blue-500/5 border-blue-500/30",
  flagged: "bg-destructive/5 border-destructive/30",
} as const;

export function ReviewableComponent({
  componentId,
  label,
  type,
  confidence,
  decision,
  children,
  editableValue,
  onApprove,
  onEdit,
  onFlag,
  className,
}: ReviewableComponentProps) {
  const [loading, setLoading] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(editableValue ?? "");

  const status = decision?.status;

  const handleApprove = useCallback(async () => {
    setLoading("approve");
    try {
      await onApprove(componentId);
    } finally {
      setLoading(null);
    }
  }, [componentId, onApprove]);

  const handleFlag = useCallback(async () => {
    setLoading("flag");
    try {
      await onFlag(componentId);
    } finally {
      setLoading(null);
    }
  }, [componentId, onFlag]);

  const handleSaveEdit = useCallback(async () => {
    if (!onEdit) return;
    setLoading("edit");
    try {
      await onEdit(componentId, editValue);
      setEditing(false);
    } finally {
      setLoading(null);
    }
  }, [componentId, editValue, onEdit]);

  const borderStyle =
    status && status in STATUS_COLORS
      ? STATUS_COLORS[status as keyof typeof STATUS_COLORS]
      : TYPE_STYLES[type];

  return (
    <div
      className={cn(
        "group relative rounded-lg border transition-all",
        borderStyle,
        className,
      )}
    >
      <div className="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-inherit bg-muted/30">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </span>

        {confidence != null && confidence > 0 && (
          <Badge
            variant="outline"
            className={cn(
              "text-[9px] px-1.5 py-0",
              confidence >= 0.8
                ? "border-success/30 text-success"
                : confidence >= 0.6
                  ? "border-warning/30 text-warning"
                  : "border-destructive/30 text-destructive",
            )}
          >
            {Math.round(confidence * 100)}%
          </Badge>
        )}

        {status && (
          <Badge
            variant="outline"
            className={cn(
              "text-[9px] px-1.5 py-0 capitalize",
              status === "approved"
                ? "border-success/30 text-success"
                : status === "edited"
                  ? "border-blue-500/30 text-blue-500"
                  : status === "flagged"
                    ? "border-destructive/30 text-destructive"
                    : "border-muted-foreground/30 text-muted-foreground",
            )}
          >
            {status}
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          {status !== "approved" && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 text-success hover:text-success"
                  onClick={handleApprove}
                  disabled={!!loading}
                >
                  <Check className="size-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Approve</TooltipContent>
            </Tooltip>
          )}

          {onEdit && !editing && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 text-blue-500 hover:text-blue-500"
                  onClick={() => {
                    setEditValue(editableValue ?? "");
                    setEditing(true);
                  }}
                  disabled={!!loading}
                >
                  <Pencil className="size-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Edit</TooltipContent>
            </Tooltip>
          )}

          {status !== "flagged" && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 text-destructive hover:text-destructive"
                  onClick={handleFlag}
                  disabled={!!loading}
                >
                  <Flag className="size-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Flag</TooltipContent>
            </Tooltip>
          )}

          {status && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5 text-muted-foreground"
                  onClick={handleFlag}
                  disabled={!!loading}
                >
                  <RotateCcw className="size-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Reset to needs review</TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>

      <div className="p-2.5">
        {editing ? (
          <div className="space-y-2">
            <textarea
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              className="w-full min-h-[80px] p-2 text-xs rounded-md border border-input bg-background resize-y focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
            <div className="flex items-center gap-1.5 justify-end">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 text-[10px]"
                onClick={() => setEditing(false)}
              >
                <X className="size-3 mr-0.5" /> Cancel
              </Button>
              <Button
                size="sm"
                className="h-6 text-[10px]"
                onClick={handleSaveEdit}
                disabled={loading === "edit"}
              >
                <Save className="size-3 mr-0.5" /> Save
              </Button>
            </div>
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
