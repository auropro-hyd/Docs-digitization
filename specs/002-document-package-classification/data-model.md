# Data Model: Document Package Ingestion & Classification

**Feature**: 002 | **Spec Version**: v2

All entities use UTC timestamps. IDs are ULIDs unless noted. Persistence is PostgreSQL.

## 1. Enumerations

### 1.1 `DocumentRole` (YAML-extensible, pilot seed)
`BMR` | `BPCR` | `DISPENSING_LOG` | `EQUIPMENT_LOG` | `SOP` | `SPEC_SHEET` |
`CLEANING_RECORD` | `LABEL_RECONCILIATION` | `QA_APPROVAL` | `OTHER`

The list is loaded from `config/bmr/*-manifest.yaml` at startup. Python code must not
hardcode role semantics beyond the fixed `BMR`/`BPCR`/`OTHER` sentinels used by the
pipeline.

### 1.2 `PackageStatus`
`uploaded` → `classifying` → `classification_ready` → `needs_review` →
`manifest_verified` → (hand-off to Spec 001 pipeline) | `rejected`

### 1.3 `ClassificationDecisionSource`
`filename_heuristic` | `header_heuristic` | `vlm_tiebreak` | `reviewer_override`

### 1.4 `BoundaryDetectionMethod`
`page_header` | `header_cluster` | `content_classification` | `reviewer_override`

### 1.5 `SummaryScope`
`page` | `document`

## 2. Core Entities

### 2.1 `DocumentPackage`
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `manifest_id` | FK → `Manifest` | |
| `uploaded_by` | string | actor id |
| `uploaded_at` | timestamp | server-assigned |
| `status` | `PackageStatus` | state machine (1.2) |
| `canonical_bpcr_document_id` | FK → `DocumentRef`, nullable | set after reviewer designation |
| `rejection_reason` | text, nullable | populated iff `status=rejected` |

### 2.2 `DocumentRef`
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `package_id` | FK → `DocumentPackage` | |
| `physical_file_id` | FK → existing file-store | multiple refs may share one file |
| `page_range` | `[int, int]`, nullable | set for virtual docs from concatenated PDFs |
| `original_filename` | string | |
| `byte_size` | int | |
| `sha256` | string | deduplication key |
| `is_virtual` | bool | true iff derived from a concatenated PDF |
| `created_at` | timestamp | |

### 2.3 `ClassificationResult` (append-only)
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `document_ref_id` | FK | |
| `role` | `DocumentRole` | |
| `confidence` | float[0..1] | |
| `decision_source` | `ClassificationDecisionSource` | |
| `candidates` | JSONB: `[{role, score}]` | top-K from classifier |
| `supersedes_id` | FK → self, nullable | chain head = current |
| `created_at` | timestamp | |
| `created_by` | string | `system` or actor id |

### 2.4 `BoundaryOverride` (append-only)
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `package_id` | FK | |
| `source_physical_file_id` | FK | the concatenated PDF being split |
| `resulting_ranges` | JSONB: `[{start, end, role_hint}]` | |
| `detected_by` | `BoundaryDetectionMethod` | |
| `reviewed_by` | string, nullable | populated iff reviewer-corrected |
| `supersedes_id` | FK → self, nullable | |
| `created_at` | timestamp | |

### 2.5 `ClassificationOverride` (append-only)
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `classification_result_id` | FK | the result being overridden |
| `new_role` | `DocumentRole` | |
| `reason_type` | enum: `wrong_role` / `wrong_alias` / `split_required` / `merge_required` | |
| `reason_comment` | text, optional | |
| `actor_id` | string | |
| `created_at` | timestamp | |

### 2.6 `Manifest`
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `name` | string | e.g. `pilot-manifest-v1` |
| `yaml_path` | string | file path loaded at startup |
| `version` | string | semver |
| `loaded_at` | timestamp | |
| `expected_roles` | JSONB: `[{role, min, max, required, aliases[]}]` | |

### 2.7 `SummaryTemplate`
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `role` | `DocumentRole` | |
| `scope` | `SummaryScope` | |
| `template_version` | string | semver |
| `prompt_or_extractor_ref` | string | prompt id or extractor capability id |
| `required_fields` | JSONB: `[string]` | fields downstream rules depend on |

### 2.8 `Summary`
| Field | Type | Notes |
|---|---|---|
| `id` | ULID | PK |
| `document_ref_id` | FK | |
| `page_number` | int, nullable | set iff `template.scope=page` |
| `template_id` | FK → `SummaryTemplate` | |
| `content` | JSONB | structured fields per template |
| `generated_at` | timestamp | |
| `generated_by` | string | capability id + version |

## 3. Shared Value Types

| Type | Shape | Notes |
|---|---|---|
| `PageRange` | `{start: int, end: int}` | 1-indexed inclusive |
| `RoleHint` | `{role: DocumentRole, confidence: float}` | embedded in `BoundaryOverride.resulting_ranges` |

## 4. Cross-Entity Validation Rules

1. `DocumentPackage.status=manifest_verified` ⇒ every `DocumentRef` in the package has a
   current (head-of-chain) `ClassificationResult`.
2. `DocumentPackage.canonical_bpcr_document_id` MUST point to a `DocumentRef` whose current
   classification is `BPCR`.
3. A package MUST NOT contain two current-head `ClassificationResult` rows with `role=BPCR`
   and both contributing to `canonical_bpcr` ambiguity unless the reviewer has designated
   one.
4. A `BoundaryOverride` whose `detected_by=reviewer_override` MUST have `reviewed_by` set.
5. Every `DocumentRef` with `is_virtual=true` MUST have a non-null `page_range` and a
   `BoundaryOverride` chain pointing at its `physical_file_id`.
6. `Summary` MUST satisfy: `page_number` non-null iff its template's `scope=page`.
7. `ClassificationOverride.new_role` MUST exist in the active `Manifest.expected_roles` OR
   in the seed `DocumentRole` enum.
8. `DocumentPackage.status` transitions MUST be monotonic through the state machine in §1.2
   (no backtracking except via `rejected`).

## 5. Persistence Strategy

- Tables: `document_package`, `document_ref`, `classification_result`,
  `classification_override`, `boundary_override`, `manifest`, `summary_template`, `summary`.
- Indexes: `(package_id)` on all package-scoped tables; `(document_ref_id, supersedes_id)`
  on `classification_result` for chain lookups; `(role, scope)` on `summary_template`.
- Manifests + summary templates are loaded at service start from
  `config/bmr/*.yaml`, upserted by `(name, version)`, and cached in-memory for the request
  path.
- Append-only tables use a check constraint that rejects `UPDATE` in non-migration contexts.

### 5.1 Evidence refs emitted by this stage

Findings produced during INGEST / LEGIBILITY_AND_CLASSIFICATION (e.g. `ANCHOR_MISSING`,
`DUPLICATE_CANONICAL_BPCR`, `MANIFEST_MISSING_ROLE`) carry evidence as
`{document_ref_id, page_number?, region?}` per the Spec 001 evidence shape.
