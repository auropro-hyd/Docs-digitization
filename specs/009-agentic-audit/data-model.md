# Data Model: Agentic Audit Evaluation Strategy

**Feature**: 009-agentic-audit  
**Date**: 2026-04-30

---

## Updated: `AuditRule` (registry.py)

```python
@dataclass
class AuditRule:
    # ... existing fields unchanged ...
    scope: str = "page"             # "page" | "document" | "section" | "package" ← new value
    context_sources: list[dict] = field(default_factory=list)  # ← NEW
    # Each entry: {"document_type": str, "section_types": list[str]}
```

YAML representation:
```yaml
context_sources:
  - document_type: raw_material_request
    section_types: []            # [] = all sections
  - document_type: analysis_report
    section_types: [qc_results]
```

---

## New: `AgenticAuditState` (agentic/graph.py)

LangGraph `TypedDict` state threaded through all graph nodes. Updated 2026-05-01: `doc_dir` and `summarizer` removed; `doc_id` added; `section_chunks` removed (chunking happens inside `fan_out_workers` router, not stored in state).

```python
class AgenticAuditState(TypedDict):
    rule: AuditRule
    all_extractions: list[dict]
    section_map: dict[int, dict]
    llm: LLMProvider
    doc_id: str                     # replaces doc_dir: Path — path derived internally
    page_cap: int
    worker_page_limit: int
    max_concurrent: int
    max_tool_calls: int
    # Injected per-worker by fan_out_workers via Send:
    toolbox: ContextToolbox | None
    current_chunk: SectionChunk | None
    # Accumulated across workers:
    worker_results: Annotated[list[WorkerResult], operator.add]   # reducer
    final_evaluation: RuleEvaluation | None
```

---

## New: `SectionChunk` (agentic/graph.py)

Represents one unit of primary document pages dispatched to a section worker.

```python
class SectionChunk(TypedDict):
    document_type: str
    section_type: str | None
    pages: list[dict]          # extractions for this chunk
    chunk_id: str              # "{section_type}-{index}"
```

---

## New: `WorkerAction` (agentic/graph.py)

LLM output schema for each turn of the tool-calling loop inside `section_worker`.

```python
class WorkerAction(BaseModel):
    action: Literal["get_context_summary", "get_context_pages", "produce_verdict"]
    document_type: str = ""
    section_type: str | None = None
    page_nums: list[int] = Field(default_factory=list)
    verdict: WorkerVerdict | None = None
```

---

## New: `WorkerVerdict` (agentic/graph.py)

Structured verdict produced by the LLM when it chooses `produce_verdict`.

```python
class WorkerVerdict(BaseModel):
    status: str             # compliant | non_compliant | uncertain | not_applicable
    confidence: float       # 0.0–1.0
    reasoning: str          # 1-3 sentences, references specific pages/values
    evidence: str           # citation list: "[raw_material_request / all / p3]: quantity X..."
```

---

## New: `WorkerResult` (agentic/graph.py)

Result accumulated by the `Annotated[list[WorkerResult], operator.add]` reducer in `AgenticAuditState`. Derived from `WorkerVerdict` + chunk metadata.

```python
class WorkerResult(TypedDict):
    chunk_id: str
    status: str
    confidence: float
    reasoning: str
    evidence: str
    page_range: str            # e.g. "pp. 5-12"
    section_type: str | None
```

---

## New: `SynthesisOutput` (agentic/graph.py)

LLM output schema for the `synthesize` node. Converted to `RuleEvaluation` by the node.

```python
class SynthesisOutput(BaseModel):
    status: str             # compliant | non_compliant | uncertain | not_applicable
    confidence: float
    reasoning: str          # 2-4 sentences synthesising across workers
    evidence: str           # consolidated citation list from all workers
```

---

## Existing (unchanged): `RuleEvaluation` (models.py)

All agentic results are ultimately returned as `RuleEvaluation` objects — the same schema used by all other evaluators. No new top-level model entity.

