# OCR Correction Learning & Feedback Loop — Technical Specification

**Status:** Tier 1-2 Implemented | Tier 3 deferred  
**Author:** Auto-generated  
**Date:** 2026-04-14 | **Updated:** 2026-04-15  
**Depends on:** Existing correction capture infrastructure in `review.py`, `feedback_learning.py`

---

## 1. Motivation

The client expects that when reviewers correct OCR errors, the system should **learn from those corrections** over time. Today, the codebase captures corrections but never applies them:

- `review_corrections` — append-only log of before/after edits per document
- `correction_dictionary` — aggregated field-level corrections with top pairs
- `ocr_confusion_map` — frequency map of OCR error patterns (`"Noga -> Naga"`)
- `retraining_trigger` — heuristic flag for when enough corrections accumulate

**None of these are consumed** by the OCR pipeline, compliance evaluation, or post-processing. The feedback loop is open — corrections go in but never come back out.

This spec defines three tiers of learning, from simplest to most sophisticated:

1. **Tier 1: Runtime post-correction** — Apply known corrections to OCR output automatically
2. **Tier 2: Cross-document correction store** — Aggregate corrections across all documents
3. **Tier 3: Azure DI custom model training** — Use corrections as training signal for custom models

---

## 2. Current State Analysis

### 2.1 What's Already Captured

| Artifact | Location | Scope | Format |
|----------|----------|-------|--------|
| `review_corrections` | `result.json` per doc | Per-document | `[{source, page_num, field_id, before_value, after_value, criticality}]` — `source` is `"page_edit"` or `"component_action"` |
| `correction_dictionary` | `result.json` per doc | Per-document | `{field_updates: {field_id: int}, top_pairs: {"before -> after": int}}` — `field_updates` maps field IDs to correction counts; `top_pairs` has at most 50 entries |
| `ocr_confusion_map` | `result.json` per doc | Per-document | `{"before -> after": int, ...}` — full Counter (no cap), superset of `top_pairs` |
| `correction_summary` | `result.json` per doc | Per-document | `{total_corrections: int, critical_corrections: int, critical_correction_rate: float}` |
| `retraining_trigger` | `result.json` per doc | Per-document | `{should_trigger_retraining: bool, thresholds: {correction_rate_proxy, critical_correction_rate, min_corrections}, metrics: {total_corrections, correction_rate_proxy, critical_correction_rate}, generated_at: str}` |
| Quality dashboard | `GET /api/documents/quality-dashboard` | Cross-document | Only exposes `{total_corrections: int, documents_triggered_for_retraining: int}` under `feedback_loop` — does **not** return correction rules, dictionary, or confusion map |

### 2.2 What's Missing

| Gap | Description |
|-----|-------------|
| **No cross-doc correction store** | Each document has its own correction data; no global aggregation |
| **No runtime application** | Corrections are never applied to new OCR output |
| **`retraining_trigger` is inert** | Flag is set but nothing acts on it |
| **`custom_model_enabled` is unused** | Defined in `AzureDIConfig` (default `False`) but no Python code reads it; shadow model uses separate `custom_model_shadow_enabled` flag |
| **`extract_custom_model` not implemented** | Azure DI adapter doesn't have this method; `merge_azure_di_results` guards with `hasattr()` so it skips safely |
| **`criticality` always "major"** | The critical correction rate is always 0 |
| **No normalization layer uses corrections** | `field_normalization.py` is deterministic, not learned |

### 2.3 Existing Infrastructure to Build On

| Component | File | What It Does |
|-----------|------|-------------|
| `_append_correction()` | `review.py` | Logs corrections, rebuilds all artifacts via `build_correction_artifacts` and `evaluate_retraining_trigger`. Called from page-edit and component-action endpoints. |
| `_lookup_component_value()` | `review.py` | Searches **only** `results["extractions"]` — tries `content_component_id` → `markdown`, then KV `component_id` → `normalized_value`/`value`, then signature `component_id` → `status`. Does **not** search top-level `key_value_pairs`/`signatures`. |
| `build_correction_artifacts()` | `feedback_learning.py` | Returns `{correction_dictionary, ocr_confusion_map, summary}`. Skips rows where before==after. Uses `"unknown_field"` as default field_id. |
| `evaluate_retraining_trigger()` | `feedback_learning.py` | Params: `threshold_correction_rate`, `threshold_critical_rate`, `min_corrections_for_trigger`. `correction_rate_proxy = min(1.0, total / 250)`. Trigger requires both volume AND rate threshold. |
| `normalize_kv_record()` | `field_normalization.py` | Deterministic field cleaning — returns copy with `field_id`, `raw_value`, `normalized_value`, etc. Called in `merge_azure_di_results`. Insertion point for learned corrections is **after** this call. |
| `custom_model_profiles.yaml` | config | Per-template-family model routing |
| Shadow comparison hooks | `nodes.py` | Gate: `custom_model_shadow_enabled AND hasattr(engine, "extract_custom_model")`. Uses `summarize_shadow_delta` (imported from `custom_model_shadow`). |
| Quality dashboard | `documents.py` | Only aggregates `total_corrections` (sum) and `documents_triggered_for_retraining` (count where `should_trigger_retraining==True`) |

