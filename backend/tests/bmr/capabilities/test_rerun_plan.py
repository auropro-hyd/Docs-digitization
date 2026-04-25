"""Unit tests for ``rerun_plan.v1``.

The planner is declarative: given the raw rule YAMLs and a corrected
field name, it decides which rules need to re-run. Downstream, the HITL
service uses the plan to drive the partial re-evaluation, so these
invariants are load-bearing for the CORRECT workflow.
"""

from __future__ import annotations

from app.bmr.capabilities.rerun_plan import plan_selective_rerun_v1


def _leaf_rule(rule_id: str, *, source_field: str, target_field: str | None = None):
    rule: dict = {
        "id": rule_id,
        "version": "1.0.0",
        "context_object": {"scope": "same_page"},
        "source": {"field": source_field},
    }
    if target_field is not None:
        rule["target"] = {"field": target_field}
    return rule


def _synthesis_rule(rule_id: str, *, synthesises_from: list[str]):
    return {
        "id": rule_id,
        "version": "1.0.0",
        "context_object": {"scope": "checklist_synthesis", "group_by": "bpcr_step"},
        "synthesises_from": synthesises_from,
    }


def test_plan_includes_rules_referencing_source_field():
    rules = [
        _leaf_rule("r.weight", source_field="dispensed_weight_kg"),
        _leaf_rule("r.signature", source_field="operator_signature"),
    ]
    plan = plan_selective_rerun_v1(
        loaded_rules=rules, corrected_field="dispensed_weight_kg"
    )
    assert plan.affected_rule_ids == ("r.weight",)
    assert plan.affected_synthesis_rule_ids == ()
    assert plan.total == 1


def test_plan_includes_rules_referencing_target_field():
    rules = [
        _leaf_rule(
            "r.weight_match",
            source_field="dispensed_weight_kg",
            target_field="weight_kg",
        ),
        _leaf_rule("r.other", source_field="lot_number"),
    ]
    plan = plan_selective_rerun_v1(loaded_rules=rules, corrected_field="weight_kg")
    assert plan.affected_rule_ids == ("r.weight_match",)


def test_plan_pulls_in_synthesis_rule_when_constituent_is_affected():
    rules = [
        _leaf_rule("r.weight", source_field="dispensed_weight_kg"),
        _leaf_rule("r.signature", source_field="operator_signature"),
        _synthesis_rule(
            "r.step_complete", synthesises_from=["r.weight", "r.signature"]
        ),
        _synthesis_rule("r.unrelated", synthesises_from=["r.signature"]),
    ]
    plan = plan_selective_rerun_v1(
        loaded_rules=rules, corrected_field="dispensed_weight_kg"
    )
    assert plan.affected_rule_ids == ("r.weight",)
    # Only r.step_complete depends on r.weight; r.unrelated does not.
    assert plan.affected_synthesis_rule_ids == ("r.step_complete",)
    assert plan.total == 2


def test_plan_empty_when_no_rule_touches_field():
    rules = [_leaf_rule("r.signature", source_field="operator_signature")]
    plan = plan_selective_rerun_v1(loaded_rules=rules, corrected_field="lot_number")
    assert plan.affected_rule_ids == ()
    assert plan.affected_synthesis_rule_ids == ()


def test_plan_ignores_synthesis_rules_with_no_affected_constituents():
    rules = [
        _leaf_rule("r.weight", source_field="dispensed_weight_kg"),
        _synthesis_rule("r.other_rollup", synthesises_from=["r.unknown"]),
    ]
    plan = plan_selective_rerun_v1(
        loaded_rules=rules, corrected_field="dispensed_weight_kg"
    )
    assert plan.affected_rule_ids == ("r.weight",)
    assert plan.affected_synthesis_rule_ids == ()
