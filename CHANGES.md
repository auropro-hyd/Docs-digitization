# Changes since `main` — PR Walkthrough for Technical Review

**Audience**: technical manager. Framed to explain *why* each bucket exists, not to walk each commit.
**Scope**: 32 commits, 225 files, +30,788 / −87 LOC on branch `006-observability-and-finding-semantics`, ahead of `origin/main` at `48b1902`.
**Unified PR posture**: all of the work below ships in one PR stack — the BMR v0 vertical slice + its review-driven hardening + the observability + finding-semantics fix. Suite is 265 tests green; ruff + pyright clean on every new surface; frontend `tsc --noEmit` clean.

---

## TL;DR for the manager

Three buckets.

1. **BMR v0 pipeline (Specs 001–005)** — lands the full Batch Manufacturing Record audit pipeline as a single vertical slice, plus the authoring-side tooling for declarative compliance rules. This is the original PR #2. Single commit, ~25 KLOC across 178 files.
2. **Review-driven hardening of PR #2 (22 commits)** — findings from a structured review of that PR fixed in place on the same branch. Critical fixes: auth on every BMR endpoint; path-traversal resistance; drop-safe compliance fan-out; per-run HITL locking; content-hash reproducibility; scalar-only correction payloads; upload/OCR size caps; supersession cycle detection.
3. **Observability + Finding-semantics (Spec 006, new this PR branch)** — cross-cutting observability (W3C `traceparent`, structured JSON logs, Prometheus metrics, `/health*` endpoints) written against the OpenTelemetry shape so a future OTLP exporter is a one-line swap. Plus the real compliance-report UX bug surfaced during the engagement: `auto_approved` findings were painted success-green and made reviewers misread failures as passes.

Test coverage at the end: **265 tests** (218 BMR + 35 observability + 12 compliance). Overhead benchmark passes ≤ 5 ms p95. Production scoring is numerically unchanged on the existing pilot document.

---

## Scope at a glance

| Area | Touched | Notes |
|---|---|---|
| `backend/app/` | ≈ 60 files | Largest share: ingest, workflow, compliance, hitl, rules, observability |
| `backend/tests/` | ≈ 50 files | New BMR + observability + compliance suites |
| `frontend/src/` | 3 files | HITL badge rewrite, trace-fetch init, compliance report rollups |
| `specs/` | 20 files | Specs 001–005 (BMR v0) + Spec 006 (observability) |
| `.specify/` | 19 files | Speckit scaffolding + templates + workflow |

Dependency additions (backend): `structlog>=24`, `prometheus-client>=0.20`, `opentelemetry-api>=1.25`. No heavyweight OTEL SDK; shape-only for now.

---

## Bucket 1 — BMR audit pipeline v0 (Specs 001–005)

Original PR #2. One commit, `3b02a96`. End-to-end vertical slice so the pilot can run against a real BPCR.

**What shipped**

- **5-stage LangGraph pipeline**: `ingest → legibility / classification → extraction → compliance → report`. Pluggable Stage-3 extractor port (`SidecarExtractor` for fixtures, `OCRBackedExtractor` for production).
- **Parallel rule fan-out** on the compliance stage (per-rule `ThreadPoolExecutor`).
- **Declarative rule engine** (Specs 002 / 003): cross-document, page-aggregate, same-page evaluators; aliases; tolerances; multiplicity; fallback policies; checklist synthesis.
- **Versioned rule-spec schema** (Spec 005): JSON Schema v1.0 (Draft 2020-12) with auto-generated Markdown reference + CHANGELOG. `content_hash` stamping for deterministic replay. `deprecated` / `superseded_by` lifecycle.
- **HITL subsystem** (Spec 004): structured resolutions (`CONFIRM` / `DISMISS` / `CORRECT`), selective rule re-evaluation, immutable `AuditReportRevision` chain, WeasyPrint PDF export.
- **API surface**: `POST /api/bmr/packages`, `POST /api/bmr/runs`, `POST /api/bmr/runs/{id}/hitl/*`, WebSocket `/ws` run events.
- **`bmr-rules` CLI**: `validate | fixture-run | diff` for CI + pre-commit.
- **`bmr-rule-author` Cursor skill scaffolding**: author / tune / migrate templates.
- **Pilot config**: manifest + 3 seed rules + alias table + severity gating.
- **Speckit integration**: `.specify/` scaffolding, spec templates, workflow `specify → plan → tasks → implement`, and the constitution (`v1.1.0`).

