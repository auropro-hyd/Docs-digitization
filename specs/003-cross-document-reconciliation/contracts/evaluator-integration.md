# Evaluator Integration Contract

**Feature**: 003 | **Version**: v1

Describes how the existing rule evaluator dispatches to the new capabilities and how the
selective-rerun planner (Spec 001) reads the reverse-dependency graph populated here.

## Dispatch flow (per rule, per evaluation)

```text
                         ┌─────────────────────────────┐
                         │ engine.evaluate(rule, ctx)  │
                         └──────────────┬──────────────┘
                                        │
              ┌─────────────────────────┴─────────────────────────┐
              │ context_object.scope                              │
              │                                                   │
       same_page                      cross_document          page_aggregate
              │                               │                       │
    existing legacy evaluator     cross_doc_rule_eval.v1     page_aggregate_eval.v1
              │                               │                       │
              └────────────────┬──────────────┴───────────┬───────────┘
                               │                           │
                     findings (0..n)           resolved_context + findings
                               │                           │
                               └───────────┬───────────────┘
                                           ▼
                  stage orchestrator persists ResolvedContext + Findings
                                           ▼
                                  emits WebSocket events
```

## Selective re-run integration

The Spec 001 re-run planner consumes the reverse-dependency graph produced by this spec's
rule loader. Specifically:

1. A correction on `(document_ref_id, field_name)` on a target document is converted by the
   planner to its `(document_role, field_name)` identity.
2. The planner queries `reverse_graph[(role, field)]` to get the set of rule ids whose
   `context_object` reads that field.
3. Only those rules are re-evaluated. Same-page rules on the source document are also
   re-evaluated via the existing path.

This keeps SC-004 (selective re-run ≤ 30 s p95) achievable.

## Finding emission invariants

- `Finding.rule_id`, `Finding.rule_version` — populated from the rule's YAML.
- `Finding.scope` — populated by the capability:
  - cross_doc ⇒ `{kind: bpcr_step, step_number: …}` when source is a BPCR step page;
    otherwise `{kind: document, document_ref_id: …}`.
  - page_aggregate ⇒ always `{kind: document, document_ref_id: …}`.
- `Finding.evidence` — includes regions from the source and target documents (cross-doc) or
  every participating page (aggregate).
- `Finding.source` — capability id: `cross_doc_rule_eval.v1` or `page_aggregate_eval.v1`.
- `Finding.alcoa_tags` — copied from the rule's `alcoa_tag` field.

## Observability

- Counter `rule_eval_invocations_total{capability, match_outcome}`
- Histogram `rule_eval_duration_ms{capability}`
- Counter `rule_eval_fallback_applied_total{fallback_policy}`
- Structured log per invocation includes `rule_id`, `rule_version`, `run_id`,
  `match_outcome`, `finding_ids`.
