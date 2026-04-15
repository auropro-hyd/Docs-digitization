# Compliance Dashboard

The compliance dashboard (`/compliance`) displays the results of automated compliance analysis for a document. It visualizes a compliance score, severity breakdown, category groupings, and individual findings with recommendations.

**File:** `frontend/src/components/compliance/compliance-dashboard.tsx`

## Component Props

```typescript
interface Finding {
  rule_id: string;
  rule_category: string;
  severity: "critical" | "major" | "minor" | "observation";
  page_num: number | null;
  description: string;
  recommendation: string;
}

interface ComplianceDashboardProps {
  docId: string;
  score: number;         // 0–100
  findings: Finding[];
}
```

The parent page fetches data via:
- `getComplianceReport(docId)` — retrieves the score and findings
- `runComplianceReview(docId)` — triggers a new compliance analysis run

## Dashboard Sections

### 1. Compliance Score Card

A large circular score display with contextual color coding:

| Score Range | Color | Background |
|-------------|-------|------------|
| >= 80 | Green (`text-green-700`) | `bg-green-50` |
| >= 60 | Amber (`text-amber-700`) | `bg-amber-50` |
| < 60 | Red (`text-red-700`) | `bg-red-50` |

Includes a subtitle: `"{N} findings across {M} categories"`.

### 2. Severity Breakdown

A 4-column grid of severity cards, each showing an icon, label, and count:

| Severity | Icon | Colors |
|----------|------|--------|
| **Critical** | `AlertCircle` | Red text, red background, red border (`text-red-600 bg-red-50 border-red-200`) |
| **Major** | `AlertTriangle` | Orange text, orange background, orange border (`text-orange-600 bg-orange-50 border-orange-200`) |
| **Minor** | `Info` | Amber text, amber background, amber border (`text-amber-600 bg-amber-50 border-amber-200`) |
| **Observation** | `Info` | Blue text, blue background, blue border (`text-blue-600 bg-blue-50 border-blue-200`) |

### 3. Category Breakdown

Findings grouped by `rule_category`. Each row shows the category label and finding count.

Supported categories map to ALCOA++ principles, GMP, and operational categories:

| Category Key | Display Label |
|-------------|---------------|
| `attributable` | Attributable |
| `legible` | Legible |
| `contemporaneous` | Contemporaneous |
| `original` | Original |
| `accurate` | Accurate |
| `complete` | Complete |
| `consistent` | Consistent |
| `enduring` | Enduring |
| `available` | Available |
| `gmp` | GMP |
| `checklist` | Checklist |
| `sop` | SOP |

These align with the compliance rule modules in the backend:
- `backend/app/compliance/alcoa.py` — ALCOA++ data integrity principles
- `backend/app/compliance/gmp.py` — Good Manufacturing Practice rules
- `backend/app/compliance/checklist.py` — Document completeness checks
- `backend/app/compliance/sop.py` — Standard Operating Procedure validation

### 4. Findings List

Each finding renders as a `FindingCard` with the severity-appropriate color scheme:

```
┌──────────────────────────────────────────────────────┐
│  ⚠ ALCOA-001  │ Attributable │              Page 3   │
│                                                      │
│  Missing operator signature on batch record entry    │
│                                                      │
│  Recommendation: Add signature field with date/time  │
└──────────────────────────────────────────────────────┘
```

Each card displays:
- **Rule ID** — e.g., `ALCOA-001`
- **Category badge** — e.g., "Attributable"
- **Page number** — when applicable
- **Description** — what the finding is about
- **Recommendation** — suggested remediation

When no findings exist, a green success banner appears: "No compliance issues found".

### 5. Visual Evidence Viewer (VLM Findings)

When a finding has `evaluation_channels` including `"vision"`, additional visual analysis UI is shown:

- **Evaluation channel badges** in the collapsed row: `TEXT`, `VLM`, or `TEXT+VLM`
- **Visual evidence text** from the VLM analysis
- **Inline thumbnail** of the page image (clickable to open full viewer)
- **Visual Evidence Viewer dialog** (`visual-evidence-viewer.tsx`) with:
  - Full page image from `GET /api/documents/{docId}/pages/{pageNum}/image`
  - Semi-transparent region overlays for `visual_regions` (normalized 0-1 coordinates)
  - Zoom controls (25%–300%) with reset
  - Evidence text sidebar with detected region list

**File:** `frontend/src/components/compliance/visual-evidence-viewer.tsx`

```
┌──────────────────────────────────────────────────────────────┐
│  👁 Visual Evidence — ALC-ATT6 (Page 12)    [TEXT] [VLM]    │
├──────────────────────────────────────────────────────────────┤
│  [🔍-] 100% [🔍+] [↺]                                      │
├──────────────────────────────┬───────────────────────────────┤
│                              │  Visual Analysis              │
│   Page Image                 │  "Single-line strikethrough   │
│   with highlighted           │   detected with initials..."  │
│   region overlays            │                               │
│                              │  Detected Regions (2)         │
│   ┌─────────────┐            │  ● strikethrough              │
│   │  [violet     │            │  ● initials                   │
│   │   overlay]   │            │                               │
│   └─────────────┘            │                               │
└──────────────────────────────┴───────────────────────────────┘
```

## Data Flow

```
/compliance page
  │
  ├── runComplianceReview(docId)    POST /api/compliance/{docId}/run
  │   └── Triggers LangGraph compliance workflow
  │
  └── getComplianceReport(docId)    GET /api/compliance/{docId}/report
      └── Returns { score, findings[] }
          │
          ▼
    ┌─────────────────────────────┐
    │   ComplianceDashboard       │
    │   ├── Score Card            │
    │   ├── Severity Grid (4)     │
    │   ├── Category Breakdown    │
    │   └── Findings List         │
    └─────────────────────────────┘
```

## Related Pages

- [Frontend Overview](./overview.md) — Architecture and API client reference
- [Review Interface](./review-interface.md) — HITL review that precedes compliance analysis
- [Corrections Manager](./corrections-manager.md) — OCR correction rules management
- [Settings](../backend/configuration/settings.md) — Compliance-related configuration
- [VLM Visual Compliance Spec](../../specs/vlm-visual-compliance-spec.md) — Full technical specification for VLM integration
