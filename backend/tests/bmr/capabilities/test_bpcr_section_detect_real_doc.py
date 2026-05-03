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


def test_cover_page_matches_when_title_is_embedded_in_a_longer_header() -> None:
    """Real BPCRs put the document title at the top of every page in
    a long composite header alongside the company name and unit
    block — e.g. ``# **APITORIA PHARMA PRIVATE LIMITED, UNIT-II
    PRODUCTION BLOCK – E BATCH PRODUCTION AND CONTROL RECORD**``.
    The original ``^\\s*<alias>\\b`` anchor never matched because the
    title sat mid-line behind the company name. PR #25 relaxed the
    anchor for cover_page only (the page=1 guard prevents
    over-matching on subsequent pages). This test pins that behaviour
    against regression.
    """

    pages = [
        OCRPageResult(
            page_num=1,
            markdown=(
                "![](logo.jpg)\n"
                "# **APITORIA PHARMA PRIVATE LIMITED, UNIT-II PRODUCTION "
                "BLOCK – E BATCH PRODUCTION AND CONTROL RECORD**\n"
                "Page 1 of 35"
            ),
        ),
        # Page 2 carries the same composite header line — without the
        # page=1 guard, the relaxed anchor would inherit cover_page
        # forward across the whole document. The guard prevents that.
        OCRPageResult(
            page_num=2,
            markdown=(
                "# **APITORIA PHARMA PRIVATE LIMITED, UNIT-II PRODUCTION "
                "BLOCK – E BATCH PRODUCTION AND CONTROL RECORD**\n"
                "Page 2 of 35\n\n**REVISION SUMMARY**"
            ),
        ),
    ]
    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="embedded-title-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    assert page_to_section.get(1) == "cover_page", (
        f"page 1's embedded title must match cover_page after the anchor "
        f"relaxation; got {page_to_section.get(1)!r}"
    )
    assert page_to_section.get(2) == "revision_summary", (
        f"page 2 must classify by its own marker (REVISION SUMMARY), "
        f"not inherit cover_page from the repeating header line; "
        f"got {page_to_section.get(2)!r}"
    )


def test_transition_page_picks_new_section_over_continuation_header() -> None:
    """A real BPCR transition page carries TWO markers: the previous
    section's repeating bold header (continuation) + the next
    section's first-time mention (transition). The pre-PR detector
    picked the highest-confidence candidate per page, which always
    favoured the bold continuation header — the new section was
    invisible until a page where only it matched.

    On the user's Apitoria BPCR (2026-04-30 validation), p29 has:

      - ``**SIFTING RECORD (Ref SOP No: UIIMF052)**``  (continuation
        of the sifting span that started on p21; primary regex hit
        on bold top_of_page → confidence 1.0)
      - ``Co-Mill operation``  (new section's introduction; primary
        regex hit on plain mid_page → confidence 0.6)

    Pre-fix, sifting won and co_mill_operation was missing entirely
    from the output. Post-fix, the transition rule swaps to the
    new candidate when:

      1. The best candidate matches the previous page's section
         (continuation), AND
      2. A lower-ranked candidate names a different section, AND
      3. That candidate's confidence ≥ 0.6 (mid_page primary or
         stronger — protects against stray template mentions).

    This test pins that behaviour against regression.
    """

    pages = [
        # p1-3: a normal section run that establishes ``sifting_record``
        # as the previous-section context for the transition page.
        OCRPageResult(
            page_num=1,
            markdown="Cover page placeholder.",
        ),
        OCRPageResult(
            page_num=2,
            markdown="**SIFTING RECORD (Ref SOP No: UIIMF052)**\n| col |",
        ),
        OCRPageResult(
            page_num=3,
            # Real-world transition page: bold continuation header +
            # plain new-section text. Pre-fix: sifting wins by
            # confidence (1.0 vs 0.6). Post-fix: co_mill picked via
            # transition rule.
            markdown=(
                "**SIFTING RECORD (Ref SOP No: UIIMF052)**\n"
                "(table content)\n"
                "Co-Mill operation"
            ),
        ),
    ]
    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="multi-section-page-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    assert page_to_section.get(2) == "sifting_record", (
        "p2 should anchor sifting_record as the prior context"
    )
    assert page_to_section.get(3) == "co_mill_operation", (
        f"p3's transition rule must pick co_mill_operation over the "
        f"sifting_record continuation header; got "
        f"{page_to_section.get(3)!r}. This is the gap PR #28 closes — "
        f"without it, the new section is invisible on transition pages."
    )


