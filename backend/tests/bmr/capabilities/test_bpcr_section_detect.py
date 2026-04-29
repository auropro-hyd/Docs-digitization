"""Heuristic BPCR section detector tests (Spec 007 capability-contract).

Each test exercises one promise from
``specs/007-bpcr-layout-aware-sections/contracts/capability-contract.md``:

- detection in each band (top_of_page, top_of_table, mid_page),
- determinism for byte-equal output across reruns,
- correct span coverage invariants (no gaps, no overlaps, sorted),
- the fail-open contract (exception → ``unsectioned`` filler),
- the v0 stub raising on ``vlm`` and ``hybrid`` modes.
"""

from __future__ import annotations

from app.bmr.capabilities.bpcr_section_detect import (
    BPCRSectionMap,
    SectionSpan,
    detect_bpcr_sections,
)
from app.bmr.capabilities.bpcr_sections_spec import (
    UNSECTIONED_ID,
    BPCRSectionEntry,
    BPCRSectionsSpec,
)
from app.core.ports.ocr import (
    BoundingRegion,
    OCRPageResult,
    OCRResult,
    OCRWord,
)

# ── Test fixtures ──────────────────────────────────────────────────────────


def _spec() -> BPCRSectionsSpec:
    """Tiny canonical list — three sections, each exercising a band."""

    return BPCRSectionsSpec(
        spec_version="test-1.0",
        sections=[
            BPCRSectionEntry(
                section_id="cover",
                display_name="Cover Page",
                regex=[r"^\s*Batch\s+Production\s+and\s+Control\s+Record"],
                bands=["top_of_page"],
            ),
            BPCRSectionEntry(
                section_id="material_dispensing",
                display_name="Material Dispensing",
                aliases=["Dispensing Record"],
                bands=["top_of_page", "top_of_table"],
            ),
            BPCRSectionEntry(
                section_id="yield_calculation",
                display_name="Yield Calculation",
                aliases=["Yield Reconciliation"],
                bands=["top_of_page", "top_of_table", "mid_page"],
                requires_emphasis_for_mid_page=True,
            ),
        ],
    )


def _page(
    *,
    page_num: int,
    lines: list[tuple[str, float]] | None = None,
    markdown: str | None = None,
) -> OCRPageResult:
    """Build a page either from word coordinates (positional bands) or
    from markdown (positional band falls back to evenly-spaced lines).
    """

    if lines is not None:
        # Place each line on its own y baseline; one word per line is fine.
        words = [
            OCRWord(
                text=text,
                confidence=0.99,
                bounding_region=BoundingRegion(
                    page_num=page_num, x=0.0, y=y * 1000.0, width=200.0, height=12.0
                ),
            )
            for text, y in lines
        ]
        return OCRPageResult(
            page_num=page_num,
            page_height=1000.0,
            page_width=800.0,
            words=words,
        )
    return OCRPageResult(
        page_num=page_num,
        markdown=markdown or "",
    )


# ── Detection in each band ─────────────────────────────────────────────────


def test_detects_top_of_page_section() -> None:
    ocr = OCRResult(
        pages=[
            _page(
                page_num=1,
                lines=[
                    ("Batch Production and Control Record", 0.05),
                    ("body content", 0.5),
                ],
            ),
            _page(
                page_num=2,
                lines=[
                    ("Material Dispensing", 0.04),
                    ("filler", 0.5),
                ],
            ),
        ]
    )
    result = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=_spec())

    assert result.outcome == "ok"
    assert [s.section_id for s in result.spans] == ["cover", "material_dispensing"]
    assert result.spans[0].detection_method == "heuristic_top_of_page"
    assert result.spans[1].detection_method == "heuristic_top_of_page"


def test_detects_mid_page_section_only_with_emphasis() -> None:
    spec = _spec()
    # Yield Calculation header sits at y=0.55, NO emphasis. With
    # requires_emphasis_for_mid_page=True the heuristic must reject it.
    ocr_no_emphasis = OCRResult(
        pages=[
            _page(
                page_num=1,
                lines=[
                    ("Yield Calculation", 0.55),
                    ("body", 0.7),
                ],
            ),
        ]
    )
    no_em = detect_bpcr_sections(doc_id="bpcr", ocr=ocr_no_emphasis, sections_spec=spec)
    assert [s.section_id for s in no_em.spans] == [UNSECTIONED_ID]

    # Same line, ALL CAPS — emphasised, mid-page detection wins.
    ocr_emph = OCRResult(
        pages=[
            _page(
                page_num=1,
                lines=[
                    ("YIELD CALCULATION", 0.55),
                    ("body", 0.7),
                ],
            ),
        ]
    )
    em = detect_bpcr_sections(doc_id="bpcr", ocr=ocr_emph, sections_spec=spec)
    assert [s.section_id for s in em.spans] == ["yield_calculation"]
    assert em.spans[0].detection_method == "heuristic_mid_page"


