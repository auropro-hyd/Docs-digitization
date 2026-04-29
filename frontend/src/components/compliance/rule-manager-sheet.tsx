"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { RuleEditorDialog } from "./rule-editor-dialog";
import {
  getAgentRules,
  updateRule as apiUpdateRule,
  deleteRule as apiDeleteRule,
  updateAgent as apiUpdateAgent,
} from "@/lib/api";
import type { AgentRulesResponse, Rule } from "@/types/compliance";
import { toast } from "sonner";
import {
  Search,
  Pencil,
  Trash2,
  Plus,
  FileStack,
  Save,
  X,
  Loader2,
} from "lucide-react";

const SEVERITY_OPTIONS = ["critical", "major", "minor", "observation"] as const;

const SEVERITY_COLORS: Record<string, string> = {
  critical: "text-red-600 bg-red-50 border-red-200",
  major: "text-orange-600 bg-orange-50 border-orange-200",
  minor: "text-yellow-600 bg-yellow-50 border-yellow-200",
  observation: "text-slate-500 bg-slate-50 border-slate-200",
};

interface RuleManagerSheetProps {
  agentId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RuleManagerSheet({ agentId, open, onOpenChange }: RuleManagerSheetProps) {
  const [data, setData] = useState<AgentRulesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [activeTab, setActiveTab] = useState<string>("");

  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const editInputRef = useRef<HTMLTextAreaElement>(null);

  const [editingLabel, setEditingLabel] = useState(false);
  const [labelDraft, setLabelDraft] = useState("");

  const [addDialogMode, setAddDialogMode] = useState<"single" | "bulk" | "category" | null>(null);
  const [savingRule, setSavingRule] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    if (!agentId) return;
    setLoading(true);
    try {
      const res = await getAgentRules(agentId);
      setData(res);
      if (res.categories.length > 0 && !activeTab) {
        setActiveTab(res.categories[0].id);
      }
    } catch {
      toast.error("Failed to load rules");
    } finally {
      setLoading(false);
    }
  }, [agentId, activeTab]);

  useEffect(() => {
    if (open && agentId) {
      setSearch("");
      setEditingRuleId(null);
      setActiveTab("");
      fetchData();
    }
  }, [open, agentId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (data && data.categories.length > 0 && !activeTab) {
      setActiveTab(data.categories[0].id);
    }
  }, [data, activeTab]);

  const handleSaveRuleText = async (rule: Rule) => {
    if (!agentId || !editText.trim() || editText.trim() === rule.text) {
      setEditingRuleId(null);
      return;
    }
    setSavingRule(rule.id);
    try {
      await apiUpdateRule(agentId, rule.id, { text: editText.trim() });
      await fetchData();
      toast.success("Rule updated");
    } catch {
      toast.error("Failed to update rule");
    } finally {
      setSavingRule(null);
      setEditingRuleId(null);
    }
  };

  const handleSeverityChange = async (rule: Rule, severity: string) => {
    if (!agentId || severity === rule.severity_hint) return;
    try {
      await apiUpdateRule(agentId, rule.id, { severity_hint: severity });
      await fetchData();
    } catch {
      toast.error("Failed to update severity");
    }
  };

  const handleDeleteRule = async (rule: Rule) => {
    if (!agentId) return;
    try {
      await apiDeleteRule(agentId, rule.id);
      await fetchData();
      toast.success("Rule deleted");
    } catch {
      toast.error("Failed to delete rule");
    }
  };

  const handleSaveLabel = async () => {
    if (!agentId || !data || !labelDraft.trim()) {
      setEditingLabel(false);
      return;
    }
    try {
      await apiUpdateAgent(agentId, { label: labelDraft.trim() });
      await fetchData();
      toast.success("Agent updated");
    } catch {
      toast.error("Failed to update agent");
    } finally {
      setEditingLabel(false);
    }
  };

  const filterRules = (rules: Rule[]) => {
    if (!search.trim()) return rules;
    const q = search.toLowerCase();
    return rules.filter(
      (r) => r.text.toLowerCase().includes(q) || r.id.toLowerCase().includes(q),
    );
  };

  const totalFiltered = data
    ? data.categories.reduce((sum, cat) => sum + filterRules(cat.rules).length, 0)
    : 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-2xl lg:max-w-3xl flex flex-col"
        showCloseButton
      >
        <SheetHeader className="flex-shrink-0 pb-0">
          {data ? (
            <>
              <div className="flex items-center gap-2">
                {editingLabel ? (
                  <div className="flex items-center gap-1.5 flex-1">
                    <input
                      className="text-lg font-semibold bg-transparent border-b border-primary outline-none flex-1"
                      value={labelDraft}
                      onChange={(e) => setLabelDraft(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") handleSaveLabel(); if (e.key === "Escape") setEditingLabel(false); }}
                      autoFocus
                    />
                    <Button variant="ghost" size="sm" onClick={handleSaveLabel}><Save className="size-3.5" /></Button>
                    <Button variant="ghost" size="sm" onClick={() => setEditingLabel(false)}><X className="size-3.5" /></Button>
                  </div>
                ) : (
                  <SheetTitle className="flex items-center gap-2">
                    {data.label}
                    <button
                      onClick={() => { setLabelDraft(data.label); setEditingLabel(true); }}
                      className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <Pencil className="size-3.5" />
                    </button>
                  </SheetTitle>
                )}
              </div>
              <SheetDescription>
                {data.description} &middot; {data.total_rules} rules across {data.categories.length} categories
              </SheetDescription>
            </>
          ) : (
            <>
              <SheetTitle>Loading...</SheetTitle>
              <SheetDescription>Fetching agent rules</SheetDescription>
            </>
          )}
        </SheetHeader>

        {/* Search bar */}
        <div className="px-4 flex-shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search rules..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border bg-muted/30 outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-colors"
            />
            {search && (
              <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                {totalFiltered} match{totalFiltered !== 1 ? "es" : ""}
              </span>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : data && data.categories.length > 0 ? (
          <div className="flex-1 flex flex-col overflow-hidden px-4 pb-4">
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
              <TabsList className="flex-shrink-0 w-full overflow-x-auto justify-start h-auto flex-wrap gap-1 bg-transparent p-0 mb-3">
                {data.categories.map((cat) => {
                  const filtered = filterRules(cat.rules);
                  return (
                    <TabsTrigger
                      key={cat.id}
                      value={cat.id}
                      className="text-xs px-3 py-1.5 data-[state=active]:bg-primary/10 data-[state=active]:text-primary rounded-lg"
                    >
                      {cat.display}
                      <Badge variant="secondary" className="ml-1.5 text-[10px] px-1 py-0">
                        {filtered.length}
                      </Badge>
                    </TabsTrigger>
                  );
                })}
              </TabsList>

              {data.categories.map((cat) => (
                <TabsContent key={cat.id} value={cat.id} className="flex-1 overflow-hidden flex flex-col mt-0">
                  <ScrollArea className="flex-1">
                    <div className="space-y-1">
                      {filterRules(cat.rules).map((rule) => (
                        <div
                          key={rule.id}
                          className="group flex items-start gap-2 py-2 px-2 rounded-lg hover:bg-muted/50 transition-colors"
                        >
                          <span className="text-xs text-muted-foreground font-mono w-5 pt-0.5 text-right flex-shrink-0">
                            {rule.number}.
                          </span>

                          <div className="flex-1 min-w-0">
                            {editingRuleId === rule.id ? (
                              <div className="space-y-1.5">
                                <textarea
                                  ref={editInputRef}
                                  value={editText}
                                  onChange={(e) => setEditText(e.target.value)}
                                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSaveRuleText(rule); } if (e.key === "Escape") setEditingRuleId(null); }}
                                  className="w-full text-sm bg-background border rounded-md px-2 py-1.5 outline-none focus:ring-2 focus:ring-primary/20 resize-none"
                                  rows={2}
                                  autoFocus
                                />
                                <div className="flex gap-1">
                                  <Button
                                    size="sm"
                                    variant="default"
                                    className="h-6 text-xs px-2"
                                    onClick={() => handleSaveRuleText(rule)}
                                    disabled={savingRule === rule.id}
                                  >
                                    {savingRule === rule.id ? <Loader2 className="size-3 animate-spin" /> : "Save"}
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="h-6 text-xs px-2"
                                    onClick={() => setEditingRuleId(null)}
                                  >
                                    Cancel
                                  </Button>
                                </div>
                              </div>
                            ) : (
                              <p
                                className="text-sm text-foreground cursor-pointer hover:text-primary/80 transition-colors"
                                onClick={() => { setEditingRuleId(rule.id); setEditText(rule.text); }}
                                title="Click to edit"
                              >
                                {search ? highlightMatch(rule.text, search) : rule.text}
                              </p>
                            )}
                          </div>

                          <div className="flex items-center gap-1 flex-shrink-0">
                            <Select
                              value={rule.severity_hint}
                              onValueChange={(val) => handleSeverityChange(rule, val)}
                            >
                              <SelectTrigger className={`h-6 text-[10px] px-1.5 w-auto border rounded-md ${SEVERITY_COLORS[rule.severity_hint] || ""}`}>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {SEVERITY_OPTIONS.map((sev) => (
                                  <SelectItem key={sev} value={sev} className="text-xs">
                                    {sev}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>

                            <button
                              onClick={() => handleDeleteRule(rule)}
                              className="opacity-0 group-hover:opacity-100 p-1 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-all"
                              title="Delete rule"
                            >
                              <Trash2 className="size-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}

                      {filterRules(cat.rules).length === 0 && (
                        <p className="text-sm text-muted-foreground text-center py-8">
                          {search ? "No rules match your search" : "No rules in this category"}
                        </p>
                      )}
                    </div>
                  </ScrollArea>

                  <Separator className="my-2" />

                  <div className="flex items-center gap-2 flex-shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-xs"
                      onClick={() => setAddDialogMode("single")}
                    >
                      <Plus className="size-3.5 mr-1" /> Add Rule
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-xs text-muted-foreground"
                      onClick={() => setAddDialogMode("bulk")}
                    >
                      <FileStack className="size-3.5 mr-1" /> Bulk Import
                    </Button>
                  </div>
                </TabsContent>
              ))}
            </Tabs>

            <div className="pt-2 flex-shrink-0">
              <Button
                variant="ghost"
                size="sm"
                className="text-xs text-muted-foreground"
                onClick={() => setAddDialogMode("category")}
              >
                <Plus className="size-3.5 mr-1" /> Add Category
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
            <p className="text-sm">No categories or rules yet.</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setAddDialogMode("category")}
            >
              <Plus className="size-3.5 mr-1" /> Add Category
            </Button>
          </div>
        )}

        {/* Add rule / bulk / category dialog */}
        {addDialogMode && agentId && data && (
          <RuleEditorDialog
            open={addDialogMode !== null}
            onOpenChange={(open) => { if (!open) setAddDialogMode(null); }}
            mode={addDialogMode}
            agentId={agentId}
            category={
              addDialogMode !== "category" && activeTab
                ? { id: activeTab, display: data.categories.find((c) => c.id === activeTab)?.display || activeTab }
                : undefined
            }
            onSuccess={() => {
              setAddDialogMode(null);
              fetchData();
            }}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function highlightMatch(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="bg-yellow-200/60 rounded px-0.5">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}
