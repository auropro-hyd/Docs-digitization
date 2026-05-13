"""HTML renderer for the client-aligned compliance report.

Pure function: ``ReportDocument`` → HTML string. The PDF renderer
routes its output through WeasyPrint; the standalone HTML export
returns this string directly.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.compliance.report_renderer.types import ReportDocument

logger = logging.getLogger(__name__)

_TEMPLATE_DIR: Path = Path(__file__).parent / "templates"

_env: Environment = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    keep_trailing_newline=True,
)

# Anchor for relative ``report_logo_path`` settings. The default
# value (``backend/app/compliance/report_renderer/assets/logo.svg``)
# is written relative to the repo root, but the backend usually
# runs with CWD inside ``backend/`` — so a bare ``Path()`` resolve
# on that string falls through to "file not found" and the
# rendered HTML carries ``<img src="">``. We probe a small list
# of candidate anchors to cover both invocation styles.
_REPO_ROOT: Path = Path(__file__).resolve().parents[4]
_BACKEND_ROOT: Path = Path(__file__).resolve().parents[3]


def _load_stylesheet() -> str:
    return (_TEMPLATE_DIR / "styles.css").read_text(encoding="utf-8")


def _resolve_logo_path(logo_path: Path) -> Path | None:
    """Return the first candidate location where ``logo_path``
    points at a real file, or ``None`` if none of them exist.

    Order: literal / CWD-relative → repo-root-anchored →
    backend-root-anchored. Lets the setting carry either a
    repo-root-relative value (the default) or a backend-relative
    one without forcing operators to match the server's CWD."""

    candidates = [logo_path]
    if not logo_path.is_absolute():
        candidates.extend([
            _REPO_ROOT / logo_path,
            _BACKEND_ROOT / logo_path,
        ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _encode_logo_data_uri(logo_path: Path | None) -> str:
    """Inline the logo as a base64 data URI so the rendered HTML
    has no external file references — works whether the HTML is
    served standalone or routed through WeasyPrint.

    Returns empty string when the file can't be resolved; the
    template falls back to a text-only header (no ``<img>`` tag
    rendered with an empty ``src``)."""

    if not logo_path:
        return ""
    resolved = _resolve_logo_path(Path(logo_path))
    if resolved is None:
        logger.warning(
            "report logo not found at %s (tried CWD, repo root, backend root)",
            logo_path,
        )
        return ""
    try:
        data = resolved.read_bytes()
    except OSError:
        logger.exception("report logo unreadable at %s", resolved)
        return ""
    ext = resolved.suffix.lstrip(".").lower() or "png"
    mime = "image/svg+xml" if ext == "svg" else f"image/{ext}"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


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
