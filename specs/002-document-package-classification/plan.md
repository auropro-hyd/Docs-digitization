# Implementation Plan: Document Package Ingestion & Classification

**Branch**: `002-document-package-classification` | **Date**: 2026-04-17 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-document-package-classification/spec.md`

## Summary

Extend the existing upload/ingestion subsystem to accept a **set** of documents as one
logical **package**, run classifier + boundary detection + manifest verification + summary
generation, and hand off a ready-to-audit package to the BMR pipeline (Spec 001).

No new engines; new capabilities wired into the existing document-store + OCR pipeline:
`boundary_detect.v1`, `page_summary.v1`, `doc_summary.v1` (shared with Spec 001), plus a
`DocumentClassifier` port + adapter (hybrid: filename / header heuristics + lightweight VLM
call). Manifests and summary templates are YAML under `backend/config/bmr/`.

## Technical Context

**Language/Version**: Python 3.11+ backend; TypeScript 5.x / Node 20+ frontend.
**Primary Dependencies**: FastAPI (multipart upload), pypdfium2 (rendering), existing OCR
ports (Azure DI, Marker, Docling, Data Lab), existing VLM providers, pydantic v2, SQLAlchemy
async. Frontend: existing upload component; new package-overview / classification-review
pages.
**Storage**: Filesystem for original files + rendered page artefacts; Postgres for package
metadata, classification results, manifest-verification state, boundaries, and summaries.
**Testing**: pytest + pytest-asyncio (backend); Playwright/vitest (frontend).
**Target Platform**: Linux server, Python 3.11+, Postgres 15+, Node 20+.
**Project Type**: Web application.
**Performance Goals** (from spec SC-002, SC-003, SC-007):
- Classification + manifest verification for 10-doc pilot package ≤ 60 s.
- Reviewer override → manifest re-verify ≤ 3 s.
- Boundary detection on concatenated-PDF fixture completes without degraded-mode fallback
  for method A (page header) and method B (header cluster) in under 30 s.
**Constraints**:
- Reuse existing OCR ports (Constitution VII); do NOT fork a new document pipeline.
- All client-specific role sets, boundary hints, summary templates live in YAML
  (Constitution VI).
- Malformed input (non-PDF, corrupt, password-protected) rejected at ingest with no partial
  package persisted (FR-008).
**Scale/Scope**: Up to 25 documents / 500 pages / 250 MB per package (v1 bound). Concatenated
PDFs up to 400 pages.

## Constitution Check

Reference: `.specify/memory/constitution.md` (v1.1.0).

- [x] **I. Leverage-first**: Reuses existing upload, document-store, OCR engines, and VLM
  providers. New code is classifier adapter + boundary detect capability + summary
  capabilities + manifest verifier. No existing subsystem is replaced.
- [x] **II. 5-stage soft gates**: This feature lives entirely in `INGEST` and
  `LEGIBILITY_AND_CLASSIFICATION`. It does not cross into downstream compliance stages. Its
  gate (manifest verified, boundaries resolved, canonical BPCR designated) is a precondition
  for Spec 001's Structured-Extraction stage.
- [x] **III. Capability-first**: `boundary_detect.v1`, `page_summary.v1`, `doc_summary.v1`
  are atomic capabilities. Classification remains behind a `DocumentClassifier` port with a
  hybrid adapter.
- [x] **IV. Single final checkpoint**: No HITL introduced. Reviewer correction at the
  classification-review step and boundary correction step are NOT audit HITL — they are
  ingest-time setup. These are not finding-level and do not breach the single-final-checkpoint
  rule.
- [x] **V. Evidence-bound findings**: `ANCHOR_MISSING` and `DUPLICATE_CANONICAL_BPCR` findings
  carry document refs as evidence. Manifest-verification findings reference the affected
  document ids.
- [x] **VI. Configurable framework**: Manifest roles, expected cardinality, boundary hints,
  summary templates are YAML under `backend/config/bmr/`. No pilot specifics in Python.
- [x] **VII. Existing framework backbone**: No changes to the single-document upload path.
  Multi-document upload is a new endpoint; existing `/api/v1/upload` remains for legacy.
- [x] **VIII. ALCOA+ audit trail**: `ClassificationOverride`, `BoundaryOverride`, manifest
  verification state transitions all captured with actor + server-assigned timestamp.
- [x] **IX. Rule-as-data**: Summary templates and manifest role declarations are YAML-loaded.
  Classification is hybrid (heuristics + VLM) but its knobs (candidate role list, confidence
  threshold) are configuration.

No violations.

## Project Structure

```text
specs/002-document-package-classification/
├── spec.md
├── plan.md                       # this
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── rest-api.md
│   ├── classifier-port.md
│   └── capability-contract.md
└── checklists/requirements.md
```

```text
backend/
├── app/
│   ├── upload/                              # EXISTING — extended
│   │   ├── single_file.py                   # unchanged
│   │   └── package.py                       # NEW — multi-file / zip handler
│   ├── capabilities/
│   │   ├── boundary_detect.v1.py            # NEW (shared with spec 001)
│   │   ├── page_summary.v1.py               # NEW (shared with spec 001)
│   │   └── doc_summary.v1.py                # NEW (shared with spec 001)
│   ├── classification/                      # NEW subpackage
│   │   ├── __init__.py
│   │   ├── classifier_port.py               # port: DocumentClassifier
│   │   ├── hybrid_classifier.py             # adapter: filename + header + VLM tiebreak
│   │   ├── confidence_policy.py             # flag-for-review threshold loader
│   │   └── manifest_verifier.py             # validates package against manifest
│   ├── core/
│   │   ├── models/
│   │   │   ├── document_package.py          # NEW
│   │   │   ├── document_ref.py              # NEW
│   │   │   ├── manifest.py                  # NEW
│   │   │   ├── classification_result.py     # NEW
│   │   │   ├── classification_override.py   # NEW
│   │   │   ├── boundary_override.py         # NEW
│   │   │   ├── summary_template.py          # NEW
│   │   │   └── summary.py                   # NEW
│   │   └── ports/
│   │       ├── package_store.py             # NEW
│   │       ├── manifest_store.py            # NEW
│   │       └── summary_store.py             # NEW
│   ├── adapters/
│   │   └── storage/
│   │       ├── postgres_package.py          # NEW
│   │       ├── postgres_manifest.py         # NEW (+ YAML loader)
│   │       └── postgres_summary.py          # NEW
│   └── api/
│       └── routers/
│           └── packages.py                  # NEW
├── config/
│   └── bmr/
│       ├── pilot-manifest.yaml              # NEW
│       └── pilot-summary-templates.yaml     # NEW
└── tests/
    ├── upload/test_package_upload.py
    ├── classification/
    │   ├── test_hybrid_classifier.py
    │   ├── test_manifest_verifier.py
    │   └── test_override_audit_trail.py
    ├── capabilities/
    │   ├── test_boundary_detect.py
    │   ├── test_page_summary.py
    │   └── test_doc_summary.py
    ├── regression/test_single_file_upload_unchanged.py   # Constitution VII
    └── fixtures/bmr/
        ├── pilot-package/                    # 10 PDFs, one per role
        ├── concatenated-bundle.pdf           # 3 logical docs
        └── malformed/{non-pdf.docx, corrupt.pdf, password-protected.pdf}
