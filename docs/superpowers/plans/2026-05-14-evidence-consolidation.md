# Evidence Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `synthesize_rule_evidence()` between per-page evaluation and report assembly so that rules firing on more than 3 pages get a cross-page narrative evidence string with inline PAGE:N citations instead of a single-page verbatim snippet.

**Architecture:** A new async function `synthesize_rule_evidence()` in `evaluator.py` groups per-page `RuleEvaluation.evidence` strings by `rule_id`, skips rules with ≤3 distinct pages, batches qualifying rules into concurrent LLM calls via `asyncio.gather`, then overwrites `ev.evidence` on every matching `RuleEvaluation` in the results list before `assemble_agent_report` runs. All five agent classes insert one `await` call between their evaluation and assembly steps.

**Tech Stack:** Python asyncio, Pydantic, existing `LLMProvider.generate()` interface, pytest-asyncio.

---

## File Map

| File | Change |
|---|---|
| `backend/app/config/settings.py` | Add 3 fields to `ComplianceConfig` |
| `backend/app/compliance/evaluator.py` | Add `synthesize_rule_evidence()` + `_synthesize_batch()` |
| `backend/app/compliance/alcoa.py` | Insert synthesis call |
| `backend/app/compliance/gmp.py` | Insert synthesis call |
| `backend/app/compliance/checklist.py` | Insert synthesis call |
| `backend/app/compliance/sop.py` | Insert synthesis call |
| `backend/app/compliance/cross_page/agent.py` | Insert synthesis call |
| `backend/tests/compliance/test_evidence_synthesis.py` | New unit tests |

---

## Task 1: Add Config Fields

**Files:**
- Modify: `backend/app/config/settings.py` (around line 374, after `vlm_fallback_to_text`)

- [ ] **Step 1: Add the three fields to `ComplianceConfig`**

  Open `backend/app/config/settings.py`. After the `vlm_fallback_to_text` line (currently line 373), add:

  ```python
      # Evidence synthesis — cross-page narrative evidence
      # Synthesises a 2–4 sentence narrative (with PAGE:N citations) for rules
      # that fire on more than ``evidence_synthesis_threshold`` distinct pages.
      # Disabled at zero cost by setting evidence_synthesis_enabled=false.
      evidence_synthesis_enabled: bool = True
      evidence_synthesis_threshold: int = 3   # pages — rules with ≤ this skip synthesis
      evidence_synthesis_batch_size: int = 7  # rules per LLM synthesis call
  ```

- [ ] **Step 2: Verify settings load**

  ```bash
  cd backend
  python -c "from app.config.settings import get_settings; s = get_settings(); print(s.compliance.evidence_synthesis_enabled, s.compliance.evidence_synthesis_threshold, s.compliance.evidence_synthesis_batch_size)"
  ```

  Expected output: `True 3 7`

- [ ] **Step 3: Commit**

  ```bash
  git add backend/app/config/settings.py
  git commit -m "feat(compliance): add evidence_synthesis config fields to ComplianceConfig"
  ```

---

## Task 2: Implement `_synthesize_batch()`

**Files:**
- Modify: `backend/app/compliance/evaluator.py`
- Create: `backend/tests/compliance/test_evidence_synthesis.py`

