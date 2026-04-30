"""Detector hardening for real-world BPCR markdown.

Three regressions surfaced when running the detector against a real
35-page BPCR (the doc Akhilesh flagged on 2026-04-28):

1. **Markdown markup blocked the matcher.** Real headings come out
   as ``**MICRONIZATION OPERATION**`` or ``# **MANUFACTURING
   INSTRUCTIONS**``; the detector's regex/alias matchers are
   anchored at ``^\\s*`` so the leading ``**`` / ``#`` prevented
   any match. Fixed by stripping presentation markers in
   ``_normalise_markdown_line`` before matching.

2. **``cover_page`` matched every page.** The cover_page regex
   matches ``BATCH PRODUCTION AND CONTROL RECORD`` — which is the
   document title that repeats on every BPCR page header. Once
   matched, ``_assemble_spans`` inherited cover_page across the
   whole document. Fixed by gating cover_page to ``page_num == 1``
   in ``_evaluate_page``.

3. **Mid-page section headers were band-locked out.** The spec
   restricts most sections to ``[top_of_page, top_of_table]``
   bands. Real BPCR headers ("MICRONIZATION OPERATION" on page 24)
   sit mid-page; markdown-only mode (no word coords) computes
   synthetic y_fractions from line indices, so those headers
   land in ``mid_page`` and were rejected. Fixed by dropping
   the band check in markdown-only mode — synthetic y_fractions
   are too unreliable to gate on.

These tests pin each invariant against a synthetic mini-fixture
that mirrors the real-doc patterns.
"""

from __future__ import annotations

from app.bmr.capabilities.bpcr_section_detect import (
    _normalise_markdown_line,
    detect_bpcr_sections,
)
from app.bmr.capabilities.bpcr_sections_spec import load_spec
from app.core.ports.ocr import OCRPageResult, OCRResult


def test_normalise_strips_heading_and_bold_markup() -> None:
    """The presentation markers the layout sanitiser preserves
    around real BPCR section headers must not block matching."""

    assert _normalise_markdown_line("**MICRONIZATION OPERATION**") == "MICRONIZATION OPERATION"
    assert _normalise_markdown_line("# **MANUFACTURING INSTRUCTIONS**") == "MANUFACTURING INSTRUCTIONS"
    assert _normalise_markdown_line("###### LIST OF MAJOR EQUIPMENTS") == "LIST OF MAJOR EQUIPMENTS"
    assert _normalise_markdown_line("**SIFTING RECORD (Ref SOP No: UIIMF052)**") == "SIFTING RECORD (Ref SOP No: UIIMF052)"
    # Plain text passes through unchanged.
    assert _normalise_markdown_line("Material Dispensing") == "Material Dispensing"
    # A trailing colon AFTER the closing ``**`` is consumed by the regex's
    # optional trailing-punctuation group; a colon INSIDE the ``**…**``
    # is part of the captured content and survives. The downstream
    # section regexes use ``\b`` so an embedded trailing colon doesn't
    # block them.
    assert _normalise_markdown_line("**Yield Calculation**:") == "Yield Calculation"
    assert _normalise_markdown_line("**Yield Calculation:**") == "Yield Calculation:"


def test_detector_matches_real_bpcr_header_patterns() -> None:
    """End-to-end: a synthetic BPCR with markdown-wrapped section
    headers (mirroring the real doc) must produce sub-section spans
    other than ``unsectioned``. Before the fix, the detector
    returned all 35 pages as unsectioned because every header
    was wrapped in ``**…**`` and sat mid-page in the markdown.
    """

    # Each page replicates the real-doc structure: a repeating
    # document title at the top, then a section-specific bold
    # heading further down.
    pages = []
    page_specs = [
        (1, "**APITORIA PHARMA - BATCH PRODUCTION AND CONTROL RECORD**\nPage 1 of 5"),
        (2, "**APITORIA PHARMA - BATCH PRODUCTION AND CONTROL RECORD**\nPage 2 of 5\n\n**REVISION SUMMARY**\n| col | col |"),
        (3, "**APITORIA PHARMA - BATCH PRODUCTION AND CONTROL RECORD**\nPage 3 of 5\n\n# **LIST OF MAJOR EQUIPMENTS & SOP DETAILS**\n| col | col |"),
        (4, "**APITORIA PHARMA - BATCH PRODUCTION AND CONTROL RECORD**\nPage 4 of 5\n\n**MANUFACTURING INSTRUCTIONS**\nDate & Time of starting:"),
        (5, "**APITORIA PHARMA - BATCH PRODUCTION AND CONTROL RECORD**\nPage 5 of 5\n\n**MICRONIZATION OPERATION**\n| col | col |"),
    ]
    for page_num, md in page_specs:
        pages.append(OCRPageResult(page_num=page_num, markdown=md))

    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="test-real-bpcr",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    # Build a page → section map for assertions.
    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    # The four mid-page bold headers must each be detected — that's
    # the regression this fix targets.
    assert page_to_section.get(2) == "revision_summary", page_to_section
    assert page_to_section.get(3) == "equipment_list", page_to_section
    assert page_to_section.get(4) == "manufacturing_operations", page_to_section
    assert page_to_section.get(5) == "micronization", page_to_section


def test_cover_page_does_not_eat_every_page() -> None:
    """The cover_page regex matches ``Batch Production and Control
    Record`` as one of its aliases. That phrase repeats in EVERY
    BPCR page's header band. Without the page=1 guard, the detector
    classifies every page as cover_page and the assemble-spans pass
    inherits it across the whole document — exactly the symptom
    that hid all 13 sub-sections from a real BPCR run.
    """

    pages = [
        # Page 1: clean cover-page header that the alias matches.
        OCRPageResult(
            page_num=1,
            markdown="Batch Production and Control Record\n\nCover content goes here.",
        ),
        # Pages 2 and 3: the same alias-matching phrase appears in
        # the repeating page header. Without the guard they'd both
        # be classified as cover_page; with the guard they fall
        # back to revision_summary (page 2's actual content) and
        # inheritance (page 3 inherits page 2's section).
        OCRPageResult(
            page_num=2,
            markdown="Batch Production and Control Record\nPage 2 of 3\n\n**REVISION SUMMARY**\n| change |",
        ),
        OCRPageResult(
            page_num=3,
            markdown="Batch Production and Control Record\nPage 3 of 3\n\nbody body body",
        ),
    ]

    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="cover-page-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    # Page 1 matches cover_page (clean alias hit, no guard kicks in).
    assert page_to_section.get(1) == "cover_page", page_to_section
    # Pages 2 and 3 must NOT be cover_page — that's the bug fix.
    # Page 2 has its own marker (REVISION SUMMARY) so it gets its
    # own classification; page 3 inherits from page 2 per the
    # standard span-assembly rules.
    assert page_to_section.get(2) != "cover_page", (
        f"page 2 must not inherit cover_page from a header repetition; "
        f"got {page_to_section.get(2)!r}"
    )
    assert page_to_section.get(3) != "cover_page", page_to_section.get(3)