**Why this matters to the business**

This is the delivery of the pilot — the first end-to-end run against real BPCRs. Without it, there is no product to review. Everything downstream in this PR hardens or instruments it; it does not extend it.

---

## Bucket 2 — Review-driven hardening of PR #2 (22 commits)

After the v0 slice landed, a structured review across four dimensions (security, correctness, data integrity, robustness) produced a block-before-merge list. Each blocker became its own commit on the same branch so the history reads as one reviewable narrative.

Grouped by what the manager actually cares about:

### 2a. Security (3 commits)

| Commit | Issue | Fix |
|---|---|---|
| `4e60cd3` | Every BMR endpoint trusted `X-Actor-Id` as-is; anyone could spoof the audit trail | `require_actor` FastAPI dependency — validates the header format; optionally checks a shared bearer (`AT_BMR__API_TOKEN`). Wired into every `/api/bmr/*` route. |
| `8cbe16c` | `rules_dir` / `aliases_dir` / `extraction_path` from the request body landed in `Path()` with no containment check. Revision IDs interpolated into filesystem paths. | Safe-ID regex on IDs; `resolve().is_relative_to(base)` containment check; whitelist of allowed config roots on the run-spec paths. |
| `fe2f767` | No size caps anywhere — a 10 GB upload or a billion-laughs YAML manifest would exhaust a worker | Hard caps: 200 MiB per package upload, 50 files per package, 1 MiB per manifest, 500 OCR pages per document. All env-overridable. |

### 2b. Data integrity / audit-trail correctness (8 commits)

These are the ones the regulator would care about if we shipped without them.

| Commit | Issue | Fix |
|---|---|---|
| `9b27e50` | Compliance fan-out silently dropped findings when a single leaf rule crashed | Collect every future; wrap in `try/except`; emit an `INDETERMINATE` marker finding for the crashed rule so nothing disappears from the report |
| `49d2882` | Rule `content_hash` depended on YAML int-vs-float encoding. Two semantically identical rules produced different hashes. | Canonicalize numeric leaves (all numerics → float + repr) before JSON canonicalisation. Reproducibility guaranteed across authoring round-trips. |
| `b1a1e5e` | `superseded_by` had no cycle detection; `DISMISS/DUPLICATE_FINDING` accepted references to nonexistent findings | Post-load supersession-chain cycle check; resolution-time finding-existence check |
| `8b89b36` | `SidecarExtractor` silently accepted an extraction.json with a mismatched `package_id` via `setdefault` | Raise `ExtractionPackageMismatchError`; extraction stage turns it into a FAILED run with the id mismatch in the error |
| `59b6300` | Checklist-synthesis rollup could *downgrade* severity below a constituent — a `critical` child under an `info` synthesis rule would export as `info` and slip the gate | Take `max(worst_constituent, declared_severity)` so rollup severity can only match or exceed the worst child |
| `c099245` | `treat_as_pass` fallback emitted findings with `status=PASS` and no way to tell them apart from genuine passes | New `fallback_applied` field on `FindingDraft` + `FindingRecord`; propagates through projection + HITL re-eval |
| `35ab8b3` | `UNEVALUATED` findings shipped with empty evidence, violating the module's own Constitution V invariant | Attach a synthetic `rule_source` evidence region so every finding — even ones the evaluator couldn't tie to a document — carries provenance |
| `3c1d3cf` | `resume_after_legibility` re-ran the graph from INGEST — if the package had been edited between pause and resume, compliance would silently run on data the reviewer never approved | SHA-256 snapshot of the package at run start; on resume we verify the hash and reject if the package drifted |

