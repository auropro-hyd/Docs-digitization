# BMR Runs UI

> Back to [Wiki Index](../README.md) · See also [Frontend Overview](./overview.md), [Compliance Review](../backend/workflow/compliance-review.md)

The **BMR (Batch Manufacturing Record) runs** surface is the operator's view into the new audit pipeline (Spec 001 + Spec 007). It runs in parallel to the legacy single-document compliance flow and is purpose-built for **multi-document packages** that include a BPCR (Batch Product Compliance Record) plus supporting documents (raw-materials log, checklists, deviation forms, …).

## Terminology

| Term | Meaning |
|------|---------|
| **BMR** | The 5-stage audit *pipeline / runs surface* — Ingest → Legibility → Extraction → Compliance → Report |
| **BPCR** | An (optional) compliance *document* within a BMR package; the part the operator drills sub-sections into (cover_page, material_dispensing, manufacturing_operations, …) |
| **Package** | The uploaded artifact — one or more documents grouped under a `package_id` |
| **Run** | One execution of the 5-stage pipeline against one package — identified by `run_id` (UUID), persisted as `<base>/<run_id>/run.json` |

The legacy `/compliance` page operates on a single document and uses the [Report Renderer](../backend/report-renderer.md). The `/bmr/runs/*` pages operate on a package and surface the new pipeline's run state.

## Routes

| Route | File | Purpose |
|-------|------|---------|
| `/bmr/runs` | [`frontend/src/app/bmr/runs/page.tsx`](../../frontend/src/app/bmr/runs/page.tsx) | Runs list — every persisted run, newest first |
| `/bmr/runs/[runId]` | [`frontend/src/app/bmr/runs/[runId]/page.tsx`](../../frontend/src/app/bmr/runs/[runId]/page.tsx) | Run detail — header, live pipeline progress, BPCR sections panel, findings summary |

## Runs list (`/bmr/runs`)

Renders a table of every persisted run.

| Column | Source |
|--------|--------|
| Run | truncated `run_id` — clickable to detail page |
| Package | truncated `package_id` |
| Status | badge: `completed` (green), `failed` (red), `running` / `pending` (blue), other (gray) |
| Findings | `total_findings` (right-aligned) |
| Sections | `bpcr_section_count` (right-aligned) |
| Started | locale-formatted `started_at` |

States: error (red card), loading (spinner), empty (instructs the user to `POST /api/bmr/runs`).

Data flow — single REST fetch, no streaming on this page:

```tsx
useEffect(() => {
  listBmrRuns().then(setItems);
}, []);
```

## Run detail (`/bmr/runs/[runId]`)

Composed of four cards stacked vertically:

### 1. Run header card

Run id · Package id · Status badge · Stage label · four stat boxes:

| Stat | From |
|------|------|
| Rules evaluated | `rules_evaluated` |
| Rules loaded | `rules_loaded` |
| Skipped (deprecated) | `rules_skipped_deprecated` |
| Findings | `findings.length` |

### 2. Pipeline progress card (live)

[`run-stage-progress.tsx`](../../frontend/src/components/bmr/run-stage-progress.tsx) — a linear bar plus 5-step timeline driven by a WebSocket stream:

```
┌─────────┐   ┌─────────────────┐   ┌────────────┐   ┌────────────┐   ┌────────┐
│ Ingest  │ → │ Legibility &    │ → │ Extraction │ → │ Compliance │ → │ Report │
│         │   │ classify        │   │            │   │            │   │        │
└─────────┘   └─────────────────┘   └────────────┘   └────────────┘   └────────┘
   ●               ●                    ◐                ○                ○
  done            done                active           pending          pending
```

Drives off [`useBmrRunEvents(runId)`](../../frontend/src/hooks/useBmrRunEvents.ts) — see *WebSocket* below.

### 3. BPCR sections panel

[`bpcr-sections-panel.tsx`](../../frontend/src/components/bmr/bpcr-sections-panel.tsx) — renders per-page section assignments from `RunReport.bpcr_sections`, grouped by `doc_id`:

| Column | Notes |
|--------|-------|
| Page | 1-based |
| Section | `display_name` + `section_id` code (e.g. *Material Dispensing* + `material_dispensing`) |
| Confidence | Badge: ≥ 0.85 green, ≥ 0.6 yellow, < 0.6 gray |
| Detection method | `top_of_page`, `top_of_table`, `mid_page`, `alias`, `unsectioned` |

