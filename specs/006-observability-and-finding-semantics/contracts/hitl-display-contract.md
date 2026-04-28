# Contract — HITL Display

**Satisfies**: FR-011 (label rename), FR-012 (neutral palette for model-confirmed), FR-013 (explicit `unknown`), SC-003 (severity dominates), SC-004 (no silent approval).

Defines the exact mapping between the persisted `hitl_status` wire value and what the frontend must render. Backend and frontend agree on this document; both sides have tests that verify the mapping.

---

## Wire values

`hitl_status` on `ComplianceFinding` is one of:

- `"auto_approved"` — legacy wire value. Emitted by the current evaluator. Means "the model's confidence in this non-compliance finding exceeded the auto-approve threshold; no reviewer action required before export."
- `"system_confirmed"` — reserved. Treated identically to `"auto_approved"`. Introduced to make eventual wire migration painless — the reader already handles the new name.
- `"needs_review"` — awaiting reviewer decision.
- `"user_approved"` — reviewer explicitly approved the finding.
- `"user_rejected"` — reviewer rejected the finding (does not count toward the review-adjusted score).
- `"user_modified"` — reviewer edited severity / description / etc.
- `"unknown"` — NEW in Spec 006. Explicit missing-data marker. Emitted by the read path when a finding is deserialised without `hitl_status` set.

No other value is valid. Readers that encounter an unknown string must treat it as `"unknown"` (never fall back to `"auto_approved"`).

---

## Display mapping

| Wire | Display label | Palette | Icon (lucide) | Tooltip |
|---|---|---|---|---|
| `auto_approved` | "System-confirmed" | `neutral` | `ShieldCheck` | "Model-only review — high confidence, no reviewer needed." |
| `system_confirmed` | "System-confirmed" | `neutral` | `ShieldCheck` | (same) |
| `needs_review` | "Needs review" | `warning` | `Eye` | "Awaiting reviewer confirmation." |
| `user_approved` | "Reviewer-approved" | `success` | `ThumbsUp` | "Reviewer confirmed as a valid finding." |
| `user_rejected` | "Reviewer-rejected" | `destructive` | `ThumbsDown` | "Reviewer rejected as spurious; excluded from scoring." |
| `user_modified` | "Reviewer-modified" | `info` | `Pencil` | "Reviewer edited severity / description." |
| `unknown` | "Unknown" | `neutral` | `CircleHelp` | "HITL state missing — data integrity issue." |

---

## Palettes (Tailwind mapping)

The palette names above resolve to specific Tailwind class bundles. A unit test verifies `neutral` and `success` resolve to different background colours via `getComputedStyle`.

| Palette | Text | Border | Background |
|---|---|---|---|
| `success` | `text-success` | `border-success/30` | `bg-success/10` |
| `warning` | `text-warning` | `border-warning/20` | `bg-warning/5` |
| `destructive` | `text-destructive` | `border-destructive/20` | `bg-destructive/5` |
| `info` | `text-blue-600 dark:text-blue-400` | `border-blue-300 dark:border-blue-800` | `bg-blue-50 dark:bg-blue-900/10` |
| `neutral` | `text-muted-foreground` | `border-muted-foreground/20` | `bg-muted/40` |

**Forbidden** (test-enforced): `system_confirmed` / `auto_approved` / `unknown` MUST NOT use the `success` palette. Any code path that produces a badge with `{wire: "auto_approved", palette: "success"}` is a contract violation.

---

## Reader invariants (both sides)

**Backend** (`backend/app/api/routes/compliance.py:_score_from_findings`):

```python
# BEFORE:
status = str(f.get("hitl_status", "auto_approved"))   # WRONG — invisible escalation

# AFTER:
raw = f.get("hitl_status")
status = str(raw) if raw else "unknown"                # Explicit
```

Scoring treats `unknown` as "excluded from penalty" by default (a missing state cannot be confidently penalised). Caller that wants to include them must opt in with `include_unknown=True`.

**Frontend** (`frontend/src/components/compliance/findings-table.tsx:HITLBadge`):

```ts
// BEFORE:
const config = HITL_CONFIG[status] || HITL_CONFIG.auto_approved;  // WRONG

// AFTER:
const normalized: HitlWireValue = HITL_CONFIG[status] ? status : "unknown";
const config = HITL_CONFIG[normalized];
```

The fallback is always `unknown`, never `auto_approved`.

---

## Migration notes

Legacy persisted reports at `backend/data/documents/*/compliance_result.json` use `auto_approved`. This contract keeps the wire value — all of those reports continue to work unchanged.

When the team is ready for the wire migration:

1. Emit `system_confirmed` from the evaluator.
2. Add a one-shot rewrite script that rewrites persisted reports.
3. Drop the `auto_approved` case from `HITL_CONFIG` in a later major release.

Scheduled: not part of Spec 006.