- [ ] **Step 1: Write the failing test**

  Create `backend/tests/compliance/test_evidence_synthesis.py`:

  ```python
  """Unit tests for evidence synthesis helpers."""
  from __future__ import annotations

  import json
  import pytest
  from unittest.mock import AsyncMock

  from app.compliance.evaluator import _synthesize_batch
  from app.compliance.models import RuleBatchResult, RuleEvaluation


  def _make_llm(response: str) -> AsyncMock:
      llm = AsyncMock()
      llm.generate = AsyncMock(return_value=response)
      return llm


  class TestSynthesizeBatch:
      @pytest.mark.asyncio
      async def test_returns_narrative_for_each_rule(self):
          chunk = {
              "CHE-AUD1": [(3, "Done by: S. Patel", "compliant"),
                           (5, "Checked by: R. Kumar", "compliant"),
                           (36, "Batch 400.000 Kg", "compliant"),
                           (41, "Seed Material issued", "compliant")],
          }
          payload = json.dumps({"CHE-AUD1": "Narrative citing PAGE:3 and PAGE:36."})
          llm = _make_llm(payload)

          result = await _synthesize_batch(chunk, llm)

          assert "CHE-AUD1" in result
          assert "PAGE:3" in result["CHE-AUD1"]

      @pytest.mark.asyncio
      async def test_strips_markdown_fences(self):
          chunk = {"CHE-X1": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                               (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
          fenced = "```json\n" + json.dumps({"CHE-X1": "Narrative PAGE:1."}) + "\n```"
          llm = _make_llm(fenced)

          result = await _synthesize_batch(chunk, llm)

          assert result["CHE-X1"] == "Narrative PAGE:1."

      @pytest.mark.asyncio
      async def test_ignores_keys_not_in_chunk(self):
          chunk = {"CHE-A": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                              (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
          payload = json.dumps({"CHE-A": "narrative", "CHE-Z": "should be dropped"})
          llm = _make_llm(payload)

          result = await _synthesize_batch(chunk, llm)

          assert "CHE-Z" not in result
          assert "CHE-A" in result

      @pytest.mark.asyncio
      async def test_raises_on_invalid_json(self):
          chunk = {"CHE-A": [(1, "ev", "compliant"), (2, "ev2", "compliant"),
                              (3, "ev3", "compliant"), (4, "ev4", "compliant")]}
          llm = _make_llm("not-json")

          with pytest.raises(Exception):
              await _synthesize_batch(chunk, llm)
  ```

- [ ] **Step 2: Run to verify it fails**

  ```bash
  cd backend
  pytest tests/compliance/test_evidence_synthesis.py::TestSynthesizeBatch -v 2>&1 | head -20
  ```

  Expected: `ImportError` or `AttributeError` — `_synthesize_batch` does not exist yet.

- [ ] **Step 3: Implement `_synthesize_batch()` in `evaluator.py`**

  In `backend/app/compliance/evaluator.py`, add the following near the bottom of the file (before `_build_document_scope_prompt`):

  ```python
  async def _synthesize_batch(
      chunk: dict[str, list[tuple[int, str, str]]],
      llm: LLMProvider,
  ) -> dict[str, str]:
      """Call the LLM once to synthesise cross-page evidence narratives.

      Args:
          chunk: mapping of rule_id → [(page_num, evidence_snippet, status), ...]
          llm: LLM provider (uses generate(), not generate_structured())

      Returns:
          Mapping of rule_id → synthesised narrative string.
          Keys present in the response but absent from chunk are dropped.

      Raises:
          json.JSONDecodeError: if the LLM returns non-JSON (caller handles).
      """
      rules_text_parts: list[str] = []
      for rule_id, page_entries in chunk.items():
          worst_status = max(
              page_entries,
              key=lambda x: _STATUS_SEVERITY.get(x[2], 0),
          )[2]
          page_lines = "\n".join(
              f'  PAGE:{pn}: "{ev}"'
              for pn, ev, _ in sorted(page_entries, key=lambda x: x[0])
          )
          rules_text_parts.append(
              f"Rule {rule_id}\n"
              f"Status across document: {worst_status}\n"
              f"Per-page evidence:\n{page_lines}"
          )

      prompt = (
          "You are synthesising cross-page evidence for pharmaceutical compliance rules.\n\n"
          "For each rule below, write a 2–4 sentence evidence narrative that:\n"
          "- Cites specific page numbers inline, e.g. PAGE:3 or PAGE:36\n"
          "- Names specific data points (field names, quantities, dates, lot numbers)\n"
          "- Tells a traceable story across pages — what each page contributes\n"
          "- Does NOT introduce information not present in the per-page snippets\n\n"
          'Return ONLY a valid JSON object: {"rule_id": "narrative...", ...}\n\n'
          "---\n\n" + "\n\n---\n\n".join(rules_text_parts)
      )

      raw = await llm.generate(prompt)
      text = raw.strip()
      if text.startswith("```"):
          lines = text.splitlines()
          text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

      data = json.loads(text)
      return {k: str(v) for k, v in data.items() if k in chunk}
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  cd backend
  pytest tests/compliance/test_evidence_synthesis.py::TestSynthesizeBatch -v
  ```

  Expected: 4 tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add backend/app/compliance/evaluator.py backend/tests/compliance/test_evidence_synthesis.py
  git commit -m "feat(compliance): implement _synthesize_batch() for cross-page evidence synthesis"
  ```

