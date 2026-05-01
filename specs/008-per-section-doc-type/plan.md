# Implementation Plan: Per-Section Document Type Classification

**Branch**: `008-per-section-doc-type` | **Date**: 2026-04-29 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `specs/008-per-section-doc-type/spec.md`

## Summary

Enable per-page rule applicability filtering by adding a `document_type` field to `DocumentSection` and plumbing it through the segmentation prompt, the page-map builder, and the evaluator's gate call sites. The segmentation LLM is given the 9 canonical document type keys from `document_profiles.yaml` (dynamically, no hardcoding) and asked to classify each identified sub-document. `build_page_to_section` normalizes the raw LLM output via `normalize_document_type()`. The evaluator derives `effective_doc_type` per page — section value → `""` → orchestrator fallback — and passes it to all three `ApplicabilityGate` call sites instead of the single orchestrator document type. No new agents, no new ports, no YAML changes.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: Pydantic v2, PyYAML — both already present  
**Storage**: Filesystem JSON (`segmentation.json` — existing file, new field added)  
**Testing**: pytest (existing `backend/tests/compliance/` suite)  
**Target Platform**: Backend service (same as existing compliance pipeline)  
**Project Type**: Internal pipeline extension — 3 source files, 1 test file  
**Performance Goals**: No additional LLM calls; prompt length increases by ~1 line  
**Constraints**: Old `segmentation.json` caches must deserialize without error  
**Scale/Scope**: Touches 3 files; no schema migrations, no API changes

## Constitution Check

- [x] **I. Leverage-first**: Extends existing `DocumentSegmenter`, `build_page_to_section`, and `ApplicabilityGate` — no replacement of any subsystem.
- [x] **II. 5-stage soft gates + parallel compliance**: Segmentation sits in the Legibility & Classification stage. This change adds one field to its output; does not restructure the pipeline or introduce mid-pipeline HITL.
- [x] **III. Capability-first**: `build_page_to_section` is a single-responsibility utility. The evaluator change is one line per call site. No new monolithic agent.
- [x] **IV. Single final checkpoint**: No new HITL introduced; no change to re-run scope.
- [x] **V. Evidence-bound findings**: Improves which rules are applied — findings now come from the correct document-type profile rather than a single package-level type.
- [x] **VI. Configurable framework**: `document_profiles.yaml` drives both the prompt and normalization. No canonical type list hardcoded in Python.
- [x] **VII. Existing framework is the backbone**: All three changed files are extensions only. `section_type` classification, existing agents, and single-document modes are unchanged.
- [x] **VIII. ALCOA+ audit trail**: No new findings/corrections/HITL actions written; N/A.
- [x] **IX. Rule-as-data**: No new compliance logic added as Python conditionals. This change improves how existing `applicable_document_types` rule YAML is applied at runtime.

All gates pass. No complexity tracking required.

## Project Structure

### Documentation (this feature)

```text
specs/008-per-section-doc-type/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── contracts/           ← Phase 1 output
│   └── capability-contract.md
├── quickstart.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit-tasks)
```

### Source Code

```text
backend/app/compliance/
├── models.py                  ← Task 1: add document_type field
├── segmentation.py            ← Task 2 + 3: prompt update, page-map normalization
└── evaluator.py               ← Task 4: effective_doc_type at 3 gate call sites

backend/tests/compliance/
└── test_per_section_doc_type.py   ← Task 5: rewrite to match final design
```

**Structure Decision**: Single-package backend extension. No new modules or packages.

## Implementation Tasks

### Task 1 — Extend `DocumentSection` with `document_type` field

**File**: `backend/app/compliance/models.py`  
**Where**: `DocumentSection` class (line ~163)

Add one plain field with a default of `""`. No field validator — raw LLM output is preserved in `segmentation.json` for inspection. Pydantic serializes it as-is.

```python
class DocumentSection(BaseModel):
    section_id: str = ""
    name: str = ""
    section_type: str = ""
    document_type: str = ""        # NEW — sub-document classifier; normalized in build_page_to_section
    start_page: int = 0
    end_page: int = 0
    description: str = ""
```

**Acceptance**: Old `segmentation.json` (no `document_type` key) deserializes without error; field defaults to `""`.

---

### Task 2 — Update `_build_segmentation_prompt`: drop KV pairs, add canonical doc types

