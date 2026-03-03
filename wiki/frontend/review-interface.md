# HITL Review Interface

The review interface (`/review`) provides a split-pane human-in-the-loop (HITL) view for reviewing and correcting extracted document data page-by-page.

**File:** `frontend/src/components/review/review-interface.tsx`

## Layout Structure

The interface is a full-height flexbox column with three sections:

```
┌─────────────────────────────────────────────────────────┐
│  Header: Navigation  │  Confidence Badge  │  Shortcuts  │
├────────────────────────┬────────────────────────────────┤
│                        │                                │
│   Left Pane            │   Right Pane                   │
│   Original Document    │   Extracted Data               │
│   (PDF.js viewer)      │   (Markdown / structured)      │
│                        │                                │
├────────────────────────┴────────────────────────────────┤
│  Action Bar: [✓ Approve]  [✎ Edit]  [⚑ Flag]          │
└─────────────────────────────────────────────────────────┘
```

### Header

| Element | Description |
|---------|-------------|
| **Page navigation** | `◀ Page N (X of Y) ▶` with chevron buttons |
| **Confidence badge** | Color-coded score for the current page |
| **Progress text** | `X reviewed, Y remaining` |
| **Keyboard shortcut hints** | `Enter=Approve F=Flag E=Edit Arrows=Navigate` |

### Left Pane — Original Document

- Title bar: "Original Document"
- PDF.js viewer placeholder showing the current page number
- Designed to render the source PDF page for side-by-side comparison (PDF.js integration pending)

### Right Pane — Extracted Data

- Title bar: "Extracted Data"
- Scrollable content area rendering the extracted markdown
- Displayed in a `<pre>` block with `prose` typography for readability
- Falls back to "No content extracted" when markdown is empty

### Action Bar

Three action buttons with distinct visual treatment:

| Button | Color | Keyboard | Behavior |
|--------|-------|----------|----------|
| **Approve** | Green (`bg-green-600`) | `Enter` | Calls `onApprove(pageNum)`, advances to next page |
| **Edit** | Gray / Blue toggle | `E` | Toggles edit mode (button turns blue when active) |
| **Flag** | Amber (`bg-amber-100`) | `F` | Calls `onFlag(pageNum, reason)`, advances to next page |

## Component Props

```typescript
interface ReviewPage {
  pageNum: number;
  confidence: number;
  markdown: string;
  extraction?: Record<string, unknown>;
}

interface ReviewInterfaceProps {
  docId: string;
  pages: ReviewPage[];
  onApprove: (pageNum: number) => void;
  onEdit: (pageNum: number, data: Record<string, unknown>) => void;
  onFlag: (pageNum: number, reason: string) => void;
}
```

The parent component is responsible for:
- Fetching review pages via `getReviewPages(docId)` from the [API client](./overview.md#api-client)
- Sorting pages by confidence (lowest first) to implement the **smart queue**
- Handling approve/edit/flag callbacks (persisting decisions to the backend)

## Confidence Color Coding

The `ConfidenceBadge` component (`frontend/src/components/common/confidence-badge.tsx`) applies color thresholds:

| Range | Color | Meaning |
|-------|-------|---------|
| > 0.9 | Green | High confidence — likely auto-approvable |
| 0.7 – 0.9 | Amber | Medium confidence — review recommended |
| < 0.7 | Red | Low confidence — requires human verification |

These thresholds align with the backend HITL configuration:

```yaml
hitl:
  auto_approve_threshold: 0.9
  review_threshold: 0.7
```

> See [Settings](../backend/configuration/settings.md) for full HITL configuration.

## Keyboard Shortcuts

All shortcuts are active when **not** in edit mode:

| Key | Action |
|-----|--------|
| `Enter` | Approve current page and advance |
| `F` | Flag current page and advance |
| `E` | Toggle edit mode |
| `ArrowRight` | Navigate to next page |
| `ArrowLeft` | Navigate to previous page |

The keyboard handler disables during edit mode to prevent accidental approvals:

```typescript
useEffect(() => {
  function handleKeyDown(e: KeyboardEvent) {
    if (isEditing) return;
    switch (e.key) {
      case "Enter":
        if (currentPage) onApprove(currentPage.pageNum);
        goNext();
        break;
      case "f": case "F":
        if (currentPage) onFlag(currentPage.pageNum, "Flagged via keyboard");
        goNext();
        break;
      case "e": case "E":
        setIsEditing(true);
        break;
      case "ArrowRight": goNext(); break;
      case "ArrowLeft":  goPrev(); break;
    }
  }
  window.addEventListener("keydown", handleKeyDown);
  return () => window.removeEventListener("keydown", handleKeyDown);
}, [currentPage, isEditing, onApprove, onFlag, goNext, goPrev]);
```

## Smart Queue

Pages are expected to arrive **sorted by confidence ascending** (lowest first). This means the reviewer works through the most uncertain pages first, maximizing the value of human attention. The parent component should sort before passing the `pages` prop:

```typescript
const sorted = reviewPages.sort((a, b) => a.confidence - b.confidence);
```

## Progress Tracking

The header displays a real-time progress indicator:

```
{reviewed} reviewed, {total - reviewed} remaining
```

Where `reviewed` equals the current page index (pages before the current one are considered reviewed).

## Review Flow

```
Fetch review pages (GET /api/review/{docId}/pages)
  → Sort by confidence ascending (smart queue)
  → Display first page in split-pane view
  → Reviewer:  Approve ──▶ POST approve, next page
              Edit    ──▶ Modify extraction, save
              Flag    ──▶ POST flag with reason, next page
  → When all pages reviewed → status transitions to "reviewed"
```

## Related Pages

- [Frontend Overview](./overview.md) — Architecture and component map
- [Upload Flow](./upload-flow.md) — How documents arrive at the review stage
- [Compliance Dashboard](./compliance-dashboard.md) — Post-review compliance analysis
- [Settings](../backend/configuration/settings.md) — HITL threshold configuration (`auto_approve_threshold`, `review_threshold`)