def test_continuation_header_alone_does_not_force_transition() -> None:
    """The transition rule must not break section spans on pages that
    carry ONLY the previous section's continuation header. If we
    over-trigger transitions, multi-page sections fragment into
    one-page spans (or disappear into ``unsectioned`` runs)."""

    pages = [
        OCRPageResult(page_num=1, markdown="Cover page."),
        OCRPageResult(
            page_num=2,
            markdown="**SIFTING RECORD (Ref SOP No: UIIMF052)**\n| col |",
        ),
        # p3-5: continuation of sifting with the bold repeating header
        # but NO other section's marker. Span must stay sifting_record
        # all the way through.
        OCRPageResult(
            page_num=3,
            markdown="**SIFTING RECORD (Ref SOP No: UIIMF052)**\n(table content)",
        ),
        OCRPageResult(
            page_num=4,
            markdown="**SIFTING RECORD (Ref SOP No: UIIMF052)**\n(more table)",
        ),
        OCRPageResult(
            page_num=5,
            markdown="**SIFTING RECORD (Ref SOP No: UIIMF052)**\n(footer)",
        ),
    ]
    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="continuation-only-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    for p in (2, 3, 4, 5):
        assert page_to_section.get(p) == "sifting_record", (
            f"p{p} should remain sifting_record (continuation); got "
            f"{page_to_section.get(p)!r}. The transition rule must "
            f"not trigger on pages with no new-section marker."
        )


def test_low_confidence_transition_candidate_does_not_force_transition() -> None:
    """A faint mention of another section (e.g. \"yield\" appearing in
    a manufacturing-operations page's narrative) must NOT be enough
    to break the span. The 0.6 floor is the safety rail — anything
    below it stays as continuation."""

    pages = [
        OCRPageResult(page_num=1, markdown="Cover."),
        # Build a page where ``yield_calculation`` only matches via an
        # alias far down the line list (low synthetic y_fraction).
        # The alias-only confidence is 0.4, below the 0.6 transition
        # floor, so the span must stay on manufacturing_operations.
        OCRPageResult(
            page_num=2,
            markdown=(
                "**MANUFACTURING INSTRUCTIONS**\n"
                "Step 1: Add raw materials.\n"
                "Step 2: Mix at 200 rpm.\n"
                "Step 3: Verify final yield calculation matches "
                "the batch target."
            ),
        ),
    ]
    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="low-conf-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    assert page_to_section.get(2) == "manufacturing_operations", (
        "p2 must stay on manufacturing_operations — the 'final yield "
        "calculation' phrase is below the 0.6 transition floor and "
        "must not break the span"
    )


def test_real_doc_alias_additions_cover_yield_cleaning_deviation() -> None:
    """v1.2.0 spec adds three alias families that close gaps observed
    on the Apitoria BPCR (2026-04-30 validation). This test exercises
    each new alias in isolation against a synthetic page that mirrors
    the real-doc heading wording.
    """

    pages = [
        OCRPageResult(page_num=1, markdown="Cover content."),
        OCRPageResult(page_num=2, markdown="**YIELD DETAILS**\nTheoretical: 100 kg"),
        OCRPageResult(page_num=3, markdown="**EQUIPMENT CLEANING DETAILS**\n| equipment | status |"),
        OCRPageResult(page_num=4, markdown="**DESCRIPTION OF DEVIATIONS OBSERVED DURING BATCH PROCESSING**"),
    ]
    spec = load_spec()
    result = detect_bpcr_sections(
        doc_id="alias-coverage-test",
        ocr=OCRResult(pages=pages),
        sections_spec=spec,
        mode="heuristic",
    )

    page_to_section = {}
    for span in result.spans:
        for p in range(span.start_page, span.end_page + 1):
            page_to_section[p] = span.section_id

    assert page_to_section.get(2) == "yield_calculation", (
        f"'YIELD DETAILS' should match the new yield_calculation alias; "
        f"got {page_to_section.get(2)!r}"
    )
    assert page_to_section.get(3) == "cleaning_log", (
        f"'EQUIPMENT CLEANING DETAILS' should match the new cleaning_log alias; "
        f"got {page_to_section.get(3)!r}"
    )
    assert page_to_section.get(4) == "deviation", (
        f"'DESCRIPTION OF DEVIATIONS OBSERVED' should match the new deviation alias; "
        f"got {page_to_section.get(4)!r}"
    )


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
