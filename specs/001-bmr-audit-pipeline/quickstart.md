# Quickstart — BMR Audit Pipeline (v2, leverage-first)

**Spec**: 001-bmr-audit-pipeline  **Revision**: v2 (2026-04-17)

This guide walks a developer through a local end-to-end BMR audit on the pilot fixture. It
exercises the 5-stage pipeline, the narrow legibility HITL, the final-checkpoint structured
resolution, and selective re-run.

---

## 1. Prerequisites

- Postgres 15+ running at `localhost:5432` with a database `bmr_audit_dev`.
- Backend Python env: `cd backend && uv sync` (or `pip install -e .` per existing setup).
- Frontend Node env: `cd frontend && pnpm install`.
- OCR / VLM providers configured per existing `.env` conventions; for quickstart the Azure
  Document Intelligence path works best.

## 2. Database setup

```bash
cd backend
alembic upgrade head                 # existing migrations
alembic upgrade head --config alembic-bmr.ini    # new BMR tables (created by tasks.md step)
```

Tables created by the BMR migration:
`bmr_runs, bmr_stage_states, bmr_findings, bmr_resolutions, bmr_corrections,
bmr_rerun_plans, bmr_hitl_actions, bmr_audit_trail, bmr_feedback_samples`.

## 3. Load configuration

Pilot configuration lives under `backend/config/`:

- `backend/config/bmr/pilot-manifest.yaml` — document role declarations, boundary detection
  hints, ingestion rules (Spec 002).
- `backend/config/bmr/pilot-summary-templates.yaml` — page-level template for BPCR,
  doc-level templates for others.
- `backend/config/rules/pilot/alcoa/*.yaml`, `.../gmp/*.yaml`, `.../checklist/*.yaml` —
  pilot rule set with `context_object` declarations (Spec 005).

The pipeline validates all manifests + rule files at startup. Any schema error prints the
offending file + field and aborts.

## 4. Upload a fixture package

```bash
cd backend/tests/fixtures/bmr/pilot-package
ls    # BPCR.pdf, RawMaterial-01.pdf, RawMaterial-02.pdf, CheckList.pdf, AnalysisReport.pdf, CoA.pdf
```

```bash
curl -X POST http://localhost:8000/api/v1/packages \
  -H "Content-Type: multipart/form-data" \
  -F "file=@BPCR.pdf" -F "file=@RawMaterial-01.pdf" -F "file=@RawMaterial-02.pdf" \
  -F "file=@CheckList.pdf" -F "file=@AnalysisReport.pdf" -F "file=@CoA.pdf" \
  -F "manifest_id=pilot-v1"
# → { "package_id": "..." }
```

## 5. Start a run

```bash
curl -X POST http://localhost:8000/api/v1/bmr-audit/runs \
  -H "Content-Type: application/json" \
  -d '{ "package_id": "<id>", "manifest_id": "pilot-v1" }'
# → { "run_id": "...", "mode": "leverage", "current_stage": "INGEST", "status": "STARTED" }
```

## 6. Watch progress

```bash
wscat -c ws://localhost:8000/ws/bmr-audit/<run_id>
```

You will observe:
1. `stage.entered INGEST` → `stage.exited INGEST PASS`
2. `stage.entered LEGIBILITY_AND_CLASSIFICATION` → per-page `page.legibility_verdict`
3. If any page is `FAIL`: `run.awaiting_legibility_hitl` with `pending_pages`. Pipeline
   continues for non-failed pages.
4. `stage.entered STRUCTURED_EXTRACTION_AND_SUMMARISATION` → per-page extraction events →
   summaries → `stage.exited ... PASS`
5. `stage.entered COMPLIANCE` → `stage.sub_phase_changed alcoa_in_progress` ∥
   `gmp_in_progress` (both concurrently) → joined → `synthesis_in_progress` →
   `synthesis_done`.
6. `stage.entered REPORT_AND_RESOLUTION` → `run.awaiting_final_checkpoint` with
   `unresolved_critical_major` count.

## 7. Resolve legibility (if any)

Open `http://localhost:3000/bmr-audit/<run_id>/legibility`. For each flagged page, choose:
- **Re-upload**: provides a file picker; the page is re-OCR'd and re-legibility-checked; on
  PASS, downstream stages re-enter for that page only.
- **Proceed anyway**: page continues without replacement; downstream compliance findings
  attributable to poor legibility are tagged `contributing_factor:
  operator_proceeded_on_low_legibility`.

The UI MUST NOT show finding-level controls here (Constitution IV enforcement).

## 8. Review consolidated findings

Open `http://localhost:3000/bmr-audit/<run_id>/report`. The view is grouped by BPCR step by
default, with collapsible ALCOA / GMP / Checklist-Adherence sub-sections.

For each finding:
- Click **Confirm** to accept the system's verdict (optional note).
- Click **Dismiss** → choose `reason_type`, supply `observed_value_on_document` (if OCR
  misread or acceptable variance), optional note.
- Click **Correct** → choose `reason_type` (typically `OCR_MISREAD`), supply the corrected
  value + value_kind.

Submitting a **Correct** action returns a `plan_id`. The UI then shows "K rules will be
re-evaluated at scopes [...]" and prompts the reviewer to confirm before executing.

## 9. Verify selective re-run

After confirming the plan, watch the WS:
- `rerun_plan.confirmed` → the Compliance stage re-enters DIRTY for affected scopes only →
  `rerun_plan.completed` with `findings_changed`, `findings_retracted`, `stale_resolutions`.
- If any prior resolutions were superseded, the `report` page surfaces them in a "Needs
  re-action" tray.

## 10. Export

With all Critical + Major findings resolved:

```bash
curl -X POST http://localhost:8000/api/v1/bmr-audit/runs/<run_id>/export \
  -H "Content-Type: application/json" -d '{ "format": "pdf+csv" }'
# → { "export_id": "...", "download_url": "..." }
```

The exported PDF is the audit artifact; the CSV is the structured findings bundle for
ingestion into the client's QMS.

## 11. Constitution spot-checks

These assertions are the checks that most commonly flag regressions. Run them after any
change to the BMR pipeline:

```bash
cd backend
pytest tests/regression/test_existing_modes_still_pass.py     # Constitution VII
pytest tests/workflow/bmr_audit/test_parallel_compliance.py   # Principle II parallelism
pytest tests/workflow/bmr_audit/test_legibility_hitl_scope.py # Constitution IV (narrow HITL)
pytest tests/workflow/bmr_audit/test_selective_rerun.py       # Principle IV + IX
pytest tests/performance/test_rerun_latency.py                # SC-003
```

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Pipeline halts with `ANCHOR_MISSING` | Package has no classifiable BPCR | Verify manifest role declarations; re-upload with correct filenames or add manifest hints (Spec 002) |
| `UNEVALUATED_CONTEXT_MISSING` finding | Rule declares cross_document role that the package lacks | Check `context_object.role` in the rule; add missing document or set rule `fallback: treat_as_pass` if appropriate |
| Re-run plan is empty | No rule's context_object reads the corrected input | Expected when correcting a value outside any rule's scope; correction is still recorded |
| Synthesised finding persists after source retracted | Bug — violates §5 invariant | File issue; add regression test; fix in `checklist_synthesise.v1` |
| Legibility HITL page shows finding controls | UI bug — violates Constitution IV | Fix UI; add regression test asserting only `re-upload` and `proceed` are rendered |
| COMPLIANCE stage serial (not parallel) | Graph composition regression | Check `bmr_audit/stages/compliance.py` uses LangGraph `Send` for fan-out |
