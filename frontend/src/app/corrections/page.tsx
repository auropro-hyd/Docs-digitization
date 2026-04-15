"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getCorrectionRules,
  getCorrectionStats,
  getConfusionMatrix,
  toggleCorrectionRule,
  rebuildCorrections,
} from "@/lib/api";
import { toast } from "sonner";
import {
  BookOpen,
  CheckCircle,
  RefreshCw,
  Search,
  ToggleLeft,
  ToggleRight,
  XCircle,
  Loader2,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

interface CorrectionRule {
  id: string;
  pattern: string;
  replacement: string;
  field_context: string;
  occurrences: number;
  confidence: number;
  source_docs: number;
  is_active: boolean;
  created_at: string;
}

interface Stats {
  total_rules: number;
  active_rules: number;
  inactive_rules: number;
  total_corrections_processed: number;
  last_updated: string;
  rules_by_field_context: Record<string, number>;
  top_confusion_pairs: Array<{
    pattern: string;
    replacement: string;
    occurrences: number;
    confidence: number;
    field_context: string;
  }>;
}

interface ConfusionData {
  pairs: Array<{
    pattern: string;
    replacement: string;
    occurrences: number;
    confidence: number;
    field_context: string;
  }>;
  total_rules: number;
}

const CHART_COLORS = [
  "#8b5cf6", "#6366f1", "#3b82f6", "#0ea5e9", "#14b8a6",
  "#22c55e", "#84cc16", "#eab308", "#f97316", "#ef4444",
  "#ec4899", "#a855f7", "#6366f1", "#2563eb", "#0891b2",
  "#059669", "#65a30d", "#ca8a04", "#ea580c", "#dc2626",
];

function StatCard({
  title,
  value,
  icon: Icon,
  description,
}: {
  title: string;
  value: string | number;
  icon: React.ElementType;
  description?: string;
}) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-primary/10">
            <Icon className="size-5 text-primary" />
          </div>
          <div>
            <p className="text-2xl font-bold">{value}</p>
            <p className="text-xs text-muted-foreground">{title}</p>
            {description && <p className="text-[10px] text-muted-foreground mt-0.5">{description}</p>}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function CorrectionsPage() {
  const [rules, setRules] = useState<CorrectionRule[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [confusionData, setConfusionData] = useState<ConfusionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filterContext, setFilterContext] = useState("all");
  const [filterActive, setFilterActive] = useState("all");
  const [sortField, setSortField] = useState<"occurrences" | "confidence" | "pattern">("occurrences");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 25;

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const [rulesData, statsData, confData] = await Promise.all([
        getCorrectionRules(),
        getCorrectionStats(),
        getConfusionMatrix(),
      ]);
      setRules(rulesData.rules || []);
      setStats(statsData);
      setConfusionData(confData);
    } catch (err) {
      toast.error("Failed to load correction data");
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleToggle = useCallback(async (ruleId: string) => {
    setTogglingId(ruleId);
    try {
      const result = await toggleCorrectionRule(ruleId);
      setRules((prev) =>
        prev.map((r) => (r.id === ruleId ? { ...r, is_active: result.is_active } : r)),
      );
      toast.success(result.is_active ? "Rule enabled" : "Rule disabled");
    } catch {
      toast.error("Failed to toggle rule");
    } finally {
      setTogglingId(null);
    }
  }, []);

  const handleRebuild = useCallback(async () => {
    setRebuilding(true);
    try {
      const result = await rebuildCorrections();
      toast.success(`Rebuilt: ${result.total_rules} rules from ${result.total_corrections_processed} corrections`);
      await fetchAll();
    } catch {
      toast.error("Rebuild failed");
    } finally {
      setRebuilding(false);
    }
  }, [fetchAll]);

  const fieldContexts = React.useMemo(
    () => [...new Set(rules.map((r) => r.field_context))].sort(),
    [rules],
  );

  const filteredRules = React.useMemo(() => {
    let result = [...rules];

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (r) => r.pattern.toLowerCase().includes(q) || r.replacement.toLowerCase().includes(q),
      );
    }

    if (filterContext !== "all") {
      result = result.filter((r) => r.field_context === filterContext);
    }

    if (filterActive === "active") result = result.filter((r) => r.is_active);
    if (filterActive === "inactive") result = result.filter((r) => !r.is_active);

    result.sort((a, b) => {
      let cmp = 0;
      if (sortField === "occurrences") cmp = a.occurrences - b.occurrences;
      else if (sortField === "confidence") cmp = a.confidence - b.confidence;
      else cmp = a.pattern.localeCompare(b.pattern);
      return sortDir === "desc" ? -cmp : cmp;
    });

    return result;
  }, [rules, searchQuery, filterContext, filterActive, sortField, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filteredRules.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const paginatedRules = filteredRules.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

  useEffect(() => { setPage(0); }, [searchQuery, filterContext, filterActive, sortField, sortDir]);

  const toggleSort = useCallback((field: typeof sortField) => {
    if (sortField === field) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortField(field); setSortDir("desc"); }
  }, [sortField]);

  const chartData = React.useMemo(() => {
    if (!confusionData?.pairs) return [];
    return confusionData.pairs.map((p) => ({
      name: `${p.pattern.slice(0, 15)} → ${p.replacement.slice(0, 15)}`,
      fullPattern: p.pattern,
      fullReplacement: p.replacement,
      occurrences: p.occurrences,
      confidence: Math.round(p.confidence * 100),
    }));
  }, [confusionData]);

  if (loading) {
    return (
      <div className="space-y-6 p-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-96" />
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">OCR Correction Rules</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Learned corrections from reviewer edits. Rules are applied automatically during OCR post-processing.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={handleRebuild}
          disabled={rebuilding}
          className="gap-2"
        >
          {rebuilding ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          Rebuild Rules
        </Button>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Rules"
          value={stats?.total_rules ?? 0}
          icon={BookOpen}
        />
        <StatCard
          title="Active Rules"
          value={stats?.active_rules ?? 0}
          icon={CheckCircle}
          description={`${stats?.inactive_rules ?? 0} disabled`}
        />
        <StatCard
          title="Corrections Processed"
          value={stats?.total_corrections_processed ?? 0}
          icon={ArrowUpDown}
        />
        <StatCard
          title="Last Rebuilt"
          value={
            stats?.last_updated
              ? new Date(stats.last_updated).toLocaleDateString()
              : "Never"
          }
          icon={RefreshCw}
          description={
            stats?.last_updated
              ? new Date(stats.last_updated).toLocaleTimeString()
              : undefined
          }
        />
      </div>

      {/* Confusion chart */}
      {chartData.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Top OCR Confusion Pairs</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={320}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 30 }}>
                <XAxis type="number" fontSize={11} />
                <YAxis type="category" dataKey="name" width={200} fontSize={10} tick={{ fill: "hsl(var(--muted-foreground))" }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "hsl(var(--card))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: "8px",
                    fontSize: 12,
                  }}
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  formatter={(value: any, _name: any, props: any) => {
                    const p = props?.payload;
                    if (!p) return [String(value), ""];
                    return [
                      `${value} occurrences (${p.confidence}% confidence)`,
                      `"${p.fullPattern}" → "${p.fullReplacement}"`,
                    ];
                  }}
                />
                <Bar dataKey="occurrences" radius={[0, 4, 4, 0]}>
                  {chartData.map((_, idx) => (
                    <Cell key={idx} fill={CHART_COLORS[idx % CHART_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* Rules table */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <CardTitle className="text-sm">
              Rules ({filteredRules.length}/{rules.length})
            </CardTitle>
            <div className="flex items-center gap-2 flex-wrap">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                <Input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search patterns..."
                  className="h-8 text-xs pl-8 w-48"
                />
              </div>
              <Select value={filterContext} onValueChange={setFilterContext}>
                <SelectTrigger className="h-8 text-xs w-32">
                  <SelectValue placeholder="Context" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All contexts</SelectItem>
                  {fieldContexts.map((ctx) => (
                    <SelectItem key={ctx} value={ctx}>{ctx}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={filterActive} onValueChange={setFilterActive}>
                <SelectTrigger className="h-8 text-xs w-28">
                  <SelectValue placeholder="Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="inactive">Inactive</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">Status</TableHead>
                  <TableHead>
                    <button className="flex items-center gap-1 text-xs" onClick={() => toggleSort("pattern")}>
                      Pattern <ArrowUpDown className="size-3" />
                    </button>
                  </TableHead>
                  <TableHead>Replacement</TableHead>
                  <TableHead className="w-28">Context</TableHead>
                  <TableHead className="w-20">
                    <button className="flex items-center gap-1 text-xs" onClick={() => toggleSort("occurrences")}>
                      Count <ArrowUpDown className="size-3" />
                    </button>
                  </TableHead>
                  <TableHead className="w-16">Docs</TableHead>
                  <TableHead className="w-20">
                    <button className="flex items-center gap-1 text-xs" onClick={() => toggleSort("confidence")}>
                      Conf. <ArrowUpDown className="size-3" />
                    </button>
                  </TableHead>
                  <TableHead className="w-16">Toggle</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedRules.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                      {rules.length === 0
                        ? "No correction rules yet. Rules are learned from reviewer edits."
                        : "No rules match the current filters."}
                    </TableCell>
                  </TableRow>
                ) : (
                  paginatedRules.map((rule) => (
                    <TableRow key={rule.id} className={!rule.is_active ? "opacity-50" : ""}>
                      <TableCell>
                        {rule.is_active ? (
                          <CheckCircle className="size-3.5 text-success" />
                        ) : (
                          <XCircle className="size-3.5 text-muted-foreground" />
                        )}
                      </TableCell>
                      <TableCell>
                        <code className="text-xs bg-destructive/10 text-destructive px-1.5 py-0.5 rounded break-all">
                          {rule.pattern}
                        </code>
                      </TableCell>
                      <TableCell>
                        <code className="text-xs bg-success/10 text-success px-1.5 py-0.5 rounded break-all">
                          {rule.replacement}
                        </code>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-[10px]">{rule.field_context}</Badge>
                      </TableCell>
                      <TableCell className="tabular-nums text-xs">{rule.occurrences}</TableCell>
                      <TableCell className="tabular-nums text-xs">{rule.source_docs}</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={
                            rule.confidence >= 0.9
                              ? "text-[10px] text-success border-success/20"
                              : rule.confidence >= 0.7
                              ? "text-[10px] text-warning border-warning/20"
                              : "text-[10px] text-destructive border-destructive/20"
                          }
                        >
                          {Math.round(rule.confidence * 100)}%
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7"
                          disabled={togglingId === rule.id}
                          onClick={() => handleToggle(rule.id)}
                        >
                          {togglingId === rule.id ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : rule.is_active ? (
                            <ToggleRight className="size-4 text-success" />
                          ) : (
                            <ToggleLeft className="size-4 text-muted-foreground" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-3 text-xs text-muted-foreground">
              <span>
                Showing {safePage * PAGE_SIZE + 1}–{Math.min((safePage + 1) * PAGE_SIZE, filteredRules.length)} of {filteredRules.length}
              </span>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  disabled={safePage <= 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="size-3.5" />
                </Button>
                <span className="px-2 tabular-nums">{safePage + 1}/{totalPages}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  disabled={safePage >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  <ChevronRight className="size-3.5" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