`section_id === "unsectioned"` renders muted (no code, no metadata) so the table communicates *"this page wasn't claimed by any section"* clearly.

Empty state: explanatory message — "no section assignments… (detection disabled, detector failed, or no BPCR doc)".

### 4. Findings summary card

Distribution rows by status (`pass` / `fail` / `unevaluated` / `skipped`) and severity (`critical` / `major` / `minor` / `observation`), each as an inline badge.

## Data shape (`RunReport`)

[`backend/app/bmr/workflow/models.py`](../../backend/app/bmr/workflow/models.py) — Pydantic; mirrored in [`frontend/src/types/bmr.ts`](../../frontend/src/types/bmr.ts).

```ts
type RunReport = {
  run_id: string;
  package_id: string;
  status: "pending" | "running" | "awaiting_legibility_review" | "completed" | "failed";
  stage: RunStage;
  started_at: string;
  finished_at?: string;
  rules_loaded: number;
  rules_evaluated: number;
  rules_skipped_deprecated: number;
  findings: FindingRecord[];
  // Spec 007 additions:
  bpcr_sections: BpcrSectionRow[];      // per-page assignments
  package_snapshot_hash: string;        // detect package drift on resume
  // Legibility HITL:
  legibility_reasons?: string[];
  legibility_decision?: "proceed" | "reupload";
  legibility_decided_at?: string;
  legibility_decided_by?: string;
  legibility_decision_note?: string;
};
```

## API surface

