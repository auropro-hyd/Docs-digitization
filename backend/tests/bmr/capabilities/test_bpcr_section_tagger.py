"""Tagger tests (Spec 007 capability-contract).

The tagger walks an :class:`ExtractedPackage` and stamps ``section_id``
on pages whose ``doc_id`` is in the supplied section maps. It MUST:

- not mutate the input package or any nested page,
- only touch BPCR-role pages,
- pass non-BPCR pages through unchanged,
- be idempotent.
"""

from __future__ import annotations

from app.bmr.capabilities.bpcr_section_detect import (
    BPCRSectionMap,
    SectionSpan,
)
from app.bmr.capabilities.bpcr_section_tagger import tag_bpcr_pages
from app.bmr.capabilities.bpcr_sections_spec import UNSECTIONED_ID
from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)


def _bpcr_page(page_index: int) -> ExtractedPage:
    return ExtractedPage(
        doc_id="bpcr",
        document_role="BPCR",
        page_index=page_index,
        fields=[
            FieldValue(
                field="dispensed_weight_kg",
                value="10.0",
                source_doc_id="bpcr",
                source_page_index=page_index,
            )
        ],
    )


def _other_page(role: str, page_index: int) -> ExtractedPage:
    return ExtractedPage(
        doc_id=role.lower(),
        document_role=role,
        page_index=page_index,
    )


def _two_section_map() -> BPCRSectionMap:
    return BPCRSectionMap(
        doc_id="bpcr",
        spec_version="test-1.0",
        method="heuristic",
        outcome="ok",
        spans=[
            SectionSpan(
                section_id="material_dispensing",
                display_name="Material Dispensing",
                start_page=1,
                end_page=2,
                confidence=1.0,
                detection_method="heuristic_top_of_page",
            ),
            SectionSpan(
                section_id="yield_calculation",
                display_name="Yield Calculation",
                start_page=3,
                end_page=4,
                confidence=0.85,
                detection_method="heuristic_top_of_table",
            ),
        ],
    )


# ── Behaviour ──────────────────────────────────────────────────────────────


def test_tagger_stamps_bpcr_pages_with_resolved_section_id() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[
            _bpcr_page(1),
            _bpcr_page(2),
            _bpcr_page(3),
            _bpcr_page(4),
            _other_page("BMR", 1),
        ],
    )
    tagged = tag_bpcr_pages(package, section_maps={"bpcr": _two_section_map()})

    assert [p.section_id for p in tagged.pages[:4]] == [
        "material_dispensing",
        "material_dispensing",
        "yield_calculation",
        "yield_calculation",
    ]
    # Non-BPCR page is untouched.
    assert tagged.pages[-1].section_id is None


def test_tagger_uses_unsectioned_for_pages_outside_any_span() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[_bpcr_page(1), _bpcr_page(99)],
    )
    # The map only covers pages 1..4 — page 99 falls outside.
    tagged = tag_bpcr_pages(package, section_maps={"bpcr": _two_section_map()})

    assert tagged.pages[0].section_id == "material_dispensing"
    assert tagged.pages[1].section_id == UNSECTIONED_ID


def test_tagger_skips_unknown_doc_ids() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[_bpcr_page(1)],
    )
    # Map for a doc_id the package doesn't contain — must not raise.
    other_map = BPCRSectionMap(
        doc_id="other",
        spec_version="test-1.0",
        method="heuristic",
        outcome="ok",
        spans=[
            SectionSpan(
                section_id="cover",
                display_name="Cover",
                start_page=1,
                end_page=1,
                confidence=1.0,
                detection_method="heuristic_top_of_page",
            )
        ],
    )
    tagged = tag_bpcr_pages(package, section_maps={"other": other_map})

    # The bpcr page's section_id stays None because no map references it.
    assert tagged.pages[0].section_id is None


def test_input_package_is_not_mutated() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[_bpcr_page(1), _bpcr_page(3)],
    )
    snapshot_before = package.model_dump_json()
    _ = tag_bpcr_pages(package, section_maps={"bpcr": _two_section_map()})
    assert package.model_dump_json() == snapshot_before


def test_tagger_is_idempotent() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[_bpcr_page(1), _bpcr_page(2)],
    )
    once = tag_bpcr_pages(package, section_maps={"bpcr": _two_section_map()})
    twice = tag_bpcr_pages(once, section_maps={"bpcr": _two_section_map()})
    assert once.model_dump_json() == twice.model_dump_json()


def test_empty_section_maps_returns_input_unchanged() -> None:
    package = ExtractedPackage(
        package_id="pkg",
        pages=[_bpcr_page(1)],
    )
    tagged = tag_bpcr_pages(package, section_maps={})
    assert tagged is package  # short-circuit: identity
