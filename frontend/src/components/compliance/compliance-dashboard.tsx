"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import {
  ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
} from "recharts";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ProgressRing } from "@/components/common/progress-ring";
import { cn, formatPageRanges } from "@/lib/utils";
import {
  ShieldCheck,
  AlertTriangle,
  AlertCircle,
  Info,
  CheckCircle,
} from "lucide-react";

interface Finding {
  finding_id: string;
  rule_id: string;
  rule_category: string;
  severity: "critical" | "major" | "minor" | "observation";
  description: string;
  recommendation?: string;
  page_numbers: number[];
  resolved?: boolean;
}

interface ComplianceDashboardProps {
  score: number;
  findings: Finding[];
}

const SEVERITY_CONFIG: Record<string, { label: string; iconClass: string; bgClass: string; badgeClass: string; icon: typeof AlertCircle }> = {
  critical: { label: "Critical", iconClass: "text-destructive", bgClass: "bg-destructive/10", badgeClass: "bg-destructive/10 text-destructive border-destructive/20", icon: AlertCircle },
  major: { label: "Major", iconClass: "text-warning", bgClass: "bg-warning/10", badgeClass: "bg-warning/10 text-warning border-warning/20", icon: AlertTriangle },
  minor: { label: "Minor", iconClass: "text-warning", bgClass: "bg-warning/10", badgeClass: "bg-warning/10 text-warning border-warning/20", icon: Info },
  observation: { label: "Observation", iconClass: "text-success", bgClass: "bg-success/10", badgeClass: "bg-success/10 text-success border-success/20", icon: CheckCircle },
};

const CATEGORY_LABELS: Record<string, string> = {
  alcoa: "ALCOA+",
  gmp: "GMP",
  checklist: "Checklist",
  sop: "SOP",
};

