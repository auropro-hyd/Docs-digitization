# BPCR Section Spec — Changelog

Append-only. Every edit to `bpcr-section-spec.yaml` MUST land with a
matching entry here. The spec's `spec_version` MUST advance with the
appropriate semver bump:

- patch: added `section_id`, added alias, added regex pattern.
- minor: renamed `display_name`, changed `bands`.
- major: removed or renamed `section_id`, removed
  `requires_emphasis_for_mid_page`.

---

## 1.0.0-pilot — 2026-04-29

Initial pilot spec (Spec 007). Placeholder canonical list derived from
typical pharma BPCR layouts:

- `cover`
- `material_dispensing`
- `granulation`
- `compression`
- `coating`
- `in_process_qc`
- `yield_calculation`
- `packaging`
- `reconciliation`
- `sign_off`

To be replaced with the client-confirmed list after the call. The
`-pilot` suffix on the version string is intentional and MUST be
removed when the list is locked.
