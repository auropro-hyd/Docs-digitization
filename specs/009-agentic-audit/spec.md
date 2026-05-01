# Feature Specification: Agentic Audit Evaluation Strategy

**Feature Branch**: `009-agentic-audit`  
**Created**: 2026-04-30  
**Status**: Draft  

## Overview

The compliance audit system currently evaluates rules against individual pages using text and/or vision channels. This feature introduces two related capabilities:

1. **Agentic Audit Strategy** — a new evaluation strategy that enables rules to reason across multiple documents and sections, not just a single page. Rules configured with this strategy instruct an autonomous agent to gather context from related documents and sections, synthesize evidence, and produce a consolidated compliance verdict.

2. **Configurable Summary Generation** — a supporting capability that generates summaries at both the page level and document level. Summaries are configurable by document type and section type (aligned with the existing segmentation logic) and serve as the primary context tier for agentic evaluation before raw page samples are consulted.

---

## User Scenarios & Testing *(mandatory)*

> These scenarios represent test patterns for the key behaviours. Additional scenarios will exist in practice; these cover the primary cases required to validate the feature.

### User Story 1 — Document Package Completeness Check (Priority: P1)

A compliance reviewer wants to verify that a document package contains all required document types (e.g., five checklists, three test reports, an IPC report, a certificate of analysis, and batch closure records). Today, this check cannot be automated because no single page contains all the evidence — the system must look across the entire package.

**Why this priority**: This is the foundational use case. Package completeness is a go/no-go gate in pharmaceutical manufacturing release workflows. Automating it removes significant manual review effort.

**Independent Test**: Configure a single agentic audit rule with a package completeness instruction and a list of required document types in context. Submit a document package — half complete, half missing items. Verify the rule reports non-compliant with an evidence summary listing exactly which document types are present and which are absent.

**Acceptance Scenarios**:

1. **Given** a rule with `evaluation_strategy: agentic_audit` and a list of required document types in its context configuration, **When** the agent evaluates a complete document package, **Then** the rule status is `compliant` and evidence lists all required document types found.
2. **Given** the same rule, **When** one or more required document types are absent from the package, **Then** the rule status is `non_compliant` and evidence explicitly names the missing document types.
3. **Given** a rule with no `applicable_section_types` specified, **When** evaluated, **Then** the rule applies across all sections of the configured applicable document types.

---

### User Story 2 — Cross-Section Data Integrity Verification (Priority: P2)

A compliance reviewer wants to verify that every raw material mentioned in a manufacturing operations section has a corresponding entry in both the material dispensing section and the raw material request document. This requires joining data across three distinct sections/documents.

**Why this priority**: Cross-section traceability is a core ALCOA+ requirement. Manual verification is time-consuming and error-prone across large batch records.

**Independent Test**: Configure a rule scoped to `manufacturing_operations` sections of `batch_record` documents, with context pointing to the `material_dispensing` section of the same batch record and all sections of the `raw_material_request` document. Run against a batch record where one raw material is missing a dispensing record. Verify the rule is `non_compliant` with evidence citing the specific raw material and the missing record.

**Acceptance Scenarios**:

1. **Given** a rule scoped to `applicable_section_types: [manufacturing_operations]` and `applicable_document_types: [batch_record]`, **When** the agent finds all raw materials in that section have matching dispensing records and raw material requests, **Then** status is `compliant`.
2. **Given** the same rule, **When** a raw material referenced in manufacturing operations has no dispensing record, **Then** status is `non_compliant` with a finding citing the material name and missing record type.
3. **Given** the same rule, **When** a raw material has a dispensing record but no raw material request, **Then** status is `non_compliant` with evidence identifying both the material and the absent request document.

---

### User Story 3 — Graceful Handling of Unavailable Context Documents (Priority: P3)

A compliance reviewer runs an agentic audit rule where one of the context documents is not present in the package. The system should not crash or silently pass — it should report that context could not be gathered and reflect appropriate uncertainty.

**Why this priority**: Robustness under incomplete data is critical in regulated environments where silent failures are worse than flagged uncertainties.

