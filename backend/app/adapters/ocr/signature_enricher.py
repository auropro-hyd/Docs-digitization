"""Four-layer signature-marker enrichment for Datalab OCR output.

CONTEXT

Datalab's signature classifier is inconsistent across documents.
On Akhilesh's first BPCR package it emitted 53 ``[Signature]``
inline markers across 13 pages; on a second BPCR package (same
product family, sparser initial-style handwriting) it emitted
zero. The downstream pipeline — HITL side-pane, rule 5
(signed-step verification), sub-section detector — keys off
those markers and silently lost all signature signal on the
second package.

This module is a deterministic, idempotent post-OCR pass that
restores the missing signal without re-running OCR. It treats
the marker as the canonical surface: regardless of whether
Datalab classified the signature, classified it as Handwriting,
or returned nothing, downstream consumers see the same
``[Signature]`` text in the markdown they already parse.

LAYER HIERARCHY (highest confidence first)

  L0 (0.85)  ``<!-- block_type: Signature -->`` in markdown
             — Datalab's explicit signature block. Already
             captured by :func:`datalab._parse_signatures`;
             this enricher just counts them for telemetry.

  L1 (0.80)  ``[Signature]`` inline text in markdown
             — Datalab's inline form (the 53 markers from the
             first package were all this layer). Same handling
             as L0: counted, not modified.

  L2 (0.65)  A JSON-tree ``Signature`` block whose bounding
             polygon falls inside a ``TableCell`` whose column
             header matches the configured signature-column
             pattern. Synthesizes ``[Signature]`` into that
             cell in the markdown.

  L3 (0.45)  A JSON-tree ``Handwriting`` block whose polygon
             falls inside a TableCell in a signature-named
             column, AND the cell currently has no
             ``[Signature]`` marker. Fires last so an
             explicit-Signature classification at L2 always
             wins.

The L0/L1 layers are reflexive — Datalab already did the work;
we just observe. L2 and L3 are this module's contribution.

DESIGN PRINCIPLES

* **Pure functions.** No I/O, no globals, no side-effects.
  ``enrich`` takes markdown + JSON blocks + config; returns a
  new markdown + telemetry.
* **Single source of truth for column headers.** The list lives
  in ``document_profiles.yaml`` under ``signature_column_headers``,
  loaded via :func:`load_profiles`. Adding new column phrasings
  is a YAML edit.
* **Idempotent.** A cell that already contains ``[Signature]``
  is never touched. Running ``enrich`` N times produces the
  same output as running it once.
* **Backward compatible.** Old-doc happy path produces the
  same 53 markers via L0/L1; the enricher injects zero
  additional markers. New-doc unhappy path gains markers via
  L2/L3 at lower confidence, surfacing the missed signature
  signal without claiming false certainty.
* **Observable.** Every injection records its layer so the
  per-page telemetry shows ``{L0: 41, L1: 12, L2: 8, L3: 3}``
  and the next regression has a visible signal at boot.
* **Kill-switch.** When
  ``DatalabConfig.signature_enrichment=False``, the pure
  function still computes telemetry but injects nothing. Useful
  for diagnostic A/B against the raw classifier.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Confidence carried into the SignatureRegion record per layer.
# Picked so the layer ordering is preserved even after the
# confidence floor that downstream rule 5 / VLM-arbitration uses
# (PR #26 system-prompt mapping: <0.6 → uncertain).
LAYER_CONFIDENCE: dict[str, float] = {
    "L0": 0.85,
    "L1": 0.80,
    # L_IMG — Datalab classified the signature CELL as an image
    # region rather than text, and emitted an
    # ``<img data-bbox="..." src="HASH_img.jpg"/>`` tag inline
    # in the markdown. Strong positive signal: Datalab cropped
    # the region specifically because it detected handwritten
    # content. Confidence 0.75 — below L1 (Datalab's explicit
    # text marker) but above the heuristic layers because the
    # block extraction is deterministic, not a guess.
    "L_IMG": 0.75,
    "L2": 0.65,
    # L_HWTEXT — Datalab transcribed the handwritten initials as
    # text wrapped in italic markup (``<i>FE</i>`` /
    # ``<i>N089</i>``). The italic wrapper is Datalab's marker
    # for "this is handwriting" even though it could OCR the
    # characters. When the cell sits in a signature-named
    # column, that's a deterministic-classifier signal worth
    # 0.55 confidence — below L2 (explicit Signature block)
    # but above the unstructured text layers.
    "L_HWTEXT": 0.55,
    "L3": 0.45,
    # L_TEXT — last-resort short-text heuristic. Fires when a
    # cell in a signature-named column contains short
    # (≤ MAX_SIG_TEXT_CHARS) non-date, non-verdict content
    # without any of the above signals. Confidence 0.40 —
    # just above L4 because the presence of *some* content is
    # marginally more signal than just a date.
    "L_TEXT": 0.40,
    # L4 — date-only / page-evidence heuristic. Fires when NO
    # JSON-tree evidence (Signature or Handwriting blocks) is
    # available on the page, but the cell sits in a
    # signature-named column AND carries date-only content.
    # Diagnostic on the May 4 doc showed Datalab returns
    # ``handwritten_count=0`` on every page even when 12
    # ``[Signature]`` markers are inline — so the JSON tree is
    # unreliable as evidence. L4 closes the gap. Confidence
    # floored at 0.30 so it never satisfies the
    # ``>=0.6 → firm`` mapping; downstream consumers treat
    # these as ``uncertain``-tier signals.
    "L4": 0.30,
}


# Maximum non-date character length for L_TEXT to fire. The 105
# real-doc samples on 2538105061.pdf show 100 % of legitimate
# initial-transcription cells are ≤ 20 chars after stripping
# dates / italic / br tags; we set a 40-char cap to give
# headroom for compound initials like "R. K. Jha" without
# catching legitimate prose ("Sampled by quality team" etc).
MAX_SIG_TEXT_CHARS = 40


# Words operators legitimately type into signature columns as
# review verdicts — NOT signatures. Skip these from L_HWTEXT
# and L_TEXT to avoid false positives where an "OK" or "PASS"
# in a Done-by column gets stamped as signed when no actual
# signature was captured.
_VERDICT_WORDS: frozenset[str] = frozenset({
    "ok", "okay", "pass", "passed", "fail", "failed",
    "na", "n/a", "n.a.", "nil", "none",
    "yes", "no", "y", "n",
    "approved", "approve", "rejected", "reject",
    "compliant", "non-compliant", "noncompliant",
    "satisfactory", "unsatisfactory",
    "done", "complete", "completed", "pending",
    "tbd", "tba",
})


# A date in any of the formats we've seen on real BPCR pages:
#   03/10/2025  03-10-2025  03.10.2025
#   3/10/25     03/10/25
# The pattern is intentionally permissive — false-positive cost
# is low (date-matched cells in signature columns are exactly
# the cells we want to consider) and false-negative cost is
# high (a missed date means a missed signature-column hit).
DATE_RE = re.compile(
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
)


# Datalab's existing markers we treat as "already authoritative".
EXISTING_MARKER_RE = re.compile(r"\[Signature\]", re.IGNORECASE)
EXISTING_BLOCK_RE = re.compile(
    r"<!--\s*block_type:\s*Signature\s*-->", re.IGNORECASE
)


# Datalab's image-region marker for handwriting/signature blocks.
# Format: ``<img data-bbox="x1 y1 x2 y2" src="<HASH>_img.jpg"/>``
# When this appears INSIDE a signature-named column cell, it's a
# strong positive signature signal — Datalab cropped that exact
# region because it detected non-text content there. Diagnostic
# on the 2538105061.pdf run showed page 3 alone has 32 such tags
# in Done-by / Checked-by cells.
IMG_TAG_RE = re.compile(
    r"<img\s+[^>]*data-bbox\s*=\s*\"[^\"]+\"[^>]*>",
    re.IGNORECASE,
)


# Datalab wraps transcribed handwriting in italic markup.
# Example real-doc cells: ``<i>FE</i><br>22/11/2025`` /
# ``<i>N089</i><br>22/11/2025``. The italic tag is a strong
# Datalab-emitted signal that the underlying ink was
# handwritten — independent of the cell's text content.
ITALIC_TAG_RE = re.compile(r"<i\b[^>]*>", re.IGNORECASE)


# Markup we strip from a cell to compute its "real text content"
# for L_HWTEXT and L_TEXT length / verdict checks. Excluding
# these from the cleaned length means a cell like
# ``<i>AK</i><br>03/10/2025`` has cleaned content ``"AK"`` —
# 2 chars, clearly a signature initial.
_TAG_STRIP_RE = re.compile(r"</?(?:i|b|em|strong|br|span|p|div)\b[^>]*>", re.IGNORECASE)


def _clean_for_signature_check(cell: str) -> str:
    """Strip markup, dates, and filler chars from a cell so the
    L_HWTEXT / L_TEXT layers can reason about the "real" content.

    Returns the cleaned text. An empty result means the cell
    held only filler (dates, dashes, whitespace, tags) — no
    text content worth treating as a signature.
    """
    text = _TAG_STRIP_RE.sub("", cell)
    text = DATE_RE.sub("", text)
    text = text.replace("&nbsp;", " ")
    return text.strip(" -—|<>/\t\n\r")


@dataclass(frozen=True)
class JsonBlock:
    """A single block extracted from Datalab's JSON tree.

    Only the fields the enricher needs — kept minimal so the
    caller (datalab adapter) can construct these from whatever
    shape the SDK returns without coupling.
    """

    block_type: str
    polygon: tuple[tuple[float, float], ...]
    page_num: int
    text: str = ""


@dataclass
class EnrichmentTelemetry:
    """Per-page counters surfaced to ``extraction_telemetry``."""

    layer_counts: dict[str, int] = field(
        default_factory=lambda: {
            "L0": 0, "L1": 0, "L_IMG": 0, "L2": 0,
            "L_HWTEXT": 0, "L3": 0, "L_TEXT": 0, "L4": 0,
        }
    )
    injected_count: int = 0
    skipped_idempotent: int = 0
    skipped_verdict: int = 0
    skipped_long_text: int = 0
    signature_columns_detected: int = 0
    tables_scanned: int = 0

    def merge(self, other: "EnrichmentTelemetry") -> None:
        for k, v in other.layer_counts.items():
            self.layer_counts[k] = self.layer_counts.get(k, 0) + v
        self.injected_count += other.injected_count
        self.skipped_idempotent += other.skipped_idempotent
        self.skipped_verdict += other.skipped_verdict
        self.skipped_long_text += other.skipped_long_text
        self.signature_columns_detected += other.signature_columns_detected
        self.tables_scanned += other.tables_scanned

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_counts": dict(self.layer_counts),
            "injected_count": self.injected_count,
            "skipped_idempotent": self.skipped_idempotent,
            "skipped_verdict": self.skipped_verdict,
            "skipped_long_text": self.skipped_long_text,
            "signature_columns_detected": self.signature_columns_detected,
            "tables_scanned": self.tables_scanned,
        }


@dataclass(frozen=True)
class EnrichmentResult:
    """Return value of :func:`enrich_page`."""

    markdown: str
    telemetry: EnrichmentTelemetry


# ── Layer detection helpers ──────────────────────────────────


def _count_existing_markers(markdown: str) -> tuple[int, int]:
    """Count L0 + L1 markers already present in the markdown.

    Returns ``(l0_count, l1_count)``. Used for telemetry only —
    these layers don't trigger injections.
    """
    l0 = len(EXISTING_BLOCK_RE.findall(markdown))
    l1 = len(EXISTING_MARKER_RE.findall(markdown))
    return l0, l1


def _bbox_contains(outer: JsonBlock, inner: JsonBlock) -> bool:
    """True when ``inner``'s polygon centre falls inside ``outer``'s."""
    if not outer.polygon or not inner.polygon:
        return False
    if outer.page_num != inner.page_num:
        return False

    o_xs = [p[0] for p in outer.polygon]
    o_ys = [p[1] for p in outer.polygon]
    o_x_min, o_x_max = min(o_xs), max(o_xs)
    o_y_min, o_y_max = min(o_ys), max(o_ys)

    i_xs = [p[0] for p in inner.polygon]
    i_ys = [p[1] for p in inner.polygon]
    cx = sum(i_xs) / len(i_xs)
    cy = sum(i_ys) / len(i_ys)

    return o_x_min <= cx <= o_x_max and o_y_min <= cy <= o_y_max


def _normalize_header(text: str) -> str:
    return " ".join(text.lower().split())


def _is_signature_column_header(text: str, patterns: tuple[str, ...]) -> bool:
    """True when the header text contains any signature-column pattern."""
    if not text or not patterns:
        return False
    norm = _normalize_header(text)
    return any(p in norm for p in patterns)


_FILLER_TOKENS = {"-", "—", "--", "---", "----", "_", "__", "___", "____"}


def _is_filler_only(cell_text: str) -> bool:
    """True for cells whose content reduces to nothing but
    filler — pure whitespace, dashes, or markup tags. These are
    legitimate ``no data captured`` cells; the L4 / L_TEXT
    layers must NEVER stamp a ``[Signature]`` over them
    (observed false positive on 2538105061.pdf where
    ``[Signature] ----`` was being injected into dash-only
    cells — 19 such cells across the doc pre-fix).
    """
    text = _TAG_STRIP_RE.sub("", cell_text).strip()
    if not text:
        return True
    if text in _FILLER_TOKENS:
        return True
    # Repeated dash / underscore / dot runs of any length count
    # as filler.
    if all(c in "-—_." for c in text):
        return True
    return False


def _is_date_only_or_empty(cell_text: str) -> bool:
    """True for cells that should be considered signature-column-empty
    AND contain a date.

    Used by the L4 path: when a cell carries a date with NOTHING
    else meaningful (after markup, filler, tags are stripped),
    L4 treats the date as evidence that the row was signed
    (the operator wrote the date but their initials weren't
    captured by OCR — common with light pen strokes).

    Tag-stripping handles ``<br>27/11/2025`` (1 page on the
    UIIBEHSII28 run had this shape and was being missed
    because the ``<br>`` survived the strip).

    Filler-only cells (dashes, underscores, empty) return False
    here — they're missing-signature findings, not date-only
    signatures. Use :func:`_is_filler_only` to test for that.
    """
    text = _TAG_STRIP_RE.sub("", cell_text).strip()
    if not text:
        return False  # filler — handled separately
    if text in _FILLER_TOKENS:
        return False  # filler
    # After tag/filler strip, must contain a date AND nothing
    # else of substance.
    if not DATE_RE.search(text):
        return False
    without_dates = DATE_RE.sub("", text).strip(" -—_.|")
    if not without_dates:
        return True
    if without_dates in _FILLER_TOKENS:
        return True
    return False


# ── Markdown table walker ────────────────────────────────────


def _split_table_row(row: str) -> list[str]:
    """Split a markdown table row into cell strings.

    Strips leading/trailing pipes; preserves internal whitespace
    so cell content can be matched against the JSON tree's
    extracted text.
    """
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return inner.split("|")


def _is_separator_row(row: str) -> bool:
    """True for the ``|---|---|`` row Markdown uses to split header from body."""
    return bool(re.fullmatch(r"\|[\s\-:|]+\|", row.strip()))


def _enumerate_tables(markdown: str) -> list[tuple[int, int, list[str]]]:
    """Walk the markdown and yield each table as ``(start_idx, end_idx, rows)``.

    ``start_idx`` / ``end_idx`` are character offsets in the
    original markdown so the enricher can splice replacements
    back in without re-stringifying the whole document.
    """
    tables: list[tuple[int, int, list[str]]] = []
    # Each table is a run of consecutive lines that start with '|'
    # and contain at least one separator row.
    lines = markdown.split("\n")
    offset = 0
    line_offsets: list[int] = []
    for line in lines:
        line_offsets.append(offset)
        offset += len(line) + 1  # +1 for the \n

    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].strip().startswith("|"):
            i += 1
        end = i  # exclusive
        rows = lines[start:end]
        if any(_is_separator_row(r) for r in rows):
            start_offset = line_offsets[start]
            end_offset = (
                line_offsets[end] if end < len(lines)
                else len(markdown)
            )
            tables.append((start_offset, end_offset, rows))
    return tables


# ── L2 + L3: injection ───────────────────────────────────────


def _enrich_table(
    rows: list[str],
    signature_column_patterns: tuple[str, ...],
    page_has_signature_block: bool,
    page_has_handwriting_block: bool,
    *,
    aggressive: bool = True,
) -> tuple[list[str], EnrichmentTelemetry]:
    """Walk a single markdown table; inject ``[Signature]`` into
    qualifying cells. Returns ``(new_rows, telemetry)``.

    The injection layer depends on what evidence the page carries:

    * **L2 path** — page has at least one JSON-tree ``Signature``
      block. Datalab classified at least one signature on the
      page; cells in signature-named columns with date-only
      content are stamped at L2 confidence.

    * **L3 path** — page has Handwriting blocks but no Signature
      classifications. The classifier saw handwriting; we
      synthesize ``[Signature]`` at L3 confidence.

    * **L4 path** (when ``aggressive=True``) — page has NO JSON-tree
      evidence at all. This is the common case on real BPCRs:
      diagnostic on the May 4 doc showed ``handwritten_count=0``
      on every page even when 12 ``[Signature]`` markers were
      inline. The JSON tree is unreliable as evidence; column-
      header + date-only content alone trigger injection at the
      lowest confidence (0.30) so downstream
      ``llm_arbitrated`` / VLM-tier consumers can downweight.

    Per-cell idempotency: cells already containing ``[Signature]``
    are skipped (counted under ``skipped_idempotent``).
    """
    telemetry = EnrichmentTelemetry()

    if not rows:
        return rows, telemetry

    telemetry.tables_scanned += 1

    # Find the header row — the first row that is not a separator.
    header_idx = None
    for i, r in enumerate(rows):
        if not _is_separator_row(r):
            header_idx = i
            break
    if header_idx is None:
        return rows, telemetry

    header_cells = _split_table_row(rows[header_idx])
    sig_columns = {
        i for i, h in enumerate(header_cells)
        if _is_signature_column_header(h, signature_column_patterns)
    }
    if not sig_columns:
        return rows, telemetry

    telemetry.signature_columns_detected += len(sig_columns)

    # Choose the strongest available layer. L2 > L3 > L4.
    # The page-level layer used for cells without their own
    # explicit evidence (i.e., date-only-or-empty cells the
    # heuristic layers backfill).
    page_layer: str | None = None
    if page_has_signature_block:
        page_layer = "L2"
    elif page_has_handwriting_block:
        page_layer = "L3"
    elif aggressive:
        page_layer = "L4"
    # else: no page-level evidence + aggressive disabled —
    # heuristic layers don't fire. L_IMG still can.

    new_rows = list(rows)
    for i, row in enumerate(rows):
        if i == header_idx or _is_separator_row(row):
            continue
        cells = _split_table_row(row)
        if not cells:
            continue
        modified = False
        for col_idx in sig_columns:
            if col_idx >= len(cells):
                continue
            cell = cells[col_idx]
            if EXISTING_MARKER_RE.search(cell):
                telemetry.skipped_idempotent += 1
                continue

            # ── L_IMG: cell contains a Datalab image-region tag
            # for handwritten content. Datalab cropped the region
            # deliberately because it detected non-text; that's a
            # strong positive signature signal regardless of any
            # page-level block evidence. Inject ``[Signature]``
            # text ALONGSIDE the <img> tag so frontends render
            # both: the text marker (for grep/search/rule 5) AND
            # the image (visual evidence for HITL reviewers).
            if IMG_TAG_RE.search(cell):
                cells[col_idx] = f" [Signature] {cell.strip()} "
                telemetry.layer_counts["L_IMG"] += 1
                telemetry.injected_count += 1
                modified = True
                continue

            # An empty / filler-only cell in a signature column
            # is a legitimate missing-signature finding —
            # preserve it (don't inject anything below this
            # point). Tightened on 2026-05-12 after Akhilesh
            # reported false positives on dash-only cells
            # (``[Signature] ----`` was being stamped).
            # ``_is_filler_only`` returns True for whitespace,
            # any-length dash / underscore / dot runs, and
            # bare markup tags.
            if _is_filler_only(cell):
                continue

            # Compute the cell's "real" text content with dates,
            # italic / br markup, and filler stripped. Used by
            # L_HWTEXT verdict-skip and L_TEXT length check.
            cleaned = _clean_for_signature_check(cell)
            cleaned_lower = cleaned.lower()

            # Verdict words ("OK", "PASS", "NA", "approved", …)
            # are operator-typed review outcomes, NOT signatures.
            # Skip them at every layer so a Done-by cell carrying
            # ``OK`` doesn't get falsely stamped as signed.
            if cleaned_lower in _VERDICT_WORDS:
                telemetry.skipped_verdict += 1
                continue

            # ── L_HWTEXT: italic-wrapped handwritten transcription.
            # Datalab marks handwritten text with ``<i>...</i>``.
            # When that appears in a signature column, Datalab is
            # telling us the underlying ink was handwriting —
            # even though the OCR could read it. Inject the marker
            # at L_HWTEXT confidence (0.55) — above the pure
            # heuristic layers, below explicit Signature blocks.
            if ITALIC_TAG_RE.search(cell) and cleaned:
                cells[col_idx] = f" [Signature] {cell.strip()} "
                telemetry.layer_counts["L_HWTEXT"] += 1
                telemetry.injected_count += 1
                modified = True
                continue

            # ── L4: cell content reduces to a date only — the
            # date-only-cell path. Requires page-level evidence
            # of handwriting (L2/L3) OR aggressive=True (L4).
            # Date alone is the most ambiguous signal — could be
            # a typed scheduled date, not a signature date —
            # so keep it gated on page-layer evidence.
            if page_layer is not None and _is_date_only_or_empty(cell):
                cells[col_idx] = f" [Signature] {cell.strip()} "
                telemetry.layer_counts[page_layer] += 1
                telemetry.injected_count += 1
                modified = True
                continue

            # ── L_TEXT: short non-date text in a signature
            # column with no other signal. The user's rule:
            # "if there are defined columns for signature and
            # anything except date comes in, identify as
            # signature." Bounded by MAX_SIG_TEXT_CHARS to keep
            # legitimate prose ("Sampled by quality team") from
            # being misclassified. Confidence 0.40.
            if cleaned and len(cleaned) <= MAX_SIG_TEXT_CHARS:
                cells[col_idx] = f" [Signature] {cell.strip()} "
                telemetry.layer_counts["L_TEXT"] += 1
                telemetry.injected_count += 1
                modified = True
                continue
            elif cleaned:
                # Too long — likely prose / a description.
                # Track in telemetry so we can revisit the cap
                # if real BPCRs surface long legitimate initials.
                telemetry.skipped_long_text += 1
        if modified:
            # Reassemble the row preserving leading/trailing pipes.
            inner = "|".join(cells)
            leading = "|" if row.strip().startswith("|") else ""
            trailing = "|" if row.strip().endswith("|") else ""
            new_rows[i] = leading + inner + trailing

    return new_rows, telemetry


def _is_empty(cell: str) -> bool:
    """A cell with no content at all (whitespace only)."""
    return not cell.strip()


# ── Public API ───────────────────────────────────────────────


def enrich_page(
    markdown: str,
    json_blocks: list[JsonBlock],
    page_num: int,
    signature_column_headers: tuple[str, ...],
    *,
    enabled: bool = True,
    aggressive: bool = True,
) -> EnrichmentResult:
    """Apply the four-layer enrichment to a single page's markdown.

    Args:
        markdown: The page's markdown as Datalab returned it (after
            any sanitization the adapter performs).
        json_blocks: Blocks extracted from Datalab's JSON tree for
            this page. Must include ``Signature`` and
            ``Handwriting`` blocks; ``TableCell`` blocks are
            optional today (reserved for a future bbox-precise
            L2 upgrade).
        page_num: The page number (for filtering blocks).
        signature_column_headers: Lowercase substring patterns from
            ``document_profiles.yaml``. Empty tuple disables L2/L3
            entirely.
        enabled: When False, only the L0/L1 telemetry is computed;
            no markdown changes are made. Kill switch for A/B
            diagnostics.

    Returns:
        :class:`EnrichmentResult` with the enriched markdown and
        per-layer telemetry.
    """
    telemetry = EnrichmentTelemetry()

    l0, l1 = _count_existing_markers(markdown)
    telemetry.layer_counts["L0"] = l0
    telemetry.layer_counts["L1"] = l1

    if not enabled or not signature_column_headers:
        return EnrichmentResult(markdown=markdown, telemetry=telemetry)

    patterns = tuple(_normalize_header(h) for h in signature_column_headers if h)
    if not patterns:
        return EnrichmentResult(markdown=markdown, telemetry=telemetry)

    page_blocks = [b for b in json_blocks if b.page_num == page_num]
    page_has_signature_block = any(b.block_type == "Signature" for b in page_blocks)
    page_has_handwriting_block = any(b.block_type == "Handwriting" for b in page_blocks)

    # When ``aggressive`` is False AND there's no JSON-tree
    # evidence AND the markdown has no ``<img data-bbox>`` tags
    # to drive L_IMG, short-circuit. The L_IMG path detects
    # Datalab-emitted image regions in signature columns —
    # those count as deterministic evidence (Datalab cropped
    # the region) and fire even in strict mode. With
    # aggressive=True the L4 path fires on column-header +
    # date alone for cells without <img> evidence, which is
    # the only signal available on docs where Datalab's
    # classifier emits ``[Signature]`` inline but doesn't tag
    # individual blocks.
    has_img_tag_evidence = bool(IMG_TAG_RE.search(markdown))
    if (
        not aggressive
        and not (page_has_signature_block or page_has_handwriting_block)
        and not has_img_tag_evidence
    ):
        return EnrichmentResult(markdown=markdown, telemetry=telemetry)

    tables = _enumerate_tables(markdown)
    if not tables:
        return EnrichmentResult(markdown=markdown, telemetry=telemetry)

    out = markdown
    # Walk tables in reverse so each splice doesn't shift the
    # offsets of the unprocessed tables ahead of it.
    for start, end, rows in reversed(tables):
        new_rows, table_telemetry = _enrich_table(
            rows,
            patterns,
            page_has_signature_block,
            page_has_handwriting_block,
            aggressive=aggressive,
        )
        telemetry.merge(table_telemetry)
        if table_telemetry.injected_count:
            replacement = "\n".join(new_rows)
            # Preserve the trailing newline if the original table
            # ended with one (the slice includes it).
            if out[start:end].endswith("\n"):
                replacement += "\n"
            out = out[:start] + replacement + out[end:]

    return EnrichmentResult(markdown=out, telemetry=telemetry)


def enrich_signature_telemetry_summary(per_page: dict[int, EnrichmentTelemetry]) -> dict[str, Any]:
    """Aggregate per-page telemetry for the run-level report.

    Designed to be merged into ``extraction_telemetry`` in the
    OCRResult so the next regression is visible at boot.
    """
    agg = EnrichmentTelemetry()
    for tel in per_page.values():
        agg.merge(tel)
    return {
        "by_page": {p: t.to_dict() for p, t in per_page.items()},
        "totals": agg.to_dict(),
    }
