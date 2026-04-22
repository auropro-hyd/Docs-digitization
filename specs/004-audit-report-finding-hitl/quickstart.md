# Quickstart: BMR Audit Report & Finding-Level HITL

**Feature**: 004 | **Spec Version**: v2

Walks through the reviewer's final-checkpoint experience end-to-end: open report, act on
findings, correct a value, observe selective re-run, export.

## Prerequisites

- Backend + Postgres + frontend running, Specs 001–003 merged.
- A completed run (`status=awaiting_review`) with ≥ 1 Major finding from the pilot fixture.
- `config/bmr/report-sections.yaml`, `report-severity-gating.yaml`,
  `resolution-reason-types.yaml` present.

## 1. Open the report

```
http://localhost:3000/bmr/runs/run_.../report
```

Expect the default grouped view:
- Step groups (Step 1, Step 2, …) ordered by step number.
- Inside each: collapsible ALCOA / GMP / Checklist-Adherence sub-sections.
- Severity counts badge per group.
- Export button disabled with tooltip "2 blocking findings pending".

Toggle "Flat by severity" — findings re-render grouped by severity. Toggle back.

## 2. Action a finding — CONFIRM

Click a Major ALCOA finding → detail pane opens with evidence viewer on the right.

Click "Confirm", leave note blank, submit.

Expect:
- Finding moves to Confirmed state; card gets a green pill.
- WebSocket event `resolution.created` in browser devtools:
  `{action: "CONFIRM", feedback_sample_id: "fbs_..."}`.
- A `FeedbackSample` row exists:
  `SELECT COUNT(*) FROM feedback_sample WHERE resolution_id='res_...'` → 1.

## 3. Action a finding — DISMISS with reason

Click another Major finding, choose "Dismiss".

Expect form:
- `reason_type` select: OCR_MISREAD | ACCEPTABLE_VARIANCE | DUPLICATE_FINDING | OUT_OF_SCOPE | RULE_MISCONFIGURED | OTHER.
- Choose OCR_MISREAD — form reveals required `observed_value_on_document` text input.
- `system_extracted_value` shows as readonly snapshot of the finding.

Try to submit with empty `observed_value_on_document`: expect client-side error AND server
returns HTTP 422. Fill it in (e.g., "12.7 kg"). Submit — resolution created; finding card
shows "Dismissed (OCR misread)".

Query: `SELECT COUNT(*) FROM feedback_sample WHERE reason_type='OCR_MISREAD'` → 1.

## 4. Action a finding — CORRECT with selective re-run

Click a finding whose underlying extraction is wrong. Click "Correct".

- Form shows current extracted value (readonly) + an "Edit value" input.
- Enter the correct value, pick `reason_type: ocr_misread`, add comment.
- Click "Preview re-run scope".

Expect a preview:
```
These rules will re-evaluate:
  - alcoa.accurate.bpcr-raw-material-weight-match
Estimated 3 findings affected, ~2.8s
```

Click "Confirm & re-run". Expect:
- `correction.confirmed` → `rerun.planned` → `rerun.in_progress` → `rerun.completed`
  events in quick succession.
- The finding we corrected from is retracted (strikethrough + "Retracted after correction"
  marker).
- One other finding moves from "Confirmed" to "Needs re-action" because its Confirm was
  superseded. The "Needs re-action" tray in the header shows a count of 1 and a jump link.

## 5. Address "Needs re-action"

Click the needs-re-action item. The previous resolution is visible (greyed) with a
"Supersedes: res_xyz" indicator. Re-confirm or re-dismiss as appropriate.

Expect export-gate status to flip to `ready` once all blocking findings are resolved:
- WebSocket event `report.gate_changed` → `{status: "ready"}`.
- Export button enables.

## 6. Export

Click "Export". Expect:
- PDF download link + JSON bundle link.
- `audit_report_revision` row with `revision_number=1`.
- PDF sections in order: Package Metadata, Executive Summary, Findings by Severity,
  Findings by ALCOA+ Tag, Rule Evaluation Appendix, System Confidence Appendix.
- `grep -i "compliance.*score" exported.pdf.txt` → zero hits (extracted via pdftotext).
- `jq 'keys' bundle.json` has no `compliance_score` / `overall_pass_fail` keys.

## 7. Evidence viewer — cross-doc navigation

Click a cross-doc reconciliation finding. Expect:
- Viewer opens with the source document (BPCR) rendered at the correct page with region
  highlighted.
- Source switcher shows "1 of 2" — click → target document (RawMaterialPage) renders with
  its own region highlighted, finding card remains open.
- Viewer-to-region time < 1.5 s.

## 8. Evidence viewer — synthesised finding navigation

Click a Checklist-Adherence finding that synthesises from ALCOA/GMP upstream findings.
Expect:
- Detail pane shows a "Contributing findings" drawer with deep links to each upstream
  finding.
- Clicking a contributing link scrolls the main view to that finding and opens its
  evidence on the right.

## 9. Export blocked flow

On a new run, try to Export without actioning a Critical finding. Expect:
- HTTP 409 `code: "export_blocked"`.
- UI dialog lists pending findings with jump links.
- No revision row created; no blob written.

## 10. Post-export immutability

After a successful export, action another Minor finding (permitted since the gate was
`ready`). Re-export. Expect:
- `revision_number=2`, `predecessor_id = rev_1`.
- Rev 1 PDF and bundle URLs still return the original bytes unchanged (sha256 match).

## 11. Constitution spot-checks

- Single final checkpoint: `grep -r "finding.review_requested" backend/app/{report,resolution,feedback}` → zero hits.
- Evidence-bound: `pytest backend/tests/report/test_projection_grouping.py::test_every_finding_has_evidence_links` passes.
- Configurable framework: `grep -r "OCR_MISREAD\|ACCEPTABLE_VARIANCE" backend/app/resolution` — only reads YAML, no string literals in business logic (only `schema.py` loads the enum from YAML).
- Append-only: `SELECT COUNT(*) FROM structured_resolution WHERE actor_id IS NULL` → 0; attempting UPDATE errors out via trigger.
- Feedback corpus: every `StructuredResolution` with `action in ('DISMISS','CORRECT')` has a matching `FeedbackSample`.

## 12. Regression

```bash
cd backend && uv run pytest tests/report tests/resolution tests/correction -v
cd frontend && npx playwright test e2e/
```

All green = Spec 004 is ship-ready.
