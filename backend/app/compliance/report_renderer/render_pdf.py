"""PDF renderer for the client-aligned compliance report.

Routes the HTML output through WeasyPrint (HTML→PDF). Catches
WeasyPrint exceptions so the route handler can fall back to HTML
when the native dep stack is broken (e.g. missing pango on Linux,
or wrong DYLD path on macOS).
"""

from __future__ import annotations

import logging

from app.compliance.report_renderer.render_html import render_html
from app.compliance.report_renderer.types import ReportDocument

logger = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """Raised when WeasyPrint can't load its native deps or fails
    to render. Caller catches this to fall back to HTML."""


def render_pdf(doc: ReportDocument) -> bytes:
    """Render ``doc`` to PDF bytes.

    Lazy import of ``weasyprint`` so the module loads on systems
    that don't have pango/cairo installed (e.g. CI containers where
    only the unit tests run).
    """

    try:
        import weasyprint  # type: ignore[import-not-found]
    except OSError as exc:  # native libs missing — typical on macOS without pango brew install
        logger.exception("WeasyPrint native libs not available; PDF render unavailable")
        raise PdfRenderError(
            "PDF rendering requires WeasyPrint with native pango / cairo. "
            "Install via `brew install pango` on macOS or `apt-get install "
            "libpango-1.0-0 libcairo2` on Linux."
        ) from exc

    html = render_html(doc)
    try:
        return weasyprint.HTML(string=html).write_pdf()
    except Exception as exc:
        logger.exception("WeasyPrint failed to render report PDF")
        raise PdfRenderError(f"WeasyPrint render failed: {exc!s}") from exc