### 2c. Concurrency / robustness (5 commits)

| Commit | Issue | Fix |
|---|---|---|
| `59f3430` | HITL `record_resolution` / `record_correction` / `export_report` had no locking; concurrent requests on the same run could clobber state or collide on revision numbers | Per-run `threading.Lock` registry; load-mutate-save sequence is atomic within the process |
| `a2730d0` | `export_report` read HITL state three times (once via `project_report`, twice directly). Hard to reason about whether the bundle reflected the gate decision. | Snapshot once at the top of `_export_report_locked`; reuse for gate check, render, save. Added belt-and-braces re-check that rejects export if any active resolution still flags `needs_re_action` |
| `c2ab3ae` | `StructuredResolution` was declared `frozen=False` despite the spec's immutability claim | `frozen=True`; no code in the repo mutates resolutions post-save (verified by full suite) |
| `cc18192` | OCR sidecar write was non-atomic; a crash mid-write corrupted the JSON and stopped the next run | `.tmp` + atomic rename, same pattern as RunStore |
| `d23ab89` | `EventBus.publish` released the lock before scheduling; lost events on subscriber teardown races went silently into an empty `except RuntimeError` | Hold the lock through scheduling; log every actual drop so audit-trail gaps are visible |

### 2d. Input / payload validation (3 commits)

| Commit | Issue | Fix |
|---|---|---|
| `177a111` | `corrected_value` on `CORRECT` accepted `Any` — reviewers could inject arbitrary JSON objects/lists into extracted data, short-circuiting tolerance checks | Restrict to finite scalars (`str` / `int` / `float` / `bool`); reject NaN/inf; cap string length; cap reason_comment length |
| `a877207` | `page_filter: "by_index"` with empty `page_indices` silently matched zero pages — every rule UNEVALUATED with no signal | Semantic check in the schema validator at load; runtime coercion returns an empty selection so the failure is loud |
| `54cc5c1` | HITL stores + RunStore + PackageStore caught JSON/OS/validation errors and returned `None` with no log | `logger.error(..., target_path, exc)` on every skip; behaviour unchanged but corruption is now detectable |

### 2e. Operational ergonomics (3 commits)

| Commit | Issue | Fix |
|---|---|---|
| `e90a296` | CORS misconfigured: wildcard + `allow_credentials=True` — browsers reject it, dev flow was broken | Explicit allowlist in prod, explicit localhost list in debug |
| `eb2bde8` | After the above, ops ran the frontend on `localhost:3100` and every fetch still failed CORS | `allow_origin_regex` matching any localhost port in debug so frontends on non-default ports work out of the box |
| `3f740d6` | `rerun_plan` for CORRECT matched affected rules declaratively on `source/target/expected.field` — alias-sensitive fields like `entity_name` could leave unrelated cross-doc rules stale | Full-rerun fallback when the corrected field is alias-sensitive (`entity_name`, `material_name`, `lot_id`, …) |

### 2f. Lint / house-keeping (1 commit)

- `92f05b0` — fixes ruff violations the review commits introduced (undefined variable in `stores.py`, `E402` import order after helper injection, `UP037` quoted forward refs).

---

## Bucket 3 — Observability (Spec 006, new)

A dedicated speckit feature. Designed *first* (spec.md → plan.md → research.md → data-model.md → contracts/ → tasks.md → checklists) and *implemented* in five parallel-safe tracks. Cross-cutting — every request, every stage, every HITL write is now instrumented from a single small package that the domain code never depends on directly.

### 3a. Spec pack (`docs(006)`, `5e935a4`)

`specs/006-observability-and-finding-semantics/` — 2,005 lines across:

