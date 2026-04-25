# Rule Loader Contract (Spec 003 extension)

**Feature**: 003 | **Version**: v1

Extends the existing `app/rules/loader.py` to understand `context_object`. The loader runs
at service startup and on any admin-triggered rule bank reload; it validates and binds each
rule to its resolver.

## Load-time responsibilities

1. **Schema validation** — delegate to Spec 005's JSON Schema validator. Any rule failing
   schema validation causes a hard startup failure with a pointer to file + rule id +
   schema path.
2. **Context binding** — parse `context_object` and attach a resolver handle:
   - `same_page` → legacy evaluator path (existing behaviour).
   - `cross_document` → `CrossDocumentResolver(rule)` bound.
   - `page_aggregate` → `PageAggregateResolver(rule)` bound.
3. **Alias resolution** — if `context_object.entity_match.aliases_file` is set, verify the
   file exists, is valid YAML, and parses into an `AliasTable`. Cache by `yaml_path`.
   Missing or invalid aliases file is a hard startup failure.
4. **Tolerance invariant** — if the rule's `source.field` is numeric and `tolerance` is
   absent, hard startup failure (FR-006 / Constitution VIII — Accurate).
5. **Reverse-graph indexing** — for each rule, populate the reverse-dependency graph with
   edges `(document_role, field) → rule_id` for every field referenced in `source`,
   `target`, `expected`, and `page_selector`.
6. **Rules-manifest snapshot** — emit a per-load manifest with rule id + version +
   `context_object_digest`. Spec 001's `Run` entity captures this snapshot id at run start
   for reproducibility.

## Failure modes

| Condition | Behaviour |
|---|---|
| Schema validation failure | Hard fail at startup; service does NOT accept traffic |
| Missing `tolerance` on numeric rule | Hard fail |
| Missing `aliases_file` | Hard fail |
| Unknown `entity_match.strategy` | Hard fail |
| Unknown `aggregation` | Hard fail |
| Load succeeded but graph indexing detected a cycle | Hard fail (rules MUST NOT reference each other cyclically through context_object) |

## API surface (Python, illustrative)

```python
class RuleLoader:
    async def load_all(self, rule_dir: Path, aliases_dir: Path) -> RuleBank: ...

class RuleBank:
    rules: list[LoadedRule]                    # validated + resolver-bound
    reverse_graph: ReverseDependencyGraph
    rules_manifest_id: str

class LoadedRule:
    rule: Rule                                 # data
    resolver: Resolver                         # bound handle
```
