"""Generate signature image crops for cells Datalab transcribed as text.

CONTEXT

Datalab's signature classifier sometimes crops handwritten initials
out as inline ``<img data-bbox="..." src="HASH_img.jpg"/>`` regions
(the L_IMG layer in signature_enricher catches those), and other
times transcribes the same handwriting AS TEXT — wrapping it in
italic markup like ``<i>FE</i>`` or even just rendering it as plain
text like ``N089``. The L_HWTEXT and L_TEXT layers inject
``[Signature]`` markers in those text-transcribed cases, but a
HITL reviewer scanning the side-pane still wants to see the
visual signature stroke alongside the text.

WHAT THIS MODULE DOES

For each signature-column cell where Datalab transcribed text
(L_HWTEXT / L_TEXT path), this module:

1. Looks up the cell's bbox from the JSON-tree TableCell blocks
2. Renders the PDF page at that bbox at a reasonable DPI
3. Saves the crop to ``<doc_dir>/images/p{page}_sigcrop_{idx}.jpg``
4. Returns a mapping ``page_num -> [(bbox, filename), ...]`` so
   the caller can rewrite the markdown ``[Signature] <i>FE</i>``
   into ``[Signature] <img src="...filename"/> <i>FE</i>``

DESIGN

* **Pure module** — no I/O bound to a sink / context manager. Takes
  inputs, returns outputs, plus writes files to a target directory.
* **Fail-open** — every step is wrapped in try/except. If
  ``pypdfium2`` is missing, if a page render fails, if a crop falls
  outside page bounds — we log and return what we have. The
  pipeline never crashes on a crop failure.
* **Idempotent** — file naming is deterministic per (page, bbox);
  rerunning the crop pass on the same doc produces the same files
  without duplicates.
* **Bounded** — caps total crops per doc to prevent runaway disk
  use when a doc has hundreds of signature cells; logs an overflow
  warning when the cap is hit.
"""

from __future__ import annotations

import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:  # pragma: no cover — exercised via tests
    import pypdfium2 as pdfium  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    pdfium = None  # type: ignore[assignment]


# How many pixels per PDF point when rendering. 2.0 produces
# ~150 DPI which is enough to make handwritten initials clearly
# readable while keeping crop file sizes <10 KB each.
RENDER_SCALE = 2.0

# Hard cap on total crops we'll write per document. Beyond this
# we log overflow and stop; the markdown rewrite still preserves
# the ``[Signature]`` text marker so the side-pane stays usable.
MAX_CROPS_PER_DOC = 2_000

# Pad each crop bbox by this many PDF points on each side so the
# signature stroke isn't cut off at the cell edge. Small enough to
# not overlap adjacent cells.
BBOX_PAD = 2.0


def _bbox_key(page_num: int, bbox: tuple[float, float, float, float]) -> str:
    """Deterministic filename component from (page, bbox).

    Two crops of the same region produce the same key so reruns
    are idempotent.
    """
    raw = f"{page_num}:{bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"p{page_num}_sigcrop_{digest}.jpg"