**Independent Test**: Configure an agentic audit rule whose context references a document type not present in the submission. Verify that the rule returns `uncertain` status with reasoning explaining which context source was unavailable.

**Acceptance Scenarios**:

1. **Given** an agentic audit rule whose context references a missing document type, **When** evaluated, **Then** the rule returns `uncertain` with reasoning naming the unavailable context source and reflecting this in the evidence.
2. **Given** partial context (some context sources present, some missing), **When** evaluated, **Then** the agent reasons from available sources and explicitly flags the missing ones in evidence, returning either `uncertain` or a reasoned verdict depending on whether the available data is sufficient.

---

### Edge Cases

- What happens when a rule has `agentic_audit` strategy but no context sources are configured? (Expected: system falls back to the standard text evaluation strategy internally — no error is surfaced to the reviewer.)
- How does the system handle context documents with zero extractable pages? (Expected: treated as unavailable and reflected in the evidence, not silently ignored.)
- What if context sources match hundreds of pages? (Expected: the agent uses pre-generated summaries as the first tier; raw page samples are retrieved only for items that require thorough investigation, governed by configurable limits.)
- What happens if the same rule fires on multiple pages because multiple pages match the scope? (Expected: results are merged using the existing worst-status-per-rule merge logic.)
- What if an agentic rule has a missing or empty `applicable_document_types`? (Expected: validation error raised at rule load time — the rule is rejected before evaluation begins.)
- What if `applicable_section_types` is empty and `section_map` is unavailable? (Expected: all pages matching `applicable_document_types` are used as primary scope; section boundaries are not required.)

---

## Clarifications

### Session 2026-04-30

- Q: Should the agentic evaluator use LangGraph internally? → A: Yes — each agentic rule invocation runs as a LangGraph flow (`agentic_graph.ainvoke()`). The graph handles: context pre-generation, parallel section fan-out via Send API, per-worker tool-calling loop (conditional edge), and synthesis aggregation. `run_agentic_postpass()` is the adapter bridging the existing agent calling convention to the graph — callers see no LangGraph. This satisfies the constitution constraint ("LangGraph is the only orchestration runtime").
- Q: How should WebSocket progress be reported during the agentic post-pass? → A: Per-rule WS events — one update per agentic rule as it completes, matching the per-batch granularity of the existing page-pass. The `run_agentic_postpass()` utility accepts an optional progress_callback consistent with the existing agent pattern.
- Q: Should there be a concurrency cap on agentic section workers? → A: Reuse the existing `max_concurrent_batches` from `ComplianceConfig` to cap section workers per rule — no new config field needed.
- Q: When the registry loads an agentic rule with missing or empty `applicable_document_types`, what should happen? → A: Skip the offending rule with a `logger.error()` entry; remaining rules load normally. No exception raised — one bad rule must not halt the entire registry or take down other agents.
- Q: When section_map is empty or applicable_section_types is unset, how should the evaluator scope primary document pages? → A: If `applicable_section_types` is empty/missing, include all pages matching `applicable_document_types` (no section filtering). If `applicable_document_types` is missing or empty, raise a validation error at rule load time — document type is mandatory for agentic rules.
- Q: Which agents should wire the agentic post-pass in this feature? → A: All three — ALCOA, GMP, and Checklist. The post-pass logic is extracted as a shared `run_agentic_postpass()` utility function; each agent calls it at the end of `review_document()`. compliance_graph.py requires no changes. SOP agent is deferred.
- Q: What is the parallelization granularity for primary document evaluation? → A: One worker per section; if a section exceeds 12 pages, it splits into two parallel workers (page-chunked). Synthesis aggregates all workers regardless of whether they came from the same section or different sections.
- Q: What is the per-worker page limit before a section splits into two threads? → A: 12 pages (configurable default).
- Q: What does the synthesis LLM call receive as input? → A: Partial verdicts, evidence, reasoning, and rule description with page references only — no raw page content, no fetched summaries. Synthesis call is always lightweight regardless of document size.
- Q: When parallel page evaluations complete, what aggregation strategy produces the final verdict? → A: LLM synthesis pass — collect all partial verdicts + evidence from parallel workers, run one lightweight LLM call to produce a final consolidated verdict and evidence summary; this allows the synthesizer to reason about contradictions and nuance across pages rather than blindly taking worst-status.
- Q: How should the evaluator include primary document content (e.g., BPCR pages) alongside context_sources in the LLM prompt? → A: Parallel fan-out — evaluate applicable primary document pages in parallel (each page or page-group as an independent agent invocation); each parallel worker has access to context sources as tools (`get_context_summary`, `get_context_pages`) and pulls summary first, raw pages only if needed; all parallel results are aggregated in one place for a final consolidated verdict. No single stuffed prompt; latency is bounded by the slowest page evaluation rather than total page count.
- Q: Should summaries be regenerated on every evaluation run or stored and reused? → A: Store and reuse following the segmentation pattern. `gather_context` calls `load_summary(doc_dir, doc_type, section_type)` first; only generates via LLM if absent, then stores with `store_summary(doc_dir, ...)`. Summaries persist under `doc_dir/summaries/` as JSON files, mirroring `segmentation.json` location. This avoids redundant LLM calls across re-runs and multiple agentic rules targeting the same context source.