---

## 3. Architecture: Three Tiers

### Tier 1: Runtime Post-Correction (Quick Win)

**Goal:** Automatically apply known OCR error corrections to new documents without any model retraining.

```
OCR Output → Post-Correction Layer → Corrected Text → Compliance
                    ↑
          Global Correction Store
          (aggregated from all documents)
```

#### 3.1.1 Global Correction Store

Create a persistent store of learned corrections, aggregated across all reviewed documents:

```
backend/
├── data/
│   └── corrections/
│       └── global_corrections.json    # Aggregated correction rules
├── app/
│   └── core/
│       └── services/
│           └── ocr_post_correction.py # NEW: apply corrections to OCR output
```

**Schema:**

```python
class CorrectionRule(BaseModel):
    """A learned OCR correction rule."""
    pattern: str           # Original OCR text (exact match or regex)
    replacement: str       # Corrected text
    field_context: str     # Where this correction applies (e.g., "signature", "date", "any")
    occurrences: int       # How many times reviewers made this correction
    confidence: float      # Derived from consistency (same before→after across docs)
    source_docs: int       # Number of distinct documents this correction appeared in

class GlobalCorrectionStore(BaseModel):
    """Aggregated corrections across all documents."""
    rules: list[CorrectionRule] = []
    last_updated: str = ""
    total_corrections_processed: int = 0
    min_occurrences_to_apply: int = 3    # Only apply if seen 3+ times
    min_source_docs: int = 2            # Must appear in 2+ distinct documents
```

#### 3.1.2 Aggregation Pipeline

Triggered on each document review save AND as a batch job:

```python
async def rebuild_global_corrections() -> GlobalCorrectionStore:
    """Scan all result.json files, aggregate review_corrections entries,
    and build rules that pass confidence thresholds."""

    # 1. Walk all doc dirs, read review_corrections from each result.json
    #    (correction_dictionary.top_pairs can also be used for pre-aggregated counts)
    # 2. Merge before→after pairs across documents
    # 3. Filter: only keep pairs with occurrences >= min_occurrences
    #    AND source_docs >= min_source_docs
    # 4. Compute confidence = consistency ratio
    #    (same replacement for same pattern / total corrections for that pattern)
    # 5. Write global_corrections.json
```

#### 3.1.3 Post-Correction Application

Apply corrections during OCR merge, **after** the adapter returns but **before** compliance evaluation:

```python
class OCRPostCorrector:
    """Apply learned corrections to OCR output."""

    def __init__(self, store: GlobalCorrectionStore):
        self._rules = {r.pattern: r for r in store.rules if r.confidence >= 0.8}

    def correct_markdown(self, markdown: str) -> tuple[str, list[dict]]:
        """Apply corrections, return (corrected_text, applied_corrections)."""
        applied = []
        for pattern, rule in self._rules.items():
            if pattern in markdown:
                markdown = markdown.replace(pattern, rule.replacement)
                applied.append({
                    "pattern": pattern,
                    "replacement": rule.replacement,
                    "confidence": rule.confidence,
                })
        return markdown, applied

    def correct_kv_value(self, field_key: str, value: str) -> tuple[str, list[dict]]:
        """Apply field-context-specific corrections to a KV value."""
        # Filter rules by field_context matching field_key
        ...
```

**Integration point:** In `merge_azure_di_results` (or the Datalab equivalent), after sanitization but before writing to `extractions`:

```python
if settings.feedback.auto_correct_enabled:
    corrector = OCRPostCorrector(load_global_corrections())
    page_markdown, applied = corrector.correct_markdown(page_markdown)
    if applied:
        extraction["auto_corrections"] = applied
```

#### 3.1.4 Configuration

```python
class FeedbackConfig(BaseModel):
    """OCR correction learning settings."""
    auto_correct_enabled: bool = False        # Opt-in; off by default
    min_correction_occurrences: int = 3       # Apply only if seen 3+ times
    min_correction_source_docs: int = 2       # Must appear in 2+ documents
    min_correction_confidence: float = 0.8    # Consistency threshold
    rebuild_on_review_save: bool = True       # Rebuild store on each save
    correction_store_path: str = "data/corrections/global_corrections.json"
```

**Environment variables:**