---

## Task 3: Implement `synthesize_rule_evidence()`

**Files:**
- Modify: `backend/app/compliance/evaluator.py`
- Modify: `backend/tests/compliance/test_evidence_synthesis.py`

- [ ] **Step 1: Write the failing tests**

  Append to `backend/tests/compliance/test_evidence_synthesis.py`:

  ```python
  from app.compliance.evaluator import synthesize_rule_evidence


  def _make_results(
      rule_id: str,
      page_evidences: list[tuple[int, str, str]],
  ) -> list[tuple[str, int, RuleBatchResult]]:
      """Build a minimal results list with one RuleEvaluation per page."""
      results = []
      for page_num, evidence, status in page_evidences:
          ev = RuleEvaluation(rule_id=rule_id, status=status, evidence=evidence)
          batch = RuleBatchResult(evaluations=[ev])
          results.append((f"batch-{page_num}", page_num, batch))
      return results


  class TestSynthesizeRuleEvidence:
      @pytest.mark.asyncio
      async def test_skips_rules_with_threshold_or_fewer_pages(self):
          """A rule with exactly 3 pages must NOT be synthesised."""
          results = _make_results(
              "CHE-A",
              [(1, "ev1", "compliant"), (2, "ev2", "compliant"), (3, "ev3", "compliant")],
          )
          llm = AsyncMock()
          llm.generate = AsyncMock()

          await synthesize_rule_evidence(results, llm, threshold=3)

          llm.generate.assert_not_called()

      @pytest.mark.asyncio
      async def test_patches_evidence_for_qualifying_rules(self):
          """A rule with 4 pages must have all its evidence fields overwritten."""
          page_entries = [
              (1, "ev1", "compliant"), (2, "ev2", "compliant"),
              (3, "ev3", "compliant"), (4, "ev4", "compliant"),
          ]
          results = _make_results("CHE-B", page_entries)
          narrative = "Synthesised narrative citing PAGE:1 and PAGE:4."
          llm = _make_llm(json.dumps({"CHE-B": narrative}))

          await synthesize_rule_evidence(results, llm, threshold=3)

          for _, _, batch in results:
              for ev in batch.evaluations:
                  if ev.rule_id == "CHE-B":
                      assert ev.evidence == narrative

      @pytest.mark.asyncio
      async def test_skips_not_applicable_pages_for_threshold(self):
          """Pages with status=not_applicable must not count toward the threshold."""
          page_entries = [
              (1, "ev1", "compliant"), (2, "ev2", "compliant"),
              (3, "", "not_applicable"), (4, "ev4", "compliant"),
          ]
          results = _make_results("CHE-C", page_entries)
          llm = AsyncMock()
          llm.generate = AsyncMock()

          # 3 applicable pages (pages 1, 2, 4) — at threshold, so skip
          await synthesize_rule_evidence(results, llm, threshold=3)

          llm.generate.assert_not_called()

      @pytest.mark.asyncio
      async def test_llm_failure_leaves_evidence_intact(self):
          """If the LLM raises, original evidence must be preserved."""
          page_entries = [
              (1, "original1", "compliant"), (2, "original2", "compliant"),
              (3, "original3", "compliant"), (4, "original4", "compliant"),
          ]
          results = _make_results("CHE-D", page_entries)
          llm = AsyncMock()
          llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

          await synthesize_rule_evidence(results, llm, threshold=3)

          evidences = [
              ev.evidence
              for _, _, batch in results
              for ev in batch.evaluations
          ]
          assert all(e.startswith("original") for e in evidences)

      @pytest.mark.asyncio
      async def test_skips_none_page_num(self):
          """Document-scope results (page_num=None) must not be counted."""
          ev = RuleEvaluation(rule_id="CHE-E", status="compliant", evidence="doc-ev")
          batch = RuleBatchResult(evaluations=[ev])
          results = [("doc-batch", None, batch)]
          llm = AsyncMock()
          llm.generate = AsyncMock()

          await synthesize_rule_evidence(results, llm, threshold=3)

          llm.generate.assert_not_called()
  ```

