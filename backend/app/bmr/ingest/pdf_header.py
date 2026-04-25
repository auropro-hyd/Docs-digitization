"""First-page header-text extraction for the hybrid classifier.

Uses pypdfium2 (already a dependency) to pull the top 30% of page 1 as
plain text. Non-PDF bytes or unreadable files produce an empty string —
the classifier handles that gracefully as ``header_extractor_failed``.
"""

from __future__ import annotations

from io import BytesIO

try:  # pragma: no cover — import guard, tested via tests
    import pypdfium2 as pdfium  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    pdfium = None  # type: ignore[assignment]

HEADER_BAND_FRACTION = 0.30  # top 30% of page 1


def extract_first_page_header(content: bytes, filename: str) -> str:
    """Return plain-text from the top band of page 1.

    Empty string on failure. ``filename`` is accepted for the
    ``HeaderTextExtractor`` signature but not used here.
    """

    del filename  # signature compat only
    if not content:
        return ""
    if pdfium is None:
        return ""

    try:
        pdf = pdfium.PdfDocument(BytesIO(content))
    except Exception:
        return ""

    try:
        if len(pdf) == 0:
            return ""
        page = pdf[0]
        try:
            width, height = page.get_size()
            textpage = page.get_textpage()
            try:
                # Coordinates in pypdfium2 use PDF coords (origin bottom-left).
                # Top 30% band => y in [height * (1 - 0.30), height].
                top_y = height
                bottom_y = height * (1.0 - HEADER_BAND_FRACTION)
                text = textpage.get_text_bounded(
                    left=0.0,
                    bottom=bottom_y,
                    right=width,
                    top=top_y,
                )
            finally:
                textpage.close()
        finally:
            page.close()
    except Exception:
        return ""
    finally:
        pdf.close()

    return text or ""


__all__ = ["HEADER_BAND_FRACTION", "extract_first_page_header"]
