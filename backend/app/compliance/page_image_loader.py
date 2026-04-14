"""Lazy page-image loader for VLM compliance evaluation.

Checks for a pre-rendered PNG first; if absent, renders from the original
PDF on-demand via ``pypdfium2`` and optionally caches to disk.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pypdfium2 as pdfium

from app.config.settings import VLMConfig, get_settings

logger = logging.getLogger(__name__)


def _render_page(pdf_path: Path, page_num: int, scale: float) -> bytes:
    """Render a single 1-based page from *pdf_path* to PNG bytes."""
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_num - 1]
        bmp = page.render(scale=scale)
        pil_image = bmp.to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def _doc_dir(doc_id: str) -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path) / doc_id


def _images_dir(doc_id: str) -> Path:
    return _doc_dir(doc_id) / "page_images"


def _image_path(doc_id: str, page_num: int) -> Path:
    return _images_dir(doc_id) / f"page_{page_num:03d}.png"


async def load_page_image(
    doc_id: str,
    page_num: int,
    *,
    vlm_config: VLMConfig | None = None,
) -> bytes | None:
    """Return PNG bytes for *page_num* of *doc_id*, or ``None`` on failure.

    1. Check cached PNG on disk.
    2. If missing, render from the original PDF and optionally cache.
    """
    if vlm_config is None:
        vlm_config = get_settings().vlm

    cached = _image_path(doc_id, page_num)
    if cached.exists():
        return cached.read_bytes()

    # Find the original PDF
    doc_path = _doc_dir(doc_id)
    pdfs = list(doc_path.glob("*.pdf"))
    if not pdfs:
        logger.warning("No PDF found for doc %s — cannot render page image", doc_id)
        return None

    try:
        image_bytes = _render_page(pdfs[0], page_num, vlm_config.render_scale)
    except Exception:
        logger.exception("Failed to render page %d for doc %s", page_num, doc_id)
        return None

    if vlm_config.store_page_images:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(image_bytes)
        except OSError:
            logger.warning("Could not cache page image to %s", cached, exc_info=True)

    return image_bytes


async def ensure_page_images(doc_id: str, total_pages: int) -> Path:
    """Pre-render all pages for *doc_id* if not already cached.

    Returns the directory containing the PNGs.
    """
    vlm_config = get_settings().vlm
    out_dir = _images_dir(doc_id)

    if out_dir.exists() and len(list(out_dir.glob("*.png"))) >= total_pages:
        return out_dir

    doc_path = _doc_dir(doc_id)
    pdfs = list(doc_path.glob("*.pdf"))
    if not pdfs:
        logger.warning("No PDF found for doc %s", doc_id)
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdfs[0]))
    try:
        for idx in range(min(len(doc), total_pages)):
            dest = out_dir / f"page_{idx + 1:03d}.png"
            if dest.exists():
                continue
            page = doc[idx]
            bmp = page.render(scale=vlm_config.render_scale)
            pil_image = bmp.to_pil()
            pil_image.save(str(dest), format="PNG")
    finally:
        doc.close()

    logger.info("Rendered %d page images for doc %s", total_pages, doc_id)
    return out_dir