- [ ] **Step 2: Run to verify they fail**

  ```bash
  cd backend
  pytest tests/compliance/test_evidence_synthesis.py::TestSynthesizeRuleEvidence -v 2>&1 | head -20
  ```

  Expected: `ImportError` — `synthesize_rule_evidence` does not exist yet.

- [ ] **Step 3: Implement `synthesize_rule_evidence()` in `evaluator.py`**

  Add this function to `backend/app/compliance/evaluator.py` directly above `_synthesize_batch`:

  ```python
  async def synthesize_rule_evidence(
      results: list[tuple[str, int | None, RuleBatchResult]],
      llm: LLMProvider,
      threshold: int = 3,
      batch_size: int = 7,
  ) -> list[tuple[str, int | None, RuleBatchResult]]:
      """Synthesise cross-page evidence for rules that fire on more than *threshold* pages.

      Mutates ``ev.evidence`` in-place on all qualifying ``RuleEvaluation`` objects
      so that ``assemble_agent_report`` sees the synthesised narrative regardless of
      which page's evaluation wins the severity-wins merge.

      Rules with ``page_num=None`` (document-scope evaluations) are ignored.
      Rules with ≤ threshold distinct applicable pages are left unchanged.
      LLM failures are caught and logged — original evidence is preserved on error.
      """
      _SNIPPET_LIMIT = 400

      # Build evidence_map: rule_id → [(page_num, snippet, status)]
      evidence_map: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
      for _batch_id, page_num, result in results:
          if page_num is None:
              continue
          for ev in result.evaluations:
              if ev.status != "not_applicable" and ev.evidence:
                  evidence_map[ev.rule_id].append(
                      (page_num, ev.evidence[:_SNIPPET_LIMIT], ev.status)
                  )

      # Filter: only rules with more than threshold distinct pages
      qualifying: dict[str, list[tuple[int, str, str]]] = {
          rule_id: entries
          for rule_id, entries in evidence_map.items()
          if len({pn for pn, _, _ in entries}) > threshold
      }

      if not qualifying:
          return results

      # Chunk and gather concurrently
      rule_ids = list(qualifying.keys())
      chunks = [
          {rid: qualifying[rid] for rid in rule_ids[i: i + batch_size]}
          for i in range(0, len(rule_ids), batch_size)
      ]

      synthesised: dict[str, str] = {}
      chunk_outcomes = await asyncio.gather(
          *[_synthesize_batch(chunk, llm) for chunk in chunks],
          return_exceptions=True,
      )
      for outcome in chunk_outcomes:
          if isinstance(outcome, Exception):
              logger.warning(
                  "Evidence synthesis batch failed — leaving original evidence intact: %s",
                  outcome,
              )
          else:
              synthesised.update(outcome)

      if not synthesised:
          return results

      # Patch every matching RuleEvaluation in the results list
      for _batch_id, page_num, result in results:
          for ev in result.evaluations:
              if ev.rule_id in synthesised:
                  ev.evidence = synthesised[ev.rule_id]

      logger.info(
          "Evidence synthesis complete: %d rules synthesised across %d chunks",
          len(synthesised), len(chunks),
      )
      return results
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  cd backend
  pytest tests/compliance/test_evidence_synthesis.py -v
  ```

  Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add backend/app/compliance/evaluator.py backend/tests/compliance/test_evidence_synthesis.py
  git commit -m "feat(compliance): implement synthesize_rule_evidence() for cross-page narrative evidence"
  ```

---

## Task 4: Wire Into All Agent Classes

**Files:**
- Modify: `backend/app/compliance/alcoa.py`
- Modify: `backend/app/compliance/gmp.py`
- Modify: `backend/app/compliance/checklist.py`
- Modify: `backend/app/compliance/sop.py`
- Modify: `backend/app/compliance/cross_page/agent.py`

- [ ] **Step 1: Update the import in all four standard agent files**

  In each of `alcoa.py`, `gmp.py`, `checklist.py`, `sop.py`, add `synthesize_rule_evidence` to the import from `app.compliance.evaluator`:

  **alcoa.py** — line 11:
  ```python
  from app.compliance.evaluator import (
      assemble_agent_report,
      run_agent_evaluation,
      run_document_scope_evaluation,
      synthesize_rule_evidence,
  )
  ```

  **gmp.py** — line 11:
  ```python
  from app.compliance.evaluator import assemble_agent_report, run_agent_evaluation, synthesize_rule_evidence
  ```

  **checklist.py** — line 12:
  ```python
  from app.compliance.evaluator import assemble_agent_report, run_agent_evaluation, synthesize_rule_evidence
  ```

  **sop.py** — line 10:
  ```python
  from app.compliance.evaluator import assemble_agent_report, run_agent_evaluation, synthesize_rule_evidence
  ```

- [ ] **Step 2: Insert synthesis call in `alcoa.py`**

  In `ALCOAAgent.review_document`, replace:
  ```python
          all_results = page_results + doc_results
          return assemble_agent_report(AGENT_NAME, all_rules, all_results, pages)
  ```
  With:
  ```python
          if self._config.evidence_synthesis_enabled:
              page_results = await synthesize_rule_evidence(
                  page_results,
                  self._llm,
                  threshold=self._config.evidence_synthesis_threshold,
                  batch_size=self._config.evidence_synthesis_batch_size,
              )
          all_results = page_results + doc_results
          return assemble_agent_report(AGENT_NAME, all_rules, all_results, pages)
  ```

  Note: synthesis runs on `page_results` only, not `doc_results` (document-scope evaluations have `page_num=None` and are ignored by the function anyway, but applying it to `page_results` alone is cleaner and avoids unnecessary iteration).

- [ ] **Step 3: Insert synthesis call in `gmp.py`**

  Replace:
  ```python
          results = results + agentic_results
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```
  With:
  ```python
          results = results + agentic_results
          if self._config.evidence_synthesis_enabled:
              results = await synthesize_rule_evidence(
                  results,
                  self._llm,
                  threshold=self._config.evidence_synthesis_threshold,
                  batch_size=self._config.evidence_synthesis_batch_size,
              )
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```

- [ ] **Step 4: Insert synthesis call in `checklist.py`**

  Replace:
  ```python
          results = results + agentic_results
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```
  With:
  ```python
          results = results + agentic_results
          if self._config.evidence_synthesis_enabled:
              results = await synthesize_rule_evidence(
                  results,
                  self._llm,
                  threshold=self._config.evidence_synthesis_threshold,
                  batch_size=self._config.evidence_synthesis_batch_size,
              )
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```

- [ ] **Step 5: Insert synthesis call in `sop.py`**

  Replace:
  ```python
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```
  With:
  ```python
          if self._config.evidence_synthesis_enabled:
              results = await synthesize_rule_evidence(
                  results,
                  self._llm,
                  threshold=self._config.evidence_synthesis_threshold,
                  batch_size=self._config.evidence_synthesis_batch_size,
              )
          return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
  ```

- [ ] **Step 6: Wire into `cross_page/agent.py`**

  In `backend/app/compliance/cross_page/agent.py`, add the import near the top with other compliance imports:
  ```python
  from app.compliance.evaluator import assemble_agent_report, synthesize_rule_evidence
  ```

  Then in `review_document`, find Step 5 (`# Step 5: Assemble report`) and replace:
  ```python
          # Step 5: Assemble report
          pages = sorted({ext.get("page_num", 0) for ext in extractions})
          report = assemble_agent_report(AGENT_NAME, all_rules, batch_results, pages)
  ```
  With:
  ```python
          # Step 5: Assemble report
          pages = sorted({ext.get("page_num", 0) for ext in extractions})
          if self._config.evidence_synthesis_enabled:
              batch_results = await synthesize_rule_evidence(
                  batch_results,
                  self._llm,
                  threshold=self._config.evidence_synthesis_threshold,
                  batch_size=self._config.evidence_synthesis_batch_size,
              )
          report = assemble_agent_report(AGENT_NAME, all_rules, batch_results, pages)
  ```

  Note: the reconciliation agent stores the LLM as `self._llm` (check `cross_page/agent.py` constructor — use whichever attribute holds the `LLMProvider`). If it is named differently, update accordingly.

