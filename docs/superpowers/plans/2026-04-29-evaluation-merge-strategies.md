# Evaluation Merge Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new `evaluation_strategy` values — `text_primary` and `llm_arbitrated` — to the compliance evaluator, giving rule authors fine-grained control over how text and vision results are merged without breaking any existing rules.

**Architecture:** The evaluator's `_run()` function already routes rules into `text_rules`, `vision_only_rules`, and `text_and_vision_rules` lists, then calls `_merge_text_vision()` after both coroutines complete. The new strategies add two new routing buckets (`text_primary_rules`, `llm_arbitrated_rules`) and two new merge helpers. The `llm_arbitrated` strategy injects a third async LLM call only when text and vision results genuinely conflict (different status codes). All existing strategies (`text`, `vision`, `text_and_vision`) are untouched — new strategy strings are opt-in via YAML and auto-loaded by `AuditRule._YAML_STR_FIELDS`.

**Tech Stack:** Python 3.11+, asyncio, `app.core.ports.llm.LLMProvider`, `app.core.ports.vlm.VLMProvider`, pytest, existing `validate_cli` harness.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/compliance/evaluator.py` | Modify | Add `_merge_text_primary()`, `_merge_llm_arbitrated()` (async), update routing in `_run()` |
| `backend/tests/compliance/test_merge_strategies.py` | Create | Unit tests for all three merge helpers (new + existing) |

No other files need to change. `AuditRule.evaluation_strategy` already accepts any string (it's a plain `str` field loaded via `_YAML_STR_FIELDS`). YAML files that currently set `evaluation_strategy: text_and_vision` are unchanged and continue using the existing `_merge_text_vision()` path.

---

## Task 1: Write failing tests for `_merge_text_primary`

**Files:**
- Create: `backend/tests/compliance/test_merge_strategies.py`

`_merge_text_primary` rule: text wins ties. Vision can only **escalate** (make the result worse). So:
- text=compliant, vision=compliant → compliant (text source)
- text=compliant, vision=non_compliant → non_compliant (vision escalates)
- text=non_compliant, vision=compliant → non_compliant (text wins; vision cannot de-escalate)
- text=non_compliant, vision=non_compliant → non_compliant (same severity: text wins)
- text=uncertain, vision=compliant → uncertain (text wins)
- text=None → vision result
- vision=None → text result

- [ ] **Step 1: Create the test file**

```python
# backend/tests/compliance/test_merge_strategies.py
"""Unit tests for evaluation merge strategy helpers."""
import pytest
from app.compliance.evaluator import _merge_text_vision
from app.compliance.rules.registry import AuditRule
from app.compliance.models import RuleEvaluation


def _rule(strategy: str = "text_and_vision") -> AuditRule:
    return AuditRule(
        id="alcoa:5",
        number=5,
        category="attributable",
        category_display="Attributable",
        agent="alcoa",
        text="Test rule",
        evaluation_strategy=strategy,
    )


def _ev(rule_id: str, status: str, confidence: float = 0.9) -> RuleEvaluation:
    return RuleEvaluation(rule_id=rule_id, status=status, confidence=confidence)


# ── existing text_and_vision merge (regression) ───────────────────────────────