def test_alias_match_lower_confidence_than_primary() -> None:
    # Match via alias only — confidence should be the lower _CONF_ALIAS value.
    ocr = OCRResult(
        pages=[
            _page(
                page_num=1,
                lines=[
                    ("Yield Reconciliation", 0.05),
                    ("body", 0.5),
                ],
            ),
        ]
    )
    result = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=_spec())

    assert [s.section_id for s in result.spans] == ["yield_calculation"]
    # Alias match in the highest-priority band still tops out at 0.4 — the
    # primary regex would have scored 1.0; the gap signals "uncertain match".
    assert result.spans[0].confidence == 0.4


# ── Span coverage invariants ───────────────────────────────────────────────


def test_pages_before_first_header_are_unsectioned() -> None:
    ocr = OCRResult(
        pages=[
            _page(page_num=1, lines=[("preamble noise", 0.5)]),
            _page(page_num=2, lines=[("Material Dispensing", 0.05), ("body", 0.5)]),
            _page(page_num=3, lines=[("more body", 0.5)]),
        ]
    )
    result = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=_spec())

    assert result.outcome == "partial"  # contains_unsectioned_pages
    assert [s.section_id for s in result.spans] == [UNSECTIONED_ID, "material_dispensing"]
    assert result.spans[0].start_page == 1 and result.spans[0].end_page == 1
    assert result.spans[1].start_page == 2 and result.spans[1].end_page == 3


def test_spans_cover_every_page_with_no_gaps_or_overlaps() -> None:
    ocr = OCRResult(
        pages=[
            _page(page_num=n, lines=[("body", 0.5)])
            for n in range(1, 11)
        ]
    )
    # Tag pages 3 and 7 as section starters.
    ocr.pages[2] = _page(
        page_num=3, lines=[("Material Dispensing", 0.05), ("body", 0.5)]
    )
    ocr.pages[6] = _page(
        page_num=7, lines=[("YIELD CALCULATION", 0.55), ("body", 0.7)]
    )

    result = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=_spec())

    assert result.spans[0].start_page == 1
    assert result.spans[-1].end_page == 10
    # No gaps between adjacent spans, and no adjacent spans sharing
    # a section_id (merge invariant).
    for left, right in zip(result.spans, result.spans[1:], strict=False):
        assert left.end_page + 1 == right.start_page
        assert left.section_id != right.section_id


# ── Determinism ────────────────────────────────────────────────────────────


def test_heuristic_is_deterministic() -> None:
    ocr = OCRResult(
        pages=[
            _page(page_num=1, lines=[("Material Dispensing", 0.05), ("body", 0.5)]),
            _page(page_num=2, lines=[("body", 0.5)]),
            _page(page_num=3, lines=[("YIELD CALCULATION", 0.55), ("body", 0.7)]),
        ]
    )
    spec = _spec()

    first = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=spec)
    second = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=spec)

    assert first.model_dump_json() == second.model_dump_json()


# ── Fail-open contract ─────────────────────────────────────────────────────


def test_empty_ocr_yields_failed_outcome_and_unsectioned_span() -> None:
    result = detect_bpcr_sections(
        doc_id="bpcr", ocr=OCRResult(pages=[]), sections_spec=_spec()
    )
    assert result.outcome == "failed"
    assert result.spans == [
        SectionSpan(
            section_id=UNSECTIONED_ID,
            display_name="",
            start_page=1,
            end_page=1,
            confidence=0.0,
            detection_method="unmatched",
        )
    ]
    assert "empty_ocr" in result.notes


def test_vlm_and_hybrid_modes_raise_in_v0() -> None:
    ocr = OCRResult(pages=[_page(page_num=1, lines=[("body", 0.5)])])
    for mode in ("vlm", "hybrid"):
        try:
            detect_bpcr_sections(
                doc_id="bpcr",
                ocr=ocr,
                sections_spec=_spec(),
                mode=mode,  # type: ignore[arg-type]
            )
        except NotImplementedError as exc:
            assert mode in str(exc)
        else:
            raise AssertionError(f"mode={mode} should raise NotImplementedError")


# ── Result type sanity ─────────────────────────────────────────────────────


def test_section_for_page_resolves_correctly() -> None:
    ocr = OCRResult(
        pages=[
            _page(page_num=1, lines=[("Material Dispensing", 0.05), ("x", 0.5)]),
            _page(page_num=2, lines=[("x", 0.5)]),
            _page(page_num=3, lines=[("YIELD CALCULATION", 0.55), ("x", 0.7)]),
            _page(page_num=4, lines=[("x", 0.5)]),
        ]
    )
    m = detect_bpcr_sections(doc_id="bpcr", ocr=ocr, sections_spec=_spec())
    assert m.section_for_page(1) == "material_dispensing"
    assert m.section_for_page(2) == "material_dispensing"
    assert m.section_for_page(3) == "yield_calculation"
    assert m.section_for_page(4) == "yield_calculation"
    assert m.section_for_page(99) is None


def test_section_map_carries_spec_and_detector_versions() -> None:
    ocr = OCRResult(pages=[_page(page_num=1, lines=[("x", 0.5)])])
    spec = _spec()
    result: BPCRSectionMap = detect_bpcr_sections(
        doc_id="bpcr", ocr=ocr, sections_spec=spec
    )
    assert result.spec_version == spec.spec_version
    assert result.detector_version  # non-empty; pinned by the module
    assert result.method == "heuristic"