### Session 2026-05-01

- Q: After summarization moves to compliance_graph.py, what should happen to the `gather_context` node in the agentic LangGraph? → A: Remove it entirely — graph becomes `fan_out_workers → section_worker → synthesize` (3-node graph). Summaries are on disk before the graph runs; the node adds no value.
- Q: Where in compliance_graph.py should page summarization run? → A: After segmentation, before agents start (Phase 1.5, blocking). Each page receives its `section_type` from the freshly-built `section_map` before the summary is generated and stored.
- Q: Should page summarization be always-on or gated by a config flag? → A: Gate on existing `enable_cross_page` — summaries only run when the agentic post-pass can consume them; no new config field needed.
- Q: How should pages be batched for summarization LLM calls? → A: Group pages in batches of 10 and run all batches in parallel (asyncio.gather). Not one page at a time — parallel batch dispatch for throughput.
- Q: What happens to agentic/summarizer.py? → A: Delete it. Extract only `load_summary` and `store_summary` into a new `compliance/summarizer.py`. The toolbox imports only `load_summary` (read-only access). The compliance-graph page summarizer imports both. No other consumers of `store_summary`.
- Q: What should replace User Story 4 (Configurable Summary Generation)? → A: Remove entirely — page summarization is infrastructure (like segmentation), not a user-facing capability. No replacement story needed.
- Q: Is `doc_dir` needed as an explicit parameter in `run_agentic_postpass()`, the toolbox, and the graph state? → A: No — all document types in a package share one `doc_id`; `doc_dir` is always derived internally via `document_storage_dir(doc_id)` (the same function that locates `segmentation.json`). Remove `doc_dir` as an explicit parameter from `run_agentic_postpass()`, `AgentToolbox`, and `AgenticAuditState`. Pass `doc_id: str` instead wherever the storage path is needed. `load_summary` and `store_summary` accept `doc_id` (not `doc_dir`).
- Q: What is the storage layout for page summaries on disk? → A: One file per document package — `{doc_id}/summaries/page_summaries.json` — containing all page summaries as a dict keyed by page_num (string). Each entry: `{"text": str, "doc_type": str, "section_type": str | null, "generated_at": ISO-8601}`. `load_summary(doc_id, doc_type, sec_type)` loads this file, filters entries by `(doc_type, section_type)`, and returns the matching texts joined in page-number order. `store_summary(doc_id, page_num, doc_type, sec_type, text)` merges a single entry into the file (read-merge-write). Pages already present in the file are skipped during `summarize_pages_in_batches` — no regeneration on re-runs.

---

## Requirements *(mandatory)*

### Functional Requirements

**Agentic Audit Strategy**

