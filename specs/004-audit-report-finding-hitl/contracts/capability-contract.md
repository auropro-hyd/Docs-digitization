# Capability Contracts (Spec 004 additions)

**Feature**: 004 | **Version**: v1

All capabilities conform to the base `Capability` ABC defined in Spec 001 and are atomic.

## `report_project.v1`

**Purpose**: Project raw findings + resolutions + resolved contexts into the grouped
`ReportSection[]` / `EvidenceLink[]` view models consumed by the UI and the exporter.

**Inputs**:
- `run_id: str`
- `view: "grouped" | "flat"`
- `severity_filter: list[Severity] | None`

**Outputs**:
- `sections: list[ReportSection]`
- `evidence_links: map[finding_id, EvidenceLink]`
- `export_gate: ExportGateStatus`

**Invariants**:
1. Deterministic for a given run snapshot (same inputs → same output bytes for cached
   projection).
2. `export_gate.status = ready` iff every blocking-severity finding (per
   `report-severity-gating.yaml`) has an active resolution and no `needs_re_action` row.
3. Every `EvidenceLink.sources[i]` MUST reference a real `DocumentRef` in the run's
   package; stale refs after re-run are a bug.

## `report_export.v1`

**Purpose**: Produce a PDF + JSON bundle from a `ReportSection[]` projection, write an
`AuditReportRevision` row, persist the two content-addressed blobs.

**Inputs**:
- `run_id: str`
- `sections_manifest_path: str` (YAML)
- `severity_gating_path: str` (YAML)

**Outputs**:
- `revision_id: str`
- `pdf_sha256: str`
- `bundle_sha256: str`

**Invariants**:
1. MUST refuse to run if the export gate is not `ready`; returns `ExportBlockedError`.
2. MUST NOT include an overall compliance score field anywhere in the bundle; the bundle
   JSON is validated against `contracts/bundle.schema.json` which forbids the keys
   `compliance_score`, `overall_pass_fail`, and any synonym registered in the schema.
3. Bundle JSON MUST include the `sections_manifest_id` and `severity_gating_id` of the
   YAML config versions used.
4. Revision ordering: `revision_number = max(prior) + 1`; `predecessor_id` = head prior id
   or null for first export.

## `feedback_seed.v1`

**Purpose**: Create exactly one `FeedbackSample` per persisted `StructuredResolution`.

**Inputs**:
- `resolution: StructuredResolution`
- `finding_snapshot: Finding`
- `resolved_context: ResolvedContext | None`

**Outputs**:
- `feedback_sample_id: str`

**Invariants**:
1. Same-transaction commit with the resolution write; if the seeder fails, the resolution
   MUST roll back.
2. `input_context_digest = sha256(resolved_context_stable_serialization OR
   finding.observed_values)`.
3. Immutable snapshot: the sample's `finding_snapshot` MUST NOT change if the underlying
   finding is later re-computed.

## Capability execution invariants (shared)

- All three capabilities take a `CapabilityContext` with `run_id`, `actor_id`, `logger`.
- All emit logs with capability id + version and elapsed_ms.
- Exit via domain exceptions, not generic `Exception`, so the stage orchestrator can
  convert them to UI-visible error payloads.
