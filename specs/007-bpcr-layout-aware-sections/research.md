# Research Notes: BPCR Layout-Aware Section Detection

**Status**: Decisions captured for the call. The `Decision` columns reflect what the spike implements; the `Open` items are call topics.

---

## R-001 — Where in the pipeline does section detection run?

| Option | Pros | Cons | Decision |
|---|---|---|---|
| **A. New stage between classification and extraction** | Clean separation; section-aware extractors could use the result | Requires a new stage; section work without OCR field data is harder (no extracted text yet) | Rejected |
| **B. Inside the extractor (decorator over OCRBackedExtractor)** | Natural place — extractor already has the OCR layout in hand | Couples extractor to section detection; can't disable independently | Rejected |
| **C. Post-extraction enrichment, before compliance** ✅ | OCR is cached; runs on the resulting `ExtractedPackage`; trivial to disable; sidecar extractor benefits too (works on hand-crafted fixtures) | One extra step | **Chosen** — see plan.md Phase 1.3 |

Rationale: Option C composes with both `SidecarExtractor` (test fixtures) and `OCRBackedExtractor` (production). The detector reads the OCR layout independently; the tagger only needs `ExtractedPackage` + the resulting `BPCRSectionMap`. Disable/enable is a single env flag. No re-jig of stage topology — the enrichment hangs off the *end* of Stage 3.

---

## R-002 — Heuristic vs VLM vs Hybrid

| Option | Cost / 35-page BPCR | Latency | Accuracy ceiling | Risk |
|---|---|---|---|---|
| **Heuristic** (regex + word position over OCR layout) | $0 (no LLM) | <100 ms | High when section headers match canonical list verbatim or near-verbatim; brittle when OCR garbles headers or layouts shift | M — false positives on misread headers; false negatives when canonical list is incomplete |
| **VLM** (per-page Gemini call against cropped top + middle bands) | ~35 calls @ ~$0.001 each = ~$0.035; ~$0.001 per call cached | 35 × ~600 ms = ~21 s p95 if serial; ~5 s if 5-way fan-out | Highest — VLM reads layout and recognises headers visually | L for accuracy, H for cost compared to heuristic |
| **Hybrid** (heuristic first, VLM fallback only on heuristic-unsure pages) | ~5–10 VLM calls per BPCR p50 | ~1.5–3 s p95 with fan-out | Heuristic ceiling + VLM safety net | Low |

**Decision (v0 spike)**: ship `heuristic` only. Stub `vlm` and `hybrid` to `NotImplementedError`. Three reasons:

1. The canonical section list isn't locked yet — without it, the VLM has no closed vocabulary to classify into and would invent labels.
2. Heuristic gives us a baseline number to beat; we can measure "of N detected sections, M were right" and decide what VLM needs to add.
3. Heuristic is deterministic. We can golden-file its output and gate regressions.

**Decision (Phase 2)**: default to `hybrid`. The cost envelope (≤10 VLM calls per BPCR) is acceptable; the latency (≤3 s p95 with modest fan-out) sits under the existing per-stage budget.

---

## R-003 — How does the heuristic detect "mid-page" section headers?

Per the 2026-04-28 client reply, the *Yield Calculation* header sits in the second half of its page, not at the top. The heuristic must therefore scan three bands per page, in priority order:

1. **top-of-page** (top 20% by `page_height`) — the highest-confidence band; section headers are most often here.
2. **top-of-table** (any line whose y-coordinate sits within ~10 px of a detected table-header row) — captures sections whose start coincides with the start of a major table.
3. **mid-page** (anywhere from 20%–80% of the page) — lowest priority; only matches when a candidate header has explicit emphasis (bold OR all-caps OR larger-than-body font where `StyleSpan` is available).

If multiple bands match the same canonical section on a single page, the highest-priority band wins. If different canonical sections match in different bands on the same page, the **top-of-page** match wins; the others become evidence for the *next* section starting later on that page (but the page itself is assigned to the top-of-page section).

---

## R-004 — Schema major bump or minor bump?

