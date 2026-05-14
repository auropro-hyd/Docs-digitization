"""Pin the segmentation post-process pipeline.

Akhilesh's 2026-05-13 voice notes on a fresh segmentation surfaced
five classes of issue the prompt cues alone couldn't close:

1. ``section_type='unsectioned'`` (and other LLM-emitted free-form
   types not in document_profiles.yaml) leaking into the output.
2. Hallucinated page ranges — most notably a ``BPCR Review Check
   List`` section emitted with ``start_page=1, end_page=2`` that
   overlaps the real cover page.
3. Overlaps between sections that double-bill page counts.
4. Raw material packets where every sub-section's
   ``section_type`` echoes the ``document_type``
   (``raw_material_request``) instead of using the proper sub-type
   (``material_request`` / ``material_issue`` /
   ``packing_material_request`` / ``solvent_transfer_note``).
5. SCADA cluster sections emitted with non-canonical names
   (``data_monitoring_parameters`` / ``alarm_log``) that
   downstream cross-section filters can't resolve.

Three deterministic post-processes close these:

* ``clamp_page_ranges()`` — clip end_page > total_pages; drop
  sections whose start_page falls outside the doc.
* ``resolve_overlaps()`` — walk sorted sections and clamp any
  overlapping range to start after the previous section ends.
* ``normalize_section_types_to_canonical()`` — fold drift onto
  canonical types via the profile alias map, preserve known
  whole-doc-as-section emissions, collapse the rest to
  ``"unknown"``.
"""

from __future__ import annotations

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation import (
    clamp_page_ranges,
    normalize_section_types_to_canonical,
    resolve_overlaps,
)


def _seg(*sections: DocumentSection) -> DocumentSegmentation:
    return DocumentSegmentation(
        sections=list(sections),
        document_type="batch_record",
        confidence=0.9,
    )


def _sec(
    section_id: str,
    start: int,
    end: int,
    *,
    section_type: str = "manufacturing_operations",
    document_type: str = "batch_record",
    name: str = "",
) -> DocumentSection:
    return DocumentSection(
        section_id=section_id,
        name=name or section_id.replace("_", " ").title(),
        section_type=section_type,
        document_type=document_type,
        start_page=start,
        end_page=end,
        description="",
    )


# ── clamp_page_ranges ──────────────────────────────────────────


def test_clamp_clips_end_page_beyond_total_pages() -> None:
    """A section ending after the last page is the most common
    LLM artefact (off-by-one on the final page). Clip
    ``end_page`` to ``total_pages`` rather than dropping the
    section entirely — the content is real, just over-reported."""

    seg = _seg(_sec("manufacturing", 1, 130))
    out = clamp_page_ranges(seg, total_pages=115)
    assert len(out.sections) == 1
    assert out.sections[0].end_page == 115


def test_clamp_drops_section_whose_start_is_beyond_doc() -> None:
    """If the LLM hallucinates a section starting past the last
    page, we have no evidence where it actually belongs — drop it
    rather than invent a position. ``fill_gaps_with_unknown`` will
    catch the resulting gap and emit an explicit unknown section
    that HITL can re-classify."""

    seg = _seg(
        _sec("real", 1, 100),
        _sec("hallucinated", 200, 250),
    )
    out = clamp_page_ranges(seg, total_pages=115)
    assert {s.section_id for s in out.sections} == {"real"}


def test_clamp_clips_negative_start_page() -> None:
    seg = _seg(_sec("manufacturing", -3, 5))
    out = clamp_page_ranges(seg, total_pages=20)
    assert out.sections[0].start_page == 1


def test_clamp_is_idempotent() -> None:
    """Running the clamp twice produces the same output — the
    pipeline reruns it after re-segmentation and we don't want
    double-clipping."""

    seg = _seg(_sec("manufacturing", 1, 130))
    once = clamp_page_ranges(seg, total_pages=115)
    twice = clamp_page_ranges(once, total_pages=115)
    assert once.model_dump() == twice.model_dump()


def test_clamp_noop_when_total_pages_zero() -> None:
    """We don't know how to clip when the caller hasn't supplied
    a page count. Pass-through rather than guessing."""

    seg = _seg(_sec("manufacturing", 1, 130))
    out = clamp_page_ranges(seg, total_pages=0)
    assert out.sections[0].end_page == 130