**File**: `backend/app/compliance/segmentation.py`  
**Where**: `_build_segmentation_prompt` function

Two changes in one function:

**2a — Remove `key_value_pairs` parameter and block from prompt body.** The parameter is removed from `_build_segmentation_prompt`'s signature. The caller (`DocumentSegmenter.segment`) still receives `key_value_pairs` from `compliance_graph.py` — just no longer forwards it to the prompt builder.

**2b — Add canonical document type instruction.** Load canonical keys dynamically from `document_profiles.yaml` (sorted, joined). Add two instruction lines per section:

```python
from app.compliance.rules.profiles import load_profiles

def _build_segmentation_prompt(extractions: list[dict], filename: str = "") -> str:
    profiles = load_profiles()
    allowed_doc_types = ", ".join(sorted(profiles.document_profiles.keys()))
    # ...existing page_summaries block...
    return (
        f"Analyze this multi-part document and identify each distinct sub-document/section.\n\n"
        f"Look for: page numbering restarts, document titles, headers that change, "
        f"form layout shifts, and content topic changes.\n\n"
        f"FILENAME: {filename}\n\n"
        f"PAGE SUMMARIES:\n" + "\n\n".join(page_summaries) + "\n\n"
        f"For each section return:\n"
        f"- section_id: short lowercase_snake_case slug\n"
        f"- name: descriptive human-readable name\n"
        f"- section_type: descriptive type in lowercase_snake_case (be specific)\n"
        f"- document_type: one of: {allowed_doc_types}\n"
        f"  If this section is a sub-section of a larger document already classified above, "
        f"repeat that document's type.\n"
        f"- start_page / end_page: inclusive page range\n"
        f"- description: brief description of the section content\n\n"
        f"Also return the overall document_type and your confidence (0.0-1.0)."
    )
```

**Critical**: `section_aliases` from `document_profiles.yaml` are NOT included — only `profiles.document_profiles.keys()` is used.

**Acceptance**: Prompt string contains the 9 canonical keys; does not contain KV pair text; does not contain section alias keys.

---

### Task 3 — Update `build_page_to_section`: normalize `document_type`

**File**: `backend/app/compliance/segmentation.py`  
**Where**: `build_page_to_section` function

Add `document_type` to the `info` dict, normalized via `normalize_document_type()`:

```python
from app.compliance.rules.profiles import normalize_document_type, normalize_section_type

def build_page_to_section(seg: DocumentSegmentation) -> dict[int, dict]:
    page_map: dict[int, dict] = {}
    for sec in seg.sections:
        info = {
            "section_id": sec.section_id,
            "section_name": sec.name,
            "section_type": normalize_section_type(sec.section_type),
            "document_type": normalize_document_type(sec.document_type) if sec.document_type else "",
            "start_page": sec.start_page,
            "end_page": sec.end_page,
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
```

`normalize_document_type()` resolves aliases (`"batch manufacturing record"` → `"batch_record"`, `"vacuum dryer scada"` → `"scada_report"`) using the `aliases` list on each profile in `document_profiles.yaml`. If the value has no alias match and is not a canonical key, it returns the value unchanged — the evaluator's `or document_type` fallback handles this case.

**Acceptance**: `build_page_to_section` output contains `document_type` key on every page entry; alias inputs are resolved to canonical keys.

---

### Task 4 — Update `evaluator.py`: `effective_doc_type` at 3 gate call sites

**File**: `backend/app/compliance/evaluator.py`  
**Where**: `run_agent_evaluation` function — two inner functions `_prescreen_page` and `_run`

**4a — `_prescreen_page`** (line ~338): After `sec_info` is resolved, add one line and replace `document_type=document_type` with `document_type=effective_doc_type`:

```python
async def _prescreen_page(ext: dict) -> None:
    page_num = ext.get("page_num", 0)
    sec_info = section_map.get(page_num) if section_map else None
    effective_doc_type = (sec_info or {}).get("document_type") or document_type  # NEW
    page_type = classify_page_type(ext)
    candidate_rules, _, _ = await gate.filter_rules_hybrid(
        all_agent_rules,
        document_type=effective_doc_type,   # was: document_type
        ...
    )
```

