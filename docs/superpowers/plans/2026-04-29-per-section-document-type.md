# Per-Section Document Type Classification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the compliance applicability gate to filter rules using the correct document type per page, derived from which sub-document that page belongs to in a mixed-document package.

**Architecture:** Add `document_type` to `DocumentSection` (with a self-validating field validator that uses `document_profiles.yaml` as the source of truth), emit it from the segmenter prompt dynamically, include it in the `section_map` page info dict, and have the evaluator derive `effective_doc_type` per page from the section map rather than using the single orchestrator document type for all pages.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, pytest-asyncio

---

## File Map

| File | Change |
|---|---|
| `backend/app/compliance/models.py` | Add `document_type: str = ""` + `@field_validator` to `DocumentSection` |
| `backend/app/compliance/segmentation.py` | Dynamic doc types in prompt; add `document_type` to `build_page_to_section` output |
| `backend/app/compliance/evaluator.py` | `effective_doc_type` lookup in `_prescreen_page` and `_run` (3 call sites) |
| `backend/tests/compliance/test_per_section_doc_type.py` | New test file covering all three changes |

---

### Task 1: `DocumentSection` — add `document_type` field with profile-aware validator

**Files:**
- Modify: `backend/app/compliance/models.py`
- Test: `backend/tests/compliance/test_per_section_doc_type.py`

- [ ] **Step 1: Create the test file with failing tests for the field validator**

Create `backend/tests/compliance/test_per_section_doc_type.py`:

```python
"""Tests for per-section document type classification."""
from __future__ import annotations

import pytest
from app.compliance.models import DocumentSection


class TestDocumentSectionDocumentType:
    def test_empty_string_stays_empty(self):
        sec = DocumentSection(section_type="manufacturing_operations", document_type="")
        assert sec.document_type == ""

    def test_omitted_defaults_to_empty(self):
        sec = DocumentSection(section_type="manufacturing_operations")
        assert sec.document_type == ""

    def test_canonical_key_passes_through(self):
        sec = DocumentSection(section_type="manufacturing_operations", document_type="batch_record")
        assert sec.document_type == "batch_record"

    def test_alias_resolves_to_canonical(self):
        # "bmr" is an alias for batch_record in document_profiles.yaml
        sec = DocumentSection(section_type="cover_page", document_type="bmr")
        assert sec.document_type == "batch_record"

    def test_unrecognized_value_collapses_to_empty(self):
        sec = DocumentSection(section_type="unknown_section", document_type="logbook")
        assert sec.document_type == ""

    def test_paraphrase_resolves_via_alias(self):
        # "scada data" is a listed alias for scada_report
        sec = DocumentSection(section_type="scada_section", document_type="scada data")
        assert sec.document_type == "scada_report"

    def test_all_canonical_types_pass_through(self):
        canonical_types = [
            "batch_record", "raw_material_request", "scada_report",
            "ipc_report", "analysis_report", "certificate",
            "batch_closure", "operation_checklist", "qc_analytical_package",
        ]
        for doc_type in canonical_types:
            sec = DocumentSection(section_type="some_section", document_type=doc_type)
            assert sec.document_type == doc_type, f"Expected {doc_type} to pass through"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestDocumentSectionDocumentType -v
```

Expected: `AttributeError` or `FAILED` — `document_type` field does not exist yet.

- [ ] **Step 3: Add the field and validator to `DocumentSection` in `models.py`**

Open `backend/app/compliance/models.py`. Find `class DocumentSection` and update it:

```python
from pydantic import BaseModel, Field, field_validator

# Add these imports at the top of the file if not already present:
# from app.compliance.rules.profiles import load_profiles, normalize_document_type


class DocumentSection(BaseModel):
    """A distinct sub-document identified during segmentation."""

    section_id: str = ""
    name: str = ""
    section_type: str = ""
    document_type: str = ""
    start_page: int = 0
    end_page: int = 0
    description: str = ""

    @field_validator("document_type", mode="after")
    @classmethod
    def _normalize_doc_type(cls, v: str) -> str:
        if not v:
            return ""
        from app.compliance.rules.profiles import load_profiles, normalize_document_type
        normalized = normalize_document_type(v)
        profiles = load_profiles()
        return normalized if normalized in profiles.document_profiles else ""
```

Note: the import is inside the validator to avoid a circular import (models.py is imported by profiles.py indirectly). If no circular import exists, move the import to the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestDocumentSectionDocumentType -v
```

Expected: all 7 tests PASS.


---

### Task 2: `build_page_to_section` — include `document_type` in page info dict

**Files:**
- Modify: `backend/app/compliance/segmentation.py`
- Test: `backend/tests/compliance/test_per_section_doc_type.py`

- [ ] **Step 1: Add failing tests for `build_page_to_section`**

Append to `backend/tests/compliance/test_per_section_doc_type.py`:

```python
from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import build_page_to_section


