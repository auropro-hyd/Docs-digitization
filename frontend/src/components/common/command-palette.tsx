"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import {
  LayoutDashboard,
  FileText,
  PenLine,
  ShieldCheck,
  Upload,
  Loader2,
} from "lucide-react";
import { useLayoutStore } from "@/stores/layout-store";
import { listDocuments } from "@/lib/api";

interface DocItem {
  doc_id: string;
  filename: string | null;
}

export function CommandPalette() {
  const router = useRouter();
  const { commandPaletteOpen, setCommandPaletteOpen } = useLayoutStore();
  const [documents, setDocuments] = useState<DocItem[]>([]);
  const [isPending, startTransition] = useTransition();
  const [docsError, setDocsError] = useState(false);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setCommandPaletteOpen(!commandPaletteOpen);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, [commandPaletteOpen, setCommandPaletteOpen]);

  useEffect(() => {
    if (!commandPaletteOpen) return;
    startTransition(async () => {
      try {
        const data = await listDocuments();
        setDocuments(Array.isArray(data.documents) ? data.documents : []);
        setDocsError(false);
      } catch {
        setDocsError(true);
      }
    });
  }, [commandPaletteOpen]);

  const navigate = (path: string) => {
    setCommandPaletteOpen(false);
    router.push(path);
  };

  return (
    <CommandDialog open={commandPaletteOpen} onOpenChange={setCommandPaletteOpen}>
      <CommandInput placeholder="Search pages, documents, actions..." />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Pages">
          <CommandItem onSelect={() => navigate("/")}>
            <LayoutDashboard className="size-4 mr-2" />
            Dashboard
          </CommandItem>
          <CommandItem onSelect={() => navigate("/documents")}>
            <FileText className="size-4 mr-2" />
            Documents
          </CommandItem>
          <CommandItem onSelect={() => navigate("/review")}>
            <PenLine className="size-4 mr-2" />
            Page Review
          </CommandItem>
          <CommandItem onSelect={() => navigate("/compliance")}>
            <ShieldCheck className="size-4 mr-2" />
            Compliance
          </CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Documents">
          {isPending ? (
            <CommandItem disabled>
              <Loader2 className="size-4 mr-2 animate-spin" />
              Loading documents...
            </CommandItem>
          ) : docsError ? (
            <CommandItem disabled>
              <span className="text-destructive text-xs">Failed to load documents</span>
            </CommandItem>
          ) : documents.length === 0 ? (
            <CommandItem disabled>
              <span className="text-muted-foreground text-xs">No documents found</span>
            </CommandItem>
          ) : (
            documents.slice(0, 10).map((doc) => (
              <CommandItem
                key={doc.doc_id}
                onSelect={() => navigate(`/review?doc=${doc.doc_id}`)}
              >
                <FileText className="size-4 mr-2 text-muted-foreground" />
                <span className="truncate">{doc.filename || "Untitled"}</span>
                <span className="ml-auto text-[10px] text-muted-foreground font-mono">
                  {doc.doc_id.slice(0, 8)}
                </span>
              </CommandItem>
            ))
          )}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          <CommandItem onSelect={() => navigate("/")}>
            <Upload className="size-4 mr-2" />
            Upload Document
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
