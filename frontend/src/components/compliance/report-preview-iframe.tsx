"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Eye } from "lucide-react";
import { getCompliancePreviewUrl } from "@/lib/api";

interface ReportPreviewIframeProps {
  docId: string;
  agent?: string;
  triggerLabel?: string;
}

/** Modal embedding the ``/preview`` endpoint via ``<iframe>``.
 *
 * The preview shares the export cache so the iframe pull is free
 * after the first export was rendered. Mounts the iframe lazily —
 * only when the dialog opens — so closed previews don't sit on
 * stale bytes in the browser. */
export function ReportPreviewIframe({
  docId,
  agent,
  triggerLabel = "Preview",
}: ReportPreviewIframeProps) {
  const [open, setOpen] = useState(false);
  const url = getCompliancePreviewUrl(docId, { agent });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Eye className="size-4 mr-2" /> {triggerLabel}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-5xl w-[92vw] h-[88vh] p-0 flex flex-col">
        <DialogHeader className="px-5 py-3 border-b">
          <DialogTitle className="text-sm">Compliance Report Preview</DialogTitle>
          <DialogDescription className="text-xs">
            Inline preview of the same artifact downloaded via Export.
          </DialogDescription>
        </DialogHeader>
        <div className="flex-1 bg-muted">
          {open && (
            <iframe
              src={url}
              title="Compliance report preview"
              className="w-full h-full border-0"
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
