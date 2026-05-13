"""HTML renderer for the client-aligned compliance report.

Pure function: ``ReportDocument`` → HTML string. The PDF renderer
routes its output through WeasyPrint; the standalone HTML export
returns this string directly.
"""

from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.compliance.report_renderer.types import ReportDocument

_TEMPLATE_DIR: Path = Path(__file__).parent / "templates"

_env: Environment = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    keep_trailing_newline=True,
)


def _load_stylesheet() -> str:
    return (_TEMPLATE_DIR / "styles.css").read_text(encoding="utf-8")


def _encode_logo_data_uri(logo_path: Path | None) -> str:
    """Inline the logo as a base64 data URI so the rendered HTML
    has no external file references — works whether the HTML is
    served standalone or routed through WeasyPrint."""

    if not logo_path:
        return ""
    try:
        path = Path(logo_path)
        if not path.is_file():
            return ""
        data = path.read_bytes()
        ext = path.suffix.lstrip(".").lower() or "png"
        mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except OSError:
        return ""


def render_html(doc: ReportDocument) -> str:
    """Render ``doc`` to a self-contained HTML string."""

    template = _env.get_template("report.html.j2")
    logo_uri = _encode_logo_data_uri(doc.header.logo_path)
    multi_agent = len({row.agent for row in doc.rows}) > 1

    return template.render(
        header=doc.header,
        rows=doc.rows,
        footer=doc.footer,
        stylesheet=_load_stylesheet(),
        logo_data_uri=logo_uri,
        multi_agent=multi_agent,
    )