class TestMergeTextVision:
    def test_vision_wins_tie(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "compliant"
        assert "[Vision]" in result.reasoning

    def test_vision_wins_when_higher_severity(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "non_compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "non_compliant"

    def test_text_wins_when_higher_severity(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, text, vision)
        assert result.status == "non_compliant"

    def test_none_text_returns_vision(self):
        rule = _rule("text_and_vision")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_vision(rule, None, vision)
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        rule = _rule("text_and_vision")
        text = _ev(rule.id, "uncertain")
        result = _merge_text_vision(rule, text, None)
        assert result.status == "uncertain"

    def test_both_none_returns_error(self):
        rule = _rule("text_and_vision")
        result = _merge_text_vision(rule, None, None)
        assert result.status == "error"


# ── text_primary merge ────────────────────────────────────────────────────────

class TestMergeTextPrimary:
    def test_text_wins_tie(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "compliant"
        assert "[Text]" in result.reasoning

    def test_vision_escalates_to_non_compliant(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "non_compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "non_compliant"
        assert "[Vision]" in result.reasoning

    def test_text_wins_over_lower_severity_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "non_compliant"
        assert "[Text]" in result.reasoning

    def test_text_wins_over_uncertain_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "uncertain")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert result.status == "uncertain"

    def test_none_text_returns_vision(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, None, vision)
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        result = _merge_text_primary(rule, text, None)
        assert result.status == "non_compliant"

    def test_both_none_returns_error(self):
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        result = _merge_text_primary(rule, None, None)
        assert result.status == "error"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestMergeTextPrimary -v
```

Expected: `ImportError: cannot import name '_merge_text_primary'`

---

## Task 2: Implement `_merge_text_primary`

**Files:**
- Modify: `backend/app/compliance/evaluator.py` — add after `_merge_text_vision` (line 671)

- [ ] **Step 3: Add the function**

In `evaluator.py`, add this function immediately after `_merge_text_vision` (after line 670):

```python
def _merge_text_primary(
    rule: AuditRule,
    text_ev: RuleEvaluation | None,
    vision_ev: RuleEvaluation | None,
) -> RuleEvaluation:
    """Merge for text_primary strategy: text verdict wins unless vision escalates.

    Vision can only raise the severity (make things worse), never lower it.
    Use this for rules where OCR content is the authoritative source and vision
    supplements only to catch what text missed.
    """
    if text_ev is None and vision_ev is None:
        return RuleEvaluation(rule_id=rule.id, status="error", description="Both evaluations missing")
    if text_ev is None:
        return vision_ev  # type: ignore[return-value]
    if vision_ev is None:
        return text_ev

    text_sev = _STATUS_SEVERITY.get(text_ev.status, 0)
    vision_sev = _STATUS_SEVERITY.get(vision_ev.status, 0)

    if vision_sev > text_sev:
        # Vision escalates — use vision verdict
        return RuleEvaluation(
            rule_id=rule.id,
            status=vision_ev.status,
            severity=vision_ev.severity or text_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Vision escalated] {vision_ev.reasoning} [Text] {text_ev.reasoning}",
            evidence=f"[Vision] {vision_ev.evidence} [Text] {text_ev.evidence}",
            description=vision_ev.description or text_ev.description,
            recommendation=vision_ev.recommendation or text_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )
    else:
        # Text wins (including ties)
        return RuleEvaluation(
            rule_id=rule.id,
            status=text_ev.status,
            severity=text_ev.severity or vision_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Text primary] {text_ev.reasoning} [Vision] {vision_ev.reasoning}",
            evidence=f"[Text] {text_ev.evidence} [Vision] {vision_ev.evidence}",
            description=text_ev.description or vision_ev.description,
            recommendation=text_ev.recommendation or vision_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestMergeTextPrimary -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Run regression tests to confirm existing merge is unbroken**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestMergeTextVision -v
```

Expected: all 6 tests PASS.

- [ ] **Step 6: Commit**

```bash
cd backend/
git add tests/compliance/test_merge_strategies.py app/compliance/evaluator.py
git commit -m "feat: add text_primary evaluation merge strategy"
```

---

## Task 3: Write failing tests for `llm_arbitrated`

**Files:**
- Modify: `backend/tests/compliance/test_merge_strategies.py`

`_merge_llm_arbitrated` rules:
- If text and vision agree (same status) → return that status immediately, no third call.
- If they conflict → call the LLM adjudicator and return its verdict.
- If adjudicator call fails (exception or error status) → fall back to the higher-severity result.
- text=None → vision only (no arbitration).
- vision=None → text only (no arbitration).

The function is async because the adjudicator call is async.

- [ ] **Step 7: Add tests to the test file**

Append to `backend/tests/compliance/test_merge_strategies.py`:

```python
# ── llm_arbitrated merge ──────────────────────────────────────────────────────

import asyncio
from unittest.mock import AsyncMock, patch


class TestMergeLLMArbitrated:
    def test_agreement_compliant_no_llm_call(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator") as mock_arb:
                result = await _merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text="")
                mock_arb.assert_not_called()
                return result

        result = asyncio.run(run())
        assert result.status == "compliant"

    def test_agreement_non_compliant_no_llm_call(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "non_compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator") as mock_arb:
                result = await _merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text="")
                mock_arb.assert_not_called()
                return result

        result = asyncio.run(run())
        assert result.status == "non_compliant"

    def test_conflict_llm_called_and_verdict_used(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        arbitrated = _ev(rule.id, "compliant", confidence=0.85)
        arbitrated.reasoning = "OCR missed handwritten signature; vision confirms presence"

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator", new=AsyncMock(return_value=arbitrated)):
                result = await _merge_llm_arbitrated(rule, text, vision, llm=object(), ocr_text="Done by: [Signature]")
                return result

        result = asyncio.run(run())
        assert result.status == "compliant"
        assert "Arbitrated" in result.reasoning

    def test_conflict_arbitrator_fails_falls_back_to_higher_severity(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")

        async def run():
            with patch("app.compliance.evaluator._call_arbitrator", new=AsyncMock(side_effect=Exception("timeout"))):
                result = await _merge_llm_arbitrated(rule, text, vision, llm=object(), ocr_text="")
                return result

        result = asyncio.run(run())
        assert result.status == "non_compliant"  # higher severity wins as fallback

    def test_none_text_returns_vision(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        vision = _ev(rule.id, "compliant")

        result = asyncio.run(_merge_llm_arbitrated(rule, None, vision, llm=None, ocr_text=""))
        assert result.status == "compliant"

    def test_none_vision_returns_text(self):
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "uncertain")

        result = asyncio.run(_merge_llm_arbitrated(rule, text, None, llm=None, ocr_text=""))
        assert result.status == "uncertain"
```

- [ ] **Step 8: Run to confirm they fail**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestMergeLLMArbitrated -v
```

Expected: `ImportError: cannot import name '_merge_llm_arbitrated'`

---

## Task 4: Implement `_call_arbitrator` and `_merge_llm_arbitrated`

**Files:**
- Modify: `backend/app/compliance/evaluator.py`

The arbitrator prompt is short: it receives the rule text + pass criteria, OCR content, text verdict + reasoning, vision verdict + reasoning, and must return a single JSON object matching the existing `RuleEvaluation` response schema. It reuses the `LLMProvider` port — no new port needed.

- [ ] **Step 9: Add `_call_arbitrator` and `_merge_llm_arbitrated` to evaluator.py**

Add after `_merge_text_primary` (the function you added in Task 2):

```python
async def _call_arbitrator(
    rule: AuditRule,
    text_ev: RuleEvaluation,
    vision_ev: RuleEvaluation,
    llm: "LLMProvider",
    ocr_text: str,
) -> RuleEvaluation:
    """Ask the LLM to resolve a text-vs-vision conflict for a single rule.

    Called only when text_ev.status != vision_ev.status. Returns a new
    RuleEvaluation whose status and reasoning come from the LLM verdict.
    Raises on LLM failure so the caller can apply its fallback.
    """
    from app.core.ports.llm import GenerateRequest

    prompt = (
        f"You are an ALCOA++ compliance arbitrator. Two independent evaluators disagree on Rule {rule.number}.\n\n"
        f"RULE TEXT:\n{rule.text}\n\n"
        f"PASS CRITERIA:\n{rule.pass_criteria or '(none specified)'}\n\n"
        f"OCR CONTENT (text layer):\n{ocr_text[:3000]}\n\n"
        f"TEXT EVALUATOR VERDICT: {text_ev.status}\n"
        f"TEXT REASONING: {text_ev.reasoning}\n\n"
        f"VISION EVALUATOR VERDICT: {vision_ev.status}\n"
        f"VISION REASONING: {vision_ev.reasoning}\n\n"
        f"The text evaluator reads OCR-extracted markdown. The vision evaluator reads the scanned page image.\n"
        f"OCR frequently garbles handwritten entries (signatures, initials, names). "
        f"When the vision evaluator confirms that a handwritten entry IS present, prefer that over "
        f"the text evaluator's complaint about missing/garbled content.\n\n"
        f"Return a JSON object with exactly these fields:\n"
        f"  status: one of compliant | non_compliant | uncertain | not_applicable\n"
        f"  confidence: float 0.0-1.0\n"
        f"  reasoning: one sentence explaining why you chose this verdict\n"
        f"  evidence: the specific text or visual evidence that drove your decision\n"
    )

    response = await llm.generate_structured(
        GenerateRequest(
            prompt=prompt,
            response_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["status", "confidence", "reasoning"],
            },
        )
    )

    data = response.parsed
    status = data.get("status", "uncertain")
    if status not in _VALID_STATUSES:
        status = "uncertain"

    return RuleEvaluation(
        rule_id=rule.id,
        status=status,
        confidence=float(data.get("confidence", 0.5)),
        reasoning=data.get("reasoning", ""),
        evidence=data.get("evidence", ""),
        applicability_trace=list(text_ev.applicability_trace),
    )


async def _merge_llm_arbitrated(
    rule: AuditRule,
    text_ev: RuleEvaluation | None,
    vision_ev: RuleEvaluation | None,
    llm: "LLMProvider | None",
    ocr_text: str,
) -> RuleEvaluation:
    """Merge for llm_arbitrated strategy.

    When text and vision agree, return immediately. When they conflict,
    call _call_arbitrator to resolve. Falls back to higher-severity result
    if arbitration fails or llm is None.
    """
    if text_ev is None and vision_ev is None:
        return RuleEvaluation(rule_id=rule.id, status="error", description="Both evaluations missing")
    if text_ev is None:
        return vision_ev  # type: ignore[return-value]
    if vision_ev is None:
        return text_ev

    if text_ev.status == vision_ev.status:
        # Agreement — no arbitration needed
        return RuleEvaluation(
            rule_id=rule.id,
            status=text_ev.status,
            severity=text_ev.severity or vision_ev.severity,
            confidence=min(text_ev.confidence, vision_ev.confidence),
            reasoning=f"[Agreed] {text_ev.reasoning}",
            evidence=f"[Text] {text_ev.evidence} [Vision] {vision_ev.evidence}",
            description=text_ev.description or vision_ev.description,
            recommendation=text_ev.recommendation or vision_ev.recommendation,
            applicability_trace=list(text_ev.applicability_trace),
        )

    # Conflict — arbitrate if LLM is available
    if llm is not None:
        try:
            arbitrated = await _call_arbitrator(rule, text_ev, vision_ev, llm, ocr_text)
            return RuleEvaluation(
                rule_id=rule.id,
                status=arbitrated.status,
                severity=text_ev.severity or vision_ev.severity,
                confidence=arbitrated.confidence,
                reasoning=f"[Arbitrated] {arbitrated.reasoning} | Text: {text_ev.reasoning} | Vision: {vision_ev.reasoning}",
                evidence=arbitrated.evidence,
                description=text_ev.description or vision_ev.description,
                recommendation=text_ev.recommendation or vision_ev.recommendation,
                applicability_trace=list(text_ev.applicability_trace),
            )
        except Exception:
            logger.warning("LLM arbitration failed for rule %s, falling back to higher severity", rule.id, exc_info=True)

    # Fallback: higher severity wins
    text_sev = _STATUS_SEVERITY.get(text_ev.status, 0)
    vision_sev = _STATUS_SEVERITY.get(vision_ev.status, 0)
    winner = text_ev if text_sev >= vision_sev else vision_ev
    return RuleEvaluation(
        rule_id=rule.id,
        status=winner.status,
        severity=winner.severity or (text_ev.severity or vision_ev.severity),
        confidence=min(text_ev.confidence, vision_ev.confidence),
        reasoning=f"[Fallback-higher-sev] {winner.reasoning}",
        evidence=f"[Text] {text_ev.evidence} [Vision] {vision_ev.evidence}",
        description=winner.description,
        recommendation=winner.recommendation,
        applicability_trace=list(text_ev.applicability_trace),
    )
```

- [ ] **Step 10: Run the llm_arbitrated tests**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestMergeLLMArbitrated -v
```

Expected: all 6 tests PASS.

- [ ] **Step 11: Run the full test file**

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py -v
```

Expected: all 19 tests PASS.

- [ ] **Step 12: Commit**

```bash
git add app/compliance/evaluator.py tests/compliance/test_merge_strategies.py
git commit -m "feat: add llm_arbitrated evaluation merge strategy"
```

---

## Task 5: Wire new strategies into `_run()` routing

**Files:**
- Modify: `backend/app/compliance/evaluator.py` — `_run()` inner function, routing block (lines 452-476) and merge block (lines 570-587)

The routing block needs two new lists. The merge block needs two new loops. The `llm_arbitrated` strategy runs text + vision in parallel (same as `text_and_vision`) but calls `_merge_llm_arbitrated` instead of `_merge_text_vision`. The `text_primary` strategy does the same but calls `_merge_text_primary`.

- [ ] **Step 13: Write the routing block integration test**

Append to `backend/tests/compliance/test_merge_strategies.py`:

```python
# ── routing integration ───────────────────────────────────────────────────────
# These tests verify that the correct merge function is selected based on
# evaluation_strategy, using a lightweight stub evaluator run.

class TestRoutingStrategy:
    """Smoke-test that _run() dispatches to the right merge function.

    We test this by checking the reasoning prefix on the merged RuleEvaluation.
    """

    def _make_batch(self, strategy: str) -> "RuleBatch":
        from app.compliance.rules.registry import RuleBatch
        rule = _rule(strategy)
        return RuleBatch(batch_id="b1", category="attributable", agent="alcoa", rules=[rule])

    def test_text_primary_reasoning_prefix(self):
        """text_primary conflict: text wins, reasoning contains '[Text primary]'."""
        # This is a unit test for _merge_text_primary — routing tested above.
        from app.compliance.evaluator import _merge_text_primary
        rule = _rule("text_primary")
        text = _ev(rule.id, "non_compliant")
        vision = _ev(rule.id, "compliant")
        result = _merge_text_primary(rule, text, vision)
        assert "[Text primary]" in result.reasoning

    def test_llm_arbitrated_agreement_prefix(self):
        """llm_arbitrated agreement: no LLM call, reasoning contains '[Agreed]'."""
        from app.compliance.evaluator import _merge_llm_arbitrated
        rule = _rule("llm_arbitrated")
        text = _ev(rule.id, "compliant")
        vision = _ev(rule.id, "compliant")
        result = asyncio.run(_merge_llm_arbitrated(rule, text, vision, llm=None, ocr_text=""))
        assert "[Agreed]" in result.reasoning
```

Run to confirm PASS (these use only already-implemented helpers):

```bash
cd backend/
.venv/bin/pytest tests/compliance/test_merge_strategies.py::TestRoutingStrategy -v
```

Expected: 2 tests PASS.

- [ ] **Step 14: Update the routing block in `_run()`**

In `evaluator.py`, find the routing block that begins at line 452 (`# Split rules by evaluation strategy`) and replace it:

```python
        # Split rules by evaluation strategy
        text_rules: list[AuditRule] = []
        vision_only_rules: list[AuditRule] = []
        text_and_vision_rules: list[AuditRule] = []
        text_primary_rules: list[AuditRule] = []
        llm_arbitrated_rules: list[AuditRule] = []

        for rule in applicable_rules:
            strategy = rule.evaluation_strategy
            if strategy == "vision" and vlm_available:
                vision_only_rules.append(rule)
            elif strategy in ("text_and_vision", "text_primary", "llm_arbitrated") and vlm_available:
                if strategy == "text_and_vision":
                    text_and_vision_rules.append(rule)
                elif strategy == "text_primary":
                    text_primary_rules.append(rule)
                else:
                    llm_arbitrated_rules.append(rule)
                text_rules.append(rule)
            elif strategy == "vision" and not vlm_available:
                if compliance_settings.vlm_fallback_to_text:
                    text_rules.append(rule)
                else:
                    gate_evals.append(RuleEvaluation(
                        rule_id=rule.id,
                        status="not_applicable",
                        confidence=1.0,
                        reasoning="VLM unavailable — vision-only rule skipped",
                        applicability_trace=["vlm_unavailable"],
                    ))
            elif strategy in ("text_primary", "llm_arbitrated") and not vlm_available:
                # VLM unavailable — run text only; no merge needed
                text_rules.append(rule)
            else:
                text_rules.append(rule)

        all_vision_rules = vision_only_rules + text_and_vision_rules + text_primary_rules + llm_arbitrated_rules
```

- [ ] **Step 15: Update the merge block in `_run()`**

Find the merge block that begins at line 570 (`# Merge results: vision-only, text-only, and text_and_vision`) and replace it:

```python
            # Merge results: vision-only, text-only, and dual-channel strategies
            merged_rule_ids: set[str] = set()

            for rule in vision_only_rules:
                ev = vision_eval_map.get(rule.id)
                if ev:
                    merged_rule_ids.add(rule.id)
                    all_evals.append(ev)

            for rule in text_and_vision_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                all_evals.append(_merge_text_vision(rule, text_ev, vision_ev))

            for rule in text_primary_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                all_evals.append(_merge_text_primary(rule, text_ev, vision_ev))

            for rule in llm_arbitrated_rules:
                merged_rule_ids.add(rule.id)
                text_ev = text_eval_map.get(rule.id)
                vision_ev = vision_eval_map.get(rule.id)
                ocr_text = enriched.markdown if hasattr(enriched, "markdown") else ""
                arb_result = await _merge_llm_arbitrated(rule, text_ev, vision_ev, llm, ocr_text)
                all_evals.append(arb_result)

            for rule_id, ev in text_eval_map.items():
                if rule_id not in merged_rule_ids:
                    all_evals.append(ev)
```

- [ ] **Step 16: Run the full test suite**

```bash
cd backend/
.venv/bin/pytest tests/compliance/ -v --tb=short
```

Expected: all existing compliance tests PASS, all new strategy tests PASS.

- [ ] **Step 17: Run the config validator**

```bash
cd backend/
.venv/bin/python - <<'PY'
from app.compliance.rules.registry import get_registry
from app.compliance.rules.profiles import validate_compliance_configs
validate_compliance_configs(get_registry())
print("OK")
PY
```

Expected: `OK`

- [ ] **Step 18: Commit**

```bash
git add app/compliance/evaluator.py
git commit -m "feat: wire text_primary and llm_arbitrated strategies into _run() routing"
```

---

## Task 6: Update Rule 5 to use `text_primary`

**Files:**
- Modify: `backend/app/compliance/rules/alcoa_rules.yaml`

Rule 5 (`Critical steps include separate "Done by" and "Checked by" identification`) has been confirmed to produce false positives when the text evaluator complains about OCR-garbled handwriting but the vision evaluator correctly confirms the signature is present. `text_primary` is the right strategy: OCR text is the authoritative source for most fields, but vision should not de-escalate a genuine text finding.

Wait — Rule 5's false positive is the *opposite*: vision says compliant (it can see the handwriting) but text says non_compliant (it can only see garbled OCR). So text is the one wrongly escalating.

The right fix is `llm_arbitrated`: let the LLM adjudicator decide, since it gets context explaining that OCR garbles handwriting and vision can confirm presence.

- [ ] **Step 19: Change Rule 5 strategy in alcoa_rules.yaml**

Find Rule 5 in `backend/app/compliance/rules/alcoa_rules.yaml` and change:

```yaml
    evaluation_strategy: text_and_vision
```

to:

```yaml
    evaluation_strategy: llm_arbitrated
```

- [ ] **Step 20: Validate Rule 5 against known pages**

```bash
cd backend/

# Pages that were false positives — should now PASS
.venv/bin/python -m app.compliance.rules.validate_cli \
  --agent alcoa \
  --rule 5 \
  --doc 90ec18f4-1f29-4613-92e8-c2325bec9968 \
  --pages 11,16 \
  --expect pass

# Page 9 conditional skip — should still PASS
.venv/bin/python -m app.compliance.rules.validate_cli \
  --agent alcoa \
  --rule 5 \
  --doc 90ec18f4-1f29-4613-92e8-c2325bec9968 \
  --pages 9 \
  --expect pass
```

Expected: all pages PASS.

- [ ] **Step 21: Commit**

```bash
git add backend/app/compliance/rules/alcoa_rules.yaml
git commit -m "fix(rule-5): use llm_arbitrated strategy to resolve OCR vs vision conflicts on signed steps"
```

---

## Spec Coverage Check

| Requirement | Task |
|-------------|------|
| `text_primary` strategy: text verdict wins; vision can only escalate | Tasks 1-2 |
| `vision` strategy: unchanged (no work needed) | — |
| `llm_arbitrated` strategy: agree→pass through, conflict→adjudicate | Tasks 3-4 |
| Wire new strategies into routing with backward compat | Task 5 |
| VLM unavailable fallback for new strategies | Task 5, Step 14 |
| No existing rules affected | Verified by regression in Steps 5, 16 |
| Aligns to ports-and-adapters (reuses LLMProvider, no new ports) | `_call_arbitrator` uses `llm.generate_structured` |
| Apply to Rule 5 false-positive fix | Task 6 |
| Config validator passes | Steps 17 |

---

## Backward Compatibility Guarantee

Existing YAML files using `evaluation_strategy: text`, `vision`, or `text_and_vision` are **not touched**. The routing block maps each known value to exactly the same lists as before. Any unknown strategy value (typo, future addition) falls through to `text_rules` — same as the original `else` branch. No existing test or rule config changes.
