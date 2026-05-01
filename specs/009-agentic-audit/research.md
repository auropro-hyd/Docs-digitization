# Research: Agentic Audit Evaluation Strategy

**Feature**: 009-agentic-audit | **Date**: 2026-04-30

---

## Decision 1: LangGraph as the agentic orchestration runtime

**Decision**: The agentic evaluator is implemented as a LangGraph `StateGraph` (`graph.py`) with four nodes: `gather_context`, `section_worker` (dispatched in parallel via `Send`), and `synthesize`. `run_agentic_postpass()` in `postpass.py` is the adapter bridging the existing agent calling convention to `graph.ainvoke()`.

**Rationale**: The constitution explicitly requires "LangGraph is the only orchestration runtime — no ad-hoc coroutine chains outside a declared graph." Raw `asyncio.gather` inside `review_document()` would violate this. The LangGraph `Send` API is the idiomatic pattern for parallel fan-out + result accumulation within a declared graph. Encapsulating the graph inside `postpass.py` means no agent class or `compliance_graph.py` needs to import LangGraph directly.

**Alternatives considered**:
- Raw `asyncio.gather` inside `review_document()` → constitution violation. Rejected.
- New LangGraph node in `compliance_graph.py` → requires changing the graph topology and compliance_graph.py. Rejected — scope expansion.

---

## Decision 2: Tool-calling strategy — structured multi-turn loop

**Decision**: Each `section_worker` node runs a bounded Python while loop calling `generate_structured(prompt, WorkerAction)` on each turn. `WorkerAction` is a discriminated Pydantic model with `action: Literal["get_context_summary", "get_context_pages", "produce_verdict"]`.

**Rationale**: `LLMProvider` exposes only `generate()` and `generate_structured()`. Adding `generate_with_tools()` would require all concrete adapters to be updated, introducing regression risk. The structured multi-turn approach achieves the same logical behaviour as native tool use with zero port changes. Budget is bounded: max tool calls = `max_concurrent_batches` (reused config field), after which a final `generate_structured(prompt, WorkerVerdict)` forces a verdict.

**Alternatives considered**:
- Native Anthropic tool use → requires port extension + all adapter updates. Rejected.
- Free-text regex parsing for tool calls → fragile. Rejected.

---

## Decision 3: Parallel fan-out via LangGraph Send API

**Decision**: After `gather_context` identifies section chunks, `route_to_workers()` returns a list of `Send("section_worker", {...})` objects — one per chunk. LangGraph dispatches them in parallel. Results accumulate in `AgenticAuditState.worker_results` via an `operator.add` reducer.

**Rationale**: `Send` is the idiomatic LangGraph mechanism for dynamic parallel fan-out. The reducer pattern (`Annotated[list[WorkerResult], operator.add]`) collects partial results as each worker completes, then `synthesize` fires once all workers are done. No manual `asyncio.gather` or result tracking needed.

---

## Decision 4: Section chunk splitting — bisect at midpoint

**Decision**: Bisect at midpoint when `len(section_pages) > worker_page_limit` (default: 12).

```python
mid = len(pages) // 2
chunks = [pages[:mid], pages[mid:]]
```

**Rationale**: Simple bisection keeps both chunks ≈ equal. For a 13-page section: 6 + 7 pages. For rare 24+ page sections, the summary tier absorbs load (workers call `get_context_summary` first). Exactly two workers per oversized section — consistent with spec.

**Alternatives considered**: Fixed-size windows → unequal final window. Rejected.

---

## Decision 5: Summary generation — stored to disk and reused (segmentation pattern)

**Decision**: `gather_context` calls `toolbox.pre_generate_summaries(rule.context_sources, doc_dir)` before emitting `Send` objects. For each `(doc_type, section_type)` pair, the toolbox calls `load_summary(doc_dir, doc_type, section_type)` first; only generates via LLM if no stored summary exists, then calls `store_summary(doc_dir, ...)` before caching in memory. Workers hit the in-memory cache synchronously — no LLM calls during the worker loop.

**Rationale**: Follows the exact segmentation pattern (`load_segmentation` → generate if None → `store_segmentation`). Summaries for a given document package are computed once and reused across re-runs, re-evaluations, or multiple agentic rules targeting the same context source. Avoids redundant LLM calls across runs — the main cost driver for large packages. Same failure semantics as segmentation: if load fails, regenerate; if store fails, log and continue with the in-memory version.

**Storage format**: `doc_dir / "summaries" / f"{doc_type}__{section_type or 'all'}.json"` — plain JSON with keys `text`, `doc_type`, `section_type`, `generated_at`. Path mirrors `segmentation.json` location — same `doc_dir` root.

**Alternatives considered**:
- In-memory only (original plan) → redundant LLM calls on re-runs. Rejected after user clarification.
- Database-backed cache → over-engineering; `doc_dir` is already the canonical artifact location. Rejected.

---

## Decision 6: Worker conversation accumulation — concatenated string

**Decision**: Tool results are appended to the conversation as a single growing string between `generate_structured` calls.

**Rationale**: `generate_structured()` takes `prompt: str`. A growing string is human-readable, debuggable, and provider-agnostic. Budget stays safe: `max_concurrent_batches` tool calls × (≤500-char summaries or ≤2000-char raw pages) + initial section content.

---

## Decision 7: Synthesis input — structured worker outputs only