- `spec.md` — 6 prioritised user stories (P1/P2), 17 FRs, 5 NFRs, 7 Success Criteria, edge cases, explicit out-of-scope list.
- `plan.md` — Constitution Check green on all 9 principles, 5 parallel tracks, risk register, release strategy.
- `research.md` — 10 binding decisions (R1–R10) with rationale: W3C Trace Context over custom headers, `structlog` over stdlib, `prometheus-client` over OTEL-SDK-now, `contextvars` over threadlocal, label-whitelist enforcement, display-rename vs wire-rename, dedup mode parameter, k8s-style health split, redaction on by default, OTLP-migration-later.
- `data-model.md` — `TraceContext`, `RequestScope`, `LogEvent`, `Metric`, `HITLDisplayState` entities with invariants and lifecycle.
- `contracts/` — trace-header contract, closed log-event catalogue, closed metrics catalogue with label cardinality budget, HITL display-state contract with allowed palettes. Every contract carries a **Satisfies:** stanza back-linking the FR/SC it binds.
- `quickstart.md` — local walkthrough + OTLP migration recipe.
- `tasks.md` — 5 tracks, ~30 tasks, closing FR/NFR/SC coverage matrix.
- `checklists/requirements.md` — pre-merge gate tied 1:1 to FRs + NFRs + SCs + constitution principles.

### 3b. Track 1 — Foundation (`feat(006)`, `2911b0d`)

`backend/app/observability/` — a self-contained package. 8 modules:

- `context.py` — `TraceContext` (W3C v3) + `RequestScope` dataclasses on `contextvars`. Closed set of allowed scope keys enforced at bind time.
- `tracing.py` — `parse_traceparent` / `mint_trace` / `span()` / `@traced` + `submit_with_context` bridge for `ThreadPoolExecutor`.
- `logging.py` — `structlog` config with a stdlib bridge (every existing `logging.getLogger(__name__)` call site auto-enriches). JSON-to-stdout in production, colourised in dev (TTY-detected). Processor chain injects trace + scope + redacts oversized/binary payloads.
- `redaction.py` — defence against log-pipeline exfiltration: drops values > 2 KiB or matching the PDF/base64 heuristic, counts redactions so ops can see misuse.
- `metrics.py` — **26 named Prometheus metrics**, registered exactly once against a module-level `REGISTRY`. `ALLOWED_LABELS` frozenset (18 keys) enforced at import. `reset_for_tests()` helper.
- `middleware.py` — FastAPI middleware: parses inbound `traceparent`, binds handler child span, reads `doc_id` / `run_id` from path params and `X-Actor-Id` from headers into scope, emits `trace.request.{started,finished}`, records `http_*` metrics, synthesises a 500 response carrying correlation headers on unhandled exceptions (so errors still carry a trace id out).
- `health.py` — `/health` liveness, `/health/ready` readiness (storage writable + rule bank loads + event bus ready), `/metrics` Prometheus text exposition. All three served *outside* the `require_actor` gate because they're platform-internal.
- `__init__.py` — narrow public surface: `get_logger`, `bind_context`, `traced`, `span`, `submit_with_context`, `current_trace`.

Tests (8 new files, 32 cases): context bind/reset, traceparent parse fuzz, cross-executor propagation, metrics-catalogue drift + label whitelist, redaction fuzz, middleware in/out/malformed/error, health reachability without auth.

### 3c. Track 2 — Domain wiring (`feat(006)`, `524bb63`)

Thin, fail-open. No domain package imports FastAPI or Prometheus — observability enters through module-level `get_logger` + `bind_context` calls at scope boundaries.