def crop_cell_regions(
    pdf_path: str,
    cell_bboxes: dict[int, list[tuple[float, float, float, float]]],
    output_dir: Path,
    page_pixel_sizes: dict[int, tuple[float, float]] | None = None,
) -> dict[int, list[tuple[tuple[float, float, float, float], str]]]:
    """Crop ``cell_bboxes`` from the PDF and write JPEG files.

    Args:
        pdf_path: Path to the source PDF.
        cell_bboxes: Mapping ``page_num -> list of (x1, y1, x2, y2)``
            cell bboxes in OCR coordinate space — same as what
            Datalab emits in the JSON tree (image-space, origin
            top-left, units = OCR pixels).
        output_dir: Directory to write crops into. Created if missing.
        page_pixel_sizes: Optional mapping ``page_num -> (w_px, h_px)``
            from Datalab's ``page_width`` / ``page_height`` extraction
            telemetry. Used to scale OCR-space bboxes into PDF-space
            crop rectangles. When None, assumes 1:1 mapping (only
            correct when OCR coordinates already match PDF points).

    Returns:
        ``page_num -> [(bbox, filename), ...]`` — same shape as the
        input, but with each bbox paired with the deterministic
        filename written under ``output_dir``. Empty list for pages
        where rendering failed.

    Fail-open: missing pypdfium2, malformed PDF, out-of-bound
    bboxes — all logged and skipped without raising.
    """
    if pdfium is None:
        logger.warning(
            "cell_image_crop: pypdfium2 not available — skipping crops"
        )
        return {pn: [] for pn in cell_bboxes}

    if not cell_bboxes:
        return {}

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("cell_image_crop: cannot create output_dir %s", output_dir)
        return {pn: [] for pn in cell_bboxes}

    try:
        pdf_bytes = Path(pdf_path).read_bytes()
    except Exception:
        logger.exception("cell_image_crop: cannot read PDF %s", pdf_path)
        return {pn: [] for pn in cell_bboxes}

    try:
        pdf = pdfium.PdfDocument(BytesIO(pdf_bytes))
    except Exception:
        logger.exception("cell_image_crop: failed to open PDF")
        return {pn: [] for pn in cell_bboxes}

    results: dict[int, list[tuple[tuple[float, float, float, float], str]]] = {}
    total_written = 0
    overflow_logged = False

    try:
        for page_num, bboxes in cell_bboxes.items():
            if not bboxes:
                results[page_num] = []
                continue

            try:
                if page_num < 1 or page_num > len(pdf):
                    logger.warning(
                        "cell_image_crop: page %d out of range (1..%d)",
                        page_num, len(pdf),
                    )
                    results[page_num] = []
                    continue
                page = pdf[page_num - 1]
            except Exception:
                logger.exception(
                    "cell_image_crop: cannot open page %d", page_num
                )
                results[page_num] = []
                continue

            try:
                pdf_w, pdf_h = page.get_size()
                # Map OCR-space (image pixels, origin top-left) to
                # PDF coords (points, origin bottom-left).
                ocr_size = page_pixel_sizes.get(page_num) if page_pixel_sizes else None
                if ocr_size and ocr_size[0] > 0 and ocr_size[1] > 0:
                    sx = pdf_w / ocr_size[0]
                    sy = pdf_h / ocr_size[1]
                else:
                    # No OCR image size hint — derive it from the
                    # bbox extents. Datalab emits bboxes in
                    # OCR-image pixel space which is typically
                    # 2.08x (150 DPI) the PDF point space. We
                    # find the max-extent bbox on this page and
                    # assume that's roughly the OCR image size.
                    # If the max extent is smaller than the PDF
                    # size we're already in PDF coords (scale=1).
                    max_x = max((b[2] for b in bboxes), default=0.0)
                    max_y = max((b[3] for b in bboxes), default=0.0)
                    if max_x > pdf_w * 1.2 or max_y > pdf_h * 1.2:
                        sx = pdf_w / max(max_x, pdf_w)
                        sy = pdf_h / max(max_y, pdf_h)
                    else:
                        sx = sy = 1.0

                page_results: list[
                    tuple[tuple[float, float, float, float], str]
                ] = []

                # Deduplicate within the page so the same bbox
                # produces a single crop even if multiple cells
                # reference it (rare but possible).
                seen_keys: set[str] = set()

                for bbox in bboxes:
                    if total_written >= MAX_CROPS_PER_DOC:
                        if not overflow_logged:
                            logger.warning(
                                "cell_image_crop: hit MAX_CROPS_PER_DOC=%d — "
                                "remaining cells will not be cropped",
                                MAX_CROPS_PER_DOC,
                            )
                            overflow_logged = True
                        break

                    key = _bbox_key(page_num, bbox)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    out_path = output_dir / key

                    # Convert bbox to PDF coords. OCR space:
                    # (x1, y1) top-left, (x2, y2) bottom-right,
                    # measured in OCR pixels. PDF space: origin
                    # bottom-left, measured in points.
                    x1, y1, x2, y2 = bbox
                    px1 = max(0.0, x1 * sx - BBOX_PAD)
                    py1 = max(0.0, pdf_h - y2 * sy - BBOX_PAD)
                    px2 = min(pdf_w, x2 * sx + BBOX_PAD)
                    py2 = min(pdf_h, pdf_h - y1 * sy + BBOX_PAD)

                    if px2 <= px1 or py2 <= py1:
                        # Degenerate bbox — out of page or zero-area.
                        continue

                    if out_path.exists() and out_path.stat().st_size > 0:
                        # Idempotent — already produced this crop.
                        page_results.append((bbox, key))
                        continue

                    try:
                        # Render the full page then PIL-crop the
                        # ROI. pypdfium2 supports rendering a
                        # cropbox directly via ``crop`` kwarg
                        # which is more efficient on large pages.
                        bitmap = page.render(
                            scale=RENDER_SCALE,
                            crop=(px1, pdf_h - py2, pdf_w - px2, py1),
                        )
                        try:
                            pil_image = bitmap.to_pil()
                            pil_image.save(out_path, format="JPEG", quality=85)
                            page_results.append((bbox, key))
                            total_written += 1
                        finally:
                            bitmap.close()
                    except Exception:
                        logger.exception(
                            "cell_image_crop: render failed for "
                            "page %d bbox %s", page_num, bbox,
                        )

                results[page_num] = page_results
            finally:
                page.close()
    finally:
        pdf.close()

    return results


