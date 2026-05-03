"""Heuristic BPCR layout-aware section detection (Spec 007).

Pure capability — no I/O, no globals, deterministic. Given an
:class:`~app.core.ports.ocr.OCRResult` for a single BPCR document and
the canonical :class:`BPCRSectionsSpec`, returns a
:class:`BPCRSectionMap` covering every page in the document.

Detection bands (in priority order, declared per-section in the spec):

- ``top_of_page`` — top 20 % of the page by ``page_height``.
- ``top_of_table`` — within ~10 px of any detected table-header row
  (we use the first non-empty line whose words sit on a single
  baseline as a proxy when explicit table metadata is absent).
- ``mid_page`` — anywhere from 20 %–80 % of the page; only counts
  when the matched line carries emphasis (``bold`` / ``all caps``
  / larger-than-body font where ``StyleSpan`` is available).

Every page lands in exactly one :class:`SectionSpan`. Pages without a
confident match fall into a synthesised ``unsectioned`` span. The
detector fails open: any unhandled exception is converted to a single
``unsectioned`` span covering the whole document plus an entry in
``BPCRSectionMap.notes``.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.bmr.capabilities.bpcr_sections_spec import (
    UNSECTIONED_ID,
    BPCRSectionEntry,
    BPCRSectionsSpec,
)
from app.core.ports.ocr import OCRPageResult, OCRResult

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "1.0.0"
"""Bumped by anyone who changes the heuristic in a way that could
shift detection output for previously-stable inputs. Tests pin it."""

DetectionMode = Literal["heuristic", "vlm", "hybrid"]

# Confidence thresholds — tests pin these explicitly.
_CONF_PRIMARY_TOP = 1.0      # primary regex match in highest-priority band
_CONF_SECONDARY_BAND = 0.85  # primary regex in a lower-priority band
_CONF_MID_PAGE = 0.7         # primary regex match in mid-page band
_CONF_ALIAS = 0.4            # alias-only match in any band
_CONF_NONE = 0.0             # filler / unsectioned span

# Vertical band cutoffs as fractions of page height.
_TOP_BAND_FRACTION = 0.20
_MID_BAND_TOP = 0.20
_MID_BAND_BOTTOM = 0.80


class SectionSpan(BaseModel):
    """A contiguous run of pages assigned the same section_id."""

    section_id: str
    display_name: str
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    confidence: float = Field(ge=0.0, le=1.0)
    detection_method: str
    matched_text: str = ""
    matched_band: str = ""

    model_config = ConfigDict(frozen=True)


class BPCRSectionMap(BaseModel):
    """Per-document section assignment table.

    Invariants (asserted at construction time by the detector, not by
    Pydantic — keeping the model permissive for tests that build
    partial maps directly):

    1. ``spans`` covers every page in the document with no gaps and
       no overlaps.
    2. ``spans`` is sorted by ``start_page`` ascending.
    3. ``outcome == 'failed'`` ⇒ ``spans`` has exactly one entry,
       ``unsectioned`` covering the whole document.
    """

    doc_id: str
    spec_version: str
    detector_version: str = DETECTOR_VERSION
    method: DetectionMode = "heuristic"
    outcome: Literal["ok", "partial", "failed"] = "ok"
    spans: list[SectionSpan] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=True)

    def section_for_page(self, page_index: int) -> str | None:
        span = self.span_for_page(page_index)
        return span.section_id if span is not None else None

    def span_for_page(self, page_index: int) -> SectionSpan | None:
        """Return the full :class:`SectionSpan` covering ``page_index``.

        Like :meth:`section_for_page` but returns the entire span so
        callers (the tagger, the report stage) can read confidence,
        detection_method, and display_name without re-running the
        detector or threading the spec around.
        """

        for span in self.spans:
            if span.start_page <= page_index <= span.end_page:
                return span
        return None


# ── Per-page candidate detection ────────────────────────────────────────────


class _PageCandidate(BaseModel):
    """An internal struct linking a page to its strongest section hit."""

    page_num: int
    section: BPCRSectionEntry | None = None
    confidence: float = _CONF_NONE
    detection_method: str = "unmatched"
    matched_text: str = ""
    matched_band: str = ""

    model_config = ConfigDict(frozen=True)


# Patterns used by ``_normalise_markdown_line`` to strip presentation
# markers before regex/alias matching. Compiled once at module load
# rather than per call. The detector regexes are anchored at ``^\s*``
# and the alias matcher uses ``re.escape`` on the human-readable
# alias (e.g. ``"Material Dispensing"``); without normalisation a
# real-world heading like ``**LIST OF MAJOR EQUIPMENTS**`` or
# ``# **MANUFACTURING INSTRUCTIONS**`` never matches because the
# leading ``**`` / ``#`` block the anchor. Stripping these is purely
# a presentation concern and doesn't change which sections can match
# — only which lines are visible to the matcher in the first place.
_HEADING_PREFIX_RE = re.compile(r"^\s*#{1,6}\s+")
_BOLD_WRAP_RE = re.compile(r"^\*\*\s*(.+?)\s*\*\*\s*[:.;,]?\s*$")


def _normalise_markdown_line(line: str) -> str:
    """Strip leading heading / bold markers so the matcher can see the
    actual section name underneath.

    Returns the original line untouched when no markup is present —
    this path is hot enough (every line × every section × every alias)
    that an unconditional regex substitution would be measurable.

    Examples (all map to ``LIST OF MAJOR EQUIPMENTS & SOP DETAILS``):
      ``# **LIST OF MAJOR EQUIPMENTS & SOP DETAILS**``
      ``**LIST OF MAJOR EQUIPMENTS & SOP DETAILS**``
      ``###### LIST OF MAJOR EQUIPMENTS & SOP DETAILS``
    """

    text = line
    # Drop a leading markdown heading marker (``#`` through ``######``).
    # Multiple loops are unnecessary — the regex matches at most one
    # heading prefix, and Pandoc-style markdown doesn't nest them.
    if text.startswith("#"):
        text = _HEADING_PREFIX_RE.sub("", text, count=1)
    # Unwrap a single ``**…**`` bold span if it covers the whole line
    # (with an optional trailing ``:``/``.``). This pattern is the one
    # the layout sanitiser preserves around document section headers
    # in real BPCR scans.
    bold_match = _BOLD_WRAP_RE.match(text.strip())
    if bold_match:
        text = bold_match.group(1)
    return text


def _page_lines(page: OCRPageResult) -> list[tuple[str, float]]:
    """Return ``(line_text, y_fraction)`` for every line on the page.

    ``y_fraction`` is the line's vertical position as a fraction of
    the page height (0 = top, 1 = bottom). When the page lacks word
    bounding regions we fall back to evenly-spaced lines from the
    markdown so ``mid_page`` detection stays meaningful for fixtures
    that don't ship layout coordinates.

    Every emitted line is run through :func:`_normalise_markdown_line`
    so heading and bold markup don't block the anchored regex/alias
    matchers downstream.
    """

    page_height = page.page_height or 0.0
    if page.words and page_height > 0:
        # Group words by their y-baseline (rounded to ~10 px) so words
        # on the same visual line collapse into one entry. This is a
        # cheap proxy for "lines" without a layout-segmentation pass.
        rows: dict[int, list[tuple[float, str]]] = {}
        for word in page.words:
            br = word.bounding_region
            if br is None:
                continue
            row_key = int(br.y // 10) * 10
            rows.setdefault(row_key, []).append((br.x, word.text))
        lines: list[tuple[str, float]] = []
        for y_key, items in sorted(rows.items()):
            items.sort(key=lambda pair: pair[0])
            text = _normalise_markdown_line(
                " ".join(text for _, text in items).strip()
            )
            if not text:
                continue
            y_fraction = min(max(y_key / page_height, 0.0), 1.0)
            lines.append((text, y_fraction))
        if lines:
            return lines

    # Fallback path — markdown-only fixtures (most of the test suite).
    md_lines = [
        _normalise_markdown_line(line.strip())
        for line in page.markdown.splitlines()
        if line.strip()
    ]
    md_lines = [line for line in md_lines if line]
    if not md_lines:
        return []
    step = 1.0 / max(len(md_lines), 1)
    return [(text, idx * step) for idx, text in enumerate(md_lines)]


def _band_for_y(y_fraction: float) -> str:
    if y_fraction <= _TOP_BAND_FRACTION:
        return "top_of_page"
    if y_fraction >= _MID_BAND_BOTTOM:
        return "mid_page"  # treat near-bottom as mid_page for our purposes
    return "mid_page"


def _is_emphasised(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):  # markdown heading from layout sanitiser
        return True
    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return True
    # ``**emphasised**`` (with an optional trailing ``:``) is the markdown
    # convention for bold runs the layout sanitiser preserves. We strip
    # the trailing ``:`` (single character) before checking the closing
    # ``**`` — using ``.rstrip("**")`` would peel any leading/trailing
    # ``*`` characters, which is misleading.
    candidate = stripped.removesuffix(":") if stripped.endswith(":") else stripped
    return candidate.startswith("**") and candidate.endswith("**")


def _patterns_for(
    section: BPCRSectionEntry,
) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    """Return ``(primary_patterns, alias_patterns)`` for one section.

    Compiled fresh on every call. ``re.compile`` itself caches at the
    pattern-string level, so this is cheap. We deliberately avoid an
    id()-keyed module cache because Python recycles object ids after
    GC; that subtly couples test runs and breaks determinism.

    ``cover_page`` gets a more permissive anchor than every other
    section. Real BPCRs put the document title at the top of every
    page in a long composite header like
    ``# **APITORIA PHARMA PRIVATE LIMITED, UNIT-II PRODUCTION BLOCK – E
    BATCH PRODUCTION AND CONTROL RECORD**``. The standard
    ``^\\s*<alias>\\b`` anchor never matches because the title is
    embedded mid-line behind the company name. The ``page_num == 1``
    guard from ``_evaluate_page`` already prevents this relaxation
    from over-matching on subsequent pages, so it's safe to let the
    anchor accept any prefix here.
    """

    is_cover = section.section_id == "cover_page"
    line_anchor = "^.*?" if is_cover else r"^\s*"

    if section.regex:
        if is_cover:
            # Replace the spec's own ``^\s*`` anchor with the relaxed
            # form so existing cover_page regex entries keep working
            # on real-world embedded-title pages without a YAML edit.
            primary = [
                re.compile(_relax_cover_anchor(p), re.IGNORECASE)
                for p in section.regex
            ]
        else:
            primary = [re.compile(p, re.IGNORECASE) for p in section.regex]
    else:
        # Fall back to a literal-display-name match so a section with
        # no regex is still detectable.
        primary = [
            re.compile(
                rf"{line_anchor}{re.escape(section.display_name)}\b",
                re.IGNORECASE,
            )
        ]
    aliases = [
        re.compile(rf"{line_anchor}{re.escape(alias)}\b", re.IGNORECASE)
        for alias in section.aliases
    ]
    return primary, aliases


def _relax_cover_anchor(pattern: str) -> str:
    """Rewrite a cover_page YAML regex's leading anchor to ``^.*?``.

    Idempotent — patterns that don't start with ``^\\s*`` pass through
    unchanged so an author who deliberately wrote a stricter anchor
    keeps that intent.
    """

    if pattern.startswith(r"^\s*"):
        return "^.*?" + pattern[len(r"^\s*"):]
    return pattern


def _evaluate_page_candidates(
    page: OCRPageResult, *, spec: BPCRSectionsSpec
) -> list[_PageCandidate]:
    """Return per-section best candidates for ``page``, sorted by
    confidence descending.

    Replaces the old ``_evaluate_page`` (which returned only the single
    globally-best candidate). Real BPCR pages frequently carry markers
    for two adjacent sections — a section's repeating header
    (continuation of a span that started earlier) plus a new section's
    initial header. With only the globally-best candidate, the new
    section was lost; ``_pick_assigned_candidate`` in the assembly
    phase now uses these per-section bests to detect transition pages.
    """

    lines = _page_lines(page)
    if not lines:
        return []

    # Markdown-only mode (no word-level layout coordinates): synthetic
    # y_fractions from line indexes are too unreliable a proxy for
    # real spatial position to gate on bands. A real BPCR has its
    # section headers mid-page (e.g. "MICRONIZATION OPERATION" on
    # page 24 between two tables), but markdown-line position depends
    # on how dense each preceding line is. Drop the band check in
    # this mode so the regex/alias matchers can do their job; the
    # confidence weighting still differentiates strong vs weak hits
    # via the matched-band assignment below.
    has_real_layout = bool(page.words) and (page.page_height or 0.0) > 0

    best_per_section: dict[str, _PageCandidate] = {}
    for section in spec.sections:
        primary, aliases = _patterns_for(section)
        # ``cover_page`` claims to be page 1 by definition. The
        # canonical regex matches the document title which repeats on
        # every BPCR page header — without this guard, every page
        # gets matched as cover_page and the assemble-spans pass
        # inherits it forward across the whole document. Pin the
        # constraint here rather than in the spec so a future spec
        # change can't accidentally weaken it.
        if section.section_id == "cover_page" and page.page_num != 1:
            continue
        for line_text, y_fraction in lines:
            band = _band_for_y(y_fraction)
            if has_real_layout and band not in section.bands:
                continue
            primary_hit = any(p.search(line_text) for p in primary)
            alias_hit = False if primary_hit else any(
                p.search(line_text) for p in aliases
            )
            if not (primary_hit or alias_hit):
                continue
            if (
                has_real_layout
                and band == "mid_page"
                and section.requires_emphasis_for_mid_page
                and not _is_emphasised(line_text)
            ):
                continue

            # Confidence assignment per FR-005 / Confidence semantics.
            preferred_band = section.bands[0]
            if primary_hit:
                if band == "top_of_page" and band == preferred_band:
                    confidence = _CONF_PRIMARY_TOP
                elif band == "mid_page":
                    confidence = _CONF_MID_PAGE
                else:
                    confidence = _CONF_SECONDARY_BAND
            else:
                confidence = _CONF_ALIAS

            method = f"heuristic_{band}"

            existing = best_per_section.get(section.section_id)
            if existing is None or confidence > existing.confidence:
                best_per_section[section.section_id] = _PageCandidate(
                    page_num=page.page_num,
                    section=section,
                    confidence=confidence,
                    detection_method=method,
                    matched_text=line_text[:120],
                    matched_band=band,
                )

    return sorted(
        best_per_section.values(),
        key=lambda c: c.confidence,
        reverse=True,
    )


def _evaluate_page(
    page: OCRPageResult, *, spec: BPCRSectionsSpec
) -> _PageCandidate:
    """Return the single best candidate for ``page`` (legacy shape).

    Kept as a thin wrapper so existing tests / callers that only need
    the top match keep working unchanged. The new
    ``_evaluate_page_candidates`` exposes the full ranked list for
    transition-aware assembly.
    """

    candidates = _evaluate_page_candidates(page, spec=spec)
    if not candidates:
        return _PageCandidate(page_num=page.page_num)
    return candidates[0]


# Confidence floor for accepting a transition candidate when the
# best candidate represents a continuation of the previous page's
# section. 0.6 corresponds to a primary-regex hit on a mid_page band
# — strong enough that a stray template mention won't trigger a
# spurious section break, but low enough that a plain-text new
# section header (like \"Co-Mill operation\" on a page that also
# carries the previous section's repeating bold header) wins the
# pick. Tuned against the user's real Apitoria BPCR validation;
# revisit if production docs show a different distribution.
_TRANSITION_CONFIDENCE_FLOOR: float = 0.6


def _pick_assigned_candidate(
    candidates: list[_PageCandidate],
    *,
    prev_section_id: str,
) -> _PageCandidate | None:
    """Choose which candidate becomes the page's assignment.

    Default: the highest-confidence candidate (preserves the legacy
    behaviour for pages with a single dominant marker).

    Transition rule: when the best candidate represents a
    continuation of the previous page's section, scan lower-ranked
    candidates for one whose section_id is *different* from the
    previous page. If such a candidate has confidence ≥
    :data:`_TRANSITION_CONFIDENCE_FLOOR`, return it instead. This
    captures the real-world case where a BPCR page carries both a
    repeating bold header for the section that's ending AND a less
    emphasised marker for the section that's starting — without
    this rule, the repeating header always wins by confidence and
    the new section is missed entirely until a page that mentions
    only the new section.

    Returns ``None`` when no candidate matched at all (caller falls
    back to inheritance / unsectioned).
    """

    if not candidates:
        return None
    best = candidates[0]
    if best.section is None:
        return None
    if best.section.section_id != prev_section_id:
        # Best is a natural transition (or first match in the run);
        # accept it as-is. Nothing the rule can do better here.
        return best
    # Best is a continuation. Look for a transition candidate.
    for cand in candidates[1:]:
        if cand.section is None:
            continue
        if cand.section.section_id == prev_section_id:
            continue
        if cand.confidence >= _TRANSITION_CONFIDENCE_FLOOR:
            return cand
    # No qualifying transition; stay on the continuation.
    return best


# ── Span assembly ───────────────────────────────────────────────────────────


def _assemble_spans(
    candidates: list[_PageCandidate],
    *,
    total_pages: int,
) -> list[SectionSpan]:
    """Convert per-page candidates into contiguous SectionSpans.

    Algorithm:

    1. Walk pages in order. A page's section_id is taken from its own
       candidate when one matched; otherwise it inherits from the
       previous page (a header on page N starts a section that runs
       until the next header).
    2. The leading run before the first detected header is
       ``unsectioned``.
    3. Adjacent spans with the same section_id are merged.
    """

    if total_pages <= 0:
        return []

    by_page = {c.page_num: c for c in candidates}
    inherited_id: str = UNSECTIONED_ID
    inherited_display: str = ""
    inherited_method: str = "unmatched"
    inherited_confidence: float = _CONF_NONE
    inherited_text: str = ""
    inherited_band: str = ""

    raw: list[tuple[int, str, str, float, str, str, str]] = []
    for page_num in range(1, total_pages + 1):
        cand = by_page.get(page_num)
        if cand and cand.section is not None:
            inherited_id = cand.section.section_id
            inherited_display = cand.section.display_name
            inherited_method = cand.detection_method
            inherited_confidence = cand.confidence
            inherited_text = cand.matched_text
            inherited_band = cand.matched_band
        raw.append(
            (
                page_num,
                inherited_id,
                inherited_display,
                inherited_confidence,
                inherited_method,
                inherited_text,
                inherited_band,
            )
        )

    spans: list[SectionSpan] = []
    for page_num, sid, display, conf, method, text, band in raw:
        if spans and spans[-1].section_id == sid:
            current = spans[-1]
            spans[-1] = SectionSpan(
                section_id=current.section_id,
                display_name=current.display_name,
                start_page=current.start_page,
                end_page=page_num,
                confidence=current.confidence,
                detection_method=current.detection_method,
                matched_text=current.matched_text,
                matched_band=current.matched_band,
            )
        else:
            spans.append(
                SectionSpan(
                    section_id=sid,
                    display_name=display,
                    start_page=page_num,
                    end_page=page_num,
                    confidence=conf,
                    detection_method=method,
                    matched_text=text,
                    matched_band=band,
                )
            )
    return spans


def _failed_map(
    *, doc_id: str, spec_version: str, total_pages: int, note: str
) -> BPCRSectionMap:
    span = SectionSpan(
        section_id=UNSECTIONED_ID,
        display_name="",
        start_page=1,
        end_page=max(total_pages, 1),
        confidence=_CONF_NONE,
        detection_method="unmatched",
    )
    return BPCRSectionMap(
        doc_id=doc_id,
        spec_version=spec_version,
        method="heuristic",
        outcome="failed",
        spans=[span],
        notes=[note],
    )


# ── Public entry point ─────────────────────────────────────────────────────


def detect_bpcr_sections(
    *,
    doc_id: str,
    ocr: OCRResult,
    sections_spec: BPCRSectionsSpec,
    mode: DetectionMode = "heuristic",
) -> BPCRSectionMap:
    """Detect canonical sections within a single BPCR document.

    See ``specs/007-bpcr-layout-aware-sections/contracts/capability-contract.md``
    for the full contract. Pure function: deterministic for a given
    ``(doc, spec, mode)``; never raises in heuristic mode (failure
    surfaces as ``outcome='failed'``).
    """

    if mode in ("vlm", "hybrid"):
        raise NotImplementedError(
            f"detect_bpcr_sections mode={mode!r} is not implemented in v0; "
            "only 'heuristic' is available until the canonical section "
            "list is locked (Spec 007 Open Question #1)."
        )

    started = time.perf_counter()
    total_pages = len(ocr.pages)
    logger.info(
        "bpcr.section_detect.entry doc_id=%s pages=%d method=%s spec_version=%s",
        doc_id,
        total_pages,
        mode,
        sections_spec.spec_version,
    )

    if total_pages == 0:
        result = _failed_map(
            doc_id=doc_id,
            spec_version=sections_spec.spec_version,
            total_pages=1,
            note="empty_ocr",
        )
        _log_exit(doc_id=doc_id, mode=mode, result=result, started=started)
        return result

    if not sections_spec.sections:
        result = _failed_map(
            doc_id=doc_id,
            spec_version=sections_spec.spec_version,
            total_pages=total_pages,
            note="empty_spec",
        )
        _log_exit(doc_id=doc_id, mode=mode, result=result, started=started)
        return result

    try:
        # Per-page evaluation now returns the FULL ranked candidate
        # list so the picker downstream can apply the transition rule.
        # Walk pages in document order, threading the previous page's
        # section_id forward — that lets a transition page (which
        # carries both a continuation header for the section that's
        # ending and a new-section header) emit the new section.
        all_candidates = [
            _evaluate_page_candidates(page, spec=sections_spec)
            for page in ocr.pages
        ]
        prev_section_id = UNSECTIONED_ID
        chosen: list[_PageCandidate] = []
        for page_candidates in all_candidates:
            pick = _pick_assigned_candidate(
                page_candidates, prev_section_id=prev_section_id,
            )
            if pick is None or pick.section is None:
                continue
            chosen.append(pick)
            prev_section_id = pick.section.section_id

        spans = _assemble_spans(chosen, total_pages=total_pages)
        any_matched = any(c.section is not None for c in chosen)
        outcome: Literal["ok", "partial", "failed"]
        notes: list[str] = []
        if not any_matched:
            outcome = "partial"
            notes.append("no_section_headers_detected")
        elif any(span.section_id == UNSECTIONED_ID for span in spans):
            outcome = "partial"
            notes.append("contains_unsectioned_pages")
        else:
            outcome = "ok"
        result = BPCRSectionMap(
            doc_id=doc_id,
            spec_version=sections_spec.spec_version,
            method="heuristic",
            outcome=outcome,
            spans=spans,
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open per FR-006
        logger.warning(
            "bpcr.section_detect.failed doc_id=%s exception_class=%s "
            "exception_message=%s",
            doc_id,
            exc.__class__.__name__,
            exc,
        )
        result = _failed_map(
            doc_id=doc_id,
            spec_version=sections_spec.spec_version,
            total_pages=total_pages,
            note=f"detector_exception:{exc.__class__.__name__}",
        )

    _log_exit(doc_id=doc_id, mode=mode, result=result, started=started)
    return result


def _log_exit(
    *, doc_id: str, mode: DetectionMode, result: BPCRSectionMap, started: float
) -> None:
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "bpcr.section_detect.exit doc_id=%s pages=%d method=%s outcome=%s "
        "duration_ms=%d n_spans=%d",
        doc_id,
        result.spans[-1].end_page if result.spans else 0,
        mode,
        result.outcome,
        duration_ms,
        len(result.spans),
    )


__all__ = [
    "DETECTOR_VERSION",
    "BPCRSectionMap",
    "DetectionMode",
    "SectionSpan",
    "detect_bpcr_sections",
]
