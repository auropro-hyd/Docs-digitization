"use client";

import { motion } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface CategoryScore {
  category_id: string;
  category_display: string;
  score: number;
  total_rules: number;
  compliant: number;
  non_compliant: number;
  not_applicable: number;
  uncertain: number;
  finding_ids: string[];
}

interface AgentReportData {
  agent: string;
  agent_display: string;
  score: number;
  model_score?: number;
  review_adjusted_score?: number;
  total_rules: number;
  total_findings: number;
  severity_counts: Record<string, number>;
  category_scores: CategoryScore[];
  pages_reviewed: number[];
}

interface AgentScorecardProps {
  report: AgentReportData;
  onFindingClick?: (findingId: string) => void;
}

export function AgentScorecard({ report, onFindingClick }: AgentScorecardProps) {
  // Prefer the review-adjusted score so reviewer rejects/approvals visibly
  // move the headline number. Fall back to the legacy score field only if
  // review_adjusted_score is unset (old persisted reports).
  const displayScore =
    typeof report.review_adjusted_score === "number"
      ? report.review_adjusted_score
      : report.score;
  const modelScore =
    typeof report.model_score === "number" ? report.model_score : report.score;
  const scoreImproved =
    typeof report.review_adjusted_score === "number" &&
    typeof report.model_score === "number" &&
    report.review_adjusted_score > report.model_score;
  const scoreColor =
    displayScore >= 80 ? "text-success" : displayScore >= 60 ? "text-warning" : "text-destructive";

  return (
    <div className="space-y-4">
      {/* Agent header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">{report.agent_display}</h3>
          <p className="text-sm text-muted-foreground">
            {report.total_rules} rules evaluated across {report.pages_reviewed?.length || 0} pages
          </p>
        </div>
        <div className="text-right">
          <p className={cn("text-2xl font-bold", scoreColor)}>{Math.round(displayScore)}</p>
          <p className="text-[10px] text-muted-foreground uppercase tracking-wider">
            Review-adjusted
          </p>
          {modelScore !== displayScore && (
            <p
              className={cn(
                "text-[10px] mt-0.5",
                scoreImproved ? "text-success" : "text-muted-foreground",
              )}
              title="Score before reviewer actions (rejected findings excluded from penalty)."
            >
              Model: {Math.round(modelScore)}
            </p>
          )}
        </div>
      </div>

      {/* Severity summary */}
      <div className="flex gap-2 flex-wrap">
        {Object.entries(report.severity_counts || {}).map(([sev, count]) => {
          if (!count) return null;
          const cls =
            sev === "critical"
              ? "bg-destructive/10 text-destructive"
              : sev === "major"
                ? "bg-warning/10 text-warning"
                : sev === "minor"
                  ? "bg-amber-100 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400"
                  : "bg-muted text-muted-foreground";
          return (
            <Badge key={sev} variant="outline" className={cn("text-xs", cls)}>
              {count} {sev}
            </Badge>
          );
        })}
        {report.total_findings === 0 && (
          <Badge variant="outline" className="text-xs text-success border-success/20">
            All compliant
          </Badge>
        )}
      </div>

      {/* Category grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {report.category_scores.map((cat, i) => (
          <motion.div
            // Some agents emit the same ``category_id`` twice (e.g. a
            // shared category split by sub-criteria). Suffix the index
            // so React's reconciler doesn't get duplicate-key warnings
            // and tear one of the cards down on every re-render.
            key={`${cat.category_id}-${i}`}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03 }}
          >
            <CategoryCard category={cat} onFindingClick={onFindingClick} />
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function CategoryCard({
  category,
  onFindingClick,
}: {
  category: CategoryScore;
  onFindingClick?: (findingId: string) => void;
}) {
  const scoreColor =
    category.score >= 80 ? "text-success" : category.score >= 60 ? "text-warning" : "text-destructive";
  const progressColor =
    category.score >= 80 ? "[&>div]:bg-success" : category.score >= 60 ? "[&>div]:bg-warning" : "[&>div]:bg-destructive";

  return (
    <Card className="hover:shadow-sm transition-shadow">
      <CardContent className="p-4 space-y-2">
        <div className="flex items-start justify-between">
          <h4 className="text-sm font-medium leading-tight">{category.category_display}</h4>
          <span className={cn("text-lg font-bold tabular-nums", scoreColor)}>
            {Math.round(category.score)}
          </span>
        </div>

        <Progress value={category.score} className={cn("h-1.5", progressColor)} />

        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{category.total_rules} rules</span>
          <span>
            {category.compliant} ok · {category.non_compliant} fail
            {category.uncertain > 0 && ` · ${category.uncertain} uncertain`}
          </span>
        </div>

        {category.finding_ids.length > 0 && onFindingClick && (
          <div className="flex flex-wrap gap-1 pt-1">
            {category.finding_ids.slice(0, 5).map((fid) => (
              <button
                key={fid}
                onClick={() => onFindingClick(fid)}
                className="text-[10px] text-primary hover:underline"
              >
                {fid}
              </button>
            ))}
            {category.finding_ids.length > 5 && (
              <span className="text-[10px] text-muted-foreground">
                +{category.finding_ids.length - 5} more
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