class TestBuildPageToSection:
    def _make_seg(self, doc_type: str, start: int = 1, end: int = 5) -> DocumentSegmentation:
        return DocumentSegmentation(
            sections=[
                DocumentSection(
                    section_id="s1",
                    name="Manufacturing Operations",
                    section_type="manufacturing_operations",
                    document_type=doc_type,
                    start_page=start,
                    end_page=end,
                )
            ],
            document_type="batch_record",
            confidence=0.9,
        )

    def test_page_map_contains_document_type_key(self):
        seg = self._make_seg("batch_record")
        page_map = build_page_to_section(seg)
        assert "document_type" in page_map[1]

    def test_page_map_document_type_matches_section(self):
        seg = self._make_seg("batch_record")
        page_map = build_page_to_section(seg)
        assert page_map[1]["document_type"] == "batch_record"

    def test_page_map_empty_document_type_preserved(self):
        # Empty string (unresolved) must flow through as-is for fallback logic
        seg = self._make_seg("")
        page_map = build_page_to_section(seg)
        assert page_map[1]["document_type"] == ""

    def test_all_pages_in_range_get_document_type(self):
        seg = self._make_seg("scada_report", start=3, end=6)
        page_map = build_page_to_section(seg)
        for page in range(3, 7):
            assert page_map[page]["document_type"] == "scada_report"

    def test_mixed_sections_get_correct_document_type(self):
        seg = DocumentSegmentation(
            sections=[
                DocumentSection(
                    section_id="bmr",
                    name="Batch Record",
                    section_type="manufacturing_operations",
                    document_type="batch_record",
                    start_page=1,
                    end_page=10,
                ),
                DocumentSection(
                    section_id="scada",
                    name="SCADA Report",
                    section_type="scada_report",
                    document_type="scada_report",
                    start_page=11,
                    end_page=15,
                ),
            ],
            document_type="batch_record",
            confidence=0.9,
        )
        page_map = build_page_to_section(seg)
        assert page_map[5]["document_type"] == "batch_record"
        assert page_map[12]["document_type"] == "scada_report"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestBuildPageToSection -v
```

Expected: `FAILED` — `document_type` key missing from page info dict.

- [ ] **Step 3: Update `build_page_to_section` in `segmentation.py`**

Open `backend/app/compliance/segmentation.py`. Find `build_page_to_section` (currently around line 119) and update:

```python
from app.compliance.rules.profiles import normalize_section_type  # already imported
# normalize_document_type is NOT needed here — validator already resolved the value


def build_page_to_section(seg: DocumentSegmentation) -> dict[int, dict]:
    """Build a lookup from page number to section info dict."""
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestBuildPageToSection -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run all compliance tests to check for regressions**

```bash
cd backend && pytest tests/compliance/ -v
```

Expected: all existing tests still PASS.

---

### Task 3: Segmenter prompt — dynamic allowed document types from profiles

**Files:**
- Modify: `backend/app/compliance/segmentation.py`
- Test: `backend/tests/compliance/test_per_section_doc_type.py`

- [ ] **Step 1: Add failing test for the prompt**

Append to `backend/tests/compliance/test_per_section_doc_type.py`:

```python
from app.compliance.rules.profiles import load_profiles
from app.compliance.segmentation import _build_segmentation_prompt


class TestSegmentationPrompt:
    def test_prompt_contains_all_canonical_doc_types(self):
        profiles = load_profiles()
        canonical_keys = sorted(profiles.document_profiles.keys())
        extractions = [{"page_num": 1, "markdown": "sample content"}]
        prompt = _build_segmentation_prompt(extractions, filename="test.pdf")
        for key in canonical_keys:
            assert key in prompt, f"Expected canonical doc type '{key}' in prompt"

    def test_prompt_contains_document_type_instruction(self):
        extractions = [{"page_num": 1, "markdown": "sample content"}]
        prompt = _build_segmentation_prompt(extractions, filename="test.pdf")
        assert "document_type" in prompt

    def test_prompt_updates_when_profiles_grow(self):
        # Confirm prompt is built dynamically, not hardcoded
        profiles = load_profiles()
        expected_count = len(profiles.document_profiles)
        extractions = [{"page_num": 1, "markdown": "content"}]
        prompt = _build_segmentation_prompt(extractions, filename="test.pdf")
        found = sum(1 for key in profiles.document_profiles if key in prompt)
        assert found == expected_count
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestSegmentationPrompt -v
```

