# Capability Contract: Per-Section Document Type

**Version**: 1.0  
**Date**: 2026-04-29

## `build_page_to_section` Output Contract

The page map returned by `build_page_to_section(seg)` adds a `document_type` key to every page entry.

### Page map entry shape (after this feature)

```python
{
    "section_id": str,          # unchanged
    "section_name": str,        # unchanged
    "section_type": str,        # unchanged — normalized via normalize_section_type()
    "document_type": str,       # NEW — normalized via normalize_document_type(); "" if unresolved
    "start_page": int,          # unchanged
    "end_page": int,            # unchanged
}
```

### Normalization rules for `document_type`

| Input | Output |
|---|---|
| `""` (empty string) | `""` |
| canonical key (e.g., `"batch_record"`) | `"batch_record"` |
| alias (e.g., `"bmr"`, `"batch manufacturing record"`) | resolved canonical key (e.g., `"batch_record"`) |
| unrecognized value (e.g., `"logbook"`) | input unchanged (e.g., `"logbook"`) |

Alias resolution uses the `aliases` list on each `document_profile` in `document_profiles.yaml`. The `section_aliases` section is not used.

---

## `effective_doc_type` Fallback Contract (Evaluator)

At each gate call site in `run_agent_evaluation`, `effective_doc_type` is computed as:

```python
effective_doc_type = (sec_info or {}).get("document_type") or document_type
```

| `sec_info["document_type"]` | `document_type` (orchestrator) | `effective_doc_type` |
|---|---|---|
| `"operation_checklist"` | `"batch_record"` | `"operation_checklist"` |
| `"scada_report"` | `"batch_record"` | `"scada_report"` |
| `""` (empty — unresolved or old cache) | `"batch_record"` | `"batch_record"` |
| `None` (sec_info absent) | `"batch_record"` | `"batch_record"` |
| `"logbook"` (unrecognized) | `"batch_record"` | `"logbook"` (no rules will match; harmless) |

---

## `segmentation.json` Schema (extended)

```json
{
  "sections": [
    {
      "section_id": "string",
      "name": "string",
      "section_type": "string",
      "document_type": "string",
      "start_page": 0,
      "end_page": 0,
      "description": "string"
    }
  ],
  "document_type": "string",
  "confidence": 0.0
}
```

`document_type` on each section is raw LLM output. Old files without this key deserialize with `document_type: ""`.

---

## Invariants

1. `section_type` and `document_type` are independent. No code derives one from the other at runtime.
2. `document_profiles.yaml` is the single source of truth. No canonical type list exists in Python code.
3. The `section_aliases` block in `document_profiles.yaml` is never used for `document_type` normalization — only per-profile `aliases` lists are.
4. Removing or renaming a canonical key in `document_profiles.yaml` automatically removes it from the segmentation prompt at next restart (LRU cache invalidated on restart).