export function ComplianceDashboard({ score, findings }: ComplianceDashboardProps) {
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");

  const filteredFindings = findings.filter((f) => {
    if (severityFilter !== "all" && f.severity !== severityFilter) return false;
    if (categoryFilter !== "all" && f.rule_category !== categoryFilter) return false;
    return true;
  });

  const severityCounts = {
    critical: findings.filter((f) => f.severity === "critical").length,
    major: findings.filter((f) => f.severity === "major").length,
    minor: findings.filter((f) => f.severity === "minor").length,
    observation: findings.filter((f) => f.severity === "observation").length,
  };

  const categories = [...new Set(findings.map((f) => f.rule_category))];
  const categoryData = categories.map((cat) => ({
    name: CATEGORY_LABELS[cat] || cat,
    count: findings.filter((f) => f.rule_category === cat).length,
  }));

  const scoreColor = score >= 80 ? "text-success" : score >= 60 ? "text-warning" : "text-destructive";

  return (
    <div className="space-y-6">
      {/* Score + severity overview */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Score gauge */}
        <Card className="lg:col-span-1">
          <CardContent className="p-6 flex flex-col items-center justify-center">
            <ProgressRing value={score} size={140} strokeWidth={12}>
              <div className="text-center">
                <p className={cn("text-3xl font-bold", scoreColor)}>{score}</p>
                <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Score</p>
              </div>
            </ProgressRing>
            <p className="text-sm font-medium text-foreground mt-4">Compliance Score</p>
            <p className="text-xs text-muted-foreground">
              {findings.length} finding{findings.length !== 1 ? "s" : ""} across {categories.length} categor{categories.length !== 1 ? "ies" : "y"}
            </p>
          </CardContent>
        </Card>

        {/* Severity breakdown */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Severity Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-4 gap-3 mb-4">
              {(["critical", "major", "minor", "observation"] as const).map((sev) => {
                const config = SEVERITY_CONFIG[sev];
                const Icon = config.icon;
                return (
                  <div key={sev} className="text-center">
                    <div className={cn("inline-flex items-center justify-center size-8 rounded-lg mb-1", config.bgClass)}>
                      <Icon className={cn("size-4", config.iconClass)} />
                    </div>
                    <p className="text-xl font-semibold text-foreground">{severityCounts[sev]}</p>
                    <p className="text-[10px] text-muted-foreground">{config.label}</p>
                  </div>
                );
              })}
            </div>
            {categoryData.length > 0 && (
              <div className="h-32">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={categoryData} layout="vertical" margin={{ left: 10, right: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border" horizontal={false} />
                    <XAxis type="number" className="text-[10px]" tick={{ fill: "var(--muted-foreground)" }} />
                    <YAxis type="category" dataKey="name" className="text-[10px]" width={60} tick={{ fill: "var(--muted-foreground)" }} />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: "var(--card)",
                        border: "1px solid var(--border)",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                    />
                    <Bar dataKey="count" fill="var(--primary)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Findings list */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <CardTitle className="text-sm">All Findings</CardTitle>
            <div className="flex items-center gap-2">
              <Select value={severityFilter} onValueChange={setSeverityFilter}>
                <SelectTrigger className="h-8 w-32 text-xs">
                  <SelectValue placeholder="Severity" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All severity</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="major">Major</SelectItem>
                  <SelectItem value="minor">Minor</SelectItem>
                  <SelectItem value="observation">Observation</SelectItem>
                </SelectContent>
              </Select>
              <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                <SelectTrigger className="h-8 w-32 text-xs">
                  <SelectValue placeholder="Category" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All categories</SelectItem>
                  {categories.map((cat) => (
                    <SelectItem key={cat} value={cat}>
                      {CATEGORY_LABELS[cat] || cat}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          {filteredFindings.length === 0 ? (
            <div className="flex flex-col items-center py-8 text-center">
              <ShieldCheck className="size-8 text-success mb-2" />
              <p className="text-sm font-medium text-foreground">No compliance issues found</p>
              <p className="text-xs text-muted-foreground">All checks passed for the current filters</p>
            </div>
          ) : (
            <Accordion type="multiple" className="space-y-2">
              {filteredFindings.map((finding, i) => {
                const config = SEVERITY_CONFIG[finding.severity] || SEVERITY_CONFIG.observation;
                const Icon = config.icon;
                return (
                  <motion.div
                    key={finding.finding_id}
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * 0.03 }}
                  >
                    <AccordionItem value={finding.finding_id} className="border rounded-lg px-4">
                      <AccordionTrigger className="hover:no-underline py-3">
                        <div className="flex items-center gap-3 text-left">
                          <Icon className={cn("size-4 flex-shrink-0", config.iconClass)} />
                          <div className="min-w-0">
                            <p className="text-sm font-medium text-foreground truncate">{finding.rule_id}</p>
                            <div className="flex items-center gap-2 mt-0.5">
                              <Badge variant="outline" className={cn("text-[10px]", config.badgeClass)}>
                                {config.label}
                              </Badge>
                              <Badge variant="outline" className="text-[10px]">
                                {CATEGORY_LABELS[finding.rule_category] || finding.rule_category}
                              </Badge>
                              {finding.resolved && (
                                <Badge variant="outline" className="text-[10px] border-success/30 text-success">
                                  Resolved
                                </Badge>
                              )}
                            </div>
                          </div>
                        </div>
                      </AccordionTrigger>
                      <AccordionContent className="pb-4">
                        <p className="text-sm text-muted-foreground mb-3">{finding.description}</p>
                        {finding.recommendation && (
                          <div className="p-3 rounded-lg bg-muted text-sm">
                            <p className="text-xs font-medium text-foreground mb-1">Recommendation</p>
                            <p className="text-xs text-muted-foreground">{finding.recommendation}</p>
                          </div>
                        )}
                        {finding.page_numbers && finding.page_numbers.length > 0 && (
                          <div className="mt-2">
                            <p className="text-xs text-muted-foreground">
                              Affected pages: {formatPageRanges(finding.page_numbers).display}
                            </p>
                          </div>
                        )}
                      </AccordionContent>
                    </AccordionItem>
                  </motion.div>
                );
              })}
            </Accordion>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