Expected: `FAILED` — prompt does not yet contain canonical doc type keys.

- [ ] **Step 3: Update `_build_segmentation_prompt` in `segmentation.py`**

Open `backend/app/compliance/segmentation.py`. Add the profiles import at the top:

```python
from app.compliance.rules.profiles import load_profiles, normalize_section_type
```

Update `_build_segmentation_prompt` to load allowed types dynamically and add a `document_type` instruction per section:

```python
def _build_segmentation_prompt(
    extractions: list[dict],
    key_value_pairs: list[dict] | None = None,
    filename: str = "",
) -> str:
    profiles = load_profiles()
    allowed_doc_types = ", ".join(sorted(profiles.document_profiles.keys()))

    page_summaries = []
    for ext in extractions:
        page_num = ext.get("page_num", 0)
        md = ext.get("markdown", "")
        page_summaries.append(f"Page {page_num}: {md[:_CHARS_PER_PAGE]}")

    kv_text = "None extracted"
    if key_value_pairs:
        kv_text = "\n".join(
            f"- {kv.get('key', '?')}: {kv.get('value', '?')}"
            for kv in key_value_pairs[:30]
        )

    return (
        f"Analyze this multi-part document and identify each distinct sub-document/section.\n\n"
        f"Look for: page numbering restarts, document titles, headers that change, "
        f"form layout shifts, and content topic changes.\n\n"
        f"FILENAME: {filename}\n\n"
        f"KEY-VALUE PAIRS:\n{kv_text}\n\n"
        f"PAGE SUMMARIES:\n" + "\n\n".join(page_summaries) + "\n\n"
        f"For each section return:\n"
        f"- section_id: short lowercase_snake_case slug\n"
        f"- name: descriptive human-readable name\n"
        f"- section_type: the specific section within its document "
        f"(e.g. 'manufacturing_operations', 'yield_calculation', 'cover_page')\n"
        f"- document_type: the standalone document type this section belongs to. "
        f"Use one of: {allowed_doc_types}. "
        f"If this section is part of a larger document already classified above, "
        f"repeat that document's type.\n"
        f"- start_page / end_page: inclusive page range\n"
        f"- description: brief description of the section content\n\n"
        f"Also return the overall document_type and your confidence (0.0-1.0)."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestSegmentationPrompt -v
```

Expected: all 3 tests PASS.

---

### Task 4: Evaluator — per-page `effective_doc_type` from section map

**Files:**
- Modify: `backend/app/compliance/evaluator.py` (lines 341–346 and 419–440)
- Test: `backend/tests/compliance/test_per_section_doc_type.py`

- [ ] **Step 1: Add failing tests for `effective_doc_type` derivation**

Append to `backend/tests/compliance/test_per_section_doc_type.py`:

```python
from app.compliance.applicability import ApplicabilityGate
from app.compliance.rules.registry import AuditRule


class TestEffectiveDocType:
    """Verify that section_map document_type controls rule applicability per page."""

    def _make_rule(self, doc_types: list[str]) -> AuditRule:
        return AuditRule(
            id="TST-ATT1",
            number=1,
            category="attributable",
            category_display="Attributable",
            agent="alcoa",
            text="Test rule",
            severity_hint="major",
            applicable_document_types=doc_types,
        )

    def _make_gate(self) -> ApplicabilityGate:
        from unittest.mock import MagicMock
        return ApplicabilityGate(llm=MagicMock())

    def test_rule_skipped_when_doc_type_mismatches_orchestrator_type(self):
        gate = self._make_gate()
        rule = self._make_rule(["scada_report"])
        sec_info = None  # no section map — uses orchestrator type
        applicable, _, _ = gate.filter_rules(
            [rule], "batch_record", "text", sec_info, {"markdown": "content"}
        )
        assert applicable == []

    def test_rule_applies_when_section_doc_type_matches(self):
        gate = self._make_gate()
        rule = self._make_rule(["scada_report"])
        sec_info = {
            "section_id": "scada_1",
            "section_type": "scada_report",
            "document_type": "scada_report",   # per-section override
        }
        # Simulate what the evaluator does: derive effective_doc_type from sec_info
        effective_doc_type = (sec_info or {}).get("document_type") or "batch_record"
        applicable, _, _ = gate.filter_rules(
            [rule], effective_doc_type, "text", sec_info, {"markdown": "content"}
        )
        assert len(applicable) == 1
        assert applicable[0].id == "TST-ATT1"

    def test_fallback_to_orchestrator_when_section_doc_type_empty(self):
        gate = self._make_gate()
        rule = self._make_rule(["batch_record"])
        sec_info = {"document_type": ""}  # old cache — no doc type
        effective_doc_type = (sec_info or {}).get("document_type") or "batch_record"
        assert effective_doc_type == "batch_record"
        applicable, _, _ = gate.filter_rules(
            [rule], effective_doc_type, "text", sec_info, {"markdown": "content"}
        )
        assert len(applicable) == 1

    def test_fallback_when_no_section_map(self):
        gate = self._make_gate()
        rule = self._make_rule(["batch_record"])
        sec_info = None
        effective_doc_type = (sec_info or {}).get("document_type") or "batch_record"
        assert effective_doc_type == "batch_record"
```