- **FR-001**: The rule schema MUST support a new `evaluation_strategy` value of `agentic_audit` alongside the existing `text`, `vision`, `text_and_vision`, `text_primary`, and `llm_arbitrated` values.
- **FR-002**: Rules with `evaluation_strategy: agentic_audit` MUST declare a non-empty `applicable_document_types` field — this is mandatory. Absence or empty value MUST cause the rule to be skipped at load time with a `logger.error()` entry; it MUST NOT raise an exception or halt loading of other rules. `applicable_section_types` is optional; if absent or empty, all sections of the declared document types are in scope.
- **FR-003**: When an agentic audit rule is triggered, the system MUST gather context from all sources declared in the rule's `context_sources` field, resolved against the document package available to that evaluation run.
- **FR-004**: The system MUST evaluate applicable primary document pages (matching `applicable_document_types` + `applicable_section_types`) in parallel at section granularity — one worker per section. If a section exceeds the configured per-worker page limit, it MUST be split into two parallel workers (page-chunked). Each worker receives its pages, the rule's `pass_criteria`, and access to context sources as callable tools; it retrieves context summaries first and raw pages only when the summary is insufficient. Concurrent section workers per rule MUST be capped using the existing `max_concurrent_batches` setting from `ComplianceConfig` — no new config field. All parallel worker results MUST be aggregated into a single final compliance verdict (`compliant`, `non_compliant`, `uncertain`, or `not_applicable`) via a synthesis LLM call.
- **FR-005**: A synthesis LLM call MUST aggregate all parallel partial verdicts into a single final verdict. Its input MUST be limited to: the rule description, each worker's verdict, reasoning, evidence, and page references. No raw page content or fetched summaries are passed to the synthesizer — it reasons purely from structured worker outputs.
- **FR-006**: If no pages match any context source, the rule MUST return `uncertain` status with reasoning explaining the missing context; it MUST NOT silently pass.
- **FR-007**: If a rule has `evaluation_strategy: agentic_audit` but no context sources are configured, the system MUST silently fall back to text evaluation for that rule rather than returning an error.
- **FR-008**: The `applicable_document_types` field MUST control which pages are in scope for primary evaluation. If `applicable_section_types` is non-empty, only pages belonging to those section types (as identified via `section_map`) are included; if `applicable_section_types` is empty or absent, all pages matching `applicable_document_types` are included regardless of section. These scoping rules apply independently of the `context_sources` used for evidence gathering.
- **FR-009**: Agentic audit results MUST be merged into the audit report using the same worst-status-per-rule logic already used for text and vision evaluations.
- **FR-010**: Rule YAML files MUST be able to express agentic audit rules without changes to the existing file format structure — only new fields within a rule entry.
- **FR-011**: The ALCOA, GMP, and Checklist agents MUST all support agentic audit rules via a shared `run_agentic_postpass()` utility function called at the end of each agent's `review_document()`. The SOP agent is out of scope for this feature. `compliance_graph.py` MUST NOT require changes — the agentic post-pass is transparent to the graph.
- **FR-019**: The agentic audit evaluator MUST be implemented as a LangGraph flow with three nodes: `fan_out_workers` (parallel section dispatch via Send API), `section_worker` (tool-calling loop with conditional edge), and `synthesize` (partial verdict aggregation). The `gather_context` node is removed — page summaries are pre-generated by `compliance_graph.py` at the segmentation phase and are on disk before the agentic graph runs. The graph MUST be invoked via `run_agentic_postpass(doc_id, ...)` which adapts the existing agent calling convention — no agent class or `compliance_graph.py` needs to import LangGraph directly. `AgenticAuditState` MUST NOT include a `doc_dir` field; the storage path is always resolved internally from `doc_id` via `document_storage_dir()`.
- **FR-018**: `run_agentic_postpass()` MUST accept an optional `progress_callback` with the same signature as the existing agent progress callbacks. It MUST emit one callback invocation per agentic rule as it completes (verdict + rule_id), matching the per-batch granularity of the page-pass. Callers that do not provide a callback receive no WS events for the agentic pass.

**Configurable Summary Generation**