**4b — `_run` LLM mode** (line ~425): After `sec_info` is resolved, same pattern:

```python
async def _run(batch: RuleBatch, ext: dict) -> ...:
    page_num = ext.get("page_num", 0)
    sec_info = section_map.get(page_num) if section_map else None
    effective_doc_type = (sec_info or {}).get("document_type") or document_type  # NEW

    if mode == "llm":
        ...
        applicable_rules, gate_evals, gate_trace_map = await gate.filter_rules_hybrid(
            batch.rules,
            document_type=effective_doc_type,   # was: document_type
            ...
        )
    else:
        applicable_rules, gate_evals, gate_trace_map = gate.filter_rules(
            batch.rules, effective_doc_type, ...  # was: document_type
        )
```

**Fallback chain**: `(sec_info or {}).get("document_type")` returns `""` for old cached segmentation (Pydantic default) or for unresolved LLM output. `"" or document_type` evaluates to `document_type` (orchestrator type). No regression possible.

**Acceptance**: All three gate call sites use `effective_doc_type`. Pages with `document_type: ""` in the page map use the orchestrator type unchanged.

---

### Task 5 — Rewrite test file to match final design

**File**: `backend/tests/compliance/test_per_section_doc_type.py`  
**Context**: The existing file tests normalization at the `DocumentSection` model level (field validator). Our design places normalization in `build_page_to_section`, so the model-level alias/collapse tests need to be removed and replaced with `build_page_to_section` tests.

**Tests to keep** (model behavior — no validator):
- `test_empty_string_stays_empty`: `DocumentSection(document_type="")` → `doc_type == ""`
- `test_omitted_defaults_to_empty`: `DocumentSection()` → `doc_type == ""`
- `test_canonical_key_passes_through`: `DocumentSection(document_type="batch_record")` → `doc_type == "batch_record"` (no transformation at model level)

**Tests to remove** (assumed field validator — no longer applicable):
- `test_alias_resolves_to_canonical` — alias resolution is not at model level
- `test_unrecognized_value_collapses_to_empty` — collapse is not at model level
- `test_paraphrase_resolves_via_alias` — alias resolution is not at model level
- `test_all_canonical_types_pass_through` — redundant with keep above; model just stores the value

**Tests to add** (normalization in `build_page_to_section`):
- `test_build_page_to_section_includes_document_type`: section with `document_type="batch_record"` → page map entry contains `document_type: "batch_record"`
- `test_build_page_to_section_resolves_alias`: section with `document_type="bmr"` → page map entry contains `document_type: "batch_record"`
- `test_build_page_to_section_unrecognized_preserved`: section with `document_type="logbook"` → page map entry contains `document_type: "logbook"` (not collapsed — evaluator fallback handles this)
- `test_build_page_to_section_empty_preserved`: section with `document_type=""` → page map entry contains `document_type: ""`
- `test_old_segmentation_json_no_document_type_field`: `DocumentSegmentation` loaded from JSON without `document_type` key → all sections have `document_type == ""`
- `test_effective_doc_type_fallback`: page map entry with `document_type=""` → `(sec_info or {}).get("document_type") or "batch_record"` evaluates to `"batch_record"` (inline logic test, no evaluator import needed)

## Dependency Order

Tasks are sequential:

```
Task 1 (models.py field) 
  → Task 2 (prompt: removes kv_pairs, adds doc type instruction)
  → Task 3 (build_page_to_section: adds normalize_document_type)
  → Task 4 (evaluator: effective_doc_type at 3 call sites)
  → Task 5 (tests: rewrite to match design)
```

Task 4 depends on Task 3 (the `document_type` key must be in the page map before the evaluator reads it). Tasks 2 and 3 are independent of each other (both depend on Task 1).

## Verification

After all tasks:

1. Run `backend/tests/compliance/test_per_section_doc_type.py` — all tests pass.
2. Delete `backend/data/documents/90ec18f4-1f29-4613-92e8-c2325bec9968/segmentation.json` (cache bust).
3. Re-run compliance pipeline on the sample document.
4. Open the new `segmentation.json` — verify every section has a `document_type` field with a value from the 9 canonical keys.
5. Confirm at least one section has `document_type: "operation_checklist"` (pages 80–97).
6. Confirm at least one section has `document_type: "scada_report"` (pages 48–67).
