# Reply to client feedback — 2026-04-28

Four items reported after a `git pull`. Three fixed in code, one is a call topic.

---

## 1. "When I reject the findings, the score is not improving."

**Confirmed.** This was a real bug. When you rejected a finding the persisted JSON updated `review_adjusted_score` correctly, but the **headline score** displayed in the agent scorecard was wired to a different field (`score`, which equals `model_score`) that is by design unaffected by reviewer actions. So the disk was right; the screen wasn't moving.

**Fix lands in this branch (commit follows this doc):**

- **Backend** ([backend/app/api/routes/compliance.py](backend/app/api/routes/compliance.py)) — `POST /{doc_id}/findings/{finding_id}/review` now returns the recomputed `model_score`, `review_adjusted_score`, `overall_score`, and per-agent `agent_scores` in the response payload, so the UI can refresh without a full report refetch.
- **Frontend agent scorecard** ([frontend/src/components/compliance/agent-scorecard.tsx](frontend/src/components/compliance/agent-scorecard.tsx)) — now displays `review_adjusted_score` as the headline number with the label "Review-adjusted". The original model_score is shown smaller below as "Model: NN" so reviewers can compare. When you reject a finding, the headline goes UP visibly.
- **Frontend findings table** ([frontend/src/components/compliance/findings-table.tsx](frontend/src/components/compliance/findings-table.tsx)) — exposes a new `onScoresUpdate` callback on every review action.
- **Frontend report view** ([frontend/src/components/compliance/compliance-report.tsx](frontend/src/components/compliance/compliance-report.tsx)) — owns a `liveScores` overlay state. Each review writes to it and the agent reports are recomputed from the overlay before render, so the change is immediate (no refetch).

**Pull, restart backend, rebuild frontend, retry your ALCOA reject — the number should drop from `model_score` to `review_adjusted_score` in front of you.**

---

## 2. "Vision calls failing: API key not valid"

The 400 INVALID_ARGUMENT despite a known-working `AT_VLM__GEMINI_API_KEY` was almost certainly a **`.env` not being loaded** issue — Pydantic's `env_file=".env"` is resolved relative to the **current working directory** when uvicorn starts. If you launched uvicorn from anywhere other than `backend/`, the .env was silently ignored and `settings.vlm.gemini_api_key` defaulted to empty string. Google's API replies "API key not valid" because the SDK sent `api_key=""`.

**Fixes:**

1. **Pin `.env` to an absolute path inside the backend package.** Settings now resolve `.env` to `backend/.env` regardless of cwd ([backend/app/config/settings.py:321-336](backend/app/config/settings.py#L321-L336)). Run uvicorn from anywhere; the key will load.
2. **Fail-fast at adapter construction** ([backend/app/adapters/vlm/gemini.py:49-90](backend/app/adapters/vlm/gemini.py#L49-L90)). The Gemini adapter now:
   - Strips whitespace and stray quotes from the key (a common .env-paste mistake).
   - Refuses an empty key with a clear `RuntimeError` pointing at the env var name.
   - Detects a placeholder string (`your-...`, `<replace-me>`, ends with `-here`).
   - Refuses keys shorter than 20 chars (your real Gemini key is 39).
   - Logs `Gemini VLM adapter ready: model=gemini-2.5-flash key=***xxxx (length=39)` at startup so you can see at-a-glance that the key was loaded.

**To verify on your side:**

```bash
cd backend
uv run uvicorn app.main:app --reload
# Look for the line: "Gemini VLM adapter ready: model=... key=***xxxx (length=39)"
```

If the startup log says length=0 or you get a `RuntimeError: gemini_api_key is empty`, the .env still isn't being read — share the uvicorn launch command and we'll trace from there. If the line appears with the right length and Gemini still rejects, the key itself was revoked (Google auto-revokes keys exposed publicly); generate a new one in Google AI Studio.

---

## 3. "Add some working rules for three types — individual / aggregated / cross-document"

Three (now four) working rules ship in the pilot bank — every one validates, loads, and runs end-to-end against the pilot fixture. New file: [backend/config/rules/pilot/README.md](backend/config/rules/pilot/README.md) is the one-page walkthrough.

| Type | YAML | Run |
|---|---|---|
| **Individual** (`same_page`) | [bank/alcoa_attributable_operator_signature.yaml](backend/config/rules/pilot/bank/alcoa_attributable_operator_signature.yaml) | already in your tree |
| **Cross-document** (`cross_document`) | [bank/alcoa_accurate_bpcr_weight_match.yaml](backend/config/rules/pilot/bank/alcoa_accurate_bpcr_weight_match.yaml) | already in your tree |
| **Aggregated** (`page_aggregate`) | [bank/alcoa_accurate_bpcr_step_sum.yaml](backend/config/rules/pilot/bank/alcoa_accurate_bpcr_step_sum.yaml) | **new** in this commit — sums dispensed weights across all BPCR step pages and compares to `batch_target_weight_kg` on the BMR doc |
| **Roll-up** (`checklist_synthesis`) | [bank/checklist_bpcr_step_complete.yaml](backend/config/rules/pilot/bank/checklist_bpcr_step_complete.yaml) | already in your tree |

To run all four against the pilot bank:

```bash
cd backend
uv run bmr-rules validate config/rules/pilot/bank
uv run bmr-rules fixture-run \
    --rules config/rules/pilot/bank \
    --fixture tests/bmr/fixtures/rules/fixtures/bpcr_weight_match.json
```

Or end-to-end via the existing `POST /api/bmr/runs` flow — drop a real package, the four rules fire across the agents and produce findings.

> **Note on the new aggregated rule.** It needs `batch_target_weight_kg` on a page tagged `document_role: BMR`. If your extraction doesn't yet capture that field, the rule deliberately surfaces an **UNEVALUATED** finding (with rule-source evidence pointing at the rule itself) — clearly distinct from a real failure. As soon as your extractor starts emitting the field, the rule lights up automatically. No schema change needed.

---

## 4. "BPCR section detection on pages 1–35"

Tagged for the call. The summary so far:

- Section markers in this BPCR can appear at the **top of a table** OR **mid-page** (e.g. the "Yield Calculation" section header sits in the second half of its page).
- Today's `app/compliance/segmentation.py` is LLM-driven section identification; the strategy is one-shot per document.
- For your case we need a layout-aware rather than purely-text-driven approach — likely either a per-page section-header *detector* (regex + position heuristics on the OCR layout JSON) feeding the existing segmentation, OR a VLM-backed pass that classifies each page header region.

**Topic for our call:**

1. Confirm the canonical list of section names you want detected on pages 1–35.
2. Decide between: (a) regex/heuristic pre-pass on the OCR layout, (b) per-page VLM check on the top + middle bands of each page, (c) hybrid — heuristics for confident matches, VLM only for the unsure pages.
3. Whether to ship this as a new BMR capability (rule-as-data, declarative section spec) or as an evaluator extension.

I'll bring a comparison table on (a)/(b)/(c) (latency × cost × accuracy) to the call so we can pick.

---

## Commit summary

This branch has one new commit covering items 1–3:

- `fix(client-feedback): score-after-reject + Gemini key fail-fast + page_aggregate sample`

Tests: 265 green. Frontend `tsc --noEmit` clean. Ruff clean on changed surfaces.
