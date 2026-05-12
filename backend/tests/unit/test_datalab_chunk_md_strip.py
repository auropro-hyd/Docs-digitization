"""Pin the chunk-level strip of ``<p>Image: signature</p>``.

PR #41 fixed the per-page strip via the signature enricher, but the
``full_markdown`` field (which feeds the HITL review pane's
"full document" view via ``raw_markdown['full']``) is built from
``chunk_md`` returned by ``_process_result`` — a separate path that
the per-page enricher never touched. Run e5e35ffc-… (2026-05-12)
came back with 38 ``Image: signature`` occurrences in
``raw_markdown['full']`` even though every per-page markdown had
clean ``[Signature]`` markers. This test pins the chunk-level
strip so that gap stays closed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.ocr.datalab import DatalabOCRAdapter
from app.config.settings import DatalabConfig


@pytest.fixture
def adapter() -> DatalabOCRAdapter:
    return DatalabOCRAdapter(DatalabConfig(api_key="a-real-looking-key-1234567890"))


def test_chunk_md_strips_image_signature_placeholder(
    adapter: DatalabOCRAdapter,
) -> None:
    """The chunk_md returned by _process_result (second tuple slot,
    which feeds ``full_markdown`` → ``raw_markdown['full']``) must
    not carry the ``<p>Image: signature</p>`` placeholder. The
    per-page enricher already converted it to a clean ``[Signature]``
    marker in the per-page output; the chunk-level path was the
    leak."""

    raw = (
        "# Page 1\n\n"
        "| Sr No | Sign | Date |\n"
        "|---|---|---|\n"
        "| 1 | <p>Image: signature</p> | 26/11/2025 |\n"
        "| 2 | <p>Image: signature</p> | 27/11/2025 |\n"
    )
    result = SimpleNamespace(markdown=raw, images=None)

    _pages, chunk_md, _tm, _sigs, _kv = adapter._process_result(
        result, page_offset=0,
    )

    assert "Image: signature" not in chunk_md, (
        f"chunk_md retained the placeholder — raw_markdown['full'] "
        f"would still show it in HITL review:\n{chunk_md}"
    )


def test_chunk_md_strip_preserves_non_signature_image_placeholders(
    adapter: DatalabOCRAdapter,
) -> None:
    """The strip targets ONLY ``<p>Image: signature</p>``. Other
    ``Image: <kind>`` placeholders (figure, chart, table) describe
    legitimate non-signature regions and must survive — they carry
    downstream-useful context (e.g. "this row has a chart inline")."""

    raw = (
        "# Page 1\n\n"
        "<p>Image: figure</p>\n\n"
        "<p>Image: chart</p>\n\n"
        "| Sr | Sign |\n"
        "|---|---|\n"
        "| 1 | <p>Image: signature</p> |\n"
    )
    result = SimpleNamespace(markdown=raw, images=None)

    _pages, chunk_md, _tm, _sigs, _kv = adapter._process_result(
        result, page_offset=0,
    )

    assert "Image: signature" not in chunk_md
    assert "Image: figure" in chunk_md
    assert "Image: chart" in chunk_md