```bash
AT_FEEDBACK__AUTO_CORRECT_ENABLED=false
AT_FEEDBACK__MIN_CORRECTION_OCCURRENCES=3
AT_FEEDBACK__MIN_CORRECTION_SOURCE_DOCS=2
AT_FEEDBACK__MIN_CORRECTION_CONFIDENCE=0.8
```

---

### Tier 2: Cross-Document Correction Store (Medium Effort)

**Goal:** Build a searchable, queryable correction knowledge base with UI visibility.

#### 3.2.1 Database-Backed Store

Move from file-based to database:

```sql
CREATE TABLE ocr_corrections (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    page_num INT,
    field_id TEXT,
    before_value TEXT NOT NULL,
    after_value TEXT NOT NULL,
    field_context TEXT,        -- "page_markdown", "kv_field", "signature"
    criticality TEXT DEFAULT 'major',
    reviewer TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE correction_rules (
    id UUID PRIMARY KEY,
    pattern TEXT NOT NULL,
    replacement TEXT NOT NULL,
    field_context TEXT,
    occurrences INT DEFAULT 1,
    source_docs INT DEFAULT 1,
    confidence FLOAT DEFAULT 0.5,
    is_active BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (pattern, replacement, field_context)
);
```

#### 3.2.2 API Endpoints

```
GET  /api/corrections/rules                    # List all learned rules
GET  /api/corrections/rules?active=true        # Only active rules
POST /api/corrections/rules/{id}/toggle        # Enable/disable a rule
POST /api/corrections/rebuild                  # Trigger global rebuild
GET  /api/corrections/stats                    # Correction analytics
GET  /api/corrections/confusion-matrix         # Visual confusion patterns
```

#### 3.2.3 Frontend: Correction Manager

A new page at `/corrections` showing:
- Table of learned correction rules with toggle to activate/deactivate
- Confusion matrix heatmap (common OCR errors)
- Correction trends over time
- Per-reviewer correction stats
- Retraining trigger status

---

### Tier 3: Azure DI Custom Model Training (Long-term)

**Goal:** Use correction data as ground truth to fine-tune Azure DI models.

#### 3.3.1 Training Data Export

```python
async def export_training_data(doc_ids: list[str]) -> Path:
    """Export corrected documents as Azure DI training format.

    For each document with review_corrections:
    1. Original PDF (input)
    2. Corrected field values (labels)
    3. Page-level corrected markdown (for layout model evaluation)
    """
```

#### 3.3.2 Custom Model Workflow

```
1. Corrections accumulate → retraining_trigger fires
2. Export training data (PDF + corrected labels)
3. Upload to Azure Blob Storage (Azure DI training requirement)
4. Call Azure DI Build Model API (custom template or neural)
5. Obtain custom model ID
6. Configure in settings (per template_family from custom_model_profiles.yaml)
7. Shadow mode: run custom model alongside prebuilt-layout
8. Compare: summarize_shadow_delta (already implemented in nodes.py)
9. Promote: switch primary model when delta is positive
```

#### 3.3.3 Implementation: Wire Existing Dead Code

The codebase already has scaffolding that needs activation:

1. **`custom_model_enabled`** in `AzureDIConfig` → currently unused in code; needs to be wired to branch `begin_analyze_document` model_id
2. **`extract_custom_model`** → implement on `AzureDIOCRAdapter` (the `hasattr` guard in `merge_azure_di_results` already supports it)
3. **`custom_model_profiles.yaml`** → `enable_custom_model` per family already defined
4. **`custom_model_shadow_enabled`** + `hasattr(engine, "extract_custom_model")` → already gated in `merge_azure_di_results`; `summarize_shadow_delta` is imported and used when shadow run succeeds
5. **Rollback guardrails** (`rollback_*` settings) → already configured in `AzureDIConfig`

---

## 4. Criticality Classification

Currently, all corrections get `criticality: "major"`. Fix to enable meaningful retraining triggers:

| Edit Type | Criticality | Example |
|-----------|-------------|---------|
| Spelling/OCR artifact | minor | "Noga" → "Naga" |
| Missing content | major | Empty field → "S. Patel" |
| Wrong value | critical | "15/03/2025" → "15/03/2024" |
| Structural error | critical | Table row merged/missing |

**Implementation:** Infer criticality from the edit:

```python
def infer_criticality(before: str, after: str, field_id: str) -> str:
    before_stripped = before.strip()
    after_stripped = after.strip()

    if not before_stripped and after_stripped:
        return "critical"      # Missing content filled in
    if _is_date(before_stripped) and _is_date(after_stripped):
        return "critical"      # Date correction (regulatory impact)
    if _edit_distance(before_stripped, after_stripped) <= 2:
        return "minor"         # Small typo/OCR artifact
    return "major"             # Default
```