def extract_signature_column_cells(
    markdown: str,
    signature_column_headers: tuple[str, ...],
) -> list[tuple[int, int, str]]:
    """Find every signature-column cell in the markdown that has
    a ``[Signature]`` marker but NO ``<img>`` tag.

    Returns a list of ``(row_idx_within_doc, col_idx, cell_text)``
    where ``row_idx_within_doc`` is a 0-based row counter across
    all tables on the page. The caller pairs these with
    JSON-tree TableCell bboxes (which are emitted in the same
    reading order).

    This is the bridge between markdown rendering and JSON-tree
    bbox info: matching by ordered index rather than by content
    avoids the markdown ↔ JSON cell correlation problem.
    """
    from app.adapters.ocr.signature_enricher import (
        EXISTING_MARKER_RE,
        IMG_TAG_RE,
        _is_signature_column_header,
        _is_separator_row,
        _split_table_row,
    )

    cells: list[tuple[int, int, str]] = []
    if not markdown or "[Signature]" not in markdown:
        return cells

    # Walk tables; track a per-page row counter (skipping header
    # + separator rows, matching how Datalab orders TableCells).
    rows = markdown.split("\n")
    row_idx = 0
    i = 0
    while i < len(rows):
        if not rows[i].strip().startswith("|"):
            i += 1
            continue
        table_start = i
        while i < len(rows) and rows[i].strip().startswith("|"):
            i += 1
        table_rows = rows[table_start:i]
        # Identify header
        header_idx = None
        for k, r in enumerate(table_rows):
            if not _is_separator_row(r):
                header_idx = k
                break
        if header_idx is None:
            continue
        header_cells = _split_table_row(table_rows[header_idx])
        sig_cols = {
            ci for ci, h in enumerate(header_cells)
            if _is_signature_column_header(h, signature_column_headers)
        }
        if not sig_cols:
            continue
        for k, r in enumerate(table_rows):
            if k == header_idx or _is_separator_row(r):
                continue
            data_cells = _split_table_row(r)
            for ci in sig_cols:
                if ci >= len(data_cells):
                    continue
                cell = data_cells[ci]
                if not EXISTING_MARKER_RE.search(cell):
                    continue
                if IMG_TAG_RE.search(cell):
                    # Already has an image — skip
                    continue
                cells.append((row_idx, ci, cell))
            row_idx += 1
    return cells


__all__ = [
    "BBOX_PAD",
    "MAX_CROPS_PER_DOC",
    "RENDER_SCALE",
    "crop_cell_regions",
    "extract_signature_column_cells",
]