[`backend/app/api/routes/bmr_runs.py`](../../backend/app/api/routes/bmr_runs.py)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/bmr/runs` | Start a new run. Body: `{package_id, rules_dir?, aliases_dir?, extraction_path?}`. Returns `RunReport` (201) after the LangGraph completes. |
| `GET` | `/api/bmr/runs` | List all runs (newest first). Returns `RunListResponse` with lightweight rows. |
| `GET` | `/api/bmr/runs/{run_id}` | Fetch the persisted report. 404 if missing. |
| `GET` | `/api/bmr/runs/{run_id}/legibility` | Legibility-review state (reasons, decision, …). Only populated when `status == awaiting_legibility_review`. |
| `POST` | `/api/bmr/runs/{run_id}/legibility/decision` | Resume after legibility review. Body: `{action: "proceed" \| "reupload", note?}`. Re-runs the pipeline from legibility onward. |
| `WS` | `/api/bmr/runs/{run_id}/events` | Streams lifecycle events. |

All endpoints require an `X-Actor-Id` header (via `require_actor`). An optional bearer token is enforced if `AT_BMR__API_TOKEN` is configured.

## API client

[`frontend/src/lib/api.ts`](../../frontend/src/lib/api.ts) (around line 540):

```ts
function bmrAuthHeaders(): HeadersInit {
  const headers: HeadersInit = { "X-Actor-Id": "ui-user" };
  const token = process.env.NEXT_PUBLIC_BMR_API_TOKEN;
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

export async function getBmrRun(runId: string): Promise<RunReport> { … }
export async function listBmrRuns(): Promise<RunListResponse> { … }
```

The list page calls `listBmrRuns()` once on mount; the detail page calls `getBmrRun(runId)` and the WebSocket subscription handles the live updates.

## WebSocket — live progress

[`useBmrRunEvents.ts`](../../frontend/src/hooks/useBmrRunEvents.ts) subscribes to `/api/bmr/runs/{runId}/events`. Envelope shape:

```json
{
  "schema_version": "1.0",
  "event": "snapshot | bmr.stage.entered | bmr.stage.completed | run.completed | run.failed | …",
  "run_id": "…",
  "timestamp": "2026-04-16T…Z",
  "payload": { "stage": "…", "stage_index": N, … }
}
```

- A **snapshot** event is replayed on every fresh connect so the UI can render the current state without waiting for the next transition.
- A reducer (`reduceEnvelope`) distils envelopes into a `StageProgress` snapshot: `{ stage, stageIndex, total, finished }`.
- On socket close, the hook waits **2 seconds** and reconnects (no infinite tight-loop on outages).
- When `finished` flips true, the detail page re-fetches `getBmrRun(runId)` to populate findings + `bpcr_sections` (these come from the persisted report, not the event stream).

## Backend pipeline (briefly)

[`backend/app/bmr/workflow/graph.py`](../../backend/app/bmr/workflow/graph.py) — a LangGraph state graph:

```
START → ingest → legibility_and_classification → extraction → compliance → report → END
```

Conditional edges: any stage that sets `status=failed` or `status=awaiting_legibility_review` short-circuits to the `report` stage (per Constitution II — report always runs so persistence is guaranteed). Legibility resume re-enters at the legibility stage with the prior decision threaded through.

[`backend/app/bmr/workflow/service.py`](../../backend/app/bmr/workflow/service.py)'s `BMRRunService` orchestrates the graph, persists snapshots via `RunStore`, and publishes events to the in-process bus. The `_service()` factory in `api/routes/bmr_runs.py` wires the section enricher (Spec 007 detector, disabled via `AT_BMR__BPCR_SECTIONS_ENABLED=false`) and the event bus.

## Persistence

```
<base>/<run_id>/
└── run.json
```

`<base>` defaults to `<storage.base_path>/../bmr-runs/`. Writes are atomic (`.tmp` then rename). No index file — listing re-reads all reports each call (acceptable at current volume; the bus event stream is what callers should subscribe to for hot data).

## Recent fixes

| Commit | Layer | Fix |
|--------|-------|-----|
| `96a826e` | frontend | Keep agent-card export buttons inside the card on narrow widths |
| `7b6b5fe` | frontend | Live stage progress on `/bmr/runs/[runId]` via WebSocket |
| `787f3df` | frontend + backend | Run-list page + enriched list endpoint (findings count, sections count) |
| `7d36b3c` | frontend | Minimal run-detail page surfacing `bpcr_sections` |
| `65eaf4d` | backend | Transition-aware section picking — closes the last detection gap |
| `c776b40` | backend | Close 4 BPCR sub-section coverage gaps on real doc |
| `07365d7` | backend | Enrich `RunReport.bpcr_sections` rows with detector metadata (confidence, method, display_name) |
| `518b8e2` | backend | Align BPCR section vocab + markdown fallback so detection runs without OCR sidecar |
| `95471aa` | backend | Wire BPCR section detection into the production audit pipeline |
| `524bb63` | backend | Wire observability into BMR domain (event bus + lifecycle event streaming) |

## Known limitations

- The runs list has **no pagination** — every persisted run is loaded on each page load. Fine at current volume; will need an index file or DB before this gets uncomfortable.
- The legibility HITL surface is **API-only** for now — the operator interacts via `POST /api/bmr/runs/{run_id}/legibility/decision`, not via a UI form. A dedicated review panel is a planned follow-up.
- Findings drill-down on the detail page is summary-only (distribution by status / severity). For per-finding HITL, the operator currently uses the legacy `/compliance?doc={doc_id}` page.

## Spec

[`specs/001-bmr-audit-pipeline/`](../../specs/001-bmr-audit-pipeline/) — full spec dir:

- `spec.md` — user stories, acceptance scenarios, requirements
- `data-model.md` — `BMRAuditRun`, `PipelineStageState`, `Finding`, `ContextObject`, `StructuredResolution`, `Correction`; state machines and invariants
- `plan.md` — implementation roadmap
- `quickstart.md` — quick reference for running the pipeline
- `research.md` — background on BMR / ALCOA / GMP / prior art

[`specs/007-bpcr-section-detection/`](../../specs/007-bpcr-section-detection/) — heuristic section detector that powers the BPCR sections panel.

## Related Pages

- [Frontend Overview](./overview.md) — Application pages, tech stack, state model
- [Compliance Review](../backend/workflow/compliance-review.md) — Rule evaluation (called from the `compliance` stage)
- [Document Segmentation & BPCR Detection](../backend/workflow/segmentation.md) — How the BPCR's sub-sections are detected (Spec 011 + Spec 007)
- [Report Renderer](../backend/report-renderer.md) — Powers the legacy `/compliance` export (BMR runs export is API-only today)
- [WebSocket Streaming](./websocket-streaming.md) — General streaming architecture