The change adds an **optional** field (`section_id` under `page_selector`). Existing v1.0 rules validate unchanged. By the project's existing schema-versioning rule (additive ⇒ minor bump), this is a **1.0 → 1.1 minor bump**.

- The two schemas live side-by-side under `backend/config/rules/schema/`.
- Rules pin their schema version via `schema_version: "1.0"` or `"1.1"`. The loader picks the matching schema.
- Tests assert that every v1.0 rule in the pilot bank still validates against v1.0 (no silent migration).

---

## R-005 — How does a rule referring to `section_id` behave when section detection is disabled?

Three options were considered:

1. **Hard-fail the run** — too aggressive; one config flip would break every section-aware rule.
2. **Apply the rule's existing `fallback` policy** — `flag_as_unevaluated` | `flag_as_indeterminate` | `treat_as_pass` are all already-defined behaviours. The author already chose how the rule should degrade if data is missing; "no section data" is just another instance of "data missing".
3. **Add a new `policy: skip_when_no_sections` knob** — adds a knob the author has to think about; not motivated until we see real misuse.

**Decision**: Option 2. Reuse `fallback`. Document the behaviour in the rule contract.

---

## R-006 — Where does `section_id` live on extracted data?

| Option | Pros | Cons | Decision |
|---|---|---|---|
| **A. New optional field on `ExtractedPage`** | Direct; obvious; cheap to filter | One model change | **Chosen** |
| **B. Reuse `tags: list[str]`** with `"section:yield_calculation"` convention | Zero model change | String-typed, no validation; fragile selector (substring matches); harder to evolve | Rejected |
| **C. Separate `BPCRSectionMap` aggregate stored alongside `ExtractedPackage`** | Cleanest separation | Two-source-of-truth problem at evaluation time; rule engine has to look up section by `(doc_id, page_index)` for every match | Rejected |

`ExtractedPage` is `frozen=True`; the tagger constructs new pages with `section_id` populated. Other fields are unchanged.

---

## R-007 — Canonical section list: where does it live and how does it evolve?

Three forces:

- **Authoring ergonomics** — the YAML must be human-editable by domain experts who don't ship Python.
- **Versioning** — the list will change as more BPCR products land. We need to know which list a finding was generated against.
- **Per-product overlays** — different products (e.g. tablet vs. capsule) have different section structures. Long-term we'll need overlays.

**v0 (this PR)**: single file `backend/config/bmr/pilot/bpcr-section-spec.yaml` with `spec_version`, `sections: [...]`, and per-section `regex`, `aliases`, `bands`. Loader reads from `AT_BMR__BPCR_SECTIONS_SPEC` (defaulting to the pilot path). The section spec is stamped onto each detected `BPCRSectionMap` via a `spec_version` field so findings can replay deterministically.

**v1 (post-pilot)**: an overlay mechanism — a base `bpcr-section-spec.yaml` plus per-product or per-customer overlays merged at load time. Out of scope for the spike.

---

## R-008 — Does this change observability (Spec 006)?

Yes — additively. New surfaces:

- **Histogram** `bpcr_section_detect_duration_seconds{method, outcome}` (label cardinality is bounded: 3 methods × 3 outcomes = 9 series).
- **Counter** `bpcr_section_detect_pages_total{method, outcome}` for ops dashboards.
- **Log line** `bpcr.section_detect.entry` and `bpcr.section_detect.exit` carrying `doc_id`, `pages`, `method`, `outcome`, `duration_ms`. PII-safe (no extracted text).

Hooks into the existing `app/observability/` package via the same `Tracer` and `MetricRegistry` injectors used by Spec 006. No new infra.

---

## R-009 — What if the BPCR's classifier `role` was wrong (a non-BPCR document tagged as BPCR)?

The detector runs anyway. The heuristic against the canonical BPCR section list will simply fail to match anything; every page lands as `unsectioned`. No findings fire incorrectly. Cost is bounded (≤35 pages × heuristic-only ≈ <100 ms). When the misclassification is fixed (HITL override or re-classification), the next run produces the right sections automatically.

This is the *deliberate* fail-open behaviour. We don't try to second-guess the classifier inside the detector.
