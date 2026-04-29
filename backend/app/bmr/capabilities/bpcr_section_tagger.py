"""Stamp BPCR section_id onto an ExtractedPackage (Spec 007).

Pure function. Walks the package; for every page whose ``doc_id``
appears in ``section_maps`` and whose ``document_role == 'BPCR'``,
returns a fresh :class:`ExtractedPage` with ``section_id`` populated
from the matching :class:`BPCRSectionMap`. All other pages pass
through unchanged. The original aggregate is not mutated — both
:class:`ExtractedPackage` and :class:`ExtractedPage` are frozen
Pydantic models, so the tagger constructs new instances.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from app.bmr.capabilities.bpcr_section_detect import BPCRSectionMap
from app.bmr.capabilities.bpcr_sections_spec import UNSECTIONED_ID
from app.bmr.capabilities.extracted_data import ExtractedPackage, ExtractedPage

logger = logging.getLogger(__name__)


def tag_bpcr_pages(
    package: ExtractedPackage,
    *,
    section_maps: Mapping[str, BPCRSectionMap],
) -> ExtractedPackage:
    """Return a new :class:`ExtractedPackage` with BPCR pages tagged.

    Args:
        package: as emitted by Stage 3 extraction.
        section_maps: ``{doc_id: BPCRSectionMap}`` from the detector.
            Documents not in this dict are passed through unchanged.

    Returns:
        A new :class:`ExtractedPackage` with the same ``package_id``
        and the same ordering of pages. Pages whose ``doc_id`` is in
        ``section_maps`` carry a populated ``section_id`` (the literal
        string ``"unsectioned"`` when the detector couldn't assign a
        canonical section to that page).

    Failure modes:
        - ``doc_id`` in ``section_maps`` but no page in ``package``
          for that doc_id: the entry is ignored.
        - Page index outside any span in the corresponding map:
          ``section_id = "unsectioned"`` (fail-open).
    """

    if not section_maps:
        return package

    started = time.perf_counter()
    n_pages_tagged = 0

    new_pages: list[ExtractedPage] = []
    for page in package.pages:
        section_map = section_maps.get(page.doc_id)
        if section_map is None or page.document_role != "BPCR":
            new_pages.append(page)
            continue

        span = section_map.span_for_page(page.page_index)
        if span is not None:
            update: dict[str, object] = {
                "section_id": span.section_id,
                "section_display_name": span.display_name,
                "section_confidence": span.confidence,
                "section_detection_method": span.detection_method,
            }
        else:
            # Page outside every span — the tagger fails open by stamping
            # ``unsectioned`` so downstream rules can still filter on the
            # field without a None-check. No span means no confidence
            # signal to surface.
            update = {
                "section_id": UNSECTIONED_ID,
                "section_display_name": None,
                "section_confidence": None,
                "section_detection_method": None,
            }
        new_pages.append(page.model_copy(update=update))
        n_pages_tagged += 1

    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "bpcr.section_tag.exit package_id=%s n_pages_tagged=%d n_bpcr_docs=%d "
        "duration_ms=%d",
        package.package_id,
        n_pages_tagged,
        len(section_maps),
        duration_ms,
    )

    return package.model_copy(update={"pages": new_pages})


__all__ = ["tag_bpcr_pages"]