# ── resolve_overlaps ───────────────────────────────────────────


def test_overlap_clamped_to_disjoint_ranges() -> None:
    """The 2026-05-13 example: 75-87 and 80-98 overlap on 80-87.
    Clamp the later section to start at 88 so the output is
    strictly disjoint and the compliance pipeline doesn't
    double-count pages."""

    seg = _seg(
        _sec("a", 75, 87, section_type="instrument_data_log"),
        _sec("b", 80, 98, section_type="instrument_data_log"),
    )
    out = resolve_overlaps(seg)
    a = next(s for s in out.sections if s.section_id == "a")
    b = next(s for s in out.sections if s.section_id == "b")
    assert (a.start_page, a.end_page) == (75, 87)
    assert (b.start_page, b.end_page) == (88, 98)


def test_overlap_dropped_when_fully_contained() -> None:
    """A duplicate section emitted entirely within an existing
    span (e.g. LLM emits both a parent and a duplicate sub-section
    on the same pages) gets dropped, not zero-width clamped."""

    seg = _seg(
        _sec("parent", 1, 29, section_type="manufacturing_operations"),
        _sec("dup", 5, 10, section_type="manufacturing_operations"),
    )
    out = resolve_overlaps(seg)
    assert {s.section_id for s in out.sections} == {"parent"}


def test_overlap_handles_the_voice_notes_bpcr_review_case() -> None:
    """The user's exact example: a 'BPCR Review Check List' section
    emitted at p1-2 overlapping the real cover_page at p1. After
    clamp + resolve_overlaps it either drops entirely (fully
    contained) or moves out of the way of the cover page."""

    seg = _seg(
        _sec("cover", 1, 1, section_type="cover_page"),
        _sec("bpcr_review", 1, 2, section_type="bpcr_review_checklist"),
    )
    out = resolve_overlaps(seg)
    # Cover wins because it starts at the same page and sorts first
    # by (start_page, end_page) when end_page is smaller.
    cover = next(s for s in out.sections if s.section_id == "cover")
    assert cover.start_page == 1 and cover.end_page == 1
    # bpcr_review_checklist either dropped or clamped to p2-2.
    bpcr_rows = [s for s in out.sections if s.section_id == "bpcr_review"]
    if bpcr_rows:
        assert bpcr_rows[0].start_page == 2


def test_resolve_overlaps_is_idempotent() -> None:
    seg = _seg(
        _sec("a", 75, 87),
        _sec("b", 80, 98),
    )
    once = resolve_overlaps(seg)
    twice = resolve_overlaps(once)
    assert once.model_dump() == twice.model_dump()


# ── normalize_section_types_to_canonical ───────────────────────


def test_alias_folds_to_canonical_type() -> None:
    """``data_monitoring_parameters`` is an alias for
    ``instrument_data_log`` (SCADA profile). After normalisation
    every cross-section filter keyed off the canonical name
    resolves correctly."""

    seg = _seg(_sec(
        "vde002_data",
        77, 87,
        section_type="data_monitoring_parameters",
        document_type="scada_report",
    ))
    out = normalize_section_types_to_canonical(seg)
    assert out.sections[0].section_type == "instrument_data_log"


def test_unsectioned_collapses_to_unknown() -> None:
    """The LLM occasionally emits ``unsectioned`` for spans it
    can't classify; collapse to the canonical ``unknown`` so
    downstream rules degrade safely."""

    seg = _seg(_sec(
        "rogue",
        50, 55,
        section_type="unsectioned",
        document_type="batch_record",
    ))
    out = normalize_section_types_to_canonical(seg)
    assert out.sections[0].section_type == "unknown"


def test_whole_doc_as_section_preserved() -> None:
    """When the LLM treats a whole IPC report as one section it
    emits ``section_type='in_process_report'`` or
    ``section_type='ipc_report'``. Both resolve to the canonical
    ``in_process_report`` (a known section_type alias) — should
    NOT collapse to unknown."""

    seg = _seg(_sec(
        "ipc",
        113, 115,
        section_type="ipc_report",
        document_type="ipc_report",
    ))
    out = normalize_section_types_to_canonical(seg)
    # in_process_report is a defined section_alias value, so it's a
    # known section_type and the normaliser folds the alias to it.
    assert out.sections[0].section_type == "in_process_report"