---

## 5. UI: Correction Transparency

### 5.1 Auto-Correction Indicator

When auto-correction is applied (Tier 1), show it in the review interface:

- Highlighted text with tooltip: "Auto-corrected: 'Noga' → 'Naga' (seen 5x across 3 docs)"
- Option to reject the auto-correction and revert

### 5.2 Correction Badge in Compliance

When compliance evaluates auto-corrected text, tag the finding:

```typescript
interface ComplianceFinding {
  // ... existing fields
  auto_corrections_applied?: { pattern: string; replacement: string; confidence: number }[];
}
```

---

## 6. Implementation Phases

### Phase 1: Tier 1 — Runtime Post-Correction (COMPLETED)
- [x] `FeedbackConfig` in settings.py — `auto_correct_enabled`, thresholds, store path
- [x] `rebuild_global_corrections()` — scans all `result.json` files, aggregates `review_corrections`, applies occurrence/source-doc/confidence thresholds
- [x] `OCRPostCorrector` class — `correct_markdown()` and `correct_kv_value()` with longest-pattern-first matching and field-context filtering
- [x] `CorrectionRule` and `GlobalCorrectionStore` Pydantic models
- [x] `load_global_corrections()` / `save_global_corrections()` file-based persistence
- [x] Integration into `merge_azure_di_results` — gated by `settings.feedback.auto_correct_enabled`, runs per-page on markdown and KV values, records `auto_corrections` on each extraction
- [x] Fix `criticality` inference — `infer_criticality()` implemented (empty->filled = critical, date changes = critical, small edit distance = minor, else major)
- [x] `.env.example` documentation — `AT_FEEDBACK__*` variables documented
- [ ] Unit tests for correction application — **not yet written**

### Phase 2: Tier 2 — Correction Store + API (COMPLETED — file-based, no DB)

Implemented as file-based store with API endpoints (consistent with existing app architecture):

- [x] `CorrectionRule` extended with `id` (UUID), `is_active` (toggleable), `created_at` (ISO timestamp) — `ocr_post_correction.py`
- [x] `OCRPostCorrector` updated to filter by `is_active` — only active rules are applied
- [x] API route `backend/app/api/routes/corrections.py` mounted at `/api/corrections` — 6 endpoints:
  - `GET /rules` — paginated list with `?active=true` filter, `?skip=0&limit=50`
  - `GET /rules/{rule_id}` — single rule by ID
  - `POST /rules/{rule_id}/toggle` — enable/disable a rule
  - `POST /rebuild` — trigger global rebuild manually
  - `GET /stats` — total rules, active/inactive counts, corrections processed, rules by field context, top confusion pairs
  - `GET /confusion-matrix` — top N confusion pairs for visualization
- [x] Router registered in `main.py` at `/api/corrections`
- [x] Frontend API client functions in `lib/api.ts` — `getCorrectionRules`, `toggleCorrectionRule`, `rebuildCorrections`, `getCorrectionStats`, `getConfusionMatrix`
- [x] Frontend corrections manager page at `/corrections` — summary cards, sortable/filterable/paginated rules table with active toggle, confusion chart (recharts)
- [x] Navigation link added to sidebar ("Corrections" in Review section)
- [ ] Unit tests for correction API endpoints — not yet written

Note: Database-backed store (SQL tables as described in spec section 3.2.1) was intentionally deferred. The app uses filesystem JSON for all state, and introducing SQLAlchemy/Alembic would break the existing architecture. The file-based `GlobalCorrectionStore` with API endpoints achieves the same functional goals.

### Phase 3: Tier 3 — Custom Model Training (DEFERRED)
- [ ] Training data export pipeline
- [ ] Implement `extract_custom_model` on Azure DI / Data Lab adapter
- [ ] Wire `custom_model_enabled` to switch model ID
- [ ] Shadow comparison dashboard
- [ ] Rollback guardrails activation
- [ ] Custom model promotion workflow

---

## 7. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| False corrections propagated | Wrong text applied to new docs | High confidence threshold + min occurrence + multi-doc requirement |
| Performance of correction scan | Slow if many rules | Index patterns; limit to top 500 rules |
| Correction conflicts | Same pattern → different replacements | Use highest-confidence replacement; flag conflicts for review |
| Azure DI custom model cost | Training costs + API pricing | Shadow mode validates before promotion |
| Privacy: corrections contain PHI | Regulatory concern | Corrections store inherits same access controls as result.json |

---

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| Auto-correction accuracy | > 95% of applied corrections are accepted by reviewers |
| Reviewer time saved | 20% reduction in time spent on OCR corrections |
| Retraining trigger accuracy | < 5% false positive rate |
| Custom model improvement | > 5% word-level accuracy gain on handwritten fields |
