# Feature Specification: Document Package Ingestion & Classification

**Feature Branch**: `002-document-package-classification`
**Created**: 2026-04-17
**Last Revised**: 2026-04-17 (v2 — adds boundary-detection method hierarchy + config-driven summary generation, per Constitution v1.1.0)
**Status**: Draft
**Input**: User description: "Multi-document package ingestion, per-document boundary detection and classification into BPCR / RawMaterialPage / ChecklistPage / AnalysisReport / CertificateOfAnalysis / Other, plus config-driven page-level and doc-level summarisation that feeds compliance."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reviewer uploads a full BMR package in one action (Priority: P1)

A QA reviewer has ~10 PDF documents that together form one batch's BMR (a BPCR, multiple raw-material-dispensing pages, equipment cleaning checklists, an analysis report, and a Certificate of Analysis). They drag all of them into the upload area (or select a zip), the system accepts them as a single package, classifies each one by role, verifies the manifest, and routes the package into the BMR audit pipeline.

**Why this priority**: Without multi-document package ingestion, the BMR audit pipeline has nothing to operate on. Every downstream spec depends on this.

**Independent Test**: Can be fully tested by uploading a known-good pilot BMR package (drag-and-drop or zip), inspecting the resulting package entity, and verifying each contained document is classified correctly and the manifest check passes.

**Acceptance Scenarios**:

1. **Given** the reviewer drags multiple PDFs (or a single zip containing PDFs) into the upload area, **When** upload completes, **Then** the system creates one package entity containing all documents with a single package identifier, and each document is individually addressable.
2. **Given** a package is created, **When** classification runs, **Then** each document is labelled with one of the known roles (BPCR, RawMaterialPage, ChecklistPage, AnalysisReport, CertificateOfAnalysis, Other) with a confidence score.
3. **Given** classification completes, **When** manifest verification runs, **Then** the system confirms all manifest-declared-required roles are present and surfaces any missing or unexpected roles as findings.

---

### User Story 2 - Reviewer corrects a misclassified document (Priority: P1)

The classifier labels a document with low confidence (e.g., a raw-material page with an unusual header gets classified as "Other"). The reviewer sees the classification result with confidence, overrides the role from a dropdown, the manifest re-verifies, and the pipeline proceeds.

**Why this priority**: The classifier will make mistakes. Without reviewer override, a misclassification blocks the whole audit.

**Independent Test**: Can be tested by uploading a package where one document is known to classify with low confidence, overriding its role, and verifying the manifest re-verifies and downstream stages proceed with the corrected role.

**Acceptance Scenarios**:

1. **Given** a document classified below the confidence threshold, **When** the reviewer opens the package, **Then** the document is flagged for classification review with the top-N candidate roles listed.
2. **Given** the reviewer selects a different role, **When** they confirm, **Then** the document's role is updated, the override is recorded in the audit trail with reviewer identity and timestamp, and manifest verification re-runs against the new role.
3. **Given** the override resolves a missing-required-role warning, **When** verification completes, **Then** the warning is cleared and the pipeline proceeds.

---

### User Story 3 - System rejects an invalid package (Priority: P2)

A reviewer accidentally uploads a single loose PDF (or a package missing multiple required roles) when BMR mode is selected. The system explains what is wrong without corrupting state.

**Why this priority**: Clear error states protect reviewers from producing an invalid audit. P2 because it's a guard-rail, not a flow.

**Independent Test**: Upload (a) a single PDF in BMR mode and (b) a package missing a required BPCR; verify both produce clear, actionable errors and no partial package is left in the system.

**Acceptance Scenarios**:

1. **Given** the user selects BMR mode and uploads a single document, **When** upload completes, **Then** the system displays "BMR mode requires a document package — upload multiple files or a zip archive" and does not create a package.
2. **Given** a package is uploaded without a document classifiable as BPCR, **When** manifest verification runs, **Then** the system emits a blocking manifest-verification finding "Required role 'BPCR' not present" and the pipeline does not advance past manifest verification until the reviewer either uploads the missing document or overrides a classification.
3. **Given** a package contains more than one document classified as BPCR with high confidence, **When** manifest verification runs, **Then** the system surfaces an ambiguity warning and requires the reviewer to designate the canonical BPCR before proceeding.

