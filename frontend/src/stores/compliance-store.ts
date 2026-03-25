import { create } from "zustand";

export type CompliancePhase = "idle" | "orchestrator" | "segmentation" | "evaluation" | "report" | "complete" | "error";

export interface RuleProgress {
  id: string;
  text: string;
  category: string;
  severity: string;
  status: "pending" | "evaluating" | "compliant" | "non_compliant" | "not_applicable" | "uncertain";
  confidence: number;
}

export interface PrescreenProgress {
  pagesDone: number;
  pagesTotal: number;
  percent: number;
  totalRules: number;
  avgApplicable: number | null;
  status: "idle" | "running" | "complete";
}

export interface AgentProgress {
  agent: string;
  status: "pending" | "prescreening" | "running" | "complete" | "skipped";
  batchesComplete: number;
  batchesTotal: number;
  percent: number;
  label: string;
  findingsCount: number;
  needsReviewCount: number;
  skipReason?: string;
  rules: RuleProgress[];
  prescreen: PrescreenProgress;
}

export interface ComplianceProgressState {
  phase: CompliancePhase;
  overallPercent: number;
  label: string;
  agents: Record<string, AgentProgress>;
  applicableAgents: string[];
  skippedAgents: { category: string; reason: string }[];
  documentType: string;
  overallScore: number | null;
  totalFindings: number;
  startedAt: number | null;
  segmentationLabel: string;
  segmentationSections: number;
}

interface ComplianceStore extends ComplianceProgressState {
  startRun: () => void;
  handleProgress: (data: Record<string, unknown>) => void;
  reset: () => void;
}

const ALL_AGENTS = ["alcoa", "gmp", "checklist", "sop", "reconciliation"];

const defaultPrescreen = (): PrescreenProgress => ({
  pagesDone: 0,
  pagesTotal: 0,
  percent: 0,
  totalRules: 0,
  avgApplicable: null,
  status: "idle",
});

const initialAgents = (): Record<string, AgentProgress> =>
  Object.fromEntries(
    ALL_AGENTS.map((a) => [
      a,
      {
        agent: a,
        status: "pending" as const,
        batchesComplete: 0,
        batchesTotal: 0,
        percent: 0,
        label: "",
        findingsCount: 0,
        needsReviewCount: 0,
        rules: [],
        prescreen: defaultPrescreen(),
      },
    ]),
  );

const INITIAL: ComplianceProgressState = {
  phase: "idle",
  overallPercent: 0,
  label: "",
  agents: initialAgents(),
  applicableAgents: [],
  skippedAgents: [],
  documentType: "",
  overallScore: null,
  totalFindings: 0,
  startedAt: null,
  segmentationLabel: "",
  segmentationSections: 0,
};

