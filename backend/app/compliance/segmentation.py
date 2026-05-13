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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SegmentationIssue:
    """One quality issue found in a segmentation output."""

    kind: str  # "overlap" | "gap" | "unknown_document_type" | "unknown_section_type"
    message: str
    section_ids: tuple[str, ...] = ()
    page_range: tuple[int, int] | None = None


def validate_segmentation(
    seg: DocumentSegmentation,
    total_pages: int | None = None,
) -> list[SegmentationIssue]:
    """Surface segmentation quality issues as a list of warnings.

    Detects:

    1. **Overlaps** — two sections whose page ranges share at least
       one page. Common when the LLM hallucinates a sub-document
       on a page that's already part of a parent section
       (e.g. ``VDE009 Data Monitoring Parameters`` pages 76-86
       overlapping ``VDE009 Alarm Log`` page 81 in Akhilesh's
       segmentation).
    2. **Coverage gaps** — pages between sections that no section
       claims. Most often a sign the LLM dropped a multi-page
       attachment or didn't realize a checklist spans pages.
       Reports the gap range so a HITL reviewer or the operator
       can decide whether to re-segment.
    3. **Unknown document_type** — ``document_type`` value not in
       ``document_profiles.yaml``. The compliance pipeline will
       silently lose every rule keyed off that doc_type unless
       it's added to the YAML.
    4. **Unknown section_type** — ``section_type`` value not in
       any profile's ``expected_sections`` or ``section_aliases``.
       Less critical than document_type drift but still worth
       surfacing so config authors can fold them in.

    Pure: no I/O, no logging. Caller decides whether to log /
    raise / surface in the run report.
    """

    from app.compliance.rules.profiles import (
        load_profiles,
        normalize_document_type,
        normalize_section_type,
    )

    profiles = load_profiles()
    known_docs = profiles.known_document_types()
    known_sections = profiles.known_section_types()

    issues: list[SegmentationIssue] = []

    # ── Overlaps and gaps ─────────────────────────────────
    sorted_secs = sorted(seg.sections, key=lambda s: (s.start_page, s.end_page))
    for i, sec in enumerate(sorted_secs):
        for other in sorted_secs[i + 1:]:
            if other.start_page > sec.end_page:
                break  # sorted, no further overlap possible
            overlap_lo = max(sec.start_page, other.start_page)
            overlap_hi = min(sec.end_page, other.end_page)
            if overlap_lo <= overlap_hi:
                issues.append(SegmentationIssue(
                    kind="overlap",
                    message=(
                        f"Sections '{sec.section_id}' "
                        f"({sec.start_page}-{sec.end_page}) and "
                        f"'{other.section_id}' "
                        f"({other.start_page}-{other.end_page}) overlap "
                        f"on pages {overlap_lo}-{overlap_hi}. The "
                        f"compliance pipeline will double-count any "
                        f"finding on those pages."
                    ),
                    section_ids=(sec.section_id, other.section_id),
                    page_range=(overlap_lo, overlap_hi),
                ))

    # Coverage gaps — only check when we know total_pages.
    if total_pages is not None and total_pages > 0:
        covered: set[int] = set()
        for sec in seg.sections:
            for p in range(sec.start_page, sec.end_page + 1):
                covered.add(p)
        all_pages = set(range(1, total_pages + 1))
        missing = sorted(all_pages - covered)
        if missing:
            # Compress consecutive gap pages into ranges.
            run_start = missing[0]
            prev = missing[0]
            for p in missing[1:] + [None]:
                if p != prev + 1 if p is not None else True:
                    if p is None or p != prev + 1:
                        issues.append(SegmentationIssue(
                            kind="gap",
                            message=(
                                f"Pages {run_start}-{prev} are not covered "
                                f"by any segmentation section. The compliance "
                                f"pipeline will never evaluate rules against "
                                f"this content."
                            ),
                            page_range=(run_start, prev),
                        ))
                        run_start = p if p is not None else prev
                if p is not None:
                    prev = p

    # ── Type drift ────────────────────────────────────────
    for sec in seg.sections:
        if sec.document_type:
            normalized = normalize_document_type(sec.document_type)
            if normalized not in known_docs:
                issues.append(SegmentationIssue(
                    kind="unknown_document_type",
                    message=(
                        f"Section '{sec.section_id}' has "
                        f"document_type='{sec.document_type}' which is "
                        f"not defined in document_profiles.yaml. Rules "
                        f"keyed to this doc_type won't fire on this "
                        f"section until the YAML is extended."
                    ),
                    section_ids=(sec.section_id,),
                ))
        if sec.section_type:
            normalized = normalize_section_type(sec.section_type)
            if normalized and normalized not in known_sections:
                issues.append(SegmentationIssue(
                    kind="unknown_section_type",
                    message=(
                        f"Section '{sec.section_id}' has "
                        f"section_type='{sec.section_type}' which is "
                        f"not in any document profile's expected_sections "
                        f"or section_aliases. Cross-section rules will "
                        f"fail to resolve this section."
                    ),
                    section_ids=(sec.section_id,),
                ))

    return issues


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

        # FLATTEN: each BPCR sub-section becomes a top-level
        # ``DocumentSection`` entry — not a nested ``BpcrSubSection``
        # row. This matches the gold-standard segmentation shape
        # Akhilesh shared on 2026-05-12: rather than one parent BPCR
        # row carrying 35 per-page sub-rows, the output has one
        # row per detected canonical sub-section, each with proper
        # ``section_type`` + ``start_page`` / ``end_page`` covering
        # the span. Downstream compliance rules already address
        # individual section_types (manufacturing_operations,
        # material_dispensing, …) so this is the natural shape;
        # the previous nested-per-page form was an artifact of
        # mirroring the BMR pipeline's display rows.
        #
        # The shared ``section_id`` is preserved across all spans
        # of the same BPCR so the frontend can still group them
        # visually if it wants.
        #
        # Flatten input is the UNION of two sources:
        #   * heuristic detector spans (preferred — proper start/end
        #     ranges derived from text matching across the markdown);
        #   * LLM-populated ``parent.sub_sections`` — since the 2026-
        #     05-12 prompt cues, the segmentation LLM frequently
        #     emits ``BpcrSubSection`` entries (often with
        #     ``detection_method='column_names'``) for cover_page /
        #     revision_summary, which the detector cannot match by
        #     heading text. Before this merge those entries were
        #     dropped on the floor: the detector either returned 0
        #     spans (parent kept whole, LLM sub_sections nested-only)
        #     or returned its own spans (parent replaced, LLM
        #     sub_sections discarded entirely).
        # Detector wins on section_type collisions because its spans
        # carry real page ranges; LLM ``page_index`` is a single int
        # so we collapse it to a 1-page span.
        flatten_plan: list[tuple[str, str, int, int]] = []
        seen_types: set[str] = set()
        for span in section_map.spans:
            normalized_section_type = (
                normalize_section_type(span.section_id) or span.section_id
            )
            if normalized_section_type in seen_types:
                continue
            seen_types.add(normalized_section_type)
            flatten_plan.append((
                normalized_section_type,
                span.display_name or section.name,
                span.start_page,
                span.end_page,
            ))
        for ss in section.sub_sections:
            normalized_section_type = (
                normalize_section_type(ss.section_id) or ss.section_id
            )
            if not normalized_section_type or normalized_section_type in seen_types:
                continue
            seen_types.add(normalized_section_type)
            page = ss.page_index or section.start_page
            # Clamp to the parent's page range so a stray LLM
            # ``page_index`` doesn't escape the BPCR's span.
            if page < section.start_page:
                page = section.start_page
            elif page > section.end_page:
                page = section.end_page
            flatten_plan.append((
                normalized_section_type,
                ss.display_name or section.name,
                page,
                page,
            ))

        if not flatten_plan:
            # Neither source produced anything — keep the parent
            # section unchanged so the page coverage isn't lost.
            enriched_sections.append(section)
            continue

        for stype, dname, sp, ep in flatten_plan:
            enriched_sections.append(DocumentSection(
                section_id=section.section_id,
                name=dname,
                section_type=stype,
                document_type="batch_record",
                start_page=sp,
                end_page=ep,
                description=section.description,
            ))

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
    """Build the segmentation prompt.

    Encodes the classification heuristics Akhilesh stated on the
    2026-05-12 call:

    * Document types EXCEPT ``batch_record``: classify from the
      document header on each page first. Fall back to content
      cues (e.g. ``VDE0**`` + temp/pressure tables → ``scada_report``)
      only when no header.
    * BPCR document_type from header; BPCR section_type from
      headings on top of tables ('LIST OF RAW MATERIALS …',
      'MICRONIZATION OPERATION', etc.).
    * BPCR ``cover_page`` and ``revision_summary`` have no
      section heading — infer from COLUMN NAMES.

    Also injects the canonical doc_type and section_type vocabulary
    from ``document_profiles.yaml`` so the LLM uses known names.
    Any free-form values the LLM emits beyond that list get caught
    by ``validate_segmentation`` as drift warnings.
    """
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

    # Inject canonical doc/section type vocabulary. Empty fallback
    # for test environments that haven't loaded profiles.
    try:
        from app.compliance.rules.profiles import load_profiles
        profiles = load_profiles()
        known_doc_types = sorted(profiles.known_document_types())
        known_section_types = sorted(profiles.known_section_types())
    except Exception:
        known_doc_types = []
        known_section_types = []

    doc_types_hint = (
        f"\nKnown document_type values (use these canonical names when "
        f"they fit): {', '.join(known_doc_types)}.\n"
        if known_doc_types else ""
    )
    section_types_hint = (
        f"Known section_type values (use these canonical names when "
        f"they fit): {', '.join(known_section_types[:80])}.\n"
        if known_section_types else ""
    )

    return (
        f"Analyze this multi-part document and identify each distinct "
        f"sub-document/section.\n\n"
        f"CLASSIFICATION HEURISTICS (in priority order):\n"
        f"1. For every document type EXCEPT ``batch_record``: classify\n"
        f"   from the document HEADER on each page first. The header\n"
        f"   typically names the document explicitly (e.g.\n"
        f"   'Raw Material Request & Issue', 'In-Process Samples\n"
        f"   Request Cum Analysis Report', 'QC Analytical Data\n"
        f"   Review Checklist'). Same header repeating across pages\n"
        f"   = same document.\n"
        f"2. If a page has NO explicit header, infer document_type\n"
        f"   from CONTENT. Examples:\n"
        f"   * ``VDE0**`` identifiers + monitoring tables (temp,\n"
        f"     pressure, vacuum) → ``scada_report``.\n"
        f"   * Chromatogram traces / instrument analysis tables →\n"
        f"     ``qc_analytical_package`` or ``analysis_report``.\n"
        f"   * Particle-size / sieving result columns →\n"
        f"     ``analysis_report``.\n"
        f"3. For ``batch_record`` (BPCR) documents: doc_type comes\n"
        f"   from the document header (e.g. 'BATCH PRODUCTION AND\n"
        f"   CONTROL RECORD'). But individual SECTIONS within the\n"
        f"   BPCR carry their OWN section heading on top of the\n"
        f"   first table on each section's page — look there:\n"
        f"   * 'LIST OF RAW MATERIALS AND WEIGHING DETAILS' →\n"
        f"     ``material_dispensing``\n"
        f"   * 'LIST OF MAJOR EQUIPMENTS & SOP DETAILS' →\n"
        f"     ``equipment_list``\n"
        f"   * 'MANUFACTURING INSTRUCTIONS' →\n"
        f"     ``manufacturing_operations``\n"
        f"   * 'YIELD CALCULATION' → ``yield_calculation``\n"
        f"   * 'SIFTING RECORD' → ``sifting_record``\n"
        f"   * 'PIN MILLING' / 'PIN MILL MIXING' →\n"
        f"     ``pin_milling_mixing``\n"
        f"   * 'MICRONIZATION OPERATION' → ``micronization``\n"
        f"   * 'CO-MILL OPERATION' → ``co_mill_operation``\n"
        f"   * 'METAL DETECTION' → ``metal_detection``\n"
        f"   * 'EQUIPMENT CLEANING' → ``cleaning_log``\n"
        f"   * 'DEVIATION' → ``deviation``\n"
        f"4. For the BPCR's cover_page and revision_summary\n"
        f"   sections specifically: there's NO section heading on\n"
        f"   top of the table. Infer from the COLUMN NAMES:\n"
        f"   * Cover page: columns like Product Name, MPCR No.,\n"
        f"     BPCR Number, Batch No., Batch Size, Market Code,\n"
        f"     Stage, Revision Number → ``cover_page``\n"
        f"   * Revision summary: columns like Change History,\n"
        f"     Revision Number, Change Description, Effective\n"
        f"     Date → ``revision_summary``\n"
        f"{doc_types_hint}"
        f"{section_types_hint}\n"
        f"GENERAL RULES:\n"
        f"* Page numbering restarts, document title changes, and\n"
        f"  form-layout shifts mark section / document boundaries.\n"
        f"* Same product family but different stages (e.g. coarser\n"
        f"  vs micronized polymorph) is still the SAME batch_record\n"
        f"  document_type — they share the BPCR header.\n"
        f"* When in doubt between two doc_types, prefer the more\n"
        f"  specific canonical name from the list above.\n"
        f"* If a section truly doesn't fit any canonical type,\n"
        f"  emit a lowercase_snake_case free-form value — drift\n"
        f"  warnings will surface it so the doc_profiles can be\n"
        f"  extended later.\n\n"
        f"FILENAME: {filename}\n\n"
        f"KEY-VALUE PAIRS:\n{kv_text}\n\n"
        f"PAGE SUMMARIES:\n" + "\n\n".join(page_summaries) + "\n\n"
        f"For each section return:\n"
        f"- section_id: short lowercase_snake_case slug\n"
        f"- name: descriptive human-readable name\n"
        f"- section_type: canonical type from the list above when it\n"
        f"  fits; lowercase_snake_case free-form only if none fit\n"
        f"- document_type: canonical doc_type from the list above\n"
        f"  when it fits\n"
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
            stamped = stamp_document_types(result)
            # Surface quality issues to the run log AND the on-disk
            # telemetry sink so post-run validation sees the
            # structured issue list, not just a log line. Pure
            # observation — never mutates segmentation output.
            try:
                issues = validate_segmentation(stamped, total_pages=total_pages)
                if issues:
                    logger.warning(
                        "segmentation quality issues (%d): %s",
                        len(issues),
                        [
                            {"kind": i.kind, "msg": i.message}
                            for i in issues[:20]
                        ],
                    )
                    # Emit one structured event per issue so the
                    # on-disk telemetry's ``by_event`` summary shows
                    # ``segmentation.overlap: N`` etc., enabling
                    # one-glance verification that the validator ran
                    # and found the expected issues.
                    try:
                        from app.observability.run_telemetry import record_event
                        for i in issues:
                            record_event(
                                f"segmentation.{i.kind}",
                                level="warning",
                                message=i.message,
                                section_ids=list(i.section_ids),
                                page_range=list(i.page_range) if i.page_range else None,
                            )

                        # Akhilesh's 2026-05-12 ask:
                        # "add a mechanism to detect & flag any of
                        # the document/section types that are not
                        # defined in document_profiles.yaml."
                        #
                        # The per-issue events above DO that. This
                        # extra summary event groups the unknown
                        # values into a single actionable digest so
                        # operators don't have to scan event-by-
                        # event — they see one ``segmentation.
                        # vocabulary_drift`` event listing every
                        # new doc_type / section_type the LLM
                        # emitted that's NOT in
                        # ``document_profiles.yaml``. The suggested
                        # YAML snippet is ready to paste.
                        unknown_doc_types = sorted({
                            i.message.split("'")[1] for i in issues
                            if i.kind == "unknown_document_type"
                            and "'" in i.message
                        })
                        unknown_section_types = sorted({
                            i.message.split("'")[1] for i in issues
                            if i.kind == "unknown_section_type"
                            and "'" in i.message
                        })
                        if unknown_doc_types or unknown_section_types:
                            suggested_yaml = []
                            if unknown_doc_types:
                                suggested_yaml.append(
                                    "# Add to document_profiles.yaml under "
                                    "``document_profiles:``"
                                )
                                for dt in unknown_doc_types:
                                    suggested_yaml.append(
                                        f"  {dt}:\n    aliases: []\n    "
                                        f"expected_sections: []"
                                    )
                            if unknown_section_types:
                                suggested_yaml.append(
                                    "# Add to document_profiles.yaml under "
                                    "the appropriate profile's "
                                    "``expected_sections:`` list"
                                )
                                for st in unknown_section_types:
                                    suggested_yaml.append(
                                        f"  - section_type: {st}\n    "
                                        f"display_name: ''\n    "
                                        f"required: false\n    aliases: []"
                                    )
                            record_event(
                                "segmentation.vocabulary_drift",
                                level="warning",
                                unknown_document_types=unknown_doc_types,
                                unknown_section_types=unknown_section_types,
                                count_unknown_doc_types=len(unknown_doc_types),
                                count_unknown_section_types=len(unknown_section_types),
                                suggested_yaml_snippet="\n".join(suggested_yaml),
                            )
                            logger.warning(
                                "segmentation.vocabulary_drift — "
                                "%d unknown document_type(s): %s | "
                                "%d unknown section_type(s): %s — "
                                "extend backend/app/compliance/rules/"
                                "document_profiles.yaml to silence",
                                len(unknown_doc_types), unknown_doc_types,
                                len(unknown_section_types), unknown_section_types,
                            )
                    except Exception:  # pragma: no cover — never break segmentation
                        pass
            except Exception:  # pragma: no cover — defensive
                logger.exception("segmentation validator raised; continuing")
            return stamped
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
            "document_type": sec.document_type
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
