# Capability Contract: `detect_bpcr_sections` and `tag_bpcr_pages`

**Module**: `app.bmr.capabilities.bpcr_section_detect`, `app.bmr.capabilities.bpcr_section_tagger`
**Spec**: 007-bpcr-layout-aware-sections

These are **pure capabilities** in the Constitution-III sense: stateless, no I/O beyond reading their inputs, and safe to call from any test or notebook without standing up the full pipeline.

---

## `detect_bpcr_sections`

```python
def detect_bpcr_sections(
    *,
    doc_id: str,
    ocr: OCRResult,
    sections_spec: BPCRSectionsSpec,
    mode: Literal["heuristic", "vlm", "hybrid"] = "heuristic",
) -> BPCRSectionMap:
    """Detect canonical sections within a single BPCR document.

    Pure function. Does not read the network, the filesystem, or any
    other capability. Determinism is required: the same (ocr,
    sections_spec, mode) MUST produce a byte-equal BPCRSectionMap.

    Returns a BPCRSectionMap covering every page in `ocr.pages`. Pages
    with no confident match land in `unsectioned` filler spans.

    Args:
        doc_id: Stamped onto the returned map (FR-007 audit trail).
        ocr: OCR layout for the document. Pages must be 1-indexed.
        sections_spec: Canonical section list (loaded from YAML).
        mode: Currently only "heuristic" is implemented; "vlm" and
            "hybrid" raise NotImplementedError.

    Raises:
        NotImplementedError: when mode is "vlm" or "hybrid" (Phase 2).

    Failure modes (all return a "failed" outcome rather than raising):
        - empty `ocr.pages` -> single `unsectioned` span (1, 1) with
          notes=["empty_ocr"]
        - `sections_spec` has zero sections -> single `unsectioned` span
          covering all pages with notes=["empty_spec"]
        - any other internal exception -> single `unsectioned` span
          covering all pages with notes=["detector_exception:<class>"]
    """
```

### Determinism

For two invocations with the same inputs, equality requires:

1. Same `spans` list (same order, same fields, same `confidence` to ≥ 6 decimal places).
2. Same `outcome`, `method`, `notes`.
3. Same `detector_version`.

Tests assert this with a golden file — `tests/bmr/capabilities/test_bpcr_section_detect.py::test_heuristic_is_deterministic`.

### Confidence semantics

- `1.0` — exact regex match in the section's most-preferred band (e.g. `top_of_page` for default spec).
- `0.85` — regex match in a less-preferred band (e.g. `top_of_table` when `top_of_page` was specified first).
- `0.7` — regex match in `mid_page` with required emphasis present.
- `0.4` — alias match (not the primary regex) in any band.
- `0.0` — `unsectioned` filler.

These thresholds are constants in the capability module (`_CONF_*`) so tests can pin exact values.

### Span coverage invariants

For any returned `BPCRSectionMap`:

- `spans[0].start_page == 1`
- `spans[-1].end_page == len(ocr.pages)`
- For all `i`: `spans[i].end_page + 1 == spans[i+1].start_page`
- No two adjacent spans have the same `section_id` (they MUST be merged before return).

A test in `test_bpcr_section_detect.py` asserts these invariants on a representative spread of inputs.

---

## `tag_bpcr_pages`

```python
def tag_bpcr_pages(
    package: ExtractedPackage,
    *,
    section_maps: dict[str, BPCRSectionMap],
) -> ExtractedPackage:
    """Return a new ExtractedPackage with section_id stamped on BPCR pages.

    Pure function. Does not mutate the input package or any of its
    nested models — they are frozen Pydantic models. Returns a fresh
    aggregate with new ExtractedPage instances.

    Args:
        package: the package emitted by Stage 3 extraction.
        section_maps: mapping from BPCR doc_id to its BPCRSectionMap.
            Documents not in this dict are passed through unchanged
            (covers all non-BPCR roles, plus BPCR docs whose detection
            failed AND was wired to skip rather than fail-open).

    Returns:
        A new ExtractedPackage with the same package_id and the same
        page ordering. For every page in the original whose doc_id
        appears in `section_maps`, the page's `section_id` is set to
        whichever section span covers `page.page_index`.

    Failure modes:
        - missing section span for a page -> page.section_id = "unsectioned"
        - doc_id in section_maps but not in package.pages -> ignored
    """
```

### Idempotence

Re-running the tagger with the same inputs MUST produce a `package` that compares equal (`Pydantic.model_dump()` deep-equal) to a single run.

### Non-mutation

The original `package` and its nested `ExtractedPage` instances MUST remain `model_dump()`-equal to themselves before and after the call. A test in `test_bpcr_section_tagger.py::test_input_is_not_mutated` asserts this with a `model_dump_json()` snapshot.

---

## Error Surfaces and Logging

Both capabilities emit observability hooks (Spec 006):

| Event | Level | Fields |
|---|---|---|
| `bpcr.section_detect.entry` | info | `doc_id, pages, method, spec_version` |
| `bpcr.section_detect.exit` | info | `doc_id, pages, method, outcome, duration_ms, n_spans` |
| `bpcr.section_detect.failed` | warn | `doc_id, method, exception_class, exception_message` |
| `bpcr.section_tag.entry` | info | `package_id, n_bpcr_docs` |
| `bpcr.section_tag.exit` | info | `package_id, n_pages_tagged, duration_ms` |

The detector wraps its main body in a try/except that converts unhandled exceptions into the `failed` outcome described above. No exception escapes to the caller in `heuristic` mode. (`vlm` and `hybrid` will keep this contract when added.)

Metrics:

- `bpcr_section_detect_duration_seconds{method, outcome}` — histogram, default buckets.
- `bpcr_section_detect_pages_total{method, outcome}` — counter.

---

## Backwards Compatibility

These capabilities are new. Their absence-or-presence MUST NOT change the behaviour of any existing capability. In particular:

- `same_page_eval_v1`, `cross_doc_rule_eval_v1`, and `page_aggregate_eval_v1` MUST behave identically when `section_id` is unset on the rule AND when `section_id` is unset on the matched pages.
- `checklist_synthesise_v1` MUST NOT be touched.
- The `bmr-rules validate|fixture-run|diff` CLI surface MUST keep its v1.0 contract; the only addition is that v1.1 rules now validate and the diff tool understands `section_id` transitions.

A test (`tests/bmr/rules/test_schema_v1_1.py::test_v1_0_rules_unaffected`) asserts the back-compat invariant by validating every fixture under `tests/bmr/fixtures/rules/valid/` against both v1.0 and v1.1 schemas and checking the reports match.
