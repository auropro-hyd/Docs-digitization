# Per-Section Document Type Classification

**Date:** 2026-04-29  
**Status:** Approved  
**Scope:** Segmentation layer only — no rule YAML, report structure, or agent changes.

---

## Problem

A document package uploaded to the compliance pipeline is often a multi-document PDF: a Batch Manufacturing Record stapled together with a SCADA printout, an operation checklist, a Certificate of Analysis, and an IPC report. Today:

- `ComplianceOrchestrator` assigns **one `document_type`** (e.g. `batch_record`) to the entire package.
- `DocumentSegmenter` identifies section boundaries and `section_type` per section, but stores no `document_type` per section.
- The applicability gate (`applicability.py`) filters every rule against the **single** orchestrator document type for all pages.
- Rules scoped to `applicable_document_types: [scada_report]` or `[operation_checklist]` are silently skipped for the entire package — even pages that are genuinely SCADA or checklist content.

`document_profiles.yaml` defines 9 distinct document types anticipating mixed packages, but the pipeline has no mechanism to apply them per sub-document.

---

## Goal

Enable the applicability gate to filter rules using the correct document type **per page**, derived from which sub-document that page belongs to. Output (one unified compliance report, findings tagged by agent + page) is unchanged. Rule YAML is unchanged. `document_profiles.yaml` is the single source of truth for valid document types throughout.

---

## Design

### 1. `backend/app/compliance/models.py` — `DocumentSection`

Add one field with a self-validating field validator that derives valid values from `document_profiles.yaml` at runtime:

```python
from app.compliance.rules.profiles import load_profiles, normalize_document_type

class DocumentSection(BaseModel):
    section_id: str = ""
    name: str = ""
    section_type: str = ""
    document_type: str = ""        # NEW
    start_page: int = 0
    end_page: int = 0
    description: str = ""

    @field_validator("document_type", mode="after")
    @classmethod
    def _normalize_doc_type(cls, v: str) -> str:
        if not v:
            return ""
        normalized = normalize_document_type(v)
        profiles = load_profiles()
        return normalized if normalized in profiles.document_profiles else ""
```

**Why the validator:** The LLM may return paraphrases or unrecognized values. The validator runs `normalize_document_type()` (which resolves aliases from `document_profiles.yaml`) and then confirms the result is a known canonical key. Anything unresolved collapses to `""`, triggering the evaluator's fallback to the orchestrator's document type.

**Cache compatibility:** Old `segmentation.json` files without the field deserialize fine — Pydantic defaults `document_type` to `""`. Those pages fall back to the orchestrator type, identical to current behaviour.

---

### 2. `backend/app/compliance/segmentation.py` — Two changes

#### `_build_segmentation_prompt` — dynamic allowed values from profiles

```python
from app.compliance.rules.profiles import load_profiles

def _build_segmentation_prompt(...) -> str:
    profiles = load_profiles()
    allowed_doc_types = ", ".join(sorted(profiles.document_profiles.keys()))
    # in the prompt:
    f"- document_type: one of: {allowed_doc_types}.\n"
    f"  If this section is part of a larger document already classified above,\n"
    f"  repeat that document's type.\n"
```

Adding a new document type to `document_profiles.yaml` automatically updates what the LLM is told to use — no code change required. The key instruction — "repeat the parent document's type if this section belongs to it" — anchors BMR sub-sections (`manufacturing_operations`, `yield_calculation`) to `batch_record`, while standalone sub-documents (`scada_report`, `certificate`) get their own type.

#### `build_page_to_section` — include `document_type` in page info dict

```python
def build_page_to_section(seg: DocumentSegmentation) -> dict[int, dict]:
    page_map: dict[int, dict] = {}
    for sec in seg.sections:
        info = {
            "section_id": sec.section_id,
            "section_name": sec.name,
            "section_type": normalize_section_type(sec.section_type),
            "document_type": sec.document_type,   # already normalized by field validator
            "start_page": sec.start_page,
            "end_page": sec.end_page,
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
```

No second normalization call needed — the field validator already resolved and validated the value.

---

### 3. `backend/app/compliance/evaluator.py` — Two locations, three call sites

The evaluator already computes `sec_info = section_map.get(page_num)` in two places: `_prescreen_page` and `_run`. In both, add one line immediately after `sec_info` is resolved:

```python
sec_info = section_map.get(page_num) if section_map else None
effective_doc_type = (sec_info or {}).get("document_type") or document_type
```

Then pass `effective_doc_type` instead of `document_type` to all three gate call sites:

```python
# _prescreen_page
gate.filter_rules_hybrid(..., document_type=effective_doc_type, ...)

# _run — LLM mode
gate.filter_rules_hybrid(..., document_type=effective_doc_type, ...)

# _run — non-LLM mode
gate.filter_rules(batch.rules, effective_doc_type, ...)
```

**Fallback chain:** `sec_info["document_type"]` (validated canonical key from segmentation) → `""` (unresolved LLM output or old cache) → `document_type` (orchestrator's type for the whole package). No regression possible for any existing document.

---

## Failure Mode Handling

| Failure | How handled |
|---|---|
| LLM returns paraphrase (e.g. `"batch manufacturing record"`) | `normalize_document_type()` resolves via aliases in `document_profiles.yaml` |
| LLM returns unrecognized value (e.g. `"logbook"`) | Field validator collapses to `""` → evaluator falls back to orchestrator type |
| Old `segmentation.json` cache (no `document_type` field) | Pydantic defaults to `""` → same fallback |
| Segmentation LLM fails entirely | Single-section fallback with `section_type="unknown"`, `document_type=""` → orchestrator type used for all pages |

---

## Files Changed

| File | Change |
|---|---|
| `backend/app/compliance/models.py` | Add `document_type: str = ""` + field validator to `DocumentSection` |
| `backend/app/compliance/segmentation.py` | Dynamic doc types in prompt; add `document_type` to `build_page_to_section` |
| `backend/app/compliance/evaluator.py` | `effective_doc_type` lookup in two places, three call sites |

**Not changed:** `document_profiles.yaml` · any `*_rules.yaml` · `applicability.py` · `compliance_graph.py` · any agent class · report models · finding structure.

---

## Constraints & Non-Goals

- `document_profiles.yaml` is the single source of truth for valid document types — no hardcoded lists in code.
- One unified compliance report — no per-sub-document splitting.
- Findings continue to be tagged by agent + page, not by sub-document type.
- Rule YAML (`applicable_document_types`, `applicable_section_types`) unchanged.
- Old `segmentation.json` caches degrade gracefully to current behaviour rather than breaking.
