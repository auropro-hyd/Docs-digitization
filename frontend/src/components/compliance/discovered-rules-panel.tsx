"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { getDiscoveredRules, promoteDiscoveredRule } from "@/lib/api";
import {
  Loader2,
  Sparkles,
  ArrowUpToLine,
  CheckCircle,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface DiscoveredRule {
  description: string;
  sections_semantic: string[];
  section_ids: string[];
  reasoning: string;
  priority: string;
  discovered_at: string;
  promoted: boolean;
}

interface DiscoveredRulesPanelProps {
  docId: string;
}

const PRIORITY_STYLES: Record<string, string> = {
  high: "bg-destructive/10 text-destructive border-destructive/20",
  medium: "bg-warning/10 text-warning border-warning/20",
  low: "bg-muted text-muted-foreground",
};

export function DiscoveredRulesPanel({ docId }: DiscoveredRulesPanelProps) {
  const [rules, setRules] = useState<DiscoveredRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [promoting, setPromoting] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getDiscoveredRules(docId);
      setRules(data);
    } catch {
      setRules([]);
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => { load(); }, [load]);

  const handlePromote = async (index: number) => {
    setPromoting(index);
    try {
      await promoteDiscoveredRule(docId, index);
      setRules((prev) =>
        prev.map((r, i) => (i === index ? { ...r, promoted: true } : r)),
      );
      toast.success("Rule promoted to predefined");
    } catch {
      toast.error("Failed to promote rule");
    } finally {
      setPromoting(null);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="p-6 flex items-center justify-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading discovered rules...
        </CardContent>
      </Card>
    );
  }

  if (rules.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <Sparkles className="size-8 mx-auto text-muted-foreground mb-2" />
          <p className="text-sm text-muted-foreground">
            No auto-discovered rules yet. Run a compliance audit with cross-page reconciliation enabled.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-amber-500" />
          <CardTitle className="text-base">Auto-Discovered Rules</CardTitle>
          <Badge variant="secondary" className="text-[10px]">
            {rules.length}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pt-0 space-y-2 max-h-80 overflow-y-auto">
        {rules.map((rule, idx) => (
          <div
            key={idx}
            className={cn(
              "p-3 rounded-lg border transition-colors",
              rule.promoted ? "bg-success/5 border-success/20" : "bg-muted/30",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <p className="text-xs text-foreground flex-1">{rule.description}</p>
              {rule.promoted ? (
                <Badge variant="outline" className="text-[9px] px-1.5 py-0 text-success border-success/30 flex-shrink-0">
                  <CheckCircle className="size-2.5 mr-0.5" /> Promoted
                </Badge>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 text-[10px] px-2 flex-shrink-0"
                  onClick={() => handlePromote(idx)}
                  disabled={promoting === idx}
                >
                  {promoting === idx ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : (
                    <>
                      <ArrowUpToLine className="size-3 mr-0.5" /> Promote
                    </>
                  )}
                </Button>
              )}
            </div>
            <div className="flex items-center gap-1.5 mt-1.5">
              <Badge
                variant="outline"
                className={cn("text-[9px] px-1.5 py-0", PRIORITY_STYLES[rule.priority] || PRIORITY_STYLES.medium)}
              >
                {rule.priority}
              </Badge>
              {rule.sections_semantic.map((s) => (
                <Badge key={s} variant="outline" className="text-[9px] px-1 py-0 font-mono">
                  {s}
                </Badge>
              ))}
            </div>
            {rule.reasoning && (
              <p className="text-[10px] text-muted-foreground mt-1 leading-relaxed">{rule.reasoning}</p>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
