"""Pin graceful per-chunk failure in the Datalab adapter.

Before this fix: ``asyncio.gather`` over per-chunk coroutines bubbled
ANY exception, the whole ``OCRResult`` was discarded, the caller got
``"OCR extraction failed: Data Lab conversion failed after 3
attempts"``. On 2026-05-13 a 117-page BPCR had three chunks complete
end-to-end but one chunk burned its submit retries on a transient
502 — the user got zero pages back.

After this fix: ``gather(return_exceptions=True)`` collects both
successes and failures; the adapter returns the successful chunks'
pages and exposes the failure metadata via:

  * ``OCRResult.raw_response["partial"] = True``
  * ``OCRResult.raw_response["failed_chunk_ranges"] = [...]``
  * ``ocr.partial_extraction`` telemetry event with structured fields

Only when EVERY chunk fails does the adapter raise — the historic
"complete failure" path is preserved so callers that already handle
the RuntimeError still see it.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def test_partition_separates_exceptions_from_successes() -> None:
    """The shape-driven split: BaseException → failure, tuple →
    success. No raw_results entry should be silently dropped."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter

    # Three chunks: success / fail / success.
    success_a = ([], "page md a", [], [], [], None, 0)
    failure_b = RuntimeError("Data Lab conversion failed after 3 attempts")
    success_c = ([], "page md c", [], [], [], None, 2)

    successes, failures = DatalabOCRAdapter._partition_chunk_results(
        [success_a, failure_b, success_c],
        page_ranges=["0-24", "25-49", "50-74"],
    )

    assert len(successes) == 2
    assert len(failures) == 1
    f = failures[0]
    assert f["chunk_idx"] == 1
    assert f["page_range"] == "25-49"
    assert f["error_type"] == "RuntimeError"
    assert "conversion failed" in f["error_message"]
    # Preserves the original exception for re-raise paths.
    assert isinstance(f["error"], RuntimeError)


def test_partition_handles_all_failures() -> None:
    """Edge case: every chunk raised. Successes is empty; failures
    carries each chunk_idx. The caller uses this to re-raise the
    first exception (historic failure path)."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter

    successes, failures = DatalabOCRAdapter._partition_chunk_results(
        [RuntimeError("a"), TimeoutError("b"), RuntimeError("c")],
        page_ranges=["0-24", "25-49", "50-74"],
    )

    assert successes == []
    assert [f["chunk_idx"] for f in failures] == [0, 1, 2]
    assert [f["error_type"] for f in failures] == [
        "RuntimeError", "TimeoutError", "RuntimeError",
    ]


def test_partition_handles_all_successes() -> None:
    """Common case: every chunk succeeded. failures is empty,
    successes preserves order. No partial flag should be set
    downstream when this happens."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter

    raw = [
        ([], "a", [], [], [], None, 0),
        ([], "b", [], [], [], None, 1),
    ]
    successes, failures = DatalabOCRAdapter._partition_chunk_results(
        raw, page_ranges=["0-24", "25-49"],
    )

    assert len(successes) == 2
    assert failures == []