---

### User Story 4 - Operator registers a new product manifest (Priority: P2)

An internal operator adds support for a new product by writing a manifest declaring the expected document roles, any cross-reference rules, and loading it into the system. The next package for that product uses the new manifest automatically.

**Why this priority**: The framework pitch depends on this, but the pilot client's manifest must work first (covered in spec 001 / this spec's P1).

**Independent Test**: Load a synthetic second-product manifest, upload a synthetic package matching it, and verify the package uses the new manifest and classification plus verification proceed correctly.

**Acceptance Scenarios**:

1. **Given** a valid new manifest file, **When** the operator loads it, **Then** the system validates the manifest schema and activates it for packages whose product identifier matches.
2. **Given** a package arrives whose product identifier does not match any loaded manifest, **When** ingest runs, **Then** the system falls back to a default generic manifest and emits a "no product-specific manifest found" warning in the audit trail.

---

### User Story 5 — System detects logical document boundaries in an uploaded PDF stream (Priority: P1)

Often the BMR arrives as fewer physical PDFs than logical documents — e.g., one 200-page scanned bundle that internally contains a BPCR, three raw-material pages, a checklist, and an analysis report back-to-back. The system must detect where each logical document starts and ends before classification runs, using a hierarchy of methods.

**Why this priority**: Without boundary detection, classification operates on a mix of unrelated pages and produces garbage labels; every downstream spec fails.

**Independent Test**: Upload a fixture bundle containing 3 known logical documents concatenated into one PDF. Verify each logical boundary is detected, each logical document is classified independently, and the boundary method used per document is recorded for auditability.

**Acceptance Scenarios**:

1. **Given** an uploaded PDF with internal `Page X of Y` headers, **When** boundary detection runs, **Then** the system uses header-based splitting as the primary method and records `boundary_method: page_header` on each resulting DocumentRef.
2. **Given** an uploaded PDF with no `X of Y` headers but with consistent repeating page headers (e.g., "BPCR – Batch XYZ" vs "Raw Material Dispensing"), **When** boundary detection runs, **Then** the system falls back to header-text clustering and records `boundary_method: header_text_cluster` with the header strings used.
3. **Given** neither structural signal is present, **When** boundary detection runs, **Then** the system falls back to content-based classification per page (treating each page as potentially a new logical doc) and records `boundary_method: content_classification_per_page`. This is the slowest, most expensive path.
4. **Given** the reviewer inspects a package, **When** a document was boundary-detected via a fallback method, **Then** the UI shows the method used and offers a "Merge with adjacent document" / "Split here" correction action.

---

### User Story 6 — System generates configurable summaries driven by role-specific templates (Priority: P1)

The compliance stage and the UI both depend on structured, predictable summaries of each document. For the BPCR, summaries are needed at **page level** (one per step page) because rules evaluate per step. For other roles (RawMaterialPage, CheckList, AnalysisReport, CoA), **document-level** summaries are sufficient. Summary shape per role is specified by a YAML template — not code.

**Why this priority**: Summaries feed the cross-document rule engine (Spec 003). Hardcoded summary shapes would break the framework pitch (Constitution VI).

**Independent Test**: Load a package and a summary-template YAML that declares `BPCR: page_level with fields [step_number, operator, timestamp, quantity, signature_present]` and `RawMaterialPage: document_level with fields [material_name, lot_number, weight_kg, dispensed_by, check_by]`. Verify summaries are produced per template, with no hardcoded fallback shape.

**Acceptance Scenarios**:

1. **Given** a loaded summary-template YAML, **When** a BPCR is processed, **Then** one page-level summary is produced per BPCR page, with exactly the fields declared in the template.
2. **Given** the same template, **When** a RawMaterialPage document is processed, **Then** exactly one document-level summary is produced with the declared fields.
3. **Given** a field declared in the template cannot be extracted by OCR/VLM (e.g., signature_present for a blank scan), **When** summarisation runs, **Then** the field is recorded with `value: null, reason: "not_extractable"` — never silently omitted.
4. **Given** the operator updates the template (e.g., adds a new field), **When** the next run starts, **Then** that new field appears in all subsequent summaries without Python code changes.

---

