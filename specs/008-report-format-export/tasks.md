# Tasks: Client-Aligned Compliance Report (Spec 008)

Each task references the user story it belongs to (US1 / US2 / US3 / US4). `[P]` marks tasks safe to parallelize (different files, no dependency on other in-flight tasks).

## Format: `[ID] [P?] [Story] Description`

`P` indicates a task that can run in parallel with other `P` tasks within the same phase.

## Path Conventions

- Backend: `backend/app/...`, `backend/tests/...`
- Frontend: `frontend/src/...`
- Spec doc references the directory layout in plan.md.

---

## Phase 1: Setup (shared infrastructure)

- [ ] **T001** Add `weasyprint` to `backend/pyproject.toml` (and `requirements.txt` if present). Run `pip install` in the backend venv. Verify import works (`python -c "import weasyprint"`).
- [ ] **T002** Add `mitigation_text: str = ""` field to `ComplianceFinding` in `backend/app/compliance/models.py`. Default empty. Backward compatible.
- [ ] **T003** [P] Create empty `backend/app/compliance/report_renderer/` package with `__init__.py`. Add `backend/tests/compliance/report_renderer/__init__.py`.
- [ ] **T004** [P] Commit a generic compliance-suite logo asset to `backend/app/compliance/report_renderer/assets/logo.png`. Neutral mark (not client-specific). Falls back to text-only header per FR-008 if missing.
- [ ] **T005** [P] Add two env vars to `backend/app/config/settings.py` under `ComplianceConfig`:
  - `AT_COMPLIANCE__REPORT_LOGO_PATH` (string; default points to bundled asset)
  - `AT_COMPLIANCE__REPORT_PRODUCT_NAME` (string; default `"BMR Compliance Intelligence Suite"`)

## Phase 2: Foundational (blocking prerequisites)

These touch shared core logic; finish before any user-story phase can start.

- [ ] **T006** [P] [US4] Implement `_bucket_status()` in `backend/app/compliance/report_renderer/status_bucket.py`. Pure function: `(status: str, hitl_override: str | None) -> Literal["compliant", "action_required", "needs_attention"] | None`. Returns `None` for `not_applicable` so the caller can drop the row.
- [ ] **T007** [P] [US4] Implement `_format_pages()` in `backend/app/compliance/report_renderer/page_formatter.py`. `_format_pages([6,7,8,9,10,11,12,13]) → "PAGE:6 to 13"`, `_format_pages([6,9,31]) → "PAGE:6, 9, 31"`, `_format_pages([]) → ""`.
- [ ] **T008** [P] [US4] Write tests `backend/tests/compliance/report_renderer/test_status_bucket.py` — every status / HITL combo from data-model.md's mapping table.
- [ ] **T009** [P] [US4] Write tests `backend/tests/compliance/report_renderer/test_page_formatter.py` — range vs comma, single page, empty, large gaps.
- [ ] **T010** [US4] Implement `build_report_document()` in `backend/app/compliance/report_renderer/builder.py`. Pure function: `(report: ComplianceReport, *, operator: str) -> ReportDocument`. Sorts rows by `(compliance_kind_priority, agent, rule_id)`. Excludes `not_applicable`.
- [ ] **T011** [US4] Write tests `backend/tests/compliance/report_renderer/test_builder.py` — fixture `ComplianceReport` → expected `ReportDocument`; row ordering; not_applicable exclusion; HITL override pickup.

## Phase 3: US1 — Operator downloads client-shareable report (P1) 🎯 MVP

**MVP exit**: `GET /api/compliance/{doc_id}/export?format=pdf` returns a PDF matching the reference layout.

### Implementation for US1

