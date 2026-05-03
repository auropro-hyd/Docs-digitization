"""Document segmentation service.

Identifies distinct sub-documents within a multi-part document packet using
a single LLM call.  Section types are free-form (LLM-generated), not enums.

A second pass — :func:`enrich_with_bpcr_sub_sections` — drills into any
section the LLM classified as a batch_record / BPCR and runs the
heuristic BPCR section detector (Spec 007) over the per-page markdown
to populate the 13 canonical sub-sections (cover_page,
material_dispensing, yield_calculation, …). The legacy compliance
pipeline previously stopped at the document-boundary level and treated
the whole BPCR as one opaque 35-page block; this enrichment closes the
gap that Akhilesh flagged on 2026-04-28 ("still not returning the
sections within batch_record") for the legacy pipeline, mirroring what
PR #9 already did for the new BMR pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.compliance.models import (
    BpcrSubSection,
    DocumentSection,
    DocumentSegmentation,
)
from app.compliance.rules.profiles import (
    infer_document_type_for_section_type,
    normalize_section_type,
)
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)


# Section-type substrings that flag a document section as a BPCR.
# The compliance LLM emits free-form snake_case section_types so we
# match by substring rather than equality. Kept narrow on purpose —
# only types that explicitly mention "batch_record",
# "batch_production_and_control", or the well-known abbreviation
# "bpcr" are treated as BPCRs. Adjacent types
# ("batch_packaging_record", "batch_release_record") are NOT
# auto-detected to avoid running the wrong section spec on the wrong
# document.
_BPCR_SECTION_TYPE_HINTS: tuple[str, ...] = (
    "batch_record",
    "batch_production_and_control_record",
    "batch_production_record",
    "bpcr",
)


def _looks_like_bpcr(section_type: str) -> bool:
    """True when ``section_type`` matches one of the BPCR hints.

    Pulled into a helper so the test suite can pin the exact
    inclusion / exclusion set.
    """

    if not section_type:
        return False
    needle = section_type.lower()
    return any(hint in needle for hint in _BPCR_SECTION_TYPE_HINTS)


def stamp_document_types(seg: DocumentSegmentation) -> DocumentSegmentation:
    """Fill empty ``DocumentSection.document_type`` fields deterministically.

    Inputs may arrive with ``document_type`` already populated (the
    LLM-driven segmentation prompt asks for it) or empty (legacy
    runs, fallback paths, BPCR enrichment). For each section whose
    field is empty we apply two rules in order:

    1. **BPCR hint**: a section whose ``section_type`` looks like a
       BPCR (matches ``_BPCR_SECTION_TYPE_HINTS``) is stamped
       ``"batch_record"`` — the BPCR detector and the legacy
       enrichment block both treat batch_record as the implicit
       owner of that section, so making it explicit costs nothing
       and lights up cross-document filters.
    2. **Profile inference**: otherwise look the section_type up in
       ``document_profiles.yaml`` (single-owner expected_sections)
       via :func:`infer_document_type_for_section_type`. If the
       section_type is listed under exactly one profile, stamp that.
       If listed under multiple (or none), leave the field empty —
       the cross-document filter will degrade to section-type-only
       matching, which is the safe default.

    Pure: returns a new ``DocumentSegmentation`` instance, never
    mutates the input. Idempotent: running it twice produces the
    same result.
    """

    updated: list[DocumentSection] = []
    for section in seg.sections:
        if section.document_type:
            updated.append(section)
            continue

        if _looks_like_bpcr(section.section_type):
            updated.append(section.model_copy(update={"document_type": "batch_record"}))
            continue

        inferred = infer_document_type_for_section_type(section.section_type)
        if inferred:
            updated.append(section.model_copy(update={"document_type": inferred}))
        else:
            updated.append(section)

    return seg.model_copy(update={"sections": updated})


def enrich_with_bpcr_sub_sections(
    seg: DocumentSegmentation,
    extractions: list[dict],
) -> DocumentSegmentation:
    """Drill BPCR-classified sections into their 13 canonical sub-sections.

    Runs the heuristic detector (Spec 007) against the per-page
    markdown carried in ``extractions``. The detector lives in
    :mod:`app.bmr.capabilities.bpcr_section_detect`; it's pure and
    has no service dependencies, so we can call it from the legacy
    compliance pipeline without touching the BMR run plumbing.

    Returns a NEW :class:`DocumentSegmentation` with ``sub_sections``
    populated on any section whose ``section_type`` matches the
    BPCR hints. Other sections are passed through unchanged. Empty
    output (no detector hits) leaves ``sub_sections`` empty rather
    than synthesising fake entries — empty is the operator signal
    that detection didn't fire and the section should be reviewed
    as a single block (the prior behaviour).

    Fail-open: any exception in the detector or spec loader is
    logged and the original segmentation is returned unchanged.
    Compliance must never fail because section detection failed.
    """

    try:
        from app.bmr.capabilities.bpcr_section_detect import (
            detect_bpcr_sections,
        )
        from app.bmr.capabilities.bpcr_sections_spec import load_spec
        from app.core.ports.ocr import OCRPageResult, OCRResult
    except Exception:  # pragma: no cover — defensive
        logger.exception("BPCR detector imports failed; skipping enrichment")
        return seg

    bpcr_sections = [s for s in seg.sections if _looks_like_bpcr(s.section_type)]
    if not bpcr_sections:
        return seg

    try:
        spec = load_spec()
    except Exception:
        logger.warning(
            "BPCR sections spec failed to load; legacy compliance "
            "segmentation will not include sub_sections this run",
            exc_info=True,
        )
        return seg

    # Build a page_num → markdown lookup once for the whole doc.
    md_by_page: dict[int, str] = {}
    for ext in extractions:
        page_num = int(ext.get("page_num", 0) or 0)
        md = ext.get("markdown") or ""
        if page_num and md:
            md_by_page[page_num] = md

    enriched_sections: list[DocumentSection] = []
    for section in seg.sections:
        if not _looks_like_bpcr(section.section_type):
            enriched_sections.append(section)
            continue

        # Synthesise an OCRResult covering only this section's pages.
        # The detector consumes ``OCRPageResult.markdown`` plus
        # word-level layout (we don't have that on the legacy pipeline
        # so the heuristic falls back to the markdown-only path).
        page_results: list[OCRPageResult] = []
        for p in range(section.start_page, section.end_page + 1):
            md = md_by_page.get(p, "")
            if md.strip():
                page_results.append(OCRPageResult(page_num=p, markdown=md))

        if not page_results:
            logger.info(
                "BPCR section %s has no markdown for pages %d–%d; "
                "skipping sub-section detection",
                section.section_id, section.start_page, section.end_page,
            )
            enriched_sections.append(section)
            continue

        ocr = OCRResult(pages=page_results)
        try:
            section_map = detect_bpcr_sections(
                doc_id=section.section_id,
                ocr=ocr,
                sections_spec=spec,
                mode="heuristic",
            )
        except Exception:
            logger.exception(
                "BPCR detector raised on section %s; falling back to "
                "single-section view for this run",
                section.section_id,
            )
            enriched_sections.append(section)
            continue

        # Walk the section_map's spans and emit one BpcrSubSection per
        # page. The wire shape mirrors the BMR pipeline's row format
        # so a future shared frontend component can render either.
        sub_sections: list[BpcrSubSection] = []
        for span in section_map.spans:
            for page_index in range(span.start_page, span.end_page + 1):
                sub_sections.append(BpcrSubSection(
                    section_id=span.section_id,
                    display_name=span.display_name,
                    page_index=page_index,
                    confidence=span.confidence,
                    detection_method=span.detection_method,
                ))

        enriched_sections.append(
            section.model_copy(update={"sub_sections": sub_sections})
        )

    return stamp_document_types(
        seg.model_copy(update={"sections": enriched_sections})
    )

_SYSTEM = (
    "You are a document structure analyst for pharmaceutical regulatory documents. "
    "You identify distinct sub-documents within a scanned document packet. "
    "You MUST respond with valid JSON matching the provided schema."
)

_CHARS_PER_PAGE = 500


def _build_segmentation_prompt(
    extractions: list[dict],
    key_value_pairs: list[dict] | None = None,
    filename: str = "",
) -> str:
    page_summaries = []
    for ext in extractions:
        page_num = ext.get("page_num", 0)
        md = ext.get("markdown", "")
        page_summaries.append(f"Page {page_num}: {md[:_CHARS_PER_PAGE]}")

    kv_text = "None extracted"
    if key_value_pairs:
        kv_text = "\n".join(
            f"- {kv.get('key', '?')}: {kv.get('value', '?')}"
            for kv in key_value_pairs[:30]
        )

    return (
        f"Analyze this multi-part document and identify each distinct sub-document/section.\n\n"
        f"Look for: page numbering restarts, document titles, headers that change, "
        f"form layout shifts, and content topic changes.\n\n"
        f"FILENAME: {filename}\n\n"
        f"KEY-VALUE PAIRS:\n{kv_text}\n\n"
        f"PAGE SUMMARIES:\n" + "\n\n".join(page_summaries) + "\n\n"
        f"For each section return:\n"
        f"- section_id: short lowercase_snake_case slug\n"
        f"- name: descriptive human-readable name\n"
        f"- section_type: descriptive type in lowercase_snake_case (be specific)\n"
        f"- start_page / end_page: inclusive page range\n"
        f"- description: brief description of the section content\n\n"
        f"Also return the overall document_type and your confidence (0.0-1.0)."
    )


class DocumentSegmenter:
    """Identifies sub-documents within a document packet via LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def segment(
        self,
        extractions: list[dict],
        key_value_pairs: list[dict] | None = None,
        filename: str = "",
        total_pages: int = 0,
    ) -> DocumentSegmentation:
        prompt = _build_segmentation_prompt(extractions, key_value_pairs, filename)
        try:
            result = await self._llm.generate_structured(
                prompt, DocumentSegmentation, system=_SYSTEM,
            )
            if not isinstance(result, DocumentSegmentation):
                result = DocumentSegmentation.model_validate(result)
            return stamp_document_types(result)
        except Exception:
            logger.exception("Segmentation failed, returning single-section fallback")
            return DocumentSegmentation(
                sections=[DocumentSection(
                    section_id="full_document",
                    name=filename or "Full Document",
                    section_type="unknown",
                    start_page=1,
                    end_page=total_pages or len(extractions),
                )],
                document_type="unknown",
                confidence=0.0,
            )


def load_segmentation(doc_dir: Path) -> DocumentSegmentation | None:
    """Load cached segmentation from disk."""
    path = doc_dir / "segmentation.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DocumentSegmentation.model_validate(data)
    except Exception:
        logger.warning("Failed to load segmentation from %s", path)
        return None


def store_segmentation(doc_dir: Path, seg: DocumentSegmentation) -> None:
    """Persist segmentation to disk."""
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "segmentation.json"
    path.write_text(seg.model_dump_json(indent=2), encoding="utf-8")


def build_page_to_section(seg: DocumentSegmentation) -> dict[int, dict]:
    """Build a lookup from page number to section info dict."""
    page_map: dict[int, dict] = {}
    for sec in seg.sections:
        info = {
            "section_id": sec.section_id,
            "section_name": sec.name,
            "section_type": normalize_section_type(sec.section_type),
            "start_page": sec.start_page,
            "end_page": sec.end_page,
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