export const useComplianceStore = create<ComplianceStore>((set, get) => ({
  ...INITIAL,

  startRun: () =>
    set({ ...INITIAL, phase: "orchestrator", label: "Analyzing document type...", startedAt: Date.now(), agents: initialAgents() }),

  handleProgress: (data) => {
    const phase = data.phase as string;
    const status = data.status as string;

    if (phase === "orchestrator" && status === "complete") {
      const applicable = (data.applicable as string[]) || [];
      const skipped = (data.skipped as { category: string; reason: string }[]) || [];
      const docType = (data.document_type as string) || "";

      const agents = { ...get().agents };
      for (const a of applicable) {
        if (agents[a]) agents[a] = { ...agents[a], status: "pending" };
      }
      for (const s of skipped) {
        if (agents[s.category])
          agents[s.category] = { ...agents[s.category], status: "skipped", skipReason: s.reason };
      }

      set({
        phase: "evaluation",
        applicableAgents: applicable,
        skippedAgents: skipped,
        documentType: docType,
        agents,
        overallPercent: 10,
        label: "Starting agent evaluation...",
      });
      return;
    }

    if (phase === "orchestrator" && status === "running") {
      set({ phase: "orchestrator", label: (data.label as string) || "Analyzing document...", overallPercent: 5 });
      return;
    }

    if (phase === "segmentation") {
      if (status === "running") {
        set({
          phase: "segmentation",
          label: (data.label as string) || "Identifying document sections...",
          segmentationLabel: (data.label as string) || "Identifying document sections...",
          overallPercent: 8,
        });
      } else if (status === "complete") {
        const sections = (data.sections_count as number) || 0;
        set({
          phase: "evaluation",
          label: "Starting agent evaluation...",
          segmentationLabel: (data.label as string) || `Identified ${sections} sections`,
          segmentationSections: sections,
          overallPercent: 10,
        });
      }
      return;
    }

    if (phase === "evaluation") {
      const agentName = data.agent as string;
      if (!agentName) return;

      if (get().phase !== "evaluation") {
        set({ phase: "evaluation" });
      }

      const agents = { ...get().agents };
      const prev = agents[agentName] || initialAgents()[agentName];

      if (status === "prescreening") {
        const pagesDone = (data.prescreen_pages_done as number) || 0;
        const pagesTotal = (data.prescreen_pages_total as number) || 0;
        const prescreenPct = (data.prescreen_percent as number) || 0;
        const totalRules = (data.prescreen_total_rules as number) || 0;

        agents[agentName] = {
          ...prev,
          status: "prescreening",
          label: (data.label as string) || prev.label,
          prescreen: {
            pagesDone,
            pagesTotal,
            percent: prescreenPct,
            totalRules,
            avgApplicable: null,
            status: "running",
          },
        };

        set({ agents, label: (data.label as string) || get().label });
        return;
      }

      if (status === "prescreen_complete") {
        const totalRules = (data.prescreen_total_rules as number) || 0;
        const avgApplicable = (data.prescreen_avg_applicable as number) ?? null;
        const pagesTotal = (data.prescreen_pages_total as number) || 0;

        agents[agentName] = {
          ...prev,
          status: "prescreening",
          label: (data.label as string) || prev.label,
          prescreen: {
            pagesDone: pagesTotal,
            pagesTotal,
            percent: 100,
            totalRules,
            avgApplicable,
            status: "complete",
          },
        };

        set({ agents, label: (data.label as string) || get().label });
        return;
      }

      if (status === "running") {
        let rules = prev.rules;

        const incomingRules = data.rules as Array<{ id: string; text: string; category: string; severity: string }> | undefined;
        if (incomingRules && incomingRules.length > 0 && rules.length === 0) {
          rules = incomingRules.map((r) => ({
            id: r.id,
            text: r.text,
            category: r.category,
            severity: r.severity,
            status: "pending" as const,
            confidence: 1.0,
          }));
        }

        const ruleUpdates = data.rule_updates as Array<{ rule_id: string; status: string; confidence: number }> | undefined;
        if (ruleUpdates && ruleUpdates.length > 0) {
          const ruleMap = new Map(rules.map((r) => [r.id, r]));
          for (const upd of ruleUpdates) {
            const existing = ruleMap.get(upd.rule_id);
            if (existing) {
              const validStatuses = ["compliant", "non_compliant", "not_applicable", "uncertain"] as const;
              const newStatus = validStatuses.includes(upd.status as typeof validStatuses[number])
                ? (upd.status as RuleProgress["status"])
                : "uncertain";
              ruleMap.set(upd.rule_id, {
                ...existing,
                status: newStatus,
                confidence: Math.min(existing.confidence, upd.confidence),
              });
            }
          }
          rules = Array.from(ruleMap.values());
        }

        agents[agentName] = {
          ...prev,
          status: "running",
          batchesComplete: (data.batches_complete as number) || prev.batchesComplete,
          batchesTotal: (data.batches_total as number) || prev.batchesTotal,
          percent: (data.percent as number) || prev.percent,
          label: (data.label as string) || prev.label,
          rules,
        };
      } else if (status === "complete") {
        const completedRules = prev.rules.map((r) => ({
          ...r,
          status: r.status === "pending" ? ("not_applicable" as const) : r.status,
        }));

        agents[agentName] = {
          ...prev,
          status: "complete",
          percent: 100,
          findingsCount: (data.findings_count as number) || 0,
          needsReviewCount: (data.needs_review_count as number) || 0,
          rules: completedRules,
        };
      }

      const applicable = get().applicableAgents;
      const completedCount = applicable.filter((a) => agents[a]?.status === "complete").length;
      const totalAgents = applicable.length || 1;
      const agentPercents = applicable.map((a) => agents[a]?.percent || 0);
      const avgPercent = agentPercents.reduce((s, p) => s + p, 0) / totalAgents;
      const overallPercent = Math.round(10 + avgPercent * 0.8);

      set({
        agents,
        overallPercent,
        label: (data.label as string) || `${completedCount}/${totalAgents} agents complete`,
      });
      return;
    }

    if (phase === "report") {
      set({ phase: "report", overallPercent: 92, label: (data.label as string) || "Generating report..." });
      return;
    }

    if (phase === "complete") {
      set({
        phase: "complete",
        overallPercent: 100,
        overallScore: (data.overall_score as number) ?? null,
        totalFindings: (data.total_findings as number) || 0,
        label: "Compliance audit complete",
      });
      return;
    }

    if (phase === "error") {
      set({ phase: "error", label: (data.label as string) || "Audit failed" });
    }
  },

  reset: () => set({ ...INITIAL, agents: initialAgents() }),
}));