- **FR-012**: The system MUST generate a plain page-level summary for every page immediately after segmentation completes (Phase 1.5 of `compliance_graph.py`), before agent evaluation begins. Each page summary MUST include the page's `section_type` from the freshly-built `section_map`. Pages MUST be grouped into batches of 10 and all batches dispatched in parallel (`asyncio.gather`) — not one page at a time. All page summaries for a document package MUST be persisted in a single file `document_storage_dir(doc_id)/summaries/page_summaries.json`, keyed by page number (string). Pages whose key already exists in the file are skipped on re-runs (load-then-generate-then-store, mirroring the segmentation pattern). `doc_dir` is never passed explicitly — all summarizer functions accept `doc_id: str` and derive the path internally. No summary profiles, section-level rollups, or document-level summaries are generated.
- **FR-013**: Summary generation uses a single fixed system prompt — no per-document-type or per-section-type profiles are needed. The `summary_profiles.yaml` file and `SummaryCapability._profiles` logic are removed.
- **FR-014**: When a context source for an agentic audit rule has a pre-generated summary available, the agent MUST use that summary as its primary input rather than raw page content. Summaries are read via `load_summary(doc_id, doc_type, section_type)` from `compliance/summarizer.py` — this function loads `page_summaries.json`, filters by `(doc_type, section_type)`, and returns the matching page texts joined in page-number order. The agentic toolbox imports only `load_summary` (read-only access). `doc_dir` is not accepted or passed; the path is derived internally from `doc_id`.
- **FR-015**: When the agent determines that a specific data point in the summary requires deeper verification, it MUST be able to retrieve targeted raw page samples for that data point only, up to a configurable per-source page cap (default: 50 pages). The per-worker page limit (default: 12 pages) controls when a section is split into two parallel workers; this limit MUST be tunable without code changes.
- **FR-016**: When no summary is available for a context source (i.e., no matching entries in `page_summaries.json` for the requested `(doc_type, section_type)`), the system MUST fall back to raw page sampling up to the configurable page cap. Summaries are written only by the compliance-graph page summarizer via `store_page_summary(doc_id, page_num, doc_type, sec_type, text)` — no agentic component writes summaries. `doc_dir` is not accepted or passed; the path is derived internally from `doc_id`.
- **FR-017**: Page summarization MUST be gated on the existing `enable_cross_page` config flag — no new config field is introduced. When `enable_cross_page` is false, summarization is skipped entirely.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A compliance reviewer can author an agentic audit rule in a YAML file without writing any code, and the rule executes correctly on the next evaluation run.
- **SC-002**: Package completeness checks that previously required full manual review are completed automatically, with evidence summaries accurate enough that reviewers agree with the verdict in at least 90% of cases.
- **SC-003**: Cross-section traceability rules produce verdicts with specific citations (material name, section name, record type) rather than generic statements, enabling reviewers to act on findings without re-reading source documents.
- **SC-004**: Agentic audit evaluation adds no more than 30 seconds of additional elapsed time per rule per document package under normal operating conditions when summaries are available.
- **SC-005**: When context documents are partially or fully unavailable, the system never silently passes a rule — it always surfaces an `uncertain` or `non_compliant` verdict with an explanation.
- **SC-006**: Page summaries are pre-generated for all pages during the segmentation phase; agentic rules consume summaries as primary context and only retrieve raw pages when the agent explicitly requests deeper verification.

---

## Assumptions

- The full document package (all document types submitted together) shares one `doc_id` and is accessible during a single evaluation run. All artifacts (extractions, `segmentation.json`, summaries) live under `document_storage_dir(doc_id)`. `doc_dir` is never passed explicitly through the agentic call chain — callers pass `doc_id` and each component resolves its own path.
- Each document in the package is already segmented into sections with `document_type` and `section_type` metadata available per page, as delivered by the existing section map infrastructure.
- The `pass_criteria` field on a rule is sufficient to carry agent instructions; no separate `instructions` field is needed.
- Agentic audit rules may be authored in the ALCOA, GMP, or Checklist agent rule YAML files. The SOP agent rule file is out of scope for this feature.
- Existing data model entities in `models.py` are reused and extended where needed; no net-new top-level model entities are introduced.
- The agentic evaluator is a LangGraph flow; LangGraph is already present as a dependency in the project.
- Context page volume per source will be capped at a configurable limit (default: 50 pages); this limit is tunable without code changes.
- Page summaries are generated for all pages during the segmentation phase (Phase 1.5) when `enable_cross_page` is true. No per-document-type or per-section-type configuration is needed. Sections without a cached summary fall back to raw page sampling automatically.