- [ ] **Step 2: Run tests to verify they pass without evaluator changes**

```bash
cd backend && pytest tests/compliance/test_per_section_doc_type.py::TestEffectiveDocType -v
```

These tests exercise the logic in isolation (not the evaluator internals). They should PASS as written — they confirm the logic is correct before we wire it into the evaluator. If any fail, fix the test fixture before proceeding.

- [ ] **Step 3: Update `_prescreen_page` in `evaluator.py`**

Open `backend/app/compliance/evaluator.py`. Find `_prescreen_page` (around line 338). Add `effective_doc_type` immediately after `sec_info` is resolved and pass it to the gate:

```python
async def _prescreen_page(ext: dict) -> None:
    nonlocal pages_done
    page_num = ext.get("page_num", 0)
    sec_info = section_map.get(page_num) if section_map else None
    effective_doc_type = (sec_info or {}).get("document_type") or document_type  # NEW
    page_type = classify_page_type(ext)
    try:
        candidate_rules, _, _ = await gate.filter_rules_hybrid(
            all_agent_rules,
            document_type=effective_doc_type,    # CHANGED (was: document_type)
            page_type=page_type,
            extraction=ext,
            page_num=page_num,
            llm=llm,
            section_info=sec_info,
            prescreen_cache=None,
        )
```

- [ ] **Step 4: Update `_run` in `evaluator.py`**

Find `_run` (around line 416). Add `effective_doc_type` after `sec_info` and pass it to both gate calls:

```python
async def _run(batch: RuleBatch, ext: dict) -> tuple[str, int, RuleBatchResult]:
    nonlocal completed
    page_num = ext.get("page_num", 0)
    sec_info = section_map.get(page_num) if section_map else None
    effective_doc_type = (sec_info or {}).get("document_type") or document_type  # NEW

    if mode == "llm":
        if page_num not in page_type_cache:
            page_type_cache[page_num] = classify_page_type(ext)
        page_type = page_type_cache[page_num]
        applicable_rules, gate_evals, gate_trace_map = await gate.filter_rules_hybrid(
            batch.rules,
            document_type=effective_doc_type,    # CHANGED (was: document_type)
            page_type=page_type,
            extraction=ext,
            page_num=page_num,
            llm=llm,
            section_info=sec_info,
            prescreen_cache=prescreen_cache,
        )
    else:
        if page_num not in page_type_cache:
            page_type_cache[page_num] = classify_page_type(ext)
        page_type = page_type_cache[page_num]
        applicable_rules, gate_evals, gate_trace_map = gate.filter_rules(
            batch.rules, effective_doc_type, page_type, sec_info, ext,  # CHANGED (was: document_type)
        )
```

- [ ] **Step 5: Run all tests**

```bash
cd backend && pytest tests/compliance/ -v
```

Expected: all tests PASS including the new ones.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
cd backend && pytest tests/ -v --ignore=tests/benchmark -q
```

Expected: no new failures.

---

## Self-Review

**Spec coverage:**
- ✅ `DocumentSection.document_type` field + profile-aware validator → Task 1
- ✅ `build_page_to_section` includes `document_type` → Task 2
- ✅ Segmenter prompt dynamically lists doc types from profiles → Task 3
- ✅ Evaluator uses `effective_doc_type` per page in all 3 gate call sites → Task 4
- ✅ Old `segmentation.json` cache compatibility → handled by Pydantic default + empty-string fallback (tested in Task 4 step 2)
- ✅ `document_profiles.yaml` as single source of truth → prompt reads from `load_profiles()`, validator validates against `load_profiles()`

**Placeholder scan:** No TBDs. All code blocks are complete and runnable.

**Type consistency:** `effective_doc_type: str` used consistently. `sec_info: dict | None` matches existing evaluator signature. `document_type: str = ""` on `DocumentSection` matches validator return type.
