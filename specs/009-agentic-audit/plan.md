# Implementation Plan: Agentic Audit Evaluation Strategy

**Branch**: `009-agentic-audit` | **Date**: 2026-05-01 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/009-agentic-audit/spec.md`

## Summary

Refactor the agentic audit pipeline to eliminate `doc_dir` as an explicit parameter throughout the call chain (replaced by `doc_id` with internal path resolution), restructure the LangGraph flow from 4 nodes to 3 nodes (removing `gather_context`, renaming the router to `fan_out_workers`), delete `agentic/summarizer.py` in favour of a new `compliance/summarizer.py` with module-level functions, and add Phase 1.5 page summarization to `compliance_graph.py` (gated on `enable_cross_page`, parallel batch dispatch, load-then-generate-then-store). All three agents (ALCOA, GMP, Checklist) are already wired to call `run_agentic_postpass()`; this plan corrects the `doc_dir`/`doc_id` mismatch and removes the stale `doc_dir` parameter.

---

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI, LangGraph, asyncio, pydantic v2, pytest  
**Storage**: Filesystem JSON under `{settings.storage.base_path}/{doc_id}/summaries/{doc_type}__{sec_type or 'all'}.json`  
**Testing**: pytest with AsyncMock / MagicMock; existing test file `test_agentic_audit.py`  
**Target Platform**: Linux server (FastAPI async backend)  
**Project Type**: Web service — compliance pipeline extension  
**Performance Goals**: Phase 1.5 summarization in parallel batches of 10; agentic rule evaluation ≤30 s/rule/package when summaries are on disk  
**Constraints**: `doc_dir` never passed explicitly; always derived from `doc_id` via `Path(get_settings().storage.base_path) / doc_id`  
**Scale/Scope**: Per-document-package scope; typically 50–200 pages, 1–20 agentic rules per agent

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Plan reuses ALCOA/GMP/Checklist agents, rule-engine, and compliance_graph unchanged at the call-site level. No subsystem is replaced — only the internal `doc_dir`→`doc_id` parameter refactor and summarization wiring.
- [x] **II. 5-stage soft gates + parallel compliance**: Phase 1.5 page summarization slots into "Structured Extraction & Summarisation" (after segmentation, before agents). ALCOA and GMP remain parallel; Checklist runs after. No new HITL introduced.
- [x] **III. Capability-first**: `compliance/summarizer.py` is a standalone capability (independently callable). `ContextToolbox` is an independently callable context-access capability.
- [x] **IV. Single final checkpoint & selective re-run**: Agentic results produce `RuleEvaluation` with `rule_id`; they join the standard merge flow. No new HITL checkpoints.
- [x] **V. Evidence-bound findings**: Each `RuleEvaluation` carries `rule_id`, `status`, `confidence`, `reasoning`, `evidence` (with page citations from workers). Synthesis receives only structured worker outputs.
- [x] **VI. Configurable framework**: `agentic_page_cap`, `agentic_worker_page_limit`, `agentic_max_tool_calls` remain in `ComplianceConfig`. Summary batch size (10) is a constant in `summarizer.py` overridable as a parameter.
- [x] **VII. Existing framework is the backbone**: `compliance_graph.py` agent call-sites are unchanged. Only Phase 1.5 block is added. All existing single-document pipeline modes continue to work; `enable_cross_page=false` skips summarization.
- [x] **VIII. ALCOA+ audit trail**: Agentic findings carry `rule_id`, `generated_at` on summaries. No reviewer resolutions in this feature.
- [x] **IX. Rule-as-data**: `agentic_audit` is an `evaluation_strategy` value in existing rule YAML; `context_sources` and `applicable_document_types` are declarative fields. No new Python conditionals encode client-specific compliance behaviour.

No violations. Complexity Tracking section not required.

---

## Project Structure

### Documentation (this feature)

```text
specs/009-agentic-audit/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit-tasks)
```

### Source Code (affected files only)

```text
backend/app/compliance/
├── summarizer.py                     CREATE — load_summary / store_summary / summarize_pages_in_batches
├── agentic/
│   ├── graph.py                      REFACTOR — remove gather_context, add fan_out_workers router, doc_dir→doc_id in state
│   ├── postpass.py                   REFACTOR — doc_dir:Path → doc_id:str, remove SummaryCapability()
│   ├── toolbox.py                    REFACTOR — remove pre_generate_summaries, doc_dir→doc_id
│   └── summarizer.py                 DELETE
├── alcoa.py                          REFACTOR — remove doc_dir param from review_document, pass doc_id to postpass
├── gmp.py                            REFACTOR — same as alcoa.py
└── checklist.py                      REFACTOR — same as alcoa.py

backend/app/workflow/
└── compliance_graph.py               ADD — Phase 1.5 page summarization block (after segmentation)

backend/app/compliance/rules/
└── summary_profiles.yaml             DELETE

backend/scripts/
└── run_checklist_agentic_postpass.py PATCH — remove segmentation= kwarg from postpass call

