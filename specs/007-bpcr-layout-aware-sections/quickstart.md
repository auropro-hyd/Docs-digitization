# Quickstart: BPCR Layout-Aware Section Detection

End-to-end walkthrough of the v0 (heuristic-only) spike.

---

## 1. Prerequisites

You already have the BMR pipeline working — `pytest tests/bmr/` is green, the pilot bank validates, and you can run the existing `bmr-rules fixture-run` against `tests/bmr/fixtures/rules/fixtures/bpcr_weight_match.json`.

Spec 007 adds:

- A new capability under `app/bmr/capabilities/`.
- A canonical section list at `backend/config/bmr/pilot/bpcr-section-spec.yaml`.
- A new schema version `rule.schema.v1.1.json` (additive over v1.0).
- One example pilot rule using `section_id`.

No new external dependencies.

---

## 2. Run the heuristic detector against a fixture

```bash
cd backend

# Validate the spec file
uv run python -m app.bmr.capabilities.bpcr_section_detect \
  --validate-spec config/bmr/pilot/bpcr-section-spec.yaml

# Detect sections on the bundled fixture
uv run python -m app.bmr.capabilities.bpcr_section_detect \
  --fixture tests/bmr/fixtures/section_detection/bpcr_35pages_ocr.json \
  --spec config/bmr/pilot/bpcr-section-spec.yaml \
  --doc-id bpcr-001
```

Expected output (truncated):

```json
{
  "doc_id": "bpcr-001",
  "spec_version": "1.0",
  "method": "heuristic",
  "outcome": "ok",
  "spans": [
    {"section_id": "cover", "start_page": 1, "end_page": 1, "confidence": 1.0, "detection_method": "heuristic_top_of_page"},
    {"section_id": "material_dispensing", "start_page": 2, "end_page": 7, "confidence": 1.0, "detection_method": "heuristic_top_of_page"},
    ...
    {"section_id": "yield_calculation", "start_page": 28, "end_page": 31, "confidence": 0.7, "detection_method": "heuristic_mid_page"},
    ...
  ]
}
```

---

## 3. Run a section-aware rule

```bash
uv run bmr-rules validate config/rules/pilot/bank
uv run bmr-rules fixture-run \
  --rule config/rules/pilot/bank/alcoa_accurate_bpcr_yield_calc.yaml \
  --fixture tests/bmr/fixtures/rules/fixtures/bpcr_section_aware.json
```

The `bpcr_section_aware.json` fixture has `section_id` populated on each page. The rule uses `page_selector.section_id: yield_calculation` and aggregates only those pages.

Expect a single PASS finding (matching the fixture's expected target weight).

---

## 4. Disable the enrichment without touching code

```bash
AT_BMR__BPCR_SECTIONS_ENABLED=false uv run pytest tests/bmr/
```

Every existing test still passes. The new `tests/bmr/workflow/test_section_enrichment.py` skips its enabled-only assertions when the flag is off.

---

## 5. Confirm v1.0 rules still validate

```bash
uv run bmr-rules validate config/rules/pilot/bank
# OR
uv run pytest tests/bmr/rules/test_schema_v1_1.py::test_v1_0_rules_unaffected
```

Output: `N rules checked, N ok`. The v1.0 schema is still authoritative for v1.0-pinned rules.

---

## 6. Compare two specs (when the canonical list evolves)

When you bump `spec_version` from `1.0` to `1.0.1` after adding a new section:

```bash
uv run python -m app.bmr.capabilities.bpcr_section_detect \
  --diff-spec config/bmr/pilot/bpcr-section-spec.yaml \
  --against /tmp/bpcr-section-spec.previous.yaml
```

This prints a structured diff listing added/removed/renamed sections — a reviewer's audit aid before merging spec changes.

---

## 7. Where to look in the code

| Concern | File |
|---|---|
| Detector | `app/bmr/capabilities/bpcr_section_detect.py` |
| Tagger | `app/bmr/capabilities/bpcr_section_tagger.py` |
| Spec loader | `app/bmr/config/bpcr_sections_spec.py` |
| Rule schema (1.1) | `backend/config/rules/schema/rule.schema.v1.1.json` |
| Schema generator | `app/bmr/rules/docgen.py` (regenerated `.md` lives next to the JSON) |
| Workflow wiring | `app/bmr/workflow/stages.py::make_extraction_stage` |
| Pilot rule | `config/rules/pilot/bank/alcoa_accurate_bpcr_yield_calc.yaml` |

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Every page comes back `unsectioned` | Spec regex doesn't match the OCR text — check encoding (smart quotes, em-dashes) | Add an alias or broaden the regex |
| Mid-page section not detected | `requires_emphasis_for_mid_page: true` and the OCR doesn't mark the header as bold | Set the flag to `false` for that section, or capture style spans in OCR |
| Section detected on the wrong page | Two sections matched the same page; check band priority ordering | Reorder `bands:` in the spec; the first match wins |
| `bmr-rules validate` rejects a v1.0 rule | `schema_version` field missing or wrong | Pin `schema_version: "1.0"` explicitly |
| Run completes but `EvidenceRegion.section_id` is null | Detection failed (check `bpcr.section_detect.failed` log) OR enrichment was disabled | Re-run with `AT_BMR__BPCR_SECTIONS_ENABLED=true`, inspect the detector error |

---

## 9. What the call needs to lock

1. The canonical section list (replace placeholder entries in `bpcr-section-spec.yaml`).
2. Whether `hybrid` becomes the default in Phase 2 (recommended).
3. Whether to add `section_id` grouping to the report viewer in Phase 3.
