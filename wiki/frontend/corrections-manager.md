# Corrections Manager

The corrections manager (`/corrections`) provides a UI for viewing and managing OCR correction rules that the system has learned from reviewer edits across all documents.

**File:** `frontend/src/app/corrections/page.tsx`

## Overview

When reviewers correct OCR errors in the [Review Interface](./review-interface.md), the system records those corrections. Over time, patterns emerge (e.g., OCR consistently misreads "Noga" as "Naga"). The correction learning pipeline aggregates these into rules that can be automatically applied to future documents.

This page provides visibility and control over that learning process.

## Page Sections

### 1. Summary Cards

Four stat cards at the top:

| Card | Data | Source |
|------|------|--------|
| **Total Rules** | Number of learned correction rules | `GET /api/corrections/stats` |
| **Active Rules** | Rules currently enabled for auto-correction | Same |
| **Inactive Rules** | Rules disabled by admin or below threshold | Same |
| **Corrections Processed** | Total individual corrections aggregated | Same |

### 2. Confusion Chart

A horizontal bar chart (Recharts `BarChart`) showing the top 20 OCR confusion pairs, e.g.:

```
Noga → Naga  ████████████ 12
Oate → Date  ███████████  11
l → 1        ████████     8
```

Data source: `GET /api/corrections/confusion-matrix?top_n=20`

### 3. Rules Table

A sortable, filterable, paginated table of all learned correction rules:

| Column | Description |
|--------|-------------|
| Pattern | Original OCR text that gets corrected |
| Replacement | The corrected text |
| Context | Field context: `any`, `page_markdown`, or specific field ID |
| Occurrences | How many times reviewers made this correction |
| Source Docs | Number of distinct documents this appeared in |
| Confidence | Consistency ratio (0-1) |
| Status | Active/Inactive toggle |
| Created | ISO timestamp of when the rule was first created |

**Features:**
- **Search filter** — text search across pattern and replacement
- **Status filter** — All / Active / Inactive
- **Context filter** — All / any / page_markdown / specific fields
- **Sorting** — click column headers (occurrences, confidence, source_docs, created_at)
- **Pagination** — 15 rules per page with Previous/Next controls
- **Toggle** — click the toggle icon on any rule to enable/disable it via `POST /api/corrections/rules/{id}/toggle`
- **Rebuild** — "Rebuild Store" button triggers `POST /api/corrections/rebuild` to re-aggregate all corrections from all documents

## API Endpoints

| Function | Method | Endpoint | Description |
|----------|--------|----------|-------------|
| `getCorrectionRules(active?)` | GET | `/api/corrections/rules` | List rules with pagination and optional `?active=true` filter |
| `getCorrectionStats()` | GET | `/api/corrections/stats` | Aggregated statistics |
| `getConfusionMatrix()` | GET | `/api/corrections/confusion-matrix` | Top N confusion pairs |
| `toggleCorrectionRule(ruleId)` | POST | `/api/corrections/rules/{id}/toggle` | Toggle rule active state |
| `rebuildCorrections()` | POST | `/api/corrections/rebuild` | Trigger global rebuild |

## Backend

The correction rules are stored in `data/corrections/global_corrections.json` (file-based, no database). The backend router is at `backend/app/api/routes/corrections.py`, mounted at `/api/corrections`.

Key models in `backend/app/core/services/ocr_post_correction.py`:
- `CorrectionRule` — individual rule with `id`, `pattern`, `replacement`, `field_context`, `occurrences`, `confidence`, `source_docs`, `is_active`, `created_at`
- `GlobalCorrectionStore` — aggregated store with `rules[]`, `last_updated`, `total_corrections_processed`

Rules are rebuilt by scanning all `result.json` files' `review_corrections` arrays, merging `before→after` pairs across documents, and filtering by occurrence/confidence thresholds defined in `FeedbackConfig`.

## Navigation

The corrections page is accessible from the sidebar under the "Review" section (icon: `SpellCheck`). Navigation is defined in `frontend/src/components/common/nav.tsx`.

## Related Pages

- [Review Interface](./review-interface.md) — Where reviewers make corrections that feed the learning loop
- [Compliance Dashboard](./compliance-dashboard.md) — Compliance findings that may reference auto-corrected text
- [Frontend Overview](./overview.md) — Architecture and component map
- [Settings](../backend/configuration/settings.md) — `FeedbackConfig` controls for correction thresholds
- [OCR Correction Spec](../../specs/ocr-correction-learning-spec.md) — Full technical specification
