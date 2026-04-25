# Contract — Capability (v2)

**Spec**: 001-bmr-audit-pipeline  **Revision**: v2 (2026-04-17) — adds `context_object` to InputSpec, adds `FindingDraft.source / source_finding_ids`.

A Capability is the atomic unit of verifiable behaviour (Constitution III). Stages and the
existing ALCOA / GMP / checklist agents compose capabilities; capabilities do not compose
stages.

---

## 1. ABC

```python
class Capability(Protocol):
    capability_id: str                       # stable, unique, e.g. "legibility_check.v1"
    version: str                             # semver
    inputs: list[InputSpec]                  # declared; runtime validates
    outputs: list[OutputSpec]                # declared; runtime validates
    depends_on: list[str]                    # other capability_ids this one requires
    supports_modes: set[Literal["leverage", "process_replication"]]

    async def invoke(
        self, ctx: CapabilityContext, inputs: Mapping[str, Any]
    ) -> CapabilityResult: ...
```

## 2. InputSpec

```python
class InputSpec:
    name: str
    kind: Literal[
        "ocr_text",           # raw OCR text of one page
        "page_image",         # rendered page image
        "document_summary",   # doc-level summary blob (from doc_summary capability)
        "page_summary",       # page-level summary (from page_summary capability)
        "extracted_field",    # structured field from extraction
        "finding",            # a previously-emitted Finding (for synthesis / cross-check)
        "context_object",     # v2 NEW: resolved per the rule's context_object declaration
    ]
    required: bool
    context_object: ContextObjectSpec | None   # when kind == "context_object"
```

`ContextObjectSpec` mirrors the rule-YAML shape documented in data-model.md §1.4. The
capability declares **that it consumes a context_object**; it does NOT declare the
context_object itself — the rule does. The runtime resolves the context_object before calling
the capability and passes the resolved structure as input.

## 3. OutputSpec

```python
class OutputSpec:
    kind: Literal[
        "finding",              # FindingDraft (most common)
        "extracted_field",      # structured field to be attached to the run's extraction store
        "summary",              # page/doc summary
        "gate_verdict",         # PASS / FAIL / MARGINAL with reason
        "intermediate",         # internal artefact not stored as a Finding
    ]
    cardinality: Literal["one", "many"]
```

## 4. CapabilityContext

Passed by the runtime, not the caller:

- `run_id: UUID`
- `rule_id: str | None` — populated when the runtime is invoking the capability against a rule
- `rule_version: str | None`
- `scope: ScopeRef` — where this invocation is scoped
- `services: CapabilityServices` — read-only handles: OCR port, VLM port, LLM port,
  DocumentStore port, RuleLoader port, FindingStore **read-only view** for synthesis inputs
- `mode: PipelineMode`
- `correlation_id: str` — for tracing
- `timeout_seconds: int`

## 5. CapabilityResult & FindingDraft

```python
class CapabilityResult:
    findings: list[FindingDraft]
    extracted_fields: list[ExtractedFieldDraft]
    summary: SummaryDraft | None
    gate_verdict: GateVerdict | None
    logs: list[LogEntry]
    metrics: CapabilityMetrics   # duration, token counts, retries, etc.

class FindingDraft:
    rule_id: str | None          # None only if capability emits findings outside the rule engine (rare)
    scope: ScopeRef
    evidence: list[EvidenceRef]  # MUST be non-empty
    alcoa_principle: ALCOAPrinciple
    gmp_category: GMPCategory | None
    severity: Severity
    raw_value: Any
    expected_value: Any | None
    tolerance_applied: ToleranceSpec | None
    source: FindingSource        # v2 NEW: "direct" | "synthesised"
    source_finding_ids: list[str]  # v2 NEW: required non-empty if source == "synthesised"
    contributing_factor: str | None
```

The runtime is responsible for assigning `logical_id` and `revision`; capabilities never
compute these.

## 6. Behavioural Contracts

- **6.1 Determinism within scope**: Given identical inputs (including resolved
  context_object) and the same rule version, a capability MUST return the same findings
  (modulo non-determinism declared in `CapabilityMetrics.nondeterministic_reason`, e.g. LLM
  calls). Non-deterministic capabilities MUST record the input digest into the result.
- **6.2 Scope hygiene**: A capability invoked at `ScopeRef = {kind: "page", document_id: D,
  page_number: P}` MUST NOT emit findings at scopes other than that page (or sub-regions).
  Cross-doc rules use `ScopeRef.kind = "entity_match"`, not a different scope field.
- **6.3 No direct store writes**: Capabilities never write to `FindingStore`, `ReviewStore`,
  `AuditTrailStore`. They return drafts; the runtime persists.
- **6.4 Evidence required**: `FindingDraft.evidence` MUST be non-empty. Violations are
  rejected by the runtime and logged as a capability bug.
- **6.5 Input-unverified handling**: If a required input is missing AND the capability's rule
  has a `fallback` declaration, the capability MUST emit a Finding with severity per the
  fallback (e.g., `UNEVALUATED_CONTEXT_MISSING`). If no fallback and no input, the capability
  returns an empty result and the runtime emits a configuration error.
- **6.6 Declared dependencies are truth**: The re-run planner trusts `depends_on` and
  `inputs[].kind / context_object` to build the reverse graph. Undeclared dependencies are
  invisible to the planner — i.e., a bug. Capability tests enforce that every consumed input
  is declared.
- **6.7 Idempotency**: Re-invoking a capability with the same inputs and rule version MUST
  produce findings with the same `logical_id`. The runtime uses this to deduplicate on
  re-run.
- **6.8 Timeout / cancellation**: Capabilities MUST respect `ctx.timeout_seconds`. On
  timeout, return a result with `metrics.timed_out = true` and no findings.

## 7. Testing obligations

Each capability ships with:
- A unit test exercising every declared `InputSpec` (present, absent with fallback,
  malformed).
- A determinism test (hash inputs → capability called twice → same logical_ids).
- A scope-hygiene test (invoked at page P → assert no emitted finding references a scope
  outside P).
- For capabilities that consume `context_object`: a fixture-per-scope test
  (`same_page`, `page_aggregate`, `cross_document`).

## 8. New v1 capability surface

| capability_id | purpose | primary input kinds | outputs |
|---|---|---|---|
| `legibility_check.v1` | per-page legibility + confidence | page_image, ocr_text | gate_verdict + optional finding (low confidence) |
| `boundary_detect.v1` | detect logical document boundaries in a package | page_image, ocr_text | extracted_field (boundaries per doc) |
| `page_summary.v1` | config-driven page-level summary (BPCR) | page_image, ocr_text, extracted_field | summary |
| `doc_summary.v1` | config-driven doc-level summary | ocr_text (whole doc) | summary |
| `page_aggregate_eval.v1` | evaluate a rule whose context_object is page_aggregate | context_object (resolved), extracted_field | finding |
| `cross_doc_rule_eval.v1` | evaluate a rule whose context_object is cross_document | context_object (resolved across roles) | finding |
| `checklist_synthesise.v1` | synthesise a checklist finding from source findings, fallback to direct | finding (source set), context_object (for fallback) | finding |

Existing capabilities surfaced from the current compliance agents (step-rule-eval,
signature-detect, timestamp-sequence, quantity-reconcile, etc.) are wrapped behind this same
ABC in a later tasks.md step. Until wrapped, the Compliance stage invokes the existing agents
as opaque units; this is acceptable per Constitution VII and does not violate Principle III
because the new work lands as capabilities.