- [ ] **Step 7: Run the existing compliance test suite**

  ```bash
  cd backend
  pytest tests/compliance/ -v --tb=short 2>&1 | tail -30
  ```

  Expected: all existing tests pass (no regressions). The new synthesis tests also pass.

- [ ] **Step 8: Commit**

  ```bash
  git add backend/app/compliance/alcoa.py \
          backend/app/compliance/gmp.py \
          backend/app/compliance/checklist.py \
          backend/app/compliance/sop.py \
          backend/app/compliance/cross_page/agent.py
  git commit -m "feat(compliance): wire synthesize_rule_evidence() into all agent review_document() methods"
  ```

---

## Task 5: Integration Smoke Test

**Files:**
- Read: `backend/data/documents/b2921434-25f4-4b7c-8509-233a72a3dd0c/compliance_result.json`

This task verifies the output shape is correct by inspecting the existing fixture result — without re-running the full pipeline (which would require live API keys).

- [ ] **Step 1: Write a fixture-schema regression test**

  Append to `backend/tests/compliance/test_evidence_synthesis.py`:

  ```python
  import json
  from pathlib import Path


  class TestEvidenceFieldShape:
      """Schema regression: evidence field must remain a plain string."""

      def test_evidence_is_string_in_fixture(self):
          fixture = Path("data/documents/b2921434-25f4-4b7c-8509-233a72a3dd0c/compliance_result.json")
          if not fixture.exists():
              pytest.skip("Fixture file not present")
          data = json.loads(fixture.read_text())
          for finding in data.get("findings", []):
              assert isinstance(finding.get("evidence", ""), str), (
                  f"evidence field for {finding['rule_id']} is not a string"
              )
          for ar in data.get("agent_reports", []):
              for ev in ar.get("all_evaluations", []):
                  assert isinstance(ev.get("evidence", ""), str)
  ```