@pytest.mark.asyncio
async def test_graceful_partial_extraction_returns_successful_pages(
    tmp_path,
) -> None:
    """Real adapter, ``extract()`` happy path with one synthetic
    chunk failure: the adapter must return the successful chunks'
    pages + flag ``raw_response['partial']`` + fire
    ``ocr.partial_extraction`` telemetry. Without this fix a single
    failed chunk discarded the entire OCRResult."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter
    from app.config.settings import DatalabConfig
    from app.core.ports.ocr import OCRPageResult
    from app.observability.run_telemetry import telemetry_run

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    adapter = DatalabOCRAdapter(cfg)

    # Fake page-counter so we don't need a real PDF.
    with patch(
        "app.adapters.ocr.datalab._count_pdf_pages", return_value=75,
    ), patch.object(adapter._config, "chunk_pages", 25):
        async def fake_chunk(
            chunk_idx, page_range, total_chunks, pdf_path,
            sem, completed_counter, progress_callback,
            in_flight=None,
        ):
            async with sem:
                if chunk_idx == 1:
                    raise RuntimeError(
                        "Data Lab conversion failed after 3 attempts"
                    )
                pages = [
                    OCRPageResult(page_num=p, markdown=f"page {p}")
                    for p in range(chunk_idx * 25 + 1, (chunk_idx + 1) * 25 + 1)
                ]
                return pages, f"chunk{chunk_idx}-md", [], [], [], 4.2, chunk_idx

        doc_dir = tmp_path / "doc"
        with patch.object(adapter, "_process_single_chunk", side_effect=fake_chunk):
            with telemetry_run("doc", doc_dir, name="test"):
                result = await adapter.extract("/tmp/fake.pdf")

    # Chunks 0 and 2 succeeded → pages 1-25 + 51-75 (50 pages total).
    assert len(result.pages) == 50, (
        f"expected 50 pages from 2 of 3 successful chunks, got {len(result.pages)}"
    )
    page_nums = {p.page_num for p in result.pages}
    assert 1 in page_nums and 25 in page_nums
    assert 51 in page_nums and 75 in page_nums
    # The failed chunk's pages must NOT appear.
    assert 26 not in page_nums and 50 not in page_nums

    # Raw response carries the partial flag + failed-range metadata.
    assert result.raw_response.get("partial") is True
    assert result.raw_response.get("failed_chunk_ranges") == ["25-49"]
    assert result.raw_response.get("successful_chunk_count") == 2
    assert result.raw_response.get("total_chunk_count") == 3

    # Telemetry event landed.
    data = json.loads((doc_dir / "telemetry-test.json").read_text())
    events = [e for e in data["events"] if e["event"] == "ocr.partial_extraction"]
    assert events, "ocr.partial_extraction event missing"
    fields = events[0]["fields"]
    assert fields["successful_chunks"] == 2
    assert fields["total_chunks"] == 3
    assert fields["failed_chunk_count"] == 1
    assert fields["failed_chunk_ranges"] == ["25-49"]
    assert "RuntimeError" in fields["failed_error_types"]
    assert fields["extracted_page_count"] == 50


@pytest.mark.asyncio
async def test_all_chunks_failed_raises_to_preserve_caller_contract() -> None:
    """The "complete OCR failure" path must still raise. Callers
    that catch RuntimeError to surface "OCR extraction failed" in
    the UI should keep working — graceful degradation is for
    SOME-failed, not ALL-failed."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter
    from app.config.settings import DatalabConfig

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    adapter = DatalabOCRAdapter(cfg)

    with patch(
        "app.adapters.ocr.datalab._count_pdf_pages", return_value=50,
    ), patch.object(adapter._config, "chunk_pages", 25):
        async def fake_chunk(
            chunk_idx, page_range, total_chunks, pdf_path,
            sem, completed_counter, progress_callback,
            in_flight=None,
        ):
            async with sem:
                raise RuntimeError(f"chunk {chunk_idx} doomed")

        with patch.object(adapter, "_process_single_chunk", side_effect=fake_chunk):
            with pytest.raises(RuntimeError, match="chunk 0 doomed"):
                await adapter.extract("/tmp/fake.pdf")


@pytest.mark.asyncio
async def test_no_failures_does_not_set_partial_flag(tmp_path) -> None:
    """Happy path: every chunk succeeded. ``raw_response['partial']``
    must NOT be set — downstream uses its presence as the signal
    that a HITL review of missing pages is needed."""

    from app.adapters.ocr.datalab import DatalabOCRAdapter
    from app.config.settings import DatalabConfig
    from app.core.ports.ocr import OCRPageResult

    cfg = DatalabConfig(api_key="a-real-looking-key-1234567890")
    adapter = DatalabOCRAdapter(cfg)

    with patch(
        "app.adapters.ocr.datalab._count_pdf_pages", return_value=50,
    ), patch.object(adapter._config, "chunk_pages", 25):
        async def fake_chunk(
            chunk_idx, page_range, total_chunks, pdf_path,
            sem, completed_counter, progress_callback,
            in_flight=None,
        ):
            async with sem:
                pages = [
                    OCRPageResult(page_num=p, markdown=f"p{p}")
                    for p in range(chunk_idx * 25 + 1, (chunk_idx + 1) * 25 + 1)
                ]
                return pages, f"m{chunk_idx}", [], [], [], 4.0, chunk_idx

        with patch.object(adapter, "_process_single_chunk", side_effect=fake_chunk):
            result = await adapter.extract("/tmp/fake.pdf")

    assert "partial" not in result.raw_response
    assert "failed_chunk_ranges" not in result.raw_response
    assert len(result.pages) == 50