```

**Structure Decision**: Web-app structure preserved. Multi-doc upload is a new endpoint on
an existing router; classification lives in a new `app/classification/` subpackage behind a
port. Manifests + summary templates are YAML files loaded at startup.

## Complexity Tracking

No violations. One clarification:

| Item | Why | Simpler Alternative Considered |
|---|---|---|
| Hybrid classifier (heuristics + VLM tiebreak) rather than pure VLM | Filename + header heuristics alone hit ~90% on pilot; VLM is expensive and only needed for ambiguous cases. Hybrid meets SC-001 (95%) and SC-002 (60 s) simultaneously. | Pure VLM classifier. Rejected: 3–5× latency and cost per package with no accuracy gain on clear cases. |

## Post-Design Constitution Re-Check

- [x] **I**: Existing OCR ports + document-store reused; new code is classifier + capabilities.
- [x] **II**: Feature lives in INGEST + LEGIBILITY_AND_CLASSIFICATION only.
- [x] **III**: Boundary + summaries are atomic capabilities (`contracts/capability-contract.md`).
- [x] **IV**: No audit-finding-level HITL added; ingest-time reviewer correction is a setup action, not a compliance action.
- [x] **V**: Findings emitted by this stage carry evidence refs (`data-model.md §5.1`).
- [x] **VI**: `config/bmr/*.yaml` is the only place pilot specifics live (`rest-api.md §6`).
- [x] **VII**: Existing single-file upload endpoint unchanged; `test_single_file_upload_unchanged.py` is a required CI gate.
- [x] **VIII**: `ClassificationOverride` and `BoundaryOverride` are append-only (`data-model.md §2.3, §2.4`).
- [x] **IX**: Summary templates + manifest roles are YAML-loaded; no client logic in Python.

All 9 gates green after Phase 1.
