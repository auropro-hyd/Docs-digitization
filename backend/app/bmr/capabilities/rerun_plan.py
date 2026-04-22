"""``rerun_plan.v1`` — pick the rule subset to re-evaluate after a CORRECT.

Given a rule bank and a reviewer's correction (field, doc_id, page_index)
return the set of rule ids whose evaluation observably depends on that
field. The heuristic for v0 is declarative-only — we read the rule YAMLs
and look at ``source.field``, ``target.field``, and ``expected.field``;
any rule referencing the corrected field name is considered affected.
Synthesis rules pull in their constituents via ``synthesises_from``.

Keeping the planner pure lets the HITL service show reviewers exactly
what will re-run before they commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RerunPlan:
    affected_rule_ids: tuple[str, ...]
    affected_synthesis_rule_ids: tuple[str, ...]
    triggering_field: str

    @property
    def total(self) -> int:
        return len(self.affected_rule_ids) + len(self.affected_synthesis_rule_ids)


@dataclass(frozen=True)
class _RuleProbe:
    rule_id: str
    scope: str
    raw: dict[str, Any]
    synthesises_from: tuple[str, ...] = field(default_factory=tuple)


def _rule_touches_field(rule: dict[str, Any], field_name: str) -> bool:
    for key in ("source", "target", "expected"):
        ref = rule.get(key)
        if isinstance(ref, dict) and ref.get("field") == field_name:
            return True
    return False


# Fields that can indirectly affect alias resolution for any rule. When
# a reviewer corrects one of these, we cannot tell declaratively which
# rules depend on a particular alias mapping, so the conservative move
# is to re-evaluate every leaf rule.
_ALIAS_SENSITIVE_FIELDS = frozenset(
    {"entity_name", "material_name", "product_name", "alias", "lot_id"}
)


def _is_alias_sensitive(field_name: str) -> bool:
    lower = field_name.lower()
    return any(tok in lower for tok in _ALIAS_SENSITIVE_FIELDS)


def plan_selective_rerun_v1(
    *,
    loaded_rules: list[dict[str, Any]],
    corrected_field: str,
) -> RerunPlan:
    """Return the set of rules whose evaluation depends on ``corrected_field``.

    Parameters
    ----------
    loaded_rules:
        Raw rule dicts as loaded from YAML (each with an ``id`` + the
        declarative context_object / source / target / etc keys).
    corrected_field:
        The field name the reviewer corrected.

    The declarative match is conservative by design: if the corrected
    field is alias-sensitive (entity_name, material_name, …) the
    planner cannot prove which unrelated cross-document rules a
    mapping change might flip, so it re-evaluates every leaf rule.
    """

    alias_touched = _is_alias_sensitive(corrected_field)

    leaf_affected: list[str] = []
    synthesis_rules: list[tuple[str, tuple[str, ...]]] = []
    for rule in loaded_rules:
        scope = (rule.get("context_object") or {}).get("scope")
        rule_id = str(rule.get("id"))
        if scope == "checklist_synthesis":
            synthesises_from = tuple(
                str(r) for r in (rule.get("synthesises_from") or [])
            )
            synthesis_rules.append((rule_id, synthesises_from))
            continue
        if alias_touched or _rule_touches_field(rule, corrected_field):
            leaf_affected.append(rule_id)

    affected_set = set(leaf_affected)
    affected_synthesis: list[str] = [
        rule_id
        for rule_id, constituents in synthesis_rules
        if any(c in affected_set for c in constituents)
    ]

    return RerunPlan(
        affected_rule_ids=tuple(leaf_affected),
        affected_synthesis_rule_ids=tuple(affected_synthesis),
        triggering_field=corrected_field,
    )


__all__ = ["RerunPlan", "plan_selective_rerun_v1"]
