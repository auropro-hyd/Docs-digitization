# Research: Document Package Ingestion & Classification

**Feature**: 002 | **Spec Version**: v2

Decisions driving the design. Each entry follows: *Decision → Why → Alternatives rejected*.

## R-1. Classifier architecture: hybrid (heuristics + VLM tiebreak)

**Decision**: First-pass classifier uses (a) filename pattern rules and (b) first-page header
text against a YAML-declared alias table. If top-1 confidence ≥ threshold (default 0.85),
accept. Otherwise, call a VLM on the first page with the candidate-role list; accept top-1 if
confidence ≥ 0.70, otherwise flag as `needs_review`.

**Why**:
- Pilot inspection showed ~90% of packages have clean filename + header signals. Heuristics
  are deterministic, fast, auditable, and cheap.
- VLM fallback handles scanned PDFs, stamped overlays, and non-standard filenames.
- Meets SC-001 (≥ 95% correct) and SC-002 (≤ 60 s for 10-doc package) without overrunning
  VLM budget.

**Alternatives rejected**:
- Pure VLM classifier — too slow and expensive for clear cases; no accuracy gain.
- Pure heuristics — fails on scanned-only PDFs and ambiguous filenames.
- Fine-tuned classifier model — training + maintenance cost not justified at pilot scale;
  revisit post-v1.

## R-2. Boundary detection hierarchy

**Decision**: Three-tier strategy, applied in order, first success wins:

1. **Page-number header parsing** — detect `Page X of Y` / `X / Y` patterns in the top/bottom
   band of each page via OCR text; a run of consecutive pages sharing the same `Y` denotes a
   single document of length `Y`.
2. **Header-text clustering** — for pages without a numbered header, cluster consecutive
   pages by normalized first-N-lines similarity (cosine over a hashed n-gram vector). Breaks
   appear where similarity drops below a tunable threshold.
3. **Content-based classification** — for any remaining ungrouped ranges, run the hybrid
   classifier on each page and group contiguous pages that classify to the same role with
   confidence ≥ 0.75.

**Why**: Matches the explicit hierarchy requested in FR-014 / FR-015 / FR-016. Each tier is
cheap relative to the next; most concatenated BMR bundles are handled by tier 1 or 2.

**Alternatives rejected**:
- Pure content classification per page — expensive and noisy; header-based detection is
  deterministic when available.
- Manual-only — violates SC-007 (automatic resolution on the concatenated-bundle fixture).

## R-3. Summary generation split: page-level (BPCR) vs. doc-level (others)

**Decision**: Summaries are driven by `SummaryTemplate` entries in
`config/bmr/*-summary-templates.yaml`. Each template keys on role and declares a `scope` of
`page` or `document` plus a prompt/extractor spec. `BPCR` uses `scope: page` (one summary per
page, feeding page-aggregate compliance rules). All other roles use `scope: document` (single
summary per doc). No role-hardcoding in Python.

**Why**: Operationalizes Constitution IX (rule-as-data). Removes the classic temptation to
branch on role inside the summariser. Lets authors add roles via YAML without code changes.

**Alternatives rejected**:
- Always-page-level — wasteful and high-latency on SOPs / spec sheets.
- Always-doc-level — breaks BPCR page-aggregate compliance rules (Spec 001).
- Hardcoded role → scope mapping in Python — violates Constitution IX.

## R-4. Manifest verification: declarative cardinality + aliases

**Decision**: `Manifest` YAML lists expected roles with `min`, `max`, optional `aliases`
(alternate role names / filename patterns), and `required` (bool). Verifier counts
classified docs per role, applies aliases, and emits one of:

- `manifest_verified` — all roles within [min, max] and no `duplicate_canonical_bpcr`.
- `needs_review` — out-of-range counts or unclassified docs.
- `rejected` — missing required doc or duplicate canonical BPCR after override.

**Why**: Pilot manifest is small and stable; YAML is readable by QA. Alias support absorbs
naming drift across clients without code changes.

**Alternatives rejected**:
- Hardcoded Python manifest — fails Constitution VI.
- Schema-per-client with DB rows — heavier than needed; YAML + diff in git is sufficient at
  pilot scale.

## R-5. Canonical BPCR designation

**Decision**: If multiple documents classify as `BPCR`, the reviewer MUST designate exactly
one as `canonical_bpcr` before the pipeline can advance. Non-canonical BPCRs remain in the
package with `role=BPCR, canonical=False`; they are not page-aggregated but ARE subjected to
legibility + ALCOA checks.

**Why**: Pilot feedback (Akhilesh) confirmed only one BPCR drives the audit; duplicates must
be explicit, not silently ignored. Keeps audit-trail complete without inflating scope.

**Alternatives rejected**:
- Auto-pick by filename / latest timestamp — fragile; risks silent wrong-doc selection.
- Reject package outright on duplicate — overkill; reviewers may legitimately have draft +
  final copies.

## R-6. Ingest-time reviewer correction is NOT finding-level HITL

**Decision**: Classification and boundary overrides occur *before* the compliance stages and
do NOT go through the finding-review workflow. They are captured as
`ClassificationOverride` / `BoundaryOverride` append-only audit-trail entries but do not
produce `Finding` rows.

**Why**: Preserves Constitution IV (single final checkpoint). Ingest setup ≠ compliance
verdict. Legibility HITL (Spec 001) is the *only* mid-pipeline HITL and is narrow to
re-upload/proceed. Classification review is earlier, is always available, and is
setup-level.

**Alternatives rejected**:
- Model overrides as `Finding` with `severity=info` — muddies findings list and forces
  reviewers to scroll past ingest choices when reviewing compliance results.

## R-7. Storage: append-only overrides, versioned classifications

**Decision**: `ClassificationResult` rows are immutable; each override creates a new row with
`supersedes_id` pointing at the previous result and a companion `ClassificationOverride`
row with actor + reason. Queries for "current classification" use the head of each
supersedes-chain. Same pattern for boundaries.

**Why**: Constitution VIII (ALCOA+ audit trail) requires append-only history. Explicit chain
is easier to reason about than event-sourcing for this small, bounded domain.

## R-8. Malformed input handling

**Decision**: Malformed files (non-PDF, corrupt, password-protected) are rejected at the
ingest endpoint with a 422 response enumerating the rejected files. No package row is
created; no partial state persists.

**Why**: FR-008. Prevents half-baked packages from entering the pipeline.

## R-9. Concatenated PDF → virtual documents

**Decision**: After boundary detection resolves a concatenated file into N logical
documents, we persist N `DocumentRef` rows pointing at the same physical file with
`page_range=[start,end]`. Downstream stages see them as ordinary documents; OCR and rendering
respect `page_range`.

**Why**: Keeps downstream code unchanged — a `DocumentRef` is always the unit of work, never
a file. The split is a metadata-only operation; original PDFs remain intact (Constitution
VIII — originals are preserved).

## R-10. Test strategy

- **Fixtures**: `tests/fixtures/bmr/pilot-package/` (10 well-labelled PDFs),
  `concatenated-bundle.pdf` (3 docs, ~120 pages total), `malformed/`.
- **Unit**: hybrid classifier (each tier independently), manifest verifier, boundary
  detector (each tier independently), summary generator (per template scope).
- **Integration**: full `POST /api/v1/packages` → status `classification_ready` flow.
- **Regression**: `test_single_file_upload_unchanged.py` asserts `/api/v1/upload` still
  works as before.
- **Performance**: budget checks against SC-002 / SC-003 / SC-007 as pytest marks.