```python
class RuleEvaluation(BaseModel):
    rule_id: str
    status: str             # compliant | non_compliant | uncertain | not_applicable
    severity: str | None
    confidence: float
    reasoning: str
    evidence: str
    applicability_trace: list[str]   # includes "agentic_no_context_fallback_to_text" etc.
```

---

## Existing (unchanged): `RuleBatchResult` (models.py)

Wraps the single `RuleEvaluation` returned by the agentic graph.

```python
class RuleBatchResult(BaseModel):
    evaluations: list[RuleEvaluation]
    cross_references: list[CrossReference]   # empty for agentic results
```

---

## Config additions: `ComplianceConfig` (settings.py)

Two new optional fields with safe defaults — existing deployments unaffected:

```python
agentic_page_cap: int = 50          # max context pages per source per rule
agentic_worker_page_limit: int = 12  # pages per section before splitting into 2 workers
```

---

## Summary storage (compliance/summarizer.py) — REVISED 2026-05-01

`agentic/summarizer.py` is **deleted**. Summary helpers move to `compliance/summarizer.py`. Generation happens in `compliance_graph.py` Phase 1.5, gated on `enable_cross_page`.

**Storage**: one file per document package  
**Path**: `{base_path}/{doc_id}/summaries/page_summaries.json`  
(Co-located with `segmentation.json`; path derived internally from `doc_id` via `get_settings()`)

**JSON shape** (dict keyed by page_num as string):
```json
{
  "1": {
    "text": "<3-5 sentence summary of page 1>",
    "doc_type": "batch_record",
    "section_type": "cover_page",
    "generated_at": "2026-05-01T12:00:00Z"
  },
  "7": {
    "text": "<summary of page 7>",
    "doc_type": "batch_record",
    "section_type": "manufacturing_operations",
    "generated_at": "2026-05-01T12:00:00Z"
  }
}
```

**Public API** (`compliance/summarizer.py`):
- `load_summary(doc_id: str, doc_type: str, sec_type: str | None) -> str | None` — loads `page_summaries.json`, filters entries matching `(doc_type, sec_type)`, returns matching texts joined in page-number order; `None` if no matches
- `store_page_summary(doc_id: str, page_num: int, doc_type: str, sec_type: str | None, text: str) -> None` — read-merge-write into `page_summaries.json`; creates file if absent
- `summarize_pages_in_batches(extractions, section_map, doc_id, llm, batch_size=10) -> None` — loads existing `page_summaries.json`, skips pages already present, generates missing pages in batches of 10 via `asyncio.gather`, calls `store_page_summary` per generated result

---

## Summary of schema purity

| Entity | Change |
|--------|--------|
| `AuditRule` | +2 fields: `context_sources`, (scope value `"package"`) |
| `ComplianceConfig` | +2 fields: `agentic_page_cap`, `agentic_worker_page_limit` |
| `AgenticAuditState` | NEW — `summarizer` and `doc_dir` removed; `doc_id: str` added; `section_chunks` removed |
| `SectionChunk` | NEW — internal to `agentic/graph.py` only |
| `WorkerAction` | NEW — internal to `agentic/graph.py` only |
| `WorkerVerdict` | NEW — internal to `agentic/graph.py` only |
| `WorkerResult` | NEW — internal to `agentic/graph.py` only |
| `SynthesisOutput` | NEW — internal to `agentic/graph.py` only |
| `RuleEvaluation` | Unchanged — agentic results use same schema |
| `RuleBatchResult` | Unchanged |
| `AgentReport` | Unchanged |
| `ComplianceFinding` | Unchanged |
| `SummaryCapability` | DELETED — replaced by module-level functions in `compliance/summarizer.py` |
| `summary_profiles.yaml` | DELETED — no profile system; plain always-on page summarization |

---

## Model Changes