- `app/bmr/workflow/service.py` — `start_run` binds `run_id` + `doc_id` on `RequestScope` for the whole run; observes `bmr_runs_total{status}` + `bmr_run_duration_seconds` on terminal status; `bmr_runs_in_flight` gauge tracks concurrency.
- `app/bmr/workflow/stages.py` — new `_observe_stage(stage, fn)` wrapper binds `stage=<name>`, opens a span, observes `bmr_stage_duration_seconds`. Applied per-stage at graph composition in `graph.py` so instrumentation is opt-in at registration time.
- `app/bmr/workflow/stages.py` — compliance fan-out switched from `executor.submit` to `submit_with_context` so trace + scope survive the `ThreadPoolExecutor` handoff into worker threads.
- `app/bmr/events/__init__.py` — every event envelope carries `trace_id` + `span_id` so WebSocket subscribers can correlate events with server-side logs.
- `app/bmr/hitl/service.py` — `_run_lock()` binds `run_id` on scope; resolutions, corrections (applied + failed), and export-gate state each increment their counter.

Integration test (`test_bmr_trace_integration.py`) confirms the inbound `traceparent` trace_id is echoed back on the response and that ids generated downstream are children of it.

### 3d. Track 5 — End-to-end evidence (`test(006)`, `ae1252f`)

- `test_trace_propagation.py` — stand up a minimal FastAPI app in-process, submit one request with a known `traceparent`, capture structlog's JSON output, assert every record emitted during that request shares the inbound `trace_id` *including* log lines from a `ThreadPoolExecutor` worker that was submitted via `submit_with_context`. This is the SC-001 evidence that a single id correlates the whole request.
- `test_overhead_benchmark.py` — 200 warm-path p95 samples with vs without the middleware; asserts Δp95 < 5 ms. Marked `@slow`, opt-in.

---

## Bucket 4 — Finding-semantics fix (Spec 006, same PR)

The real, reviewer-visible UX bug that surfaced during the engagement when the user opened `/compliance?doc=f9f7e1b6-…`. Scoring was numerically correct; the rendering turned every `auto_approved` finding into a green "✓ Auto-approved" badge — indistinguishable from a successful rule. A category scoring 0/100 would show five "Auto-approved ✓" rows underneath, making compliance reviewers misread failures as passes.

### 4a. Server (`fix(006)`, `e1450a4`)

