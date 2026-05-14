# Evidence Consolidation — Design Spec

**Date:** 2026-05-14  
**Status:** Approved for implementation

---

## Problem

`assemble_agent_report` accumulates `page_numbers` across all pages a rule fires on, but discards all but one page's `evidence` string (severity-wins merge logic). The JSON output contains a single verbatim OCR snippet from one page, with no cross-page narrative.

The desired behaviour (reference image): a 2–4 sentence narrative that cites specific `PAGE:N` references inline, synthesising the key data points from every page the rule touches — for all evaluations (compliant, non-compliant, and uncertain).

---

## Scope

- Applies to **all evaluation statuses**: compliant, non-compliant, uncertain.
- Applies to **all agents**: alcoa, gmp, checklist, sop, reconciliation.
- **Threshold**: only rules with evidence from **more than 3 distinct pages** are synthesised. Rules with 1–3 pages keep the current single-page snippet (no extra LLM call).
- **No JSON schema changes**: `evidence` remains a `str` field on `RuleResult` and `ComplianceFinding`. The synthesised narrative replaces the value in-place before assembly.

---

## Data Flow

```
run_agent_evaluation()           → list[(batch_id, page_num, RuleBatchResult)]
synthesize_rule_evidence()  NEW  → same list, evidence patched for qualifying rules
assemble_agent_report()          → unchanged — picks up better evidence strings
```

The synthesis step is inserted in each agent's `review_document()` method, between evaluation and assembly. It is a pure async function with no side effects on the pipeline beyond mutating the `evidence` field on qualifying `RuleEvaluation` objects.

---

## New Function: `synthesize_rule_evidence()`

**Location:** `backend/app/compliance/evaluator.py`

**Signature:**
```python
async def synthesize_rule_evidence(
    results: list[tuple[str, int, RuleBatchResult]],
    llm: LLMProvider,
    threshold: int = 3,
    batch_size: int = 7,
) -> list[tuple[str, int, RuleBatchResult]]:
```

**Algorithm:**

1. Walk all `(batch_id, page_num, result)` tuples. For each `RuleEvaluation` in `result.evaluations`, accumulate into:
   ```
   evidence_map: dict[rule_id, list[(page_num, evidence, status)]]
   ```
   Only include pages where `status != "not_applicable"` and `evidence` is non-empty. Truncate each snippet to 400 characters before storing — this bounds the synthesis prompt size regardless of how many pages qualify.

2. Filter to rules where `len(evidence_map[rule_id]) > threshold` (default: 3).

3. Chunk qualifying rules into groups of `batch_size` (default: 7). Fire all chunks concurrently via `asyncio.gather`, each calling `_synthesize_batch(chunk, llm)`.

4. `_synthesize_batch()` sends one LLM call with all rules in the chunk and their per-page evidence, expecting JSON `{"rule_id": "narrative..."}` back.

5. For each synthesised narrative, overwrite `ev.evidence` on **all** `RuleEvaluation` objects for that `rule_id` across the entire results list. This ensures `assemble_agent_report`'s severity-wins logic always picks up the synthesised string regardless of which page's evaluation "wins".

6. Return the mutated results list.

**Error handling:** if a batch synthesis call fails, log a warning at `WARNING` level and leave the original evidence strings intact. Never raise — synthesis failure must not abort the pipeline.

---

## LLM Prompt (inside `_synthesize_batch()`)

```
You are synthesising cross-page evidence for pharmaceutical compliance rules.

For each rule below, write a 2–4 sentence evidence narrative that:
- Cites specific page numbers inline, e.g. "PAGE:3" or "PAGE:36"
- Names specific data points (field names, quantities, dates, lot numbers)
- Tells a traceable story across pages — what each page contributes
- Does NOT introduce information not present in the per-page snippets

Return ONLY a JSON object: {"rule_id": "narrative...", ...}

---

Rule {rule_id} — "{rule_text}"
Status across document: {overall_status}
Per-page evidence:
  PAGE:{n}: "{snippet}"
  PAGE:{n}: "{snippet}"
  ...
```

**Overall status** is the worst status seen for that rule across all pages (`non_compliant` > `uncertain` > `compliant`).

---

## Example Output

**Rule CHE-DOC8** (non-compliant) — *"Batch/Lot numbers must be complete for all dispensed materials"*

Current (single-page verbatim snippet):
```
Batch No./Lot No. entries such as "C4060193-" and blank cells for Ethyl acetate* sub-rows
```

Synthesised narrative:
```
The LIST OF RAW MATERIALS on PAGE:3 shows incomplete batch numbers — Ethyl acetate 
sub-rows carry a trailing-dash entry "C4060193-" with no suffix. PAGE:4 repeats the 
same pattern for the Seed Material dispensing sub-rows. PAGE:36 (RAW MATERIAL REQUEST 
& ISSUE) records the full lot number "C4060193-02A", confirming the BPCR entries on 
PAGE:3 and PAGE:4 are truncated, not genuinely absent. PAGE:41 (allocation record) 
lists complete lot numbers for all other materials, making the Ethyl acetate omission 
on PAGE:3–4 the isolated gap requiring correction.
```

---

## Call Sites

Insert `synthesize_rule_evidence()` after evaluation and before assembly in every agent's `review_document()`:

```python
results = await run_agent_evaluation(...)
# existing agentic postpass (checklist/alcoa only)
if self._config.evidence_synthesis_enabled:
    results = await synthesize_rule_evidence(
        results,
        self._llm,
        threshold=self._config.evidence_synthesis_threshold,
        batch_size=self._config.evidence_synthesis_batch_size,
    )
return assemble_agent_report(AGENT_NAME, all_rules, results, pages)
```

**Files touched:**
| File | Change |
|---|---|
| `evaluator.py` | Add `synthesize_rule_evidence()` and `_synthesize_batch()` |
| `alcoa.py` | Insert synthesis call after postpass |
| `gmp.py` | Insert synthesis call |
| `checklist.py` | Insert synthesis call after postpass |
| `sop.py` | Insert synthesis call |
| `cross_page/agent.py` | Insert synthesis call before `assemble_agent_report` using `cross_llm` (not `self._llm`) |
| `settings.py` | Add 3 config fields to `ComplianceConfig` |

---

## Config Changes (`ComplianceConfig` in `settings.py`)

```python
evidence_synthesis_enabled: bool = True
evidence_synthesis_threshold: int = 3   # pages — rules with ≤3 pages skip synthesis
evidence_synthesis_batch_size: int = 7  # rules per LLM call
```

Environment variable overrides follow existing convention:
- `AT_COMPLIANCE__EVIDENCE_SYNTHESIS_ENABLED=false`
- `AT_COMPLIANCE__EVIDENCE_SYNTHESIS_THRESHOLD=5`
- `AT_COMPLIANCE__EVIDENCE_SYNTHESIS_BATCH_SIZE=5`

---

## What Does NOT Change

- `RuleResult`, `ComplianceFinding`, `ComplianceReport` schemas — no new fields
- `assemble_agent_report` — no changes
- `compliance_result.json` shape — `evidence` remains a `str`
- Per-page evaluation prompts — no changes
- Report renderer — the richer `evidence` string flows through automatically

---

## Testing

- Unit test `synthesize_rule_evidence()` with a mocked LLM: verify rules with ≤3 pages are skipped, rules with >3 pages get patched, LLM failure leaves evidence intact.
- Integration test: run against `b2921434-25f4-4b7c-8509-233a72a3dd0c` fixture, assert synthesised evidence contains `PAGE:` citations and is longer than the original snippet.
- Regression: assert `assemble_agent_report` output shape is unchanged.
