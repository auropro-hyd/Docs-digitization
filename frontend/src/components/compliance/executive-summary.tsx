"use client";

import { motion } from "framer-motion";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ProgressRing } from "@/components/common/progress-ring";
import { cn } from "@/lib/utils";
import {
  ShieldCheck,
  AlertTriangle,
  CheckCircle,
  FileText,
  Clock,
  Cpu,
} from "lucide-react";

interface ExecutiveSummaryProps {
  report: {
    overall_score: number;
    total_findings: number;
    document_type: string;
    executive_summary: {
      overall_assessment: string;
      key_risks: string[];
      strengths: string[];
      priority_actions: string[];
    };
    severity_counts: Record<string, number>;
    audit_trail?: {
      duration_seconds: number;
      total_llm_calls: number;
      total_rules_evaluated: number;
      evaluator_model: string;
      orchestrator_model: string;
    };
    agent_reports: { agent: string; agent_display: string; score: number; total_findings: number }[];
  };
}

const DOC_TYPE_LABELS: Record<string, string> = {
  batch_record: "Batch Record",
  sop: "SOP",
  protocol: "Protocol",
  certificate: "Certificate",
  logbook: "Logbook",
  other: "Other",
};

export function ExecutiveSummary({ report }: ExecutiveSummaryProps) {
  const score = report.overall_score;
  const scoreColor = score >= 80 ? "text-success" : score >= 60 ? "text-warning" : "text-destructive";
  const summary = report.executive_summary;
  const trail = report.audit_trail;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-4"
    >
      <Card>
        <CardContent className="p-6">
          <div className="flex flex-col lg:flex-row gap-6">
            {/* Score ring */}
            <div className="flex flex-col items-center gap-3">
              <ProgressRing value={score} size={140} strokeWidth={12}>
                <div className="text-center">
                  <p className={cn("text-3xl font-bold", scoreColor)}>{Math.round(score)}</p>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wider">Score</p>
                </div>
              </ProgressRing>
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  <FileText className="size-3 mr-1" />
                  {DOC_TYPE_LABELS[report.document_type] || report.document_type}
                </Badge>
                <Badge variant="outline" className="text-xs">
                  {report.total_findings} finding{report.total_findings !== 1 ? "s" : ""}
                </Badge>
              </div>
            </div>

            {/* Assessment + severity */}
            <div className="flex-1 space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-foreground mb-1">Overall Assessment</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {summary.overall_assessment || "No assessment available."}
                </p>
              </div>

              {/* Severity pills */}
              <div className="flex flex-wrap gap-2">
                {[
                  { key: "critical", label: "Critical", cls: "bg-destructive/10 text-destructive border-destructive/20" },
                  { key: "major", label: "Major", cls: "bg-warning/10 text-warning border-warning/20" },
                  { key: "minor", label: "Minor", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400 border-amber-200" },
                  { key: "observation", label: "Observation", cls: "bg-muted text-muted-foreground border-border" },
                ].map(({ key, label, cls }) => {
                  const count = report.severity_counts[key] || 0;
                  if (!count) return null;
                  return (
                    <Badge key={key} variant="outline" className={cn("text-xs", cls)}>
                      {count} {label}
                    </Badge>
                  );
                })}
              </div>

              {/* Audit trail summary */}
              {trail && (
                <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground pt-1">
                  <span className="flex items-center gap-1">
                    <Clock className="size-3" /> {Math.round(trail.duration_seconds)}s
                  </span>
                  <span className="flex items-center gap-1">
                    <Cpu className="size-3" /> {trail.total_llm_calls} LLM calls
                  </span>
                  <span>{trail.total_rules_evaluated} rules evaluated</span>
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Key risks + strengths + priority actions */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {summary.key_risks?.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <AlertTriangle className="size-4 text-destructive" /> Key Risks
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ul className="space-y-1.5">
                {summary.key_risks.map((r, i) => (
                  <li key={i} className="text-xs text-muted-foreground flex gap-2">
                    <span className="text-destructive font-medium">•</span> {r}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {summary.strengths?.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <CheckCircle className="size-4 text-success" /> Strengths
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ul className="space-y-1.5">
                {summary.strengths.map((s, i) => (
                  <li key={i} className="text-xs text-muted-foreground flex gap-2">
                    <span className="text-success font-medium">•</span> {s}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {summary.priority_actions?.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <ShieldCheck className="size-4 text-primary" /> Priority Actions
              </CardTitle>
            </CardHeader>
            <CardContent className="pt-0">
              <ol className="space-y-1.5 list-decimal list-inside">
                {summary.priority_actions.map((a, i) => (
                  <li key={i} className="text-xs text-muted-foreground">{a}</li>
                ))}
              </ol>
            </CardContent>
          </Card>
        )}
      </div>
    </motion.div>
  );
}