All changes are **extensions to existing models**. No existing fields are removed or renamed. No net-new top-level model classes are introduced in `models.py`. All new Pydantic/TypedDict models live inside `backend/app/compliance/agentic/graph.py`.

---

### `AuditRule` (extended) — `registry.py`

**Existing fields** (unchanged):
- `evaluation_strategy: str` — valid values: `"text"`, `"vision"`, `"text_and_vision"`, `"text_primary"`, `"llm_arbitrated"` ← extended below
- `scope: str` — valid values: `"page"`, `"document"` ← extended below

**New fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `context_sources` | `list[dict]` | `[]` | Context sources for agentic evaluation. Each dict: `{"document_type": str, "section_types": list[str]}`. |

**Extended valid values**:

| Field | Added Value | Meaning |
|-------|-------------|---------|
| `evaluation_strategy` | `"agentic_audit"` | Rule is evaluated by the agentic LangGraph flow, not the page-level LLM batch. |
| `scope` | `"package"` | Rule fires once per document package. Excluded from `get_batches(scope_filter="page")`. |

---

### YAML Rule Entry (agentic audit pattern)

```yaml
# checklist_rules.yaml  (or any agent rules file)
categories:
  package_completeness:
    rules:
      1:
        evaluation_strategy: agentic_audit
        scope: package
        applicable_document_types: [batch_record]
        applicable_section_types: []
        context_sources:
          - document_type: batch_record
            section_types: [cover_page, batch_summary]
          - document_type: raw_material_request
            section_types: []
        pass_criteria: >
          The document package must include: a batch record with a completed
          cover page and batch summary, and at least one raw material request
          document. Verify that each required item is present by examining
          the provided context. If any item is absent, the rule is non-compliant.
          List all present items and all missing items in the evidence field.
```

---

### `summary_profiles.yaml` (new config file)

Not a Python model — pure YAML config loaded by `SummaryCapability`.

```yaml
enabled: true

profiles:
  <document_type>:           # must match document_profiles.yaml canonical values
    doc_level: bool          # generate a document-level summary for this doc type
    section_types: list[str] # which sections get page-level summaries; [] = all
```

---

### Data Flow for Agentic Evaluation (REVISED 2026-05-01)

```
compliance_graph.py (Phase 1.5 — before agents run)
  └── summarize_pages_in_batches(extractions, section_map, doc_id, llm)
        └── stores {doc_id}/summaries/{doc_type}__{sec_type}.json per section

ChecklistAgent.review_document(extractions, ..., doc_id=doc_id)
  │
  ├── [existing] run_agent_evaluation(batches, scope=page)
  │     └── per-page RuleEvaluation objects → results[]
  │
  └── [new] run_agentic_postpass(doc_id=doc_id, ...)
        │
        ├── graph.ainvoke(AgenticAuditState) per agentic rule
        │     │
        │     ├── fan_out_workers(state) → list[Send]   [routing function, not a node]
        │     │     ├── build ContextToolbox(doc_id=doc_id)
        │     │     ├── group extractions by (doc_type, sec_type) filtered by rule scope
        │     │     └── chunk sections exceeding worker_page_limit into 2 workers
        │     │
        │     ├── Send("section_worker", {state + chunk + toolbox}) × N  [parallel]
        │     │     └── tool-calling loop (WorkerAction):
        │     │           toolbox.get_context_summary() → load_summary(doc_id, ...) from disk
        │     │           toolbox.get_context_pages() → raw page text
        │     │           → WorkerResult
        │     │
        │     └── synthesize node:
        │           └── SynthesisOutput → RuleEvaluation
        │
        └── assemble_agent_report(all_rules, results + agentic_results, pages)
              └── AgentReport (unchanged shape)
```

---

### `ComplianceConfig` (extended) — `settings.py`

**New fields**:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `agentic_page_cap` | `int` | `50` | Max context pages gathered per context source per agentic rule. |
| `agentic_worker_page_limit` | `int` | `12` | Pages per section before splitting into two parallel workers. |

No other settings changes.
