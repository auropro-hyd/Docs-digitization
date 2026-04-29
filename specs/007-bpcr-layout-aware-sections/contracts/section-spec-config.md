# Config Contract: `bpcr-section-spec.yaml`

**Path**: `backend/config/bmr/pilot/bpcr-section-spec.yaml` (default — overridable via `AT_BMR__BPCR_SECTIONS_SPEC`)
**Spec**: 007-bpcr-layout-aware-sections — FR-007 to FR-009

The canonical list of BPCR sections is **data, not code**. Domain experts edit this file; no Python change is required to add or modify sections.

---

## File Shape

```yaml
spec_version: "1.0"
sections:
  - section_id: <slug>
    display_name: <string>
    aliases: [<string>, ...]               # optional; alias regex generated case-insensitively
    regex: [<string>, ...]                 # optional; raw regex patterns matched against page lines
    bands: [top_of_page | top_of_table | mid_page]  # priority order
    requires_emphasis_for_mid_page: true   # default true; ignored unless mid_page is in bands
```

### Field Rules

| Field | Required | Validation |
|---|---|---|
| `spec_version` | yes | semver string. Bump on rename / removal. |
| `sections` | yes | non-empty list. |
| `section_id` | yes | matches `^[a-z][a-z0-9_]*$`. **MUST NOT** be `unsectioned` (loader rejects with a clear error). MUST be globally unique within the file. |
| `display_name` | yes | non-empty string ≤ 80 chars. |
| `aliases` | no | each alias becomes a case-insensitive substring match: `^\s*<alias>\b`. Empty list is allowed. |
| `regex` | no | each pattern is compiled with `re.IGNORECASE`. Patterns that fail to compile fail loader validation with the section_id and the failing pattern. |
| `bands` | no | defaults to `["top_of_page"]`. Each entry MUST be one of `top_of_page`, `top_of_table`, `mid_page`. Order encodes priority. |
| `requires_emphasis_for_mid_page` | no | bool, defaults to `true`. When `true`, mid-page matches require `bold`, `all caps`, OR a font-size span larger than the page-median body size to count. |

### Loader Behaviour

The loader (`app.bmr.config.bpcr_sections_spec.load_spec(path)`):

1. Reads the YAML.
2. Validates the structure with Pydantic (`BPCRSectionsSpec`).
3. Asserts `section_id` uniqueness.
4. Asserts no entry uses the reserved `unsectioned` sentinel.
5. Compiles all `regex` and `aliases` ahead of time (one CompileError aborts loading with a clear message naming the offending section).
6. Returns the validated `BPCRSectionsSpec`.

The loader is called once at run-service construction time. A bad spec fails the service startup loud (Constitution VI — config errors are blocking, not silent).

---

## Versioning

- `spec_version` follows semver.
- A new `section_id`, an added alias, or an added regex is **patch** (`1.0` → `1.0.1`).
- A renamed `display_name` or `bands` change is **minor** (`1.0` → `1.1`).
- A removed `section_id`, a renamed `section_id`, or a behaviour-changing edit (e.g. removing `requires_emphasis_for_mid_page`) is **major** (`1.0` → `2.0`).

Every spec change MUST land alongside a CHANGELOG entry — append-only — at `backend/config/bmr/pilot/bpcr-section-spec.CHANGELOG.md`.

---

## Example

```yaml
spec_version: "1.0"
sections:
  - section_id: cover
    display_name: "Cover Page"
    regex:
      - "^\\s*Batch\\s+Production\\s+and\\s+Control\\s+Record\\b"
    bands: [top_of_page]

  - section_id: material_dispensing
    display_name: "Material Dispensing"
    aliases: ["Dispensing Record"]
    bands: [top_of_page, top_of_table]

  - section_id: granulation
    display_name: "Granulation"
    bands: [top_of_page]

  - section_id: yield_calculation
    display_name: "Yield Calculation"
    aliases: ["Yield Reconciliation"]
    bands: [top_of_page, top_of_table, mid_page]
    requires_emphasis_for_mid_page: true
```

The detector tries each section's bands in priority order and stops at the first confident match. Bands are matched per-page, in their declared order; `top_of_page` always wins ties.
