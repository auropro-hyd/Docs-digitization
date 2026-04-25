# Quickstart: Cross-Document Rule Support

**Feature**: 003 | **Spec Version**: v2

Demonstrates end-to-end: authoring a cross-doc rule in YAML, loading it, running it over a
pilot fixture, inspecting the finding, issuing a correction, and observing selective re-run.

## Prerequisites

- Backend + Postgres running with Spec 001 + Spec 002 migrations applied.
- Rule loader extension and the two new capabilities merged.
- Aliases scaffolded at `backend/config/rules/pilot/aliases/materials.yaml` (can be empty).
- A completed package run (Spec 001) with at least a `BPCR` and a `RawMaterialPage`.

## 1. Author a cross-document rule

Create `backend/config/rules/pilot/alcoa/cross-doc-material-weight-match.yaml`:

```yaml
id: alcoa.accurate.bpcr-raw-material-weight-match
version: 1.0.0
severity: major
alcoa_tag: Accurate
description: "BPCR dispensed weight must match raw-material page within 0.1 kg"

context_object:
  scope: cross_document
  role: RawMaterialPage
  entity_match:
    strategy: normalise
    normalise: true
    case_insensitive: true
    punctuation_strip: ["-", "_", "."]
    aliases_file: backend/config/rules/pilot/aliases/materials.yaml
  multiplicity: error
  fallback: flag_as_unevaluated

source:
  field: dispensed_weight_kg
  scope_hint: bpcr_step_page
target:
  field: weight_kg

tolerance:
  kind: absolute
  value: 0.1
  unit: kg
```

## 2. Restart the pipeline to load the rule

```bash
cd backend
uv run uvicorn app.main:app --reload
```

In the startup log expect:

```
rule_loader: loaded 142 rules (138 legacy same_page, 3 cross_document, 1 page_aggregate)
rule_loader: reverse_graph edges: 412
rule_loader: rules_manifest snapshot id = rman_01H...
```

If the YAML is malformed, the service will refuse to start and emit the rule id + schema
path.

## 3. Start a run on the pilot fixture

```bash
curl -X POST http://localhost:8000/api/v1/bmr/runs \
  -H "Content-Type: application/json" \
  -d '{"package_id":"<pilot_with_planted_0.2kg_mismatch>"}'
```

WebSocket stream should include, during the `COMPLIANCE` stage:

```json
{"type":"finding.emitted",
 "finding_id":"fnd_...",
 "rule_id":"alcoa.accurate.bpcr-raw-material-weight-match",
 "source":"cross_doc_rule_eval.v1",
 "scope":{"kind":"bpcr_step","step_number":3},
 "alcoa_tags":["Accurate"]}
```

## 4. Inspect the finding detail

```bash
curl http://localhost:8000/api/v1/bmr/runs/<run_id>/findings/fnd_... | jq
```

Expect (per FR-010):

```json
{
  "rule_id": "alcoa.accurate.bpcr-raw-material-weight-match",
  "rule_version": "1.0.0",
  "resolved_context": {
    "scope": "cross_document",
    "matched_role": "RawMaterialPage",
    "matched_key": "material a",
    "matched_document_id": "drf_...",
    "entity_match_strategy": "normalise",
    "tolerance_applied": {"kind":"absolute","value":0.1,"unit":"kg"}
  },
  "evidence": [
    {"document_ref_id":"drf_bpcr...","page_number":3,"region":{...}},
    {"document_ref_id":"drf_raw...","page_number":1,"region":{...}}
  ],
  "observed_values": {"source":12.7, "target":12.5},
  "severity": "major"
}
```

## 5. Add an alias, verify reload-only-on-restart

Edit `backend/config/rules/pilot/aliases/materials.yaml`:

```yaml
canonical_to_aliases:
  "Material A": ["MATERIAL-A", "Mat A", "Mat. A"]
```

Without restart, a run still sees the old alias set (indeterminism guard). Restart the
service; a fresh run now matches `MATERIAL-A` to `Material A` without a fallback firing.

## 6. Trigger selective re-run via correction

From Spec 004's resolution UI (or direct API), issue a `correct` action on the target
document's `weight_kg`:

```bash
curl -X POST http://localhost:8000/api/v1/bmr/runs/<run_id>/corrections \
  -H "Content-Type: application/json" \
  -d '{
    "document_ref_id":"drf_raw...",
    "field":"weight_kg",
    "from":12.5,
    "to":12.7,
    "reason_type":"ocr_misread",
    "reason_comment":"verified against paper batch record"
  }'
```

Expect WebSocket events:

```json
{"type":"rerun.planned","rules":["alcoa.accurate.bpcr-raw-material-weight-match"], "reason":"correction on (RawMaterialPage, weight_kg)"}
{"type":"rerun.completed","elapsed_ms":812,"findings_invalidated":1,"findings_added":0}
```

Only the one rule re-evaluated, confirming SC-004 reverse-graph behaviour.

## 7. Page-aggregate fixture

Add `backend/config/rules/pilot/alcoa/bpcr-step-sum-vs-batch-target.yaml` per the example in
`data-model.md §2.3`. Run against a fixture with a planted 0.8% discrepancy; expect one
finding at `scope.kind=document`.

## 8. Regression check — legacy same-page path unchanged

```bash
cd backend && uv run pytest tests/regression/test_legacy_same_page_rules_unchanged.py -v
```

Must pass (Constitution VII gate). If any output diff appears, the change has regressed
legacy behaviour and must be fixed before merge.

## 9. Constitution spot-check

- Rule-as-data: `grep -R "RawMaterialPage" backend/app` MUST return zero hits (pilot-role
  names live only in YAML).
- Evidence-bound: pytest assertion
  `tests/capabilities/test_cross_doc_rule_eval.py::test_every_finding_has_both_evidence`
  MUST pass.
- ALCOA+ audit trail: every emitted finding has a matching `resolved_context` row.
