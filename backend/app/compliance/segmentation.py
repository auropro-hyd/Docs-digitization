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


def _find_gap_neighbours(
    sections: list[DocumentSection],
    gap_start: int,
    gap_end: int,
) -> tuple[DocumentSection | None, DocumentSection | None]:
    """Return the sections immediately before and after a gap range.

    "Before" = highest end_page strictly less than gap_start.
    "After"  = lowest start_page strictly greater than gap_end.
    Either may be None when the gap touches the document start or end.
    """

    before: DocumentSection | None = None
    after: DocumentSection | None = None
    for sec in sections:
        if sec.end_page < gap_start:
            if before is None or sec.end_page > before.end_page:
                before = sec
        elif sec.start_page > gap_end:
            if after is None or sec.start_page < after.start_page:
                after = sec
    return before, after


def validate_segmentation(
    seg: DocumentSegmentation,
    total_pages: int | None = None,
    key_value_pairs: list[dict] | None = None,
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
            # Compress consecutive gap pages into ranges, then attach
            # adjacency context to each gap so the operator sees what
            # the gap is sandwiched between (load-bearing for triage —
            # "gap between two reactor_checklist sections" almost
            # certainly means the operator should extend one of them).
            run_start = missing[0]
            prev = missing[0]
            for p in missing[1:] + [None]:
                if p != prev + 1 if p is not None else True:
                    if p is None or p != prev + 1:
                        adj_before, adj_after = _find_gap_neighbours(
                            seg.sections, run_start, prev,
                        )
                        before_desc = (
                            f"after '{adj_before.section_id}' "
                            f"({adj_before.section_type or '?'}, "
                            f"pages {adj_before.start_page}-{adj_before.end_page})"
                            if adj_before else "at the start of the document"
                        )
                        after_desc = (
                            f"before '{adj_after.section_id}' "
                            f"({adj_after.section_type or '?'}, "
                            f"pages {adj_after.start_page}-{adj_after.end_page})"
                            if adj_after else "at the end of the document"
                        )
                        same_type_hint = ""
                        if (
                            adj_before
                            and adj_after
                            and adj_before.section_type
                            and adj_before.section_type == adj_after.section_type
                        ):
                            same_type_hint = (
                                f" Both neighbours share section_type="
                                f"'{adj_before.section_type}'; the gap is "
                                f"most likely a continuation that the LLM "
                                f"split incorrectly — consider extending the "
                                f"preceding section to end_page={prev}."
                            )
                        adj_ids = tuple(
                            s.section_id
                            for s in (adj_before, adj_after)
                            if s is not None
                        )
                        issues.append(SegmentationIssue(
                            kind="gap",
                            message=(
                                f"Pages {run_start}-{prev} are not covered "
                                f"by any segmentation section ({before_desc}; "
                                f"{after_desc}). The compliance pipeline will "
                                f"never evaluate rules against this content."
                                f"{same_type_hint}"
                            ),
                            section_ids=adj_ids,
                            page_range=(run_start, prev),
                        ))
                        run_start = p if p is not None else prev
                if p is not None:
                    prev = p

    # ── Structural minimums (Spec 011 / FR-009) ───────────
    issues.extend(validate_structural_minimums(seg))

    # ── Cross-evidence (Spec 011 / FR-010, FR-011) ─────────
    issues.extend(validate_kv_coverage(seg, key_value_pairs))
    issues.extend(validate_type_consistency(seg))

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


def fill_gaps_with_unknown(
    seg: DocumentSegmentation,
    total_pages: int,
) -> DocumentSegmentation:
    """Replace LLM-left coverage gaps with explicit ``unknown`` sections.

    The robust-coverage prompt instructs the LLM to emit
    ``section_type='unknown'`` for pages it can't classify rather than
    leave them out, but the LLM sometimes still drops them. This
    post-process closes that failure mode deterministically: any page
    range from 1..``total_pages`` not covered by an existing section
    becomes its own ``DocumentSection`` with ``section_type='unknown'``
    and ``document_type=''`` (so downstream filters degrade to "no
    rules apply" rather than fabricating a verdict).

    The fill is OBSERVABLE — each filled gap fires a
    ``segmentation.gap_filled_with_unknown`` telemetry event so the
    HITL reviewer can re-classify rather than letting the gap silently
    become "unknown content nobody reviewed". This is the inverse of
    silent loss: pages are kept (so compliance has a chance to look)
    AND flagged (so reviewers know to fix the underlying segmentation).

    Pure: returns a new ``DocumentSegmentation``. Idempotent: running
    twice on the same output produces the same sections (the second
    pass finds no gaps).
    """

    if total_pages <= 0:
        return seg

    covered: set[int] = set()
    for sec in seg.sections:
        for p in range(sec.start_page, sec.end_page + 1):
            covered.add(p)
    all_pages = set(range(1, total_pages + 1))
    missing = sorted(all_pages - covered)
    if not missing:
        return seg

    # Compress consecutive missing pages into ranges.
    runs: list[tuple[int, int]] = []
    run_start = missing[0]
    prev = missing[0]
    for p in missing[1:]:
        if p == prev + 1:
            prev = p
            continue
        runs.append((run_start, prev))
        run_start = p
        prev = p
    runs.append((run_start, prev))

    filled: list[DocumentSection] = list(seg.sections)
    for start, end in runs:
        filled.append(DocumentSection(
            section_id=f"unknown_pages_{start}_{end}",
            name=f"Unclassified pages {start}-{end}",
            section_type="unknown",
            document_type="",
            start_page=start,
            end_page=end,
            description=(
                "Pages left uncovered by LLM segmentation; preserved as an "
                "explicit 'unknown' section so compliance reviewers can "
                "re-classify rather than silently losing the content."
            ),
        ))
        try:
            from app.observability.run_telemetry import record_event
            record_event(
                "segmentation.gap_filled_with_unknown",
                level="warning",
                page_range=[start, end],
                page_count=end - start + 1,
            )
        except Exception:  # pragma: no cover — never break segmentation
            pass

    # Re-sort so the section list stays page-ordered.
    filled.sort(key=lambda s: (s.start_page, s.end_page))
    return seg.model_copy(update={"sections": filled})


def deduplicate_section_ids(seg: DocumentSegmentation) -> DocumentSegmentation:
    """Suffix duplicate ``section_id`` values with ``_2`` / ``_3`` ...

    Two sources can emit the same ``section_id`` for distinct
    sections in one segmentation: the LLM (e.g. two raw-material
    forms named "raw_material_request_allocated") and
    ``enrich_with_bpcr_sub_sections`` (which historically used the
    parent's id for every flatten span). Both collide with the
    HITL overrides sidecar (US4 keys overrides on
    ``(section_id, field)`` — a duplicate means an override on
    one section silently applies to all).

    Walks sections in their existing order; the FIRST occurrence
    of each id keeps its original value, subsequent occurrences
    get a numeric suffix (``id_2``, ``id_3``, …). Order matters:
    operators editing via the segmentation editor see the FIRST
    occurrence retain its id, which matches the stable-ordering
    contract the editor depends on.

    Idempotent: running twice produces the same output because
    the second run sees no duplicates.
    """

    seen_ids: set[str] = set()
    updated: list[DocumentSection] = []
    for sec in seg.sections:
        original = sec.section_id
        if original not in seen_ids:
            seen_ids.add(original)
            updated.append(sec)
            continue
        # Find the next available suffix.
        suffix_idx = 2
        candidate = f"{original}_{suffix_idx}"
        while candidate in seen_ids:
            suffix_idx += 1
            candidate = f"{original}_{suffix_idx}"
        seen_ids.add(candidate)
        _emit_seg_event(
            "segmentation.section_id_disambiguated",
            from_id=original,
            to_id=candidate,
            page_range=[sec.start_page, sec.end_page],
        )
        updated.append(sec.model_copy(update={"section_id": candidate}))
    return seg.model_copy(update={"sections": updated})


def clamp_page_ranges(
    seg: DocumentSegmentation,
    total_pages: int,
) -> DocumentSegmentation:
    """Clip out-of-range page numbers so every section fits the doc.

    The segmentation LLM sometimes hallucinates a page range outside
    ``[1, total_pages]`` — most commonly a tail section that should
    have been at the END of the packet but ends up with
    ``start_page=1, end_page=2`` because the LLM lost track of the
    page counter. Such a section overlaps the real cover page and
    confuses every downstream consumer.

    Two deterministic clips:

    * ``end_page > total_pages``: clip ``end_page`` to ``total_pages``.
    * ``start_page > total_pages`` or ``start_page > end_page``
      after clipping: drop the section entirely (we have no evidence
      where it actually belongs; better to surface a gap that
      ``fill_gaps_with_unknown`` will catch than to invent a position).
    * ``start_page < 1``: clip to ``1``.

    Each clip / drop emits a telemetry event so HITL reviewers see
    that the LLM produced an invalid range. Idempotent."""

    if total_pages <= 0:
        return seg

    kept: list[DocumentSection] = []
    for sec in seg.sections:
        start = sec.start_page
        end = sec.end_page

        if start < 1:
            start = 1
        if end > total_pages:
            _emit_seg_event(
                "segmentation.range_clipped",
                section_id=sec.section_id,
                section_type=sec.section_type,
                from_range=[sec.start_page, sec.end_page],
                to_range=[start, total_pages],
                reason="end_page_beyond_total_pages",
            )
            end = total_pages
        if start > total_pages or start > end:
            _emit_seg_event(
                "segmentation.range_dropped",
                section_id=sec.section_id,
                section_type=sec.section_type,
                from_range=[sec.start_page, sec.end_page],
                reason="start_page_beyond_total_pages",
            )
            continue
        if (start, end) != (sec.start_page, sec.end_page):
            kept.append(sec.model_copy(update={"start_page": start, "end_page": end}))
        else:
            kept.append(sec)

    return seg.model_copy(update={"sections": kept})


def resolve_overlaps(seg: DocumentSegmentation) -> DocumentSegmentation:
    """Make overlapping section ranges disjoint deterministically.

    Walks sections in ``(start_page, end_page)`` order and, when
    section ``B`` overlaps section ``A`` (``B.start_page <=
    A.end_page``), clamps ``B.start_page = A.end_page + 1``. If that
    makes ``B`` empty (``start_page > end_page``), drop ``B`` —
    the LLM duplicated a section it had already covered. Each clamp
    / drop fires a telemetry event so HITL reviewers see the
    overlap.

    The Spec 008 export pipeline downstream of this is sensitive to
    overlaps (page counts get double-billed, and the on-screen
    rule table can render the same content twice); making the
    output strictly disjoint is the cheapest way to prevent that
    misrender."""

    sorted_secs = sorted(seg.sections, key=lambda s: (s.start_page, s.end_page))
    kept: list[DocumentSection] = []
    for sec in sorted_secs:
        if not kept:
            kept.append(sec)
            continue
        prev = kept[-1]
        if sec.start_page > prev.end_page:
            kept.append(sec)
            continue

        # Overlap: clamp B's start to A.end_page + 1.
        new_start = prev.end_page + 1
        if new_start > sec.end_page:
            _emit_seg_event(
                "segmentation.overlap_dropped",
                section_id=sec.section_id,
                section_type=sec.section_type,
                overlaps_with=prev.section_id,
                from_range=[sec.start_page, sec.end_page],
                covered_by=[prev.start_page, prev.end_page],
            )
            continue
        _emit_seg_event(
            "segmentation.overlap_clamped",
            section_id=sec.section_id,
            section_type=sec.section_type,
            overlaps_with=prev.section_id,
            from_range=[sec.start_page, sec.end_page],
            to_range=[new_start, sec.end_page],
        )
        kept.append(sec.model_copy(update={"start_page": new_start}))

    return seg.model_copy(update={"sections": kept})


def _emit_seg_event(event: str, **fields) -> None:
    """Best-effort telemetry; never break the segmentation path."""
    try:
        from app.observability.run_telemetry import record_event
        record_event(event, level="warning", **fields)
    except Exception:  # pragma: no cover
        pass


# Spec 011 / FR-006. ``fill_gaps_with_unknown`` plugs some pages
# legitimately (image-only pages, blank cover letters). 0.97 (3%
# shortfall) is the empirical threshold below which we suspect the
# LLM hit its output token cap; above 0.97 we trust the gap-fill.
# Tunable as the constant rather than a magic number in code.
_TRUNCATION_COVERAGE_THRESHOLD: float = 0.97
_TRUNCATION_MAX_RETRIES: int = 2


def detect_truncation(
    seg: DocumentSegmentation,
    total_pages: int,
    *,
    finish_reason: str | None = None,
) -> int | None:
    """Return the page from which a retry should start, or ``None``
    when coverage is acceptable.

    Two signals — coverage shortfall is primary, finish_reason is
    a tie-breaker:

    * If the LLM output covers ≥97% of ``total_pages``, no retry.
    * Otherwise, return the first uncovered page so the caller
      can re-prompt with the tail. ``finish_reason='length'`` is
      informational only — useful for telemetry when available
      but the detector doesn't require it (the spec's adapter
      port doesn't surface it).

    Note: ``seg`` here is the LLM raw output AFTER
    :func:`clamp_page_ranges` and :func:`resolve_overlaps` — gap-
    fill MUST NOT have run yet, otherwise the tail is already
    'covered' by synthetic unknown sections and we never retry.
    """

    if total_pages <= 0:
        return None
    covered: set[int] = set()
    for sec in seg.sections:
        for p in range(sec.start_page, sec.end_page + 1):
            if 1 <= p <= total_pages:
                covered.add(p)
    ratio = len(covered) / total_pages
    if ratio >= _TRUNCATION_COVERAGE_THRESHOLD:
        return None

    # First uncovered page is the retry anchor.
    for p in range(1, total_pages + 1):
        if p not in covered:
            _emit_seg_event(
                "segmentation.output_truncated",
                total_pages=total_pages,
                covered_pages=len(covered),
                coverage_ratio=round(ratio, 3),
                retry_from=p,
                finish_reason=finish_reason,
            )
            return p
    return None


def validate_kv_coverage(
    seg: DocumentSegmentation,
    key_value_pairs: list[dict] | None,
) -> list[SegmentationIssue]:
    """Emit ``no_kv_evidence`` for multi-page sections that carry
    zero OCR key-value pairs in their range.

    KV pairs are per-page metadata (batch numbers, product
    names, equipment IDs). A section claiming several pages
    with zero KV pairs is almost certainly mis-classified
    (probably image-only or a hallucinated span).

    Only sections spanning ≥3 pages are checked: legitimately
    small spans (one- or two-page checklists, single-page cover
    pages) often have zero KV evidence and shouldn't trigger
    false positives.

    Returns warnings, not errors — segmentation output is not
    mutated. HITL reviewers decide whether to re-run.
    """

    if not key_value_pairs:
        return []

    # Bucket KV pages once for an O(N+M) instead of O(N*M) scan.
    kv_pages: set[int] = set()
    for kv in key_value_pairs:
        page = kv.get("page_num")
        if isinstance(page, int) and page >= 1:
            kv_pages.add(page)

    issues: list[SegmentationIssue] = []
    for sec in seg.sections:
        span = sec.end_page - sec.start_page + 1
        if span < 3:
            continue
        # Skip ``unknown`` sections — they're intentionally
        # placeholders from ``fill_gaps_with_unknown``; HITL is
        # already aware.
        if sec.section_type == "unknown":
            continue
        kv_count = sum(
            1 for p in range(sec.start_page, sec.end_page + 1)
            if p in kv_pages
        )
        if kv_count == 0:
            issues.append(SegmentationIssue(
                kind="no_kv_evidence",
                message=(
                    f"Section '{sec.section_id}' "
                    f"(pages {sec.start_page}-{sec.end_page}, "
                    f"section_type='{sec.section_type}') has no OCR "
                    f"key-value pairs in range. Likely image-only or "
                    f"mis-classified — HITL should review."
                ),
                section_ids=(sec.section_id,),
                page_range=(sec.start_page, sec.end_page),
            ))
    return issues


def validate_type_consistency(
    seg: DocumentSegmentation,
) -> list[SegmentationIssue]:
    """Emit ``type_mismatch`` when ``section_type`` is canonical
    but doesn't belong to the section's ``document_type`` profile.

    Catches the LLM-emitted artefact where the doc_type and
    section_type each make sense in isolation but are
    contradictory together (e.g. ``manufacturing_operations``
    section_type with ``ipc_report`` document_type).

    Skips: empty section_type / document_type values, ``unknown``
    section_type (intentional placeholder), and section_types not
    found in any profile (already covered by the existing
    ``unknown_section_type`` drift check)."""

    from app.compliance.rules.profiles import load_profiles
    profiles = load_profiles()

    issues: list[SegmentationIssue] = []
    for sec in seg.sections:
        section_type = (sec.section_type or "").strip()
        document_type = (sec.document_type or "").strip()
        if not section_type or not document_type:
            continue
        if section_type == "unknown":
            continue
        profile = profiles.document_profiles.get(document_type)
        if profile is None:
            # Unknown doc_type is handled elsewhere.
            continue
        # Membership: the section_type matches the profile's
        # canonical types OR any alias.
        valid = {sec_def.section_type for sec_def in profile.expected_sections}
        for sec_def in profile.expected_sections:
            valid.update(sec_def.aliases)
        # Also accept the doc_type's slug as a valid "whole-doc-as-
        # section" pattern (already preserved by the canonical
        # normaliser).
        valid.add(document_type)
        valid.update(profile.aliases)
        if section_type not in valid:
            issues.append(SegmentationIssue(
                kind="type_mismatch",
                message=(
                    f"Section '{sec.section_id}' has section_type"
                    f"='{section_type}' which is not a valid sub-section of "
                    f"document_type='{document_type}'. The compliance "
                    f"pipeline's cross-section filter will not resolve "
                    f"this section."
                ),
                section_ids=(sec.section_id,),
                page_range=(sec.start_page, sec.end_page),
            ))
    return issues


def validate_structural_minimums(
    seg: DocumentSegmentation,
) -> list[SegmentationIssue]:
    """Surface required-section omissions per document_type.

    Walks every distinct ``document_type`` emitted by the
    segmentation; loads its profile from
    ``document_profiles.yaml``; emits one
    ``missing_required_section`` issue per ``required: true``
    section_type absent from the emitted set.

    Pure: no I/O beyond the cached profile load.
    """

    from app.compliance.rules.profiles import load_profiles
    profiles = load_profiles()

    # Bucket emitted section_types per document_type.
    emitted_by_doc: dict[str, set[str]] = {}
    for sec in seg.sections:
        doc_type = (sec.document_type or "").strip()
        if not doc_type:
            continue
        emitted_by_doc.setdefault(doc_type, set()).add(
            (sec.section_type or "").strip(),
        )

    issues: list[SegmentationIssue] = []
    for doc_type, emitted_types in emitted_by_doc.items():
        profile = profiles.document_profiles.get(doc_type)
        if profile is None:
            # Unknown doc_type is already surfaced by
            # ``validate_segmentation``; don't double-report it.
            continue
        required = {
            sec.section_type for sec in profile.expected_sections
            if sec.required and sec.section_type
        }
        missing = required - emitted_types
        for section_type in sorted(missing):
            issues.append(SegmentationIssue(
                kind="missing_required_section",
                message=(
                    f"document_type='{doc_type}' profile requires section_type"
                    f"='{section_type}' (required: true in document_profiles.yaml)"
                    f" but no segmentation section emitted it. The compliance"
                    f" pipeline will skip rules keyed to that section."
                ),
                section_ids=(),
                page_range=None,
            ))
    return issues


def merge_split_by_boundary(
    seg: DocumentSegmentation,
    units: list,
) -> DocumentSegmentation:
    """Reconcile LLM segmentation against page-header boundary units.

    Two reconciliations performed per LLM section ``S``:

    1. **Merge**: if ``S`` falls entirely inside one
       :class:`~app.compliance.segmentation_headers.BoundaryUnit`
       (its page range is a subset of the unit's), AND another
       LLM section ``T`` also falls inside the same unit, ``S``
       and ``T`` merge into one section whose page range equals
       the unit's. The winning ``section_type`` / ``document_type``
       come from the constituent that covers the most pages within
       the unit (tie-break: lowest start_page). Emits
       ``segmentation.header_boundary_merged``.

    2. **Split**: if ``S`` spans a boundary-unit transition
       (its page range crosses from unit ``U1`` into ``U2`` or
       beyond), ``S`` is split at the page where the unit changes.
       Each split piece keeps ``S``'s ``section_type`` /
       ``document_type``. Emits ``segmentation.header_boundary_split``.

    Sections that don't intersect any unit pass through unchanged
    (no header attestation, LLM's word stands).

    Idempotent: running twice on the same input produces the same
    output (after the first pass, every surviving section is
    either entirely inside one unit or entirely outside all units).
    """

    if not units:
        return seg

    # Defer the heavy lifting to a helper module to keep this file
    # readable. Avoids a circular import by lazy-importing the
    # headers module's data class only when we need it.
    sorted_sections = sorted(seg.sections, key=lambda s: (s.start_page, s.end_page))
    sorted_units = sorted(units, key=lambda u: u.start_page)

    # Index every page covered by a unit → that unit. Pages outside
    # any unit map to None.
    unit_by_page: dict[int, "_BoundaryRef"] = {}
    for idx, u in enumerate(sorted_units):
        for p in range(u.start_page, u.end_page + 1):
            unit_by_page[p] = _BoundaryRef(index=idx, unit=u)

    # Phase A — split sections that span a unit transition.
    after_split: list[DocumentSection] = []
    split_events: list[tuple[str, list[int], list[list[int]]]] = []
    for sec in sorted_sections:
        pieces = _split_by_unit_transitions(sec, unit_by_page)
        if len(pieces) > 1:
            split_events.append((
                sec.section_id,
                [sec.start_page, sec.end_page],
                [[p.start_page, p.end_page] for p in pieces],
            ))
        after_split.extend(pieces)

    for section_id, original_range, split_ranges in split_events:
        _emit_seg_event(
            "segmentation.header_boundary_split",
            section_id=section_id,
            from_range=original_range,
            to_ranges=split_ranges,
        )

    # Phase B — group sections by their owning unit (if any), then
    # merge inside each unit.
    grouped: dict[int | None, list[DocumentSection]] = {}
    for sec in after_split:
        # A piece is owned by a unit iff every page in its range
        # belongs to that unit (after Phase A this is guaranteed
        # to be either all-one-unit or all-no-unit).
        ref = unit_by_page.get(sec.start_page)
        key = ref.index if ref else None
        grouped.setdefault(key, []).append(sec)

    merged: list[DocumentSection] = []
    for key, sections in grouped.items():
        if key is None or len(sections) == 1:
            merged.extend(sections)
            continue
        unit = sorted_units[key]
        winner = _choose_winner(sections)
        merged_section = winner.model_copy(update={
            "start_page": unit.start_page,
            "end_page": unit.end_page,
        })
        _emit_seg_event(
            "segmentation.header_boundary_merged",
            from_sections=[s.section_id for s in sections],
            from_ranges=[[s.start_page, s.end_page] for s in sections],
            to_range=[unit.start_page, unit.end_page],
            chosen_section_id=winner.section_id,
        )
        merged.append(merged_section)

    merged.sort(key=lambda s: (s.start_page, s.end_page))
    return seg.model_copy(update={"sections": merged})


from dataclasses import dataclass as _dataclass


@_dataclass(frozen=True)
class _BoundaryRef:
    """Internal pointer from a page to the unit it belongs to."""
    index: int
    unit: object  # BoundaryUnit, but late-bound to avoid a circular import


def _split_by_unit_transitions(
    sec: DocumentSection,
    unit_by_page: dict,
) -> list[DocumentSection]:
    """Split ``sec`` at every page where the boundary-unit
    membership changes."""

    pieces: list[DocumentSection] = []
    current_start = sec.start_page
    current_ref = unit_by_page.get(current_start)
    for page in range(sec.start_page + 1, sec.end_page + 1):
        page_ref = unit_by_page.get(page)
        same = (current_ref is None and page_ref is None) or (
            current_ref is not None
            and page_ref is not None
            and current_ref.index == page_ref.index
        )
        if not same:
            pieces.append(sec.model_copy(update={
                "start_page": current_start,
                "end_page": page - 1,
            }))
            current_start = page
            current_ref = page_ref
    pieces.append(sec.model_copy(update={
        "start_page": current_start,
        "end_page": sec.end_page,
    }))
    return pieces


def _choose_winner(sections: list) -> DocumentSection:
    """Pick the section that wins the type/doc_type merge.

    Rule: the section with the largest page coverage wins. Tie-
    break: lowest ``start_page``. The tie-break is deterministic
    so the same LLM output produces the same merge result every
    run."""

    return max(
        sections,
        key=lambda s: (s.end_page - s.start_page + 1, -s.start_page),
    )


def normalize_section_types_to_canonical(
    seg: DocumentSegmentation,
) -> DocumentSegmentation:
    """Map LLM-emitted section_type drift onto canonical types.

    Four cases per section:

    1. **Alias hit** — ``normalize_section_type`` folds the value
       into a known section_type (e.g. ``data_monitoring_parameters``
       → ``instrument_data_log``). Replace and emit a
       ``section_type_normalised`` event.
    2. **Already canonical** — value is in any profile's
       ``expected_sections`` or top-level ``section_aliases``. Keep.
    3. **Whole-doc-as-section pattern** — value isn't a section_type
       but matches a known ``document_type`` slug (e.g.
       ``ipc_report``, ``raw_material_request``). This is the LLM
       treating the whole doc as one section because it couldn't
       discriminate sub-types. Keep the value so cross-document
       filters still resolve, but emit a
       ``section_type_matches_doc_type`` event so HITL reviewers can
       drill in. Akhilesh's 2026-05-13 voice notes specifically
       flagged this for raw_material_request.
    4. **Drift** — anything else, including the special-case
       strings ``unsectioned`` and empty. Collapse to ``"unknown"``
       and emit ``section_type_collapsed_to_unknown``. Downstream
       rules degrade to "no rules apply" rather than fabricating
       verdicts.

    Idempotent."""

    from app.compliance.rules.profiles import load_profiles
    profiles = load_profiles()
    known_sections = profiles.known_section_types()
    known_docs = profiles.known_document_types()

    updated: list[DocumentSection] = []
    _COLLAPSE_TO_UNKNOWN = {"unsectioned", ""}
    for section in seg.sections:
        raw = section.section_type or ""
        canonical = normalize_section_type(raw) if raw else ""

        # 1 + 2: alias hit or already canonical.
        if canonical and canonical in known_sections:
            if canonical != raw:
                _emit_seg_event(
                    "segmentation.section_type_normalised",
                    section_id=section.section_id,
                    from_type=raw,
                    to_type=canonical,
                )
                updated.append(section.model_copy(update={"section_type": canonical}))
            else:
                updated.append(section)
            continue

        # 3: section_type names a whole-document-as-section.
        if canonical and canonical in known_docs:
            _emit_seg_event(
                "segmentation.section_type_matches_doc_type",
                section_id=section.section_id,
                section_type=canonical,
                document_type=section.document_type,
            )
            # Preserve the original casing if the LLM didn't slug it
            # (normalize_section_type already lower-cases/snakes).
            updated.append(section.model_copy(update={"section_type": canonical}))
            continue

        # Already-explicit ``unknown`` from fill_gaps_with_unknown
        # passes through untouched.
        if raw == "unknown":
            updated.append(section)
            continue

        # 4: drift.
        reason = (
            "invalid_value"
            if raw.lower() in _COLLAPSE_TO_UNKNOWN or canonical.lower() in _COLLAPSE_TO_UNKNOWN
            else "not_in_profiles"
        )
        _emit_seg_event(
            "segmentation.section_type_collapsed_to_unknown",
            section_id=section.section_id,
            from_type=raw,
            reason=reason,
        )
        updated.append(section.model_copy(update={"section_type": "unknown"}))

    return seg.model_copy(update={"sections": updated})


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
        # Each enriched span gets a UNIQUE ``section_id`` composed
        # as ``{parent_id}__{section_type}`` (Spec 011 follow-up,
        # 2026-05-14). The original design shared the parent id
        # across all spans for visual grouping, but collisions
        # broke the HITL overrides sidecar (US4 keys overrides on
        # ``(section_id, field)``; multiple spans with the same id
        # meant an override on one applied to all) and confused
        # the frontend's scroll-to-section anchor. Frontend
        # grouping can still derive the parent from the prefix.
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
            # Unique-id composition: ``{parent}__{section_type}``.
            # Falls back to ``{parent}__{section_type}__{idx}`` when
            # two flatten entries share a section_type (rare but
            # possible if normalisation collapsed them post-hoc).
            candidate_id = f"{section.section_id}__{stype}"
            suffix_idx = 1
            existing_ids = {s.section_id for s in enriched_sections}
            while candidate_id in existing_ids:
                suffix_idx += 1
                candidate_id = f"{section.section_id}__{stype}__{suffix_idx}"
            enriched_sections.append(DocumentSection(
                section_id=candidate_id,
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
        f"HARD COVERAGE CONSTRAINTS (the most important rule):\n"
        f"* Every single page from 1 to the last page MUST be covered\n"
        f"  by exactly one section. Page ranges must be contiguous.\n"
        f"  There must be NO gaps and NO overlaps between sections.\n"
        f"* If you genuinely cannot classify a page, place it in a\n"
        f"  section with section_type='unknown' rather than leaving\n"
        f"  it uncovered — the operator can then triage that section.\n"
        f"* Before you return, mentally walk page 1 → N and confirm\n"
        f"  every page appears in exactly one section's [start_page,\n"
        f"  end_page] range. A gap is a worse failure than a wrong\n"
        f"  section_type — wrong types can be corrected; lost pages\n"
        f"  cannot.\n\n"
        f"CLASSIFICATION HEURISTICS (in priority order):\n"
        f"1. For every document type EXCEPT ``batch_record``: classify\n"
        f"   from the document HEADER on each page first. The header\n"
        f"   typically names the document explicitly (e.g.\n"
        f"   'Raw Material Request & Issue', 'In-Process Samples\n"
        f"   Request Cum Analysis Report', 'QC Analytical Data\n"
        f"   Review Checklist'). Same header repeating across pages\n"
        f"   = same document.\n"
        f"   HEADER → DOC_TYPE LOAD-BEARING MAPPINGS (use these EXACT\n"
        f"   matches; the LLM has been known to drift on them):\n"
        f"   * 'In-Process Samples Request Cum Analysis Report' OR\n"
        f"     'IPC Report' OR headers containing 'in-process samples'\n"
        f"     → document_type=``ipc_report``, section_type=\n"
        f"     ``in_process_report``. NEVER classify these as\n"
        f"     ``analysis_report`` — that's reserved for instrument\n"
        f"     analysis / particle-size reports without the IPC framing.\n"
        f"   * 'QC Analytical Data Review Checklist' →\n"
        f"     ``qc_analytical_package`` / ``analytical_data_review``.\n"
        f"   * 'Check List for <Equipment> Operations' →\n"
        f"     ``operation_checklist`` (one section per equipment;\n"
        f"     see rule 5 below).\n"
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
        f"5. OPERATION CHECKLIST BOUNDARIES (load-bearing):\n"
        f"   Each operation_checklist starts with its OWN header\n"
        f"   line of the form 'Check List for <Equipment>\n"
        f"   Operations' (reactor, scrubber, centrifuge, vacuum\n"
        f"   tray drier, sifter, pin mill, blender, co-mill, metal\n"
        f"   detector). The header is followed by a small batch /\n"
        f"   equipment ID block (Batch No., Equipment ID, Date)\n"
        f"   and then the checklist items table. RULES:\n"
        f"   * Whenever a new 'Check List for X Operations' header\n"
        f"     appears, a NEW section starts on that page — even\n"
        f"     if the previous page was a different document (e.g.\n"
        f"     a batch_release_note immediately followed by a\n"
        f"     reactor checklist). Do NOT glue the checklist's\n"
        f"     first page onto the prior document.\n"
        f"   * Within one equipment's checklist, pages BETWEEN the\n"
        f"     header page and the next document's start (or the\n"
        f"     next 'Check List for X Operations' header) all\n"
        f"     belong to that ONE checklist section. Operators\n"
        f"     historically split these — don't.\n"
        f"   * Pick the section_type matching the equipment\n"
        f"     (reactor_checklist, centrifuge_checklist,\n"
        f"     vacuum_tray_dryer_checklist, sifter_checklist,\n"
        f"     pin_mill_checklist, etc.). document_type stays\n"
        f"     ``operation_checklist`` across all of them.\n"
        f"6. RAW MATERIAL DOCUMENT SUB-TYPES (load-bearing — do not\n"
        f"   echo the document_type as the section_type):\n"
        f"   A raw_material_request packet usually contains several\n"
        f"   distinct one- or few-page forms. Classify each by its\n"
        f"   form header, not by the parent document_type:\n"
        f"   * 'Raw Material Request & Issue' / 'Raw Material Request\n"
        f"     Cum Issue Record' → ``material_request``\n"
        f"   * 'Material Issue and Allotment' / 'Allotted' /\n"
        f"     'Store Issue' → ``material_issue``\n"
        f"   * 'Packing Material Request & Allocated' →\n"
        f"     ``packing_material_request``\n"
        f"   * 'Distilled / Recovered / Spent Solvent Transfer Note'\n"
        f"     → ``solvent_transfer_note``\n"
        f"   document_type stays ``raw_material_request`` across all\n"
        f"   of them. NEVER emit section_type=``raw_material_request`` —\n"
        f"   that's the doc_type. Pick the specific sub-type instead.\n"
        f"7. SCADA REPORT CLUSTERS:\n"
        f"   VDE0** instruments emit TWO companion artifacts per\n"
        f"   batch: (a) a multi-page DATA MONITORING REPORT (temp /\n"
        f"   pressure / vacuum tables, continuous timestamps) and\n"
        f"   (b) a short ALARM REPORT (often one page, may be\n"
        f"   labelled 'no alarms recorded'). Both have\n"
        f"   document_type=``scada_report`` but they are SEPARATE\n"
        f"   sections — the alarm report's distinct title page\n"
        f"   starts a new section, do not merge it into the trailing\n"
        f"   pages of the data report.\n"
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
        f"  extended later.\n"
        f"* FINAL WALK: before you return, list the page ranges\n"
        f"  in order — section 1: 1-X, section 2: X+1-Y, …. The\n"
        f"  next section's start_page MUST equal the previous\n"
        f"  section's end_page + 1. If you find a gap, either\n"
        f"  extend an adjacent section over the gap (when the\n"
        f"  pages clearly continue that section's content) or\n"
        f"  emit a section_type='unknown' for the gap.\n"
        f"* PAGE-RANGE SANITY: start_page and end_page MUST be\n"
        f"  between 1 and the last page (inclusive). NEVER restart\n"
        f"  numbering from 1 partway through the output — a section\n"
        f"  whose content sits at the end of the packet must use\n"
        f"  the LARGE absolute page numbers, not page 1-2 just because\n"
        f"  the section happens to be called a 'cover' or 'review'.\n"
        f"  Specifically: the 'BPCR Review Check List' always sits at\n"
        f"  the END of a batch_closure / qc_analytical_package; if you\n"
        f"  emit it with start_page=1 you have misnumbered.\n\n"
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

    async def _segment_range(
        self,
        extractions: list[dict],
        retry_from: int,
        total_pages: int,
        key_value_pairs: list[dict] | None = None,
        filename: str = "",
    ) -> DocumentSegmentation:
        """Re-prompt the LLM on a sub-range of extractions.

        Used by the truncation-retry path: when the first call's
        coverage stopped well short of ``total_pages`` we issue a
        focused call on the uncovered tail. The page numbers in
        ``extractions`` are absolute (1-indexed against the
        original packet), so the LLM's emitted page ranges land
        in the original frame — no re-mapping required."""

        scoped = [
            ext for ext in extractions
            if isinstance(ext.get("page_num"), int)
            and retry_from <= ext["page_num"] <= total_pages
        ]
        prompt = _build_segmentation_prompt(scoped, key_value_pairs, filename)
        prompt += (
            f"\n\nIMPORTANT — RETRY CONTEXT: this is a follow-up call. "
            f"You are being asked to classify pages {retry_from} through "
            f"{total_pages} only. The earlier call truncated at the "
            f"output limit. Use the SAME page numbering as above (page "
            f"numbers in 'Page X: ...' refer to absolute pages in the "
            f"original document) and emit sections only inside the "
            f"[{retry_from}, {total_pages}] range."
        )
        result = await self._llm.generate_structured(
            prompt, DocumentSegmentation, system=_SYSTEM,
        )
        if not isinstance(result, DocumentSegmentation):
            result = DocumentSegmentation.model_validate(result)
        return result

    async def segment(
        self,
        extractions: list[dict],
        key_value_pairs: list[dict] | None = None,
        filename: str = "",
        total_pages: int = 0,
        doc_dir: Path | None = None,
    ) -> DocumentSegmentation:
        prompt = _build_segmentation_prompt(extractions, key_value_pairs, filename)
        try:
            result = await self._llm.generate_structured(
                prompt, DocumentSegmentation, system=_SYSTEM,
            )
            if not isinstance(result, DocumentSegmentation):
                result = DocumentSegmentation.model_validate(result)
            # Fill any LLM-left coverage gaps with explicit ``unknown``
            # sections BEFORE stamping doc_types so the rest of the
            # pipeline sees full page coverage. Each filled gap fires
            # a ``segmentation.gap_filled_with_unknown`` event.
            # Post-process order — load-bearing, see Spec 011
            # data-model.md.
            #
            #   1. Geometric cleanup (PR #69): clamp out-of-range
            #      pages, resolve overlaps.
            #   2. Fill gaps with explicit ``unknown`` sections.
            #   3. Spec 011 / US1: reconcile against page-header
            #      boundary units (merge LLM-split forms, split
            #      LLM-glued forms). Runs AFTER gap-fill so the
            #      LLM-left gaps are already explicit ``unknown``
            #      sections — the merger then either absorbs them
            #      into a boundary-attested unit (if they fall
            #      inside one) or leaves them alone.
            #   4. Normalise section_types onto the canonical
            #      vocabulary.
            #   5. Stamp document_types.
            from app.compliance.segmentation_headers import (
                group_boundary_units,
                parse_page_headers,
            )

            headers = parse_page_headers(extractions)
            boundary_units = group_boundary_units(headers)

            # Spec 011 / 2026-05-14: deduplicate section_ids
            # FIRST so the rest of the pipeline (overrides apply,
            # validators, frontend scroll targets) sees unique
            # IDs. The LLM regularly emits duplicate IDs for
            # distinct sections (raw material packets are the
            # repeat offenders) and the US4 overrides sidecar
            # depends on stable, unique IDs.
            dedup = deduplicate_section_ids(result)
            clamped = clamp_page_ranges(dedup, total_pages=total_pages)
            disjoint = resolve_overlaps(clamped)

            # Spec 011 / FR-006-008: truncation retry. Check
            # coverage BEFORE fill_gaps_with_unknown — otherwise
            # synthetic ``unknown`` sections mask the real
            # shortfall and we never retry.
            retry_from = detect_truncation(disjoint, total_pages=total_pages)
            attempt = 0
            while retry_from is not None and attempt < _TRUNCATION_MAX_RETRIES:
                attempt += 1
                try:
                    tail = await self._segment_range(
                        extractions=extractions,
                        retry_from=retry_from,
                        total_pages=total_pages,
                        key_value_pairs=key_value_pairs,
                        filename=filename,
                    )
                except Exception:  # pragma: no cover — best-effort
                    logger.exception(
                        "segmentation truncation retry %d failed; "
                        "continuing with partial output",
                        attempt,
                    )
                    break
                merged_sections = list(disjoint.sections) + list(tail.sections)
                disjoint = resolve_overlaps(
                    clamp_page_ranges(
                        disjoint.model_copy(update={"sections": merged_sections}),
                        total_pages=total_pages,
                    )
                )
                retry_from = detect_truncation(disjoint, total_pages=total_pages)

            if retry_from is not None:
                _emit_seg_event(
                    "segmentation.retry_exhausted",
                    attempts=attempt,
                    uncovered_from=retry_from,
                    total_pages=total_pages,
                )

            filled = fill_gaps_with_unknown(disjoint, total_pages=total_pages)
            boundary_reconciled = merge_split_by_boundary(filled, boundary_units)
            canonical = normalize_section_types_to_canonical(boundary_reconciled)
            stamped = stamp_document_types(canonical)

            # Spec 007 / 2026-05-14 architectural fix: enrichment
            # used to run in compliance_graph.py AFTER segment()
            # returned, which meant the geometric invariants
            # (no overlaps, full coverage) Spec 011's post-processes
            # established got CLOBBERED by enrichment's output.
            # Persisted segmentations on Akhilesh's 2026-05-14 doc
            # showed three overlapping BPCR sections (1-3 / 1-10 /
            # 1-19) and a 13-page uncovered gap because of this.
            #
            # Folding enrichment INSIDE segment() means the entire
            # contract is enforced in one place; the post-enrichment
            # re-sanitization keeps the output geometrically clean
            # even when the BPCR detector / LLM produces overlapping
            # flatten spans.
            #
            # Idempotent: enrichment short-circuits when no BPCR
            # sections exist; segments without batch_record content
            # cost essentially nothing.
            enriched = enrich_with_bpcr_sub_sections(stamped, extractions)
            if enriched is not stamped:  # something was actually enriched
                # Geometric sanitization: clamp out-of-range,
                # resolve overlaps, fill gaps. Same three steps the
                # pre-enrichment pipeline already ran — applying
                # them again to the enriched output catches every
                # enrichment artefact deterministically.
                enriched = clamp_page_ranges(enriched, total_pages=total_pages)
                enriched = resolve_overlaps(enriched)
                enriched = fill_gaps_with_unknown(enriched, total_pages=total_pages)
                # Re-dedupe in case enrichment's parent-id-derived
                # composition collided with an existing id (very
                # rare but possible when re-segmenting a previously
                # enriched doc).
                enriched = deduplicate_section_ids(enriched)
            stamped = enriched

            # Spec 011 / US4: apply HITL overrides LAST so the
            # operator's word is final — no post-process can
            # silently clobber their edits. Validators run AFTER
            # this on the final shape; any operator-introduced
            # geometric anomaly surfaces as a warning but the
            # operator's value stands.
            override_orphans: list[dict] = []
            if doc_dir is not None:
                from app.compliance.segmentation_overrides import (
                    apply_overrides,
                    load_overrides,
                )
                overrides = load_overrides(doc_dir)
                if overrides:
                    stamped, override_orphans = apply_overrides(stamped, overrides)
                    for orphan in override_orphans:
                        _emit_seg_event(
                            "segmentation.override_orphaned",
                            section_id=orphan["section_id"],
                            field=orphan["field"],
                            value=str(orphan["value"]),
                            actor=orphan.get("actor"),
                        )
            # Coverage-summary telemetry — single event per run carrying
            # the pages-covered / total-pages ratio + section count so
            # operators see whether the segmentation actually covered
            # the doc at one glance. Auto-no-op when no sink is bound.
            try:
                from app.observability.run_telemetry import record_event
                covered_pages: set[int] = set()
                for sec in stamped.sections:
                    for p in range(sec.start_page, sec.end_page + 1):
                        covered_pages.add(p)
                record_event(
                    "segmentation.coverage_summary",
                    total_pages=total_pages,
                    covered_pages=len(covered_pages),
                    coverage_ratio=(
                        len(covered_pages) / total_pages
                        if total_pages > 0 else None
                    ),
                    section_count=len(stamped.sections),
                    unknown_section_count=sum(
                        1 for s in stamped.sections
                        if s.section_type == "unknown"
                    ),
                )
            except Exception:  # pragma: no cover — never break segmentation
                pass
            # Surface quality issues to the run log AND the on-disk
            # telemetry sink so post-run validation sees the
            # structured issue list, not just a log line. Pure
            # observation — never mutates segmentation output.
            try:
                issues = validate_segmentation(
                    stamped,
                    total_pages=total_pages,
                    key_value_pairs=key_value_pairs,
                )
                # Spec 011 / US4: surface orphaned overrides as
                # validation issues alongside the rest.
                for orphan in override_orphans:
                    issues.append(SegmentationIssue(
                        kind="override_orphaned",
                        message=(
                            f"Operator override on section_id"
                            f"='{orphan['section_id']}' field='{orphan['field']}'"
                            f" was dropped — the section is no longer in the"
                            f" LLM output. The operator must re-apply via the"
                            f" segmentation editor."
                        ),
                        section_ids=(orphan["section_id"],),
                        page_range=None,
                    ))
                # Spec 011 / FR-014: attach issues to the returned
                # DocumentSegmentation so the HITL endpoint can
                # surface them to the editor without a second
                # call. Serialised via simple dicts (the model's
                # field is typed ``list[dict]``).
                stamped = stamped.model_copy(update={
                    "validation_issues": [
                        {
                            "kind": i.kind,
                            "message": i.message,
                            "section_ids": list(i.section_ids),
                            "page_range": list(i.page_range) if i.page_range else None,
                        }
                        for i in issues
                    ],
                })
                if issues:
                    # Group by kind for a compact summary line that
                    # fits on a single terminal row. Per-issue events
                    # below carry the full detail to the on-disk
                    # telemetry sink, so post-run analysis still
                    # has everything. Previously this logged the
                    # full list of issue dicts as a giant inline
                    # JSON-ish blob that wrapped across the
                    # terminal and was effectively unreadable.
                    from collections import Counter
                    by_kind = Counter(i.kind for i in issues)
                    summary = ", ".join(
                        f"{kind}={count}"
                        for kind, count in sorted(by_kind.items())
                    )
                    logger.warning(
                        "segmentation quality issues (%d total): %s — "
                        "see segmentation.<kind> events in telemetry "
                        "for per-issue detail",
                        len(issues), summary,
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
            "document_type": sec.document_type,
        }
        for p in range(sec.start_page, sec.end_page + 1):
            page_map[p] = info
    return page_map