### Edge Cases

- What happens when two files uploaded have byte-identical content? System deduplicates by content hash and records a "duplicate document" note on the package; only one is classified.
- What happens when a file is not a PDF (e.g., a Word or Excel file accidentally included)? The Ingest stage rejects non-PDF files with a clear per-file error and refuses to create the package until resolved.
- What happens when a PDF is password-protected? The system rejects it with a clear error; the reviewer must unlock before re-upload.
- What happens when a PDF is corrupt or has zero pages? The file is rejected at Ingest with a per-file error.
- What happens when the classifier's top candidate and the reviewer override disagree after repeated attempts? The reviewer override always wins; classifier confidence is logged for later model improvement but does not block.
- What happens when the same logical document appears twice (e.g., two versions of the analysis report)? The system flags an ambiguity; the reviewer must designate which version is canonical before proceeding.
- What happens when a zip archive contains nested folders? The system flattens the zip and classifies every PDF regardless of folder; non-PDF entries are ignored with a per-file note.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept multi-file uploads (drag-and-drop multiple files or a single zip archive) and treat them as one package with a single package identifier.
- **FR-002**: System MUST persist, for each uploaded document, its original filename, content hash, page count, and a reference to the file as stored.
- **FR-003**: System MUST classify each document into one of the declared roles from the active manifest (at minimum: BPCR, RawMaterialPage, ChecklistPage, AnalysisReport, CertificateOfAnalysis, Other) and attach a confidence score.
- **FR-004**: System MUST allow reviewer override of any document's classification from a dropdown of all known roles, recording the override with reviewer identity, timestamp, prior role, and new role.
- **FR-005**: System MUST verify the package against its active manifest, reporting: required roles missing, roles present multiple times where exactly one is required, and roles present that are not declared in the manifest (the last as a warning, not an error).
- **FR-006**: System MUST refuse to advance any package past manifest verification until all blocking manifest errors are resolved either by upload or by classification override.
- **FR-007**: System MUST deduplicate documents by content hash within a package and record the duplicates without losing filenames.
- **FR-008**: System MUST reject non-PDF files and corrupt, password-protected, or zero-page PDFs at Ingest with clear per-file error messages, and MUST NOT create a partially ingested package.
- **FR-009**: Manifests MUST be loadable as declarative configuration without requiring application restart or Python code changes. The set of known roles, required roles, expected cardinality per role, and cross-reference rule placeholders live in the manifest.
- **FR-010**: System MUST route each package to the manifest whose product identifier matches the package's declared product, falling back to a declared default manifest when no match exists and emitting a warning.
- **FR-011**: Packages and their contained document references MUST be persisted so that a process restart recovers the package and its classification state without requiring re-upload.
- **FR-012**: The classifier's confidence score MUST be exposed to the reviewer; documents below a configured confidence threshold MUST be flagged for reviewer attention.
- **FR-013**: System MUST treat the canonical BPCR designation as a distinguished role: exactly one document per package must be marked canonical BPCR before the BMR audit pipeline's Structured Extraction stage begins.
- **FR-014**: System MUST detect logical document boundaries within an uploaded PDF stream using a declared method hierarchy, tried in order: (a) page-header `Page X of Y` detection, (b) repeating-header text clustering, (c) content-based per-page classification as fallback. The method actually used MUST be recorded on each resulting DocumentRef as `boundary_method`.
- **FR-015**: System MUST allow the reviewer to correct boundaries post-detection (merge adjacent documents; split at a chosen page). The correction is recorded as an immutable `BoundaryOverride` entry with actor + timestamp.
- **FR-016**: System MUST generate summaries according to declarative role-specific templates (YAML). Summary granularity (page-level vs document-level) and field list MUST be driven entirely by the template. No role-specific summary shape MAY be hardcoded in Python.
- **FR-017**: For the BPCR role, summaries MUST be produced at **page level** (one per BPCR page). For all other roles, summaries MUST be produced at **document level** by default unless the template declares otherwise.
- **FR-018**: Summary fields declared in the template that cannot be extracted MUST be recorded as `null` with a machine-readable reason (`not_extractable`, `not_present`, `unknown`), never silently dropped.
- **FR-019**: Summaries produced here MUST feed the cross-document rule engine (Spec 003 / context_object resolution) without a translation layer; the summary schema IS the cross-doc context surface for `role`-based lookups.