def test_raw_material_request_kept_as_whole_doc_section_type() -> None:
    """When the section_type matches a known document_type but no
    section_type, preserve it — it's the LLM treating the whole
    sub-document as one section. The voice notes specifically
    flagged this for raw_material_request: the user wants the
    prompt to make the LLM emit a sub-type like ``material_request``
    but the post-process must NOT collapse to ``unknown`` either
    (would lose all rule applicability)."""

    seg = _seg(_sec(
        "rm",
        30, 44,
        section_type="raw_material_request",
        document_type="raw_material_request",
    ))
    out = normalize_section_types_to_canonical(seg)
    assert out.sections[0].section_type == "raw_material_request"


def test_existing_unknown_passthrough() -> None:
    """Sections already typed ``unknown`` (e.g. from
    ``fill_gaps_with_unknown``) pass through untouched."""

    seg = _seg(_sec("gap", 60, 65, section_type="unknown"))
    out = normalize_section_types_to_canonical(seg)
    assert out.sections[0].section_type == "unknown"


def test_drift_to_unknown_for_non_canonical_types() -> None:
    """A free-form value the LLM invented that matches neither a
    section_type, alias, nor doc_type → unknown."""

    seg = _seg(_sec("rogue", 10, 12, section_type="some_made_up_type"))
    out = normalize_section_types_to_canonical(seg)
    assert out.sections[0].section_type == "unknown"


def test_normaliser_is_idempotent() -> None:
    seg = _seg(_sec(
        "vde002_data",
        77, 87,
        section_type="data_monitoring_parameters",
    ))
    once = normalize_section_types_to_canonical(seg)
    twice = normalize_section_types_to_canonical(once)
    assert once.model_dump() == twice.model_dump()


# ── Pipeline order ─────────────────────────────────────────────


def test_full_postprocess_chain_handles_user_voice_notes_case() -> None:
    """The exact pathological case from Akhilesh's 2026-05-13
    voice notes: BPCR Review Checklist hallucinated at p1-2,
    SCADA section_type drift, a raw_material section echoing its
    doc_type, and a section ending past the last page. After the
    full chain runs we should have:
    - no page ranges outside [1, total_pages]
    - no overlapping page ranges
    - SCADA section_type folded to canonical
    - raw_material section_type preserved as-is (whole-doc pattern)
    - bpcr_review_checklist at p1-2 either dropped (contained in
      cover) or clamped past the cover page."""

    seg = _seg(
        _sec("cover", 1, 1, section_type="cover_page"),
        _sec("rm", 30, 44, section_type="raw_material_request",
             document_type="raw_material_request"),
        _sec("vde", 77, 87, section_type="data_monitoring_parameters",
             document_type="scada_report"),
        _sec("over_end", 110, 200, section_type="alarm_log",
             document_type="scada_report"),
        _sec("bpcr_review", 1, 2, section_type="bpcr_review_checklist"),
    )

    out = clamp_page_ranges(seg, total_pages=115)
    out = resolve_overlaps(out)
    out = normalize_section_types_to_canonical(out)

    # No page ranges past the doc.
    for s in out.sections:
        assert 1 <= s.start_page <= s.end_page <= 115

    # No overlaps.
    sorted_secs = sorted(out.sections, key=lambda s: s.start_page)
    for prev, cur in zip(sorted_secs, sorted_secs[1:]):
        assert cur.start_page > prev.end_page, (
            f"overlap left after resolve_overlaps: {prev.section_id} "
            f"({prev.start_page}-{prev.end_page}) and {cur.section_id} "
            f"({cur.start_page}-{cur.end_page})"
        )

    # SCADA folded.
    vde = next(s for s in out.sections if s.section_id == "vde")
    assert vde.section_type == "instrument_data_log"

    # alarm_log also folded.
    over_end = next(s for s in out.sections if s.section_id == "over_end")
    assert over_end.section_type == "instrument_alarm_log"
    assert over_end.end_page == 115  # clipped

    # raw_material doc-as-section preserved.
    rm = next(s for s in out.sections if s.section_id == "rm")
    assert rm.section_type == "raw_material_request"