- [ ] **Step 2: Run it**

  ```bash
  cd backend
  pytest tests/compliance/test_evidence_synthesis.py::TestEvidenceFieldShape -v
  ```

  Expected: PASS (existing fixture already has string evidence fields — this is a non-regression baseline).

- [ ] **Step 3: Run full compliance test suite one final time**

  ```bash
  cd backend
  pytest tests/compliance/ -v --tb=short 2>&1 | tail -30
  ```

  Expected: all tests pass.

- [ ] **Step 4: Final commit**

  ```bash
  git add backend/tests/compliance/test_evidence_synthesis.py
  git commit -m "test(compliance): add evidence synthesis unit tests and schema regression baseline"
  ```

---

## Self-Review

**Spec coverage:**
- ✅ All evaluations (compliant, non-compliant, uncertain) — `_synthesize_batch` includes all statuses, only `not_applicable` is excluded from page count
- ✅ All 5 agents wired (Task 4: alcoa, gmp, checklist, sop, cross_page)
- ✅ Threshold = 3 pages (filter: `> threshold`, i.e. strictly more than 3)
- ✅ Batching: `batch_size` chunks, concurrent via `asyncio.gather`
- ✅ Error handling: `return_exceptions=True` in gather, warning log, evidence preserved
- ✅ Snippet truncation at 400 chars (`_SNIPPET_LIMIT = 400`)
- ✅ `None` page_num (document-scope) skipped in accumulation loop
- ✅ Config fields with env-var overrides (Pydantic `BaseSettings` handles `AT_COMPLIANCE__*` automatically)
- ✅ No schema changes to `RuleResult`, `ComplianceFinding`, `ComplianceReport`
- ✅ `cross_page/agent.py` uses its own LLM attribute (noted to verify name in Step 6)

**Type consistency check:**
- `synthesize_rule_evidence` returns `list[tuple[str, int | None, RuleBatchResult]]` — matches input type from `run_agent_evaluation` and is compatible with `assemble_agent_report`'s expected `list[tuple[str, int | None, RuleBatchResult]]`
- `_synthesize_batch` takes `dict[str, list[tuple[int, str, str]]]` — matches what `synthesize_rule_evidence` passes
- All `AsyncMock` usages in tests target `llm.generate` (string), not `llm.generate_structured` — matches `_synthesize_batch` which calls `llm.generate()`