### Key Entities *(include if feature involves data)*

- **DocumentPackage**: The unit a BMR audit operates on. Carries package identifier, product identifier, manifest reference, list of contained document references, canonical BPCR reference, ingest timestamp, and ingest-stage status.
- **DocumentRef**: A reference to one contained document within a package. Carries document identifier, original filename (for PDF-per-file input) or `synthetic_from: { source_pdf, page_range }` (for boundary-detected documents within a multi-doc PDF), content hash, page count, classification role, classifier confidence, reviewer override (if any), **`boundary_method`** (one of `single_file | page_header | header_text_cluster | content_classification_per_page | reviewer_override`), and a reference to the underlying stored pages.
- **ClassificationResult**: The classifier's output per document. Carries top-N candidate roles with scores, final role (post-override if any), confidence, and model/version identifiers for reproducibility.
- **Manifest**: Declarative product-specific configuration. Carries product identifier, manifest version, declared roles, required role list with expected cardinality, cross-reference rule placeholders consumed by spec 003, and a reference to the applicable summary-template file.
- **ClassificationOverride**: An immutable record of a reviewer's role override. Carries reviewer identity, timestamp, document reference, prior role, new role, and reason (optional).
- **BoundaryOverride**: An immutable record of a reviewer's boundary correction. Carries actor, timestamp, the documents merged (pre-state) or the split point, the resulting documents (post-state), and optional reason.
- **ManifestVerificationResult**: The result of verifying a package against its manifest. Carries list of blocking errors, warnings, ambiguities, and the resolved canonical BPCR reference.
- **SummaryTemplate**: Declarative YAML defining summary shape per role. Carries `role`, `granularity: page | document`, `fields: [{ name, kind, extractor_hint? }]`, and `version`.
- **Summary**: A produced summary instance. Carries `document_id`, optional `page_number` (for page-level), the materialised field values (with explicit nulls + reason when un-extractable), the `template_version` used, and the extractor model/version for reproducibility.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For the pilot-client BMR package, classification accuracy is at least 95% on the pilot's document set, measured by agreement with the pilot QA's ground-truth labels.
- **SC-002**: A reviewer can upload a 10-document pilot package and have classification + manifest verification complete in under 60 seconds end-to-end.
- **SC-003**: Reviewer override of a misclassified document takes no more than two clicks and updates manifest verification in under 3 seconds.
- **SC-004**: Ingest rejects 100% of malformed inputs (non-PDF, corrupt, password-protected, zero-page) with actionable per-file errors; no partially ingested packages persist to downstream stages — verifiable by negative-input integration test.
- **SC-005**: Adding a second product manifest and onboarding a matching synthetic package requires zero Python code changes.
- **SC-006**: Duplicate detection across a package prevents any document from being processed more than once, verified by upload of two identical files producing one classification and one downstream processing run.
- **SC-007**: On a concatenated-PDF fixture with 3 known logical documents, boundary detection correctly identifies all 3 boundaries using the highest-precedence method available (page-header → header-text-cluster → content-based), and records the method used on each resulting DocumentRef.
- **SC-008**: Summaries produced against the pilot summary-template YAML match the pilot template's declared field list exactly (no extra fields, no missing fields; null + reason is acceptable for un-extractable values). Verifiable by a fixture-based integration test.
- **SC-009**: Adding a new field to the summary-template YAML produces that field in the next run's summaries without any Python code change.

## Assumptions

- Uploaded documents are PDFs; other formats are out of scope for v1.
- Package size is bounded at 25 documents / 500 pages total for v1; larger packages may work but are not a target.
- The classifier can be a hybrid of filename/header heuristics plus a lightweight model call; the spec does not prescribe the technique.
- Product identifier is either derivable from the BPCR cover page or supplied by the reviewer at upload time; automatic extraction quality is out of scope for v1 and reviewer entry is an acceptable fallback.
- The manifest schema is authored by internal staff, not by end reviewers; a governance process for manifest changes exists outside this spec.
- The existing document-store subsystem is reused for file-level persistence; only package-level relationships are newly modelled.