backend/tests/compliance/
└── test_agentic_audit.py             UPDATE — doc_id fixtures, updated imports, fan_out_workers tests
```

---

## Implementation Steps

### Step 1 — Create `compliance/summarizer.py`

New module replacing `agentic/summarizer.py`. Exposes three public functions:

```python
def load_summary(doc_id: str, document_type: str, section_type: str | None) -> str | None
def store_page_summary(doc_id: str, page_num: int, document_type: str, section_type: str | None, text: str) -> None
async def summarize_pages_in_batches(
    extractions: list[dict],
    section_map: dict[int, dict],
    doc_id: str,
    llm: LLMProvider,
    batch_size: int = 10,
) -> None
```

**Storage**: single file `{base_path}/{doc_id}/summaries/page_summaries.json` — a dict keyed by page_num (string). All page summaries for a package live in this one file.

Private path helper:
```python
def _summaries_file(doc_id: str) -> Path:
    return Path(get_settings().storage.base_path) / doc_id / "summaries" / "page_summaries.json"
```

`load_summary(doc_id, doc_type, sec_type)`:
- Load `page_summaries.json` (return `None` if file absent)
- Filter entries where `entry["doc_type"] == doc_type AND entry["section_type"] == sec_type`
- Sort by page_num, join texts with `"\n\n"`, return; `None` if no matches

`store_page_summary(doc_id, page_num, doc_type, sec_type, text)`:
- Read existing file (or `{}`)
- Set `data[str(page_num)] = {"text": text, "doc_type": doc_type, "section_type": sec_type, "generated_at": ...}`
- Write back atomically (create `summaries/` dir if absent)

`summarize_pages_in_batches`:
- Load existing `page_summaries.json` once
- Skip pages whose `str(page_num)` key already exists
- For remaining pages: generate via LLM with `_PAGE_SUMMARY_SYSTEM` prompt, batches of `batch_size`, all batches dispatched via `asyncio.gather`
- Call `store_page_summary` for each generated result
- `section_type` per page comes from `section_map.get(page_num, {}).get("section_type")`

Fixed system prompt (verbatim from current `agentic/summarizer.py`):
```
"Summarize this pharmaceutical document page in 3-5 sentences. Focus on:
- Which section/form this page belongs to
- Key data fields (material names, quantities, dates, operator names)
Be concise. Preserve specific values. This summary is consumed by a
compliance audit agent — accuracy over brevity."
```

No profile logic. No `SummaryCapability` class. No `summary_profiles.yaml` dependency.

---

### Step 2 — Refactor `agentic/toolbox.py`

Changes:
- Remove `summarizer: SummaryCapability` and `doc_dir: Path` parameters from `__init__`
- Add `doc_id: str` parameter
- Remove `pre_generate_summaries` method entirely
- `get_context_summary`: load from disk via `load_summary(self._doc_id, document_type, section_type)` (imported from `compliance.summarizer`); cache in `self._summary_cache`; return `""` if not found (worker falls back to raw pages)

```python
class ContextToolbox:
    def __init__(
        self,
        all_extractions: list[dict],
        section_map: dict[int, dict],
        doc_id: str,
        page_cap: int = 50,
    ) -> None:
        self._all_extractions = all_extractions
        self._section_map = section_map
        self._doc_id = doc_id
        self._page_cap = page_cap
        self._summary_cache: dict[tuple[str, str | None], str] = {}
    
    def get_context_summary(self, document_type: str, section_type: str | None = None) -> str:
        key = (document_type, section_type)
        if key not in self._summary_cache:
            self._summary_cache[key] = load_summary(self._doc_id, document_type, section_type) or ""
        return self._summary_cache[key]
```

---

### Step 3 — Refactor `agentic/graph.py`

**`AgenticAuditState` changes**:
- Remove: `summarizer: SummaryCapability`, `doc_dir: Path`
- Add: `doc_id: str`
- All other fields unchanged

**Replace `gather_context` node with `fan_out_workers` routing function**:

The current design has `gather_context` (regular node) + `route_to_workers` (conditional edges function). Collapse both into a single routing function `fan_out_workers`:

```python
def fan_out_workers(state: AgenticAuditState) -> list[Send]:
    rule = state["rule"]
    toolbox = ContextToolbox(
        state["all_extractions"],
        state["section_map"],
        state["doc_id"],
        state["page_cap"],
    )
    
    # Section chunking (extracted from gather_context)
    groups = _group_by_section(state["all_extractions"], state["section_map"], rule)
    chunks = _chunk_sections(groups, state["worker_page_limit"])
    
    if not chunks:
        return [Send("synthesize", state)]
    return [
        Send("section_worker", {**state, "current_chunk": chunk, "toolbox": toolbox})
        for chunk in chunks
    ]