- [ ] **T012** [US1] Implement `_summarise_compliant(rule_result) -> str` in `backend/app/compliance/report_renderer/summary.py`. v1: deterministic boilerplate `"Evaluated across {N} pages ({page_range}). All entries satisfied the {rule_category_display} criteria."`. LLM enhancement is out of scope for v1; pin the boilerplate shape with a test.
- [ ] **T013** [US1] Implement `_pick_mitigation(findings) -> str` in `backend/app/compliance/report_renderer/mitigation.py`. Priority chain: `recommendation` → `mitigation_text` → LLM synthesis → fallback boilerplate `"Review and remediate. Initiate a CAPA if the gap persists."`. The LLM call uses `compliance_evaluator_llm` from the container.
- [ ] **T014** [P] [US1] Write tests `backend/tests/compliance/report_renderer/test_mitigation.py` — priority order, force-flag, LLM-failure fallback, cost-ceiling enforcement.
- [ ] **T015** [P] [US1] Write tests `backend/tests/compliance/report_renderer/test_summary.py` — deterministic boilerplate shape; page-range formatting matches the reference.
- [ ] **T016** [US1] Build the Jinja2 template `backend/app/compliance/report_renderer/templates/report.html.j2`. Five-column rule table, header band with logo + metadata, footer with disclaimer. Match the reference PDF's visual structure.
- [ ] **T017** [US1] Build CSS `backend/app/compliance/report_renderer/templates/styles.css`. `@page` rules for A4 portrait, table styling, badge colours (green / orange / amber), font sizing. Print-CSS hints (page-break-inside: avoid for rule rows < 5 lines).
- [ ] **T018** [US1] Implement `render_html(report_document) -> str` in `backend/app/compliance/report_renderer/render_html.py`. Reads template + CSS, renders via Jinja2. Pure (no I/O).
- [ ] **T019** [US1] Implement `render_pdf(report_document) -> bytes` in `backend/app/compliance/report_renderer/render_pdf.py`. Routes HTML through `weasyprint.HTML(string=html).write_pdf()`. Catches `weasyprint` exceptions → bubbles to caller for fallback handling.
- [ ] **T020** [US1] Implement `render_md(report_document) -> str` in `backend/app/compliance/report_renderer/render_md.py`. Markdown table form (Akhilesh + operators who can't render PDF).
- [ ] **T021** [P] [US1] Write tests `backend/tests/compliance/report_renderer/test_render_html.py` — assert 5 columns in correct order, badge text correct, mitigation cell verbatim "Not Applicable" for compliant rows, page cell empty for compliant rows.
- [ ] **T022** [P] [US1] Write tests `backend/tests/compliance/report_renderer/test_render_pdf.py` — generate PDF for a fixture; extract text via `pypdf`; verify the same column-order assertions as HTML; assert no `overall_score` / `model_score` text appears.
- [ ] **T023** [P] [US1] Write tests `backend/tests/compliance/report_renderer/test_render_md.py` — markdown table shape; same content invariants.
- [ ] **T024** [US1] Update `GET /api/compliance/{doc_id}/export` in `backend/app/api/routes/compliance.py`. Default `format=pdf`. Route to the new renderer. Add render fallback path: PDF failure → HTML with `X-Render-Fallback: html`.
- [ ] **T025** [US1] Add `compliance.report_rendered` telemetry event at the `/export` route.
- [ ] **T026** [US1] Add the file-cache layer: write rendered file to `data/documents/{doc_id}/exports/report.{ext}`; serve from cache when `compliance_result.json` mtime is older. `?nocache=1` bypasses.
- [ ] **T027** [P] [US1] Add integration test `backend/tests/compliance/report_renderer/test_export_endpoint.py` — full route call with a fixture; assert response shape, content type, content disposition, cache behaviour.

**Phase 3 exit**: PDF export visually matches the reference (9-of-10 manual check passes). HTML and Markdown formats render the same shape. No frontend changes yet.

---

## Phase 4: US2 — Operator views rule-centric report on screen (P1)

### Implementation for US2

- [ ] **T028** [US2] Add `GET /api/compliance/{doc_id}/report-rows` route in `backend/app/api/routes/compliance.py`. Reuses `build_report_document()`; serialises to JSON. No file caching (frontend caches).
- [ ] **T029** [US2] Add the new route to `_QUIET_ROUTES` in `backend/app/observability/middleware.py`.
- [ ] **T030** [P] [US2] Write tests `backend/tests/compliance/report_renderer/test_report_rows_endpoint.py` — shape, agent filter, exclusion behaviour, quiet-route quieting.
- [ ] **T031** [P] [US2] Add TypeScript types in `frontend/src/types/compliance.ts`: `ReportRow`, `ReportDocument`, `ReportHeader`, `ReportFooter`, `ReportStats`, `ComplianceKind`.
- [ ] **T032** [P] [US2] Add API client functions in `frontend/src/lib/api.ts`: `getReportRows(docId, agent?)`, `getReportPreviewUrl(docId, agent?)`.
- [ ] **T033** [US2] Build `frontend/src/components/compliance/compliance-badge.tsx`. Three-state badge — `Compliant` (CheckCircle, green), `Action Required` (AlertCircle, orange), `Needs Attention` (HelpCircle, amber). Consumes `compliance_kind`.
- [ ] **T034** [US2] Build `frontend/src/components/compliance/rule-row.tsx`. Single row, 5 columns. Click handler expands the row.
- [ ] **T035** [US2] Build `frontend/src/components/compliance/rule-expand-drawer.tsx`. Expanded section showing per-page detail + HITL controls (approve / reject / modify). Read from `ComplianceReport.findings` (still available via `/report` endpoint).
- [ ] **T036** [US2] Build `frontend/src/components/compliance/rule-table.tsx`. Renders the metadata block (above the rows) + the table of `RuleRow` components. Top filters: by agent, by compliance kind. Does NOT render the score header band or exec summary (those stay in `compliance-report.tsx`).
- [ ] **T037** [US2] Update `frontend/src/components/compliance/compliance-report.tsx` — KEEP the existing `AgentScorecard` panels and `ExecutiveSummary` block at the top; SWAP the `FindingsTable` block for the new `RuleTable`; add an "Export" dropdown that hits `/api/compliance/{doc_id}/export?format={pdf|html|md}` via `downloadComplianceExport()`; add a "Preview" button that opens the iframe modal.
- [ ] **T038** [US2] `frontend/src/app/compliance/page.tsx` — leave the page header (doc name, status) and the score-rich top section intact; only the body region (findings list) changes via T037.
- [ ] **T039** [US2] Delete `frontend/src/components/compliance/findings-table.tsx`. `agent-scorecard.tsx` and `executive-summary.tsx` STAY (FR-011 — score info preserved on the on-screen view). Confirm no other consumers reference the deleted findings-table component.
- [ ] **T040** [US2] Manual smoke test: navigate to `/compliance?doc=X` for a doc with mixed compliant + non-compliant + uncertain rules. Verify scorecard + exec summary still render as today, the rule-centric table renders below them, expanding rows shows per-page detail, HITL approve/reject works. Then download the PDF and confirm NO scores appear in the rendered file (only the on-screen view shows them).

**Phase 4 exit**: On-screen compliance view matches the export shape. Score elements removed. HITL still works.

---

## Phase 5: US3 — Mitigation eager-warm + LLM fallback (P2)

### Implementation for US3

- [ ] **T041** [US3] Add `POST /api/compliance/{doc_id}/mitigation/synthesize` route in `backend/app/api/routes/compliance.py`. Body: `{"rule_ids": [...]?, "force": bool?}`. Returns counts + cost estimate.
- [ ] **T042** [US3] Implement the per-finding synthesis logic in `backend/app/compliance/report_renderer/mitigation.py`. Atomic write back to `compliance_result.json` via `.tmp` rename. Estimates cost per call (token count × model price); aborts with `429` when cumulative spend hits the configured ceiling.
- [ ] **T043** [US3] Add `AT_COMPLIANCE__MITIGATION_SYNTH_COST_CEILING_USD: float = 0.50` to `ComplianceConfig`.
- [ ] **T044** [US3] Add `compliance.mitigation_synthesised` telemetry per finding (rule_id, agent, cost_estimate_usd, duration_ms).
- [ ] **T045** [P] [US3] Write tests `backend/tests/compliance/report_renderer/test_mitigation_synth_endpoint.py` — cache priority, force flag, cost ceiling rejection, atomic write integrity.
- [ ] **T046** [US3] Frontend: add a "Synthesise mitigation" button to `compliance-report.tsx`. Calls the endpoint, displays progress toast, refetches `/report-rows`. Disabled when all non-compliant/uncertain findings already have mitigation text.

**Phase 5 exit**: An operator can warm the mitigation cache, then export sees populated Mitigation cells on every non-compliant/uncertain row.

---

## Phase 6: US1 polish — Preview iframe

### Implementation for preview

- [ ] **T047** [US1] Add `GET /api/compliance/{doc_id}/preview` route. Returns PDF bytes with `Content-Disposition: inline`. Same renderer as `/export?format=pdf`.
- [ ] **T048** [US1] Add the route to `_QUIET_ROUTES`.
- [ ] **T049** [US1] Frontend: build `report-preview-iframe.tsx` component. Modal that embeds the preview URL via `<iframe>`. Trigger from a "Preview" button on the report page.
- [ ] **T050** [US1] Manual test: preview matches the downloadable export 1:1.

---

## Phase N: Cross-cutting polish

- [ ] **T051** [P] Update OpenAPI schema (if maintained) for the new endpoints.
- [ ] **T052** [P] Update README / runbook describing the new endpoints + which are operator-facing.
- [ ] **T053** End-to-end smoke test: `tests/compliance/test_full_export_flow.py` — load a fixture doc, run compliance, render PDF, parse text, assert key invariants (5 columns, three statuses present, no `Score:` substring anywhere).
- [ ] **T054** Document `AT_COMPLIANCE__REPORT_LOGO_PATH`, `AT_COMPLIANCE__MITIGATION_SYNTH_COST_CEILING_USD` env vars somewhere durable.
- [ ] **T055** Add a "report renderer" section to the telemetry dashboards if maintained (`compliance.report_rendered`, `compliance.mitigation_synthesised`).

---

## Dependencies & Execution Order

### Phase dependencies

```
Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1 MVP) → Phase 4 (US2) → Phase 5 (US3) → Phase 6 (Preview) → Phase N (Polish)
```

Phase 4 can start after T024 (the `/export` route works) — it does not depend on T025–T027.
Phase 5 can start after T013 (mitigation logic exists) — does not depend on Phase 4.
Phase 6 depends on Phase 3 (`/preview` reuses the PDF renderer).

### User story dependencies

- US1 (P1, MVP) → independent.
- US2 (P1) → independent; can ship after US1 lands or alongside.
- US3 (P2) → depends on US1 only for the `_pick_mitigation()` shape; can ship before US2.
- US4 (P2 — backwards compat guard rail) → covered by every task touching models / on-disk JSON; no separate phase.

### Within each user story

Tests in this spec are NOT TDD-gated — implementation + test land together per task. The `[P]` tasks within a phase run in parallel; the others are sequential.

### Parallel opportunities

Within Phase 1: T003, T004, T005 in parallel.
Within Phase 2: T006, T007, T008, T009 in parallel (different files, no shared state).
Within Phase 3: T014, T015 in parallel after T013/T012. T021, T022, T023 in parallel after T018/T019/T020. T027 after T024.
Within Phase 4: T030 in parallel with T031, T032.

---

## Parallel Example: Phase 2 launch

```
# Launch foundational primitives together
task T006 (status_bucket) || task T007 (page_formatter) || task T008 (tests/status_bucket) || task T009 (tests/page_formatter)
# Then sequence:
task T010 (builder) → task T011 (tests/builder)
```

---

## Implementation Strategy

### MVP first (US1 only)

Goal: an operator can download a client-shareable PDF that matches the reference. Phases 1 → 2 → 3. Frontend untouched. Akhilesh sees a renderable artifact before any UI churn.

### Incremental delivery

After MVP, US2 (on-screen rewrite) ships independently — same renderer feeds both. US3 (mitigation eager-warm) layers on top. US4 (backwards-compat) is enforced by every task; not a separate phase.

### Parallel team strategy

Backend dev: Phases 1, 2, 3 (renderer + PDF) and Phase 5 (mitigation synth endpoint).
Frontend dev: starts Phase 4 (rule-table component) as soon as Phase 3's `/report-rows` route is mock-able; can use a static fixture until the live endpoint exists.

## Notes

- All tests live alongside their implementation per task; don't backfill at end of phase.
- Don't ship a phase exit without re-running the full backend suite + manual smoke test.
- `mitigation_text` MUST be backward compatible — existing `compliance_result.json` files load without migration.
- The HITL state machine is unchanged. Approving a non-compliant finding still flips the underlying status; the new bucket function picks up the override.
- Score fields stay on disk. The renderer never reads them.
- Reference PDF is the source of truth for layout. If the client revises it, re-anchor the spec before changes.