**Decision**: The `synthesize` node receives only `worker_results` (status, confidence, reasoning, evidence citations per chunk) — no raw page content, no fetched summaries.

**Rationale**: Keeps synthesis token budget small and predictable regardless of document size. Synthesis is aggregation, not re-evaluation. Safety rail: if synthesizer output contradicts all workers (e.g., returns `compliant` when all workers are `non_compliant`), the caller falls back to the worst-status among workers.

---

## Decision 8: Worker concurrency cap — reuse `max_concurrent_batches`

**Decision**: `run_agentic_postpass()` wraps each rule invocation with `asyncio.Semaphore(config.max_concurrent_batches)`. No new config field.

**Rationale**: `max_concurrent_batches` already governs LLM call concurrency across agents. Reusing it for agentic workers keeps config surface minimal and consistent.

---

## Decision 9: Validation error for bad agentic rules — skip + log

**Decision**: If a rule has `evaluation_strategy: agentic_audit` but empty `applicable_document_types`, `_finalise_rule()` returns `None` and emits `logger.error()`. The registry filters `None` from the rules list. No exception raised.

**Rationale**: One misconfigured rule must not halt the entire registry or take down other agents in a production system.

---

## Decision 10: `doc_dir` elimination — derive from `doc_id` internally

**Decision**: Remove `doc_dir: Path` as an explicit parameter from `run_agentic_postpass()`, `AgentToolbox.__init__`, and `AgenticAuditState`. Replace with `doc_id: str` throughout. Path derivation (`Path(get_settings().storage.base_path) / doc_id`) is encapsulated inside `compliance/summarizer.py`'s private `_summary_path()`. Callers never compute or pass the path.

**Rationale**: All document types in a package share one `doc_id`. The path expression is a one-liner already used in `compliance_graph.py:86` and `compliance_graph.py:611`. Centralising it in `compliance/summarizer.py` gives a single authoritative location and keeps the agentic components free of path concerns. The script already passes `doc_id`; agents already receive it — propagating `doc_id` is zero extra work.

**Root cause of current bug**: Agents accept `doc_dir: Path | None` in `review_document()` but `compliance_graph.py` never passes it (see line 296–303). The fallback `doc_dir or Path(".")` silently stores summaries in the process working directory instead of the document folder.

**Alternatives considered**: Introduce a `document_storage_dir(doc_id)` module-level utility (the script imports one already). Rejected — adds an import just to wrap a one-liner; hiding it inside `summarizer._summary_path` is cleaner.

---

## Decision 11: `fan_out_workers` as conditional edges routing function, replacing `gather_context` node

**Decision**: The `gather_context` regular node is deleted. Its two responsibilities split: (a) summary pre-generation moves to `compliance_graph.py` Phase 1.5; (b) toolbox construction + section chunking moves into a routing function `fan_out_workers` registered via `add_conditional_edges(START, fan_out_workers, [...])`. The graph becomes 3 execution nodes: `fan_out_workers` (router), `section_worker`, `synthesize`.

**Rationale**: With summaries pre-generated before the graph runs, `gather_context` only builds the toolbox and chunks sections — logic that naturally belongs in the router that decides how to fan out. Keeping it as a separate node would require the toolbox to be passed through `AgenticAuditState` before the routing decision. Collapsing it into the router lets `fan_out_workers` inject the toolbox directly into each `Send` call's state slice.

**LangGraph routing function pattern**:
```python
builder.add_conditional_edges(START, fan_out_workers, ["section_worker", "synthesize"])
```
This fires `fan_out_workers(state)` which returns `list[Send]` — standard LangGraph Send-based fan-out.

---

## Decision 12: `compliance/summarizer.py` — single `page_summaries.json` file per package

**Decision**: New `compliance/summarizer.py` exports module-level functions (not a class). All page summaries for a package are stored in ONE file: `{base}/{doc_id}/summaries/page_summaries.json` — a dict keyed by `str(page_num)`. `load_summary(doc_id, doc_type, sec_type)` filters this dict at read time. `store_page_summary` does a read-merge-write per page. `summarize_pages_in_batches` loads the file once, skips already-present pages, generates missing ones in parallel batches of 10.

**Storage path**: `{base_path}/{doc_id}/summaries/page_summaries.json` (one file per package)

**Rationale**: One file per package mirrors `segmentation.json` — a single authoritative artifact. File-per-page (alternative) would create hundreds of small files and require directory scans. File-per-section would require aggregation logic at write time (can't store partial section). Single file with page_num key supports incremental updates cleanly: skip pages already present, merge new entries, write back.

---

## Existing Infrastructure Reused

| Component | Reused As-Is | Extended |
|-----------|-------------|----------|
| `AuditRule` dataclass | — | +`context_sources`, scope value `"package"` |
| `RuleRegistry.get_batches(scope_filter=)` | Fully reused | — |
| `run_agent_evaluation()` | Fully reused | — |
| `assemble_agent_report()` | Fully reused | — |
| `section_map` + `extractions` threading | Fully reused | — |
| `RuleEvaluation` / `RuleBatchResult` / `AgentReport` | Fully reused | — |
| `document_profiles.yaml` taxonomy | Canonical reference for summary_profiles.yaml | — |
| `max_concurrent_batches` config | Reused as worker concurrency cap | — |
| `LangGraph StateGraph` | Pattern from `bmr/workflow/graph.py` | +Send API for parallel fan-out |
