"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import {
  FileText,
  Search,
  Upload,
  MoreHorizontal,
  PenLine,
  ShieldCheck,
  Trash2,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/common/empty-state";
import { ConfirmationDialog } from "@/components/common/confirmation-dialog";
import { listDocuments, deleteDocument } from "@/lib/api";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useDocumentStore } from "@/stores/document-store";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

interface DocumentSummary {
  doc_id: string;
  filename: string | null;
  status: string;
  total_pages: number;
}

const STATUS_STYLES: Record<string, string> = {
  completed: "border-success/30 bg-success/10 text-success",
  processed: "border-primary/30 bg-primary/10 text-primary",
  processing: "border-info/30 bg-info/10 text-info animate-pulse",
  needs_review: "border-warning/30 bg-warning/10 text-warning",
  error: "border-destructive/30 bg-destructive/10 text-destructive",
  uploaded: "border-muted-foreground/30 bg-muted text-muted-foreground",
};

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const isMobile = useIsMobile();

  const fetchDocs = useCallback(() => {
    listDocuments()
      .then((data) => {
        if (!Array.isArray(data.documents)) {
          throw new Error("Invalid response from server");
        }
        setDocuments(data.documents);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to load documents";
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === "visible") fetchDocs();
    };
    document.addEventListener("visibilitychange", onFocus);
    return () => document.removeEventListener("visibilitychange", onFocus);
  }, [fetchDocs]);

  const columns = useMemo<ColumnDef<DocumentSummary>[]>(
    () => [
      {
        accessorKey: "filename",
        header: ({ column }) => (
          <button
            className="flex items-center gap-1.5 hover:text-foreground transition-colors"
            onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
          >
            Document
            <ArrowUpDown className="size-3" />
          </button>
        ),
        cell: ({ row }) => (
          <div className="flex items-center gap-3 min-w-0">
            <div className="size-8 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
              <FileText className="size-4 text-muted-foreground" />
            </div>
            <span className="text-sm font-medium text-foreground truncate">
              {row.original.filename || "Untitled"}
            </span>
          </div>
        ),
      },
      {
        accessorKey: "doc_id",
        header: "ID",
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground font-mono">
            {row.original.doc_id.slice(0, 8)}
          </span>
        ),
      },
      {
        accessorKey: "total_pages",
        header: "Pages",
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">
            {row.original.total_pages > 0 ? row.original.total_pages : "—"}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className={cn("text-[10px] capitalize", STATUS_STYLES[row.original.status] ?? STATUS_STYLES.uploaded)}
          >
            {row.original.status.replace(/_/g, " ")}
          </Badge>
        ),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuItem asChild>
                <Link href={`/review?doc=${row.original.doc_id}`}>
                  <PenLine className="size-4 mr-2" /> Review
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href={`/compliance?doc=${row.original.doc_id}`}>
                  <ShieldCheck className="size-4 mr-2" /> Compliance
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onClick={() => setDeleteTarget(row.original.doc_id)}
              >
                <Trash2 className="size-4 mr-2" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    data: documents,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 10 } },
  });

  return (
    <div className="p-4 lg:p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Documents</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {documents.length} document{documents.length !== 1 ? "s" : ""} processed
          </p>
        </div>
        <Button asChild>
          <Link href="/">
            <Upload className="size-4 mr-2" /> Upload New
          </Link>
        </Button>
      </div>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search documents..."
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          className="w-full h-9 pl-9 pr-4 rounded-lg border border-input bg-background text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
        />
      </div>

      {/* Content */}
      {loading ? (
        <Card>
          <CardContent className="p-0">
            <div className="divide-y divide-border">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="flex items-center gap-3 p-4">
                  <Skeleton className="size-8 rounded-lg" />
                  <div className="flex-1">
                    <Skeleton className="h-4 w-48 mb-1" />
                    <Skeleton className="h-3 w-20" />
                  </div>
                  <Skeleton className="h-5 w-16 rounded-full" />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : error ? (
        <Card>
          <CardContent className="p-8">
            <EmptyState
              icon={<AlertCircle className="size-7" />}
              title="Failed to load documents"
              description={error}
              action={
                <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
                  Retry
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : documents.length === 0 ? (
        <Card>
          <CardContent className="p-8">
            <EmptyState
              icon={<FileText className="size-7" />}
              title="No documents yet"
              description="Upload a document to get started with AI-powered extraction"
              action={
                <Button size="sm" asChild>
                  <Link href="/">
                    <Upload className="size-4 mr-2" /> Upload Document
                  </Link>
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : isMobile ? (
        /* Mobile card list */
        <div className="space-y-3">
          {table.getRowModel().rows.map((row, i) => (
            <motion.div
              key={row.original.doc_id}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: i * 0.03 }}
            >
              <Card>
                <CardContent className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="size-9 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
                        <FileText className="size-4 text-muted-foreground" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-foreground truncate">{row.original.filename || "Untitled"}</p>
                        <p className="text-xs text-muted-foreground font-mono">{row.original.doc_id.slice(0, 8)}</p>
                      </div>
                    </div>
                    <Badge variant="outline" className={cn("text-[10px] capitalize flex-shrink-0", STATUS_STYLES[row.original.status] ?? STATUS_STYLES.uploaded)}>
                      {row.original.status.replace(/_/g, " ")}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 mt-3">
                    <Button variant="outline" size="sm" className="flex-1 h-8 text-xs" asChild>
                      <Link href={`/review?doc=${row.original.doc_id}`}>
                        <PenLine className="size-3.5 mr-1" /> Review
                      </Link>
                    </Button>
                    <Button variant="outline" size="sm" className="flex-1 h-8 text-xs" asChild>
                      <Link href={`/compliance?doc=${row.original.doc_id}`}>
                        <ShieldCheck className="size-3.5 mr-1" /> Compliance
                      </Link>
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      ) : (
        /* Desktop table */
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id}>
                    {hg.headers.map((header) => (
                      <TableHead key={header.id} className="text-xs">
                        {header.isPlaceholder
                          ? null
                          : flexRender(header.column.columnDef.header, header.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.map((row, i) => (
                  <motion.tr
                    key={row.id}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: i * 0.02 }}
                    className="border-b border-border hover:bg-muted/50 transition-colors group"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id} className="py-3">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </motion.tr>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Pagination */}
      {documents.length > 10 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              className="size-8"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="size-8"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      )}

      {/* Delete confirmation */}
      <ConfirmationDialog
        open={!!deleteTarget}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete document?"
        description="This action cannot be undone. The document and all associated data will be permanently removed."
        confirmLabel="Delete"
        variant="destructive"
        onConfirm={async () => {
          if (!deleteTarget) return;
          try {
            await deleteDocument(deleteTarget);
            setDocuments((prev) => prev.filter((d) => d.doc_id !== deleteTarget));
            if (useDocumentStore.getState().docId === deleteTarget) {
              useDocumentStore.getState().reset();
            }
            toast.success("Document deleted");
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Failed to delete document");
          }
          setDeleteTarget(null);
        }}
      />
    </div>
  );
}