```

Extract the grouping/chunking logic into private helpers `_group_by_section` and `_chunk_sections` (pulled from current `gather_context` body).

**Graph wiring**:
```python
builder = StateGraph(AgenticAuditState)
builder.add_node("section_worker", section_worker)
builder.add_node("synthesize", synthesize)
builder.add_conditional_edges(START, fan_out_workers, ["section_worker", "synthesize"])
builder.add_edge("section_worker", "synthesize")
builder.add_edge("synthesize", END)
```

Remove `gather_context` import from `__init__.py` if exported. Remove `SummaryCapability` import.

---

### Step 4 — Refactor `agentic/postpass.py`

Signature change:
```python
async def run_agentic_postpass(
    agent_name: str,
    registry: RuleRegistry,
    extractions: list[dict],
    section_map: dict[int, dict],
    llm: LLMProvider,
    config: ComplianceConfig,
    doc_id: str,                    # replaces doc_dir: Path
    progress_callback=None,
) -> list[tuple[str, int | None, RuleBatchResult]]:
```

State construction change:
```python
initial_state = AgenticAuditState(
    rule=rule,
    all_extractions=extractions,
    section_map=section_map,
    llm=llm,
    doc_id=doc_id,                 # replaces doc_dir=doc_dir, summarizer=summarizer
    page_cap=config.agentic_page_cap,
    worker_page_limit=config.agentic_worker_page_limit,
    max_concurrent=config.max_concurrent_batches,
    max_tool_calls=config.agentic_max_tool_calls,
    toolbox=None,
    section_chunks=[],
    worker_results=[],
    final_evaluation=None,
    current_chunk=None,
)
```

Remove `SummaryCapability()` instantiation entirely.

---

### Step 5 — Refactor `alcoa.py`, `gmp.py`, `checklist.py`

For each agent:

1. Remove `doc_dir: Path | None = None` from `review_document()` signature (keep `doc_id: str | None = None`)
2. Change postpass call:
   ```python
   # Before:
   doc_dir=doc_dir or Path("."),
   # After:
   doc_id=doc_id or "",
   ```
3. Remove `from pathlib import Path` if no longer used in the file

---

### Step 6 — Add Phase 1.5 summarization to `compliance_graph.py`

Insert after `section_map = build_page_to_section(segmentation)` within the `if config.enable_cross_page:` block:

```python
# Phase 1.5b: Page summarization (load-then-generate-then-store)
from app.compliance.summarizer import summarize_pages_in_batches

await _ws_progress(doc_id, {
    "phase": "summarization",
    "status": "running",
    "label": f"Generating page summaries ({len(extractions)} pages)...",
})
summ_llm = container.compliance_cross_page_llm
await summarize_pages_in_batches(extractions, section_map, doc_id, summ_llm)
await _ws_progress(doc_id, {
    "phase": "summarization",
    "status": "complete",
    "label": "Page summaries ready",
})
```

`compliance_graph.py` agent call-sites remain unchanged — they already pass `doc_id` to `review_document()`.

---

### Step 7 — Delete `agentic/summarizer.py` and `summary_profiles.yaml`

- Delete `backend/app/compliance/agentic/summarizer.py`
- Delete `backend/app/compliance/rules/summary_profiles.yaml`
- Remove any imports of `SummaryCapability` from remaining files

---

### Step 8 — Fix `scripts/run_checklist_agentic_postpass.py`

The script already passes `doc_id=doc_id` but also passes `segmentation=seg` which is not a parameter of `run_agentic_postpass`. Remove the stale kwarg:

```python
# Before:
results = await run_agentic_postpass(
    AGENT_NAME, registry, extractions, section_map, llm,
    settings.compliance,
    doc_id=doc_id,
    segmentation=seg,          # ← remove this
    progress_callback=None,
)

# After:
results = await run_agentic_postpass(
    AGENT_NAME, registry, extractions, section_map, llm,
    settings.compliance,
    doc_id=doc_id,
    progress_callback=None,
)
```

Note: For the standalone script, summaries may not exist on disk (no compliance_graph Phase 1.5 ran). Workers will fall back to raw pages — correct per FR-016.

---

### Step 9 — Update `tests/compliance/test_agentic_audit.py`

Changes required:
- Replace `from app.compliance.agentic.summarizer import SummaryCapability` with `from app.compliance.summarizer import load_summary, store_summary`
- Remove `gather_context` import; add `fan_out_workers` import
- `ContextToolbox` fixtures: replace `doc_dir=tmp_path` with `doc_id="test-doc"`
- `AgenticAuditState` fixtures: replace `doc_dir=Path("."), summarizer=SummaryCapability()` with `doc_id="test-doc"`
- `run_agentic_postpass` call in tests: replace `doc_dir=Path(".")` with `doc_id=""`
- Replace `gather_context` node tests with `fan_out_workers` routing tests
- Update `store_summary` / `load_summary` tests to use `doc_id` param with `tmp_path` monkeypatched into `get_settings().storage.base_path`

---

## Complexity Tracking

No Constitution violations. Section not required.