- **Remove the inverted default.** [backend/app/api/routes/compliance.py:94](backend/app/api/routes/compliance.py#L94) previously mapped missing `hitl_status` to `"auto_approved"` and applied the full severity penalty. New `_normalize_hitl_status()` maps unknown / missing values to `"unknown"`. By default, `"unknown"` findings are **excluded** from penalty (you can't confidently score what you can't trust); opt-in `include_unknown=True` for pessimistic callers. Score decomposition exposes `unknown_skipped` so ops can detect upstream contract drift.
- **Dedup mode parameter.** `_deduplicate_findings(..., mode=...)` now takes `per_agent` (default — within-agent), `cross_agent_preserve` (dedup by `(agent, rule_id)` — two agents keeping their attribution), or `cross_agent_collapse` (legacy first-seen-wins, emits a `compliance.finding.deduped` log + `compliance_dedup_merges_total{mode}` counter). Global call in `compliance_graph.py` now uses `cross_agent_preserve` — tab badge count cannot diverge from the filtered global-list count by construction.
- **`resync_agent_totals()`** helper for callers stuck on the legacy collapse behaviour: rebalances `AgentReport.total_findings` against the globally-surviving list.
- **`ComplianceReport.dedup_mode`** optional field so read-path tooling knows how the list was built.
- **Report-level metrics** at the end of a successful compliance run: `compliance_runs_total`, `compliance_run_duration_seconds`, `compliance_agent_duration_seconds`, `compliance_findings_total{agent,status,severity,hitl_status}`.

### 4b. Frontend (`feat(006)`, `082b982`)

- **Extracted `HITLBadge`** to [frontend/src/components/compliance/hitl-badge.tsx](frontend/src/components/compliance/hitl-badge.tsx) as the single source of truth for wire→display mapping. `auto_approved` / `system_confirmed` render as **"System-confirmed"** with a **neutral grey** palette, never success-green. Severity owns the "is this bad?" colour. `normalizeHitlStatus()` coerces any unknown string to `"unknown"` — **fallback is never `auto_approved` again**.
- **`TraceFetchInit`** component idempotently patches `window.fetch` at app startup so every outbound request carries a `traceparent` and responses' `X-Request-Id` lands in `localStorage.__bmr_last_request_id`. Call sites stay unchanged.
- `findings-table.tsx` drops its inline `HITL_CONFIG` + local badge; the `|| HITL_CONFIG.auto_approved` fallback is removed; filter logic merges legacy `auto_approved` with new `system_confirmed`.
- `compliance-report.tsx` rollup counters now merge both wire values and surface an `unknown` count for operators.

### 4c. Evidence the fix works

- `test_finding_semantics.py` (server) — missing `hitl_status` comes back `"unknown"`, excluded from penalty; existing pilot doc's score is unchanged (FR-017).
- `test_dedup_attribution.py` — `sum(ar.total_findings) == |{f ∈ report.findings : f.agent≠None}|` holds in preserve mode; `resync_agent_totals` fixes legacy mode.
- `test_hitl_default_removed_e2e.py` — hand-built persisted report with one finding missing `hitl_status` lands in the HTTP response without `"auto_approved"` anywhere in its score_decomposition.

### 4d. Explicitly not in scope (deferred by design, spec §Out-of-Scope)

- Wire migration from `auto_approved` to `system_confirmed` in the persisted data — display-only rename in this PR; a future spec will rewrite legacy reports.
- OpenTelemetry SDK + OTLP exporter — shape-compatible now, one-line config swap when a backend is chosen.
- Log aggregator wiring (Loki / Datadog / Elastic) — JSON-to-stdout is the contract.

---

## Numbers and verification

| Gate | Status |
|---|---|
| BMR suite | 218 tests green |
| Observability suite | 35 tests green |
| Compliance semantics suite | 12 tests green |
| **Total** | **265 tests green** |
| Observability overhead benchmark | Δp95 < 5 ms (NFR-001 met) |
| Ruff on new surface | clean |
| Pyright / tsc | clean (backend pyright configured; frontend `tsc --noEmit` clean) |
| FR coverage | 17 / 17 mapped to owning tasks + tests |
| SC coverage | 7 / 7 exercised |
| NFR coverage | 5 / 5 asserted |

---

## Risks + follow-ups

### Carryover risks (not new; inherited from v0)

- **BMR v0 is single-worker.** Per-run locking is in-process. Multi-worker deployments need a distributed lock (Redis / DB) — deferred to the Postgres swap ticket.
- **No Redis / NATS on the event bus.** In-process only; WS subscribers that disconnect and reconnect miss events emitted in the gap. Acceptable for pilot; replaced by a durable bus when we scale.
- **`auto_approved` wire value is preserved** for backward compatibility with existing persisted reports. When we migrate, the writer and a one-shot rewrite script land together.

### Follow-ups queued (out of scope for this PR)

- Swap `opentelemetry-api` usage for the full OTEL SDK + OTLP exporter once a trace backend is chosen (Tempo / Jaeger / Honeycomb). One-file change per `quickstart.md §6`.
- Dashboard + alert pack for Prometheus. The metric catalogue is published in `contracts/metrics.md` and stable enough for a consumer team to build against.
- Rule-as-data migration path: turn the current Python-side compliance agents into rule-spec entries where the schema can express them (Constitution IX, not blocked by this PR).

---

## Suggested review order for the manager

1. **Skim** this file end-to-end.
2. **Open** [specs/006-observability-and-finding-semantics/spec.md](specs/006-observability-and-finding-semantics/spec.md) + [plan.md](specs/006-observability-and-finding-semantics/plan.md). They show the process we intend to keep using.
3. **Pick one** of Bucket 2's commits (`9b27e50` drop-safe fan-out or `59f3430` per-run locking are good examples) and read the commit body — each is a self-contained review artefact with a written problem statement + fix + evidence.
4. **Ask for a demo** of `/metrics` scraped live against a compliance run, and of the relabelled HITL badge on the pilot document.
