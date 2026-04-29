"""Data Lab (Chandra) OCR adapter.

Wraps the datalab-python-sdk AsyncDatalabClient for PDF-to-Markdown
conversion with strong handwriting recognition, table extraction,
checkbox detection, and optional structured key-value extraction.
The SDK handles polling internally.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pypdfium2

from app.config.settings import DatalabConfig
from app.core.ports.ocr import (
    BoundingRegion,
    FormulaResult,
    KeyValuePair,
    OCRPageResult,
    OCRResult,
    OCRWord,
    ProgressCallback,
    SelectionMark,
    SignatureRegion,
)
from app.core.services.layout_markdown_sanitizer import sanitize_layout_markdown

logger = logging.getLogger(__name__)

PAGE_SEPARATOR = "\n\n---\n\n"
_PAGE_SPLIT_RE = re.compile(r"\n\n\{\d+\}-{2,}\n\n")

_CHECKBOX_PATTERNS = re.compile(
    r"(☑|☐|✓|✗|✘|\[x\]|\[X\]|\[ \])", re.UNICODE
)
_SELECTED_MARKS = {"☑", "✓", "[x]", "[X]"}

_HANDWRITING_BLOCK_RE = re.compile(
    r"<!-- block_type:\s*(?:Handwriting|SectionHeader|Text)\s*-->",
    re.IGNORECASE,
)

_FORMULA_INLINE_RE = re.compile(r"\$([^$]+)\$")
_FORMULA_BLOCK_RE = re.compile(r"\$\$([^$]+)\$\$")

_DASH_ONLY_RE = re.compile(r"^[\s\-—–]*$")
_NUMERIC_QTY_RE = re.compile(
    r"\d+\.?\d*\s*(?:kg|g|mg|l|ml|rpm|psi|bar|mm|cm|°?c|hr|min|sec)\b",
    re.IGNORECASE,
)
_INSPECTION_KW_RE = re.compile(
    r"\b(?:inspect|verify|check|confirm|ensure|weigh|measure|record|sample|test|observe)\b",
    re.IGNORECASE,
)
_BOLD_TAG_RE = re.compile(r"<b>|<strong>|\*\*[^*]+\*\*", re.IGNORECASE)


# ── Utility functions ────────────────────────────────────────────


def _count_pdf_pages(pdf_path: str) -> int:
    try:
        doc = pypdfium2.PdfDocument(pdf_path)
        n = len(doc)
        doc.close()
        return n
    except Exception:
        return 0


def _build_page_ranges(
    total_pages: int, chunk_pages: int, requested: list[int] | None
) -> list[str]:
    """Build 0-indexed page range strings for Data Lab's ``page_range`` param."""
    if requested:
        zero_based = sorted(p - 1 for p in requested if 1 <= p <= total_pages)
        if not zero_based:
            return []
        ranges: list[str] = []
        start = prev = zero_based[0]
        for p in zero_based[1:]:
            if p == prev + 1:
                prev = p
            else:
                ranges.append(f"{start}-{prev}" if start != prev else str(start))
                start = prev = p
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        return [",".join(ranges)]

    if total_pages <= chunk_pages:
        return [f"0-{total_pages - 1}"]
    chunks: list[str] = []
    for s in range(0, total_pages, chunk_pages):
        e = min(s + chunk_pages - 1, total_pages - 1)
        chunks.append(f"{s}-{e}")
    return chunks


def _decode_images(raw_images: dict[str, str] | None) -> dict[str, bytes]:
    if not raw_images:
        return {}
    decoded: dict[str, bytes] = {}
    for name, b64 in raw_images.items():
        try:
            decoded[name] = base64.b64decode(b64)
        except Exception:
            logger.warning("Failed to decode image %s", name)
    return decoded


# ── Per-page parsers ─────────────────────────────────────────────


def _parse_selection_marks(markdown: str, page_num: int) -> list[SelectionMark]:
    marks: list[SelectionMark] = []
    for m in _CHECKBOX_PATTERNS.finditer(markdown):
        marks.append(
            SelectionMark(
                state="selected" if m.group(1) in _SELECTED_MARKS else "unselected",
                confidence=0.9,
                page_num=page_num,
            )
        )
    return marks


def _parse_handwriting_words(markdown: str, page_num: int) -> list[OCRWord]:
    words: list[OCRWord] = []
    parts = _HANDWRITING_BLOCK_RE.split(markdown)
    for i in range(1, len(parts)):
        text = parts[i].strip()
        if not text:
            continue
        for w in text.split():
            words.append(
                OCRWord(
                    text=w,
                    confidence=0.85,
                    is_handwritten=True,
                    bounding_region=BoundingRegion(
                        page_num=page_num, x=0, y=0, width=0, height=0
                    ),
                )
            )
    return words


def _parse_signatures(markdown: str, page_num: int) -> list[SignatureRegion]:
    sigs: list[SignatureRegion] = []
    for m in re.finditer(
        r"<!-- block_type:\s*Signature\s*-->(.*?)(?=<!--|$)", markdown, re.S | re.I
    ):
        label = m.group(1).strip()[:120] or "Signature"
        sigs.append(
            SignatureRegion(
                page_num=page_num,
                status="signed",
                confidence=0.85,
                label=label,
            )
        )
    # Data Lab also renders signatures as `[Signature]` inline text (e.g. in table cells)
    for m in re.finditer(r"\[Signature\]", markdown, re.I):
        start = max(0, m.start() - 60)
        context = markdown[start:m.start()].rsplit("|", 1)
        label = context[-1].strip()[:80] if len(context) > 1 else "Signature"
        sigs.append(
            SignatureRegion(
                page_num=page_num,
                status="signed",
                confidence=0.80,
                label=label or "Signature",
            )
        )
    return sigs


def _parse_table_metadata(markdown: str, page_num: int) -> list[dict]:
    tables: list[dict] = []
    for m in re.finditer(r"(\|.+\|(?:\n\|.+\|)*)", markdown):
        rows = [r for r in m.group(1).strip().split("\n") if r.strip()]
        header_cells = [c.strip() for c in rows[0].split("|") if c.strip()] if rows else []
        data_rows = [r for r in rows if not re.fullmatch(r"\|[\s\-:|]+\|", r)]
        tables.append(
            {
                "page_num": page_num,
                "row_count": len(data_rows),
                "column_count": len(header_cells),
                "has_merged_cells": False,
            }
        )
    return tables


def _parse_formulas(markdown: str, page_num: int) -> list[FormulaResult]:
    formulas: list[FormulaResult] = []
    for m in _FORMULA_BLOCK_RE.finditer(markdown):
        formulas.append(FormulaResult(kind="display", value=m.group(1).strip(), confidence=0.9, page_num=page_num))
    for m in _FORMULA_INLINE_RE.finditer(markdown):
        val = m.group(1).strip()
        if not any(f.value == val and f.page_num == page_num for f in formulas):
            formulas.append(FormulaResult(kind="inline", value=val, confidence=0.9, page_num=page_num))
    return formulas


# ── Bounding box extraction from JSON output ─────────────────────


def _extract_bboxes_from_json(
    json_data: Any, page_offset: int
) -> dict[int, list[tuple[str, str, list[list[float]]]]]:
    """Walk the JSON block tree and collect (block_type, text, polygon) per page."""
    page_blocks: dict[int, list[tuple[str, str, list[list[float]]]]] = {}

    def _walk(node: dict, depth: int = 0) -> None:
        if not isinstance(node, dict):
            return
        block_type = node.get("block_type", "")
        polygon = node.get("polygon")
        html_text = node.get("html", "")
        block_id = node.get("id", "")

        page_num = page_offset + 1
        if block_id:
            parts = block_id.split("/")
            for p in parts:
                if p.isdigit():
                    page_num = int(p) + 1
                    break

        if polygon and block_type in ("Text", "Handwriting", "SectionHeader", "ListItem", "Span", "Line"):
            page_blocks.setdefault(page_num, []).append(
                (block_type, re.sub(r"<[^>]+>", "", html_text).strip(), polygon)
            )

        for child in node.get("children", []) or []:
            _walk(child, depth + 1)

    if isinstance(json_data, list):
        for item in json_data:
            _walk(item)
    elif isinstance(json_data, dict):
        _walk(json_data)

    return page_blocks


def _polygon_to_bounding_region(polygon: list[list[float]], page_num: int) -> BoundingRegion:
    """Convert a 4-corner polygon [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] to BoundingRegion."""
    if len(polygon) < 2:
        return BoundingRegion(page_num=page_num, x=0, y=0, width=0, height=0)
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return BoundingRegion(
        page_num=page_num,
        x=min(xs),
        y=min(ys),
        width=max(xs) - min(xs),
        height=max(ys) - min(ys),
    )


# ── Extract API result mapping ───────────────────────────────────


def _map_extraction_to_kv_pairs(
    extraction_json: dict, page_num: int
) -> list[KeyValuePair]:
    """Map flat or nested extraction_schema_json to KeyValuePair list."""
    pairs: list[KeyValuePair] = []
    if not isinstance(extraction_json, dict):
        return pairs

    for key, val in extraction_json.items():
        if key.startswith("_") or key.endswith("_score") or key.endswith("_citations"):
            continue

        score_key = f"{key}_score"
        score_data = extraction_json.get(score_key)
        confidence = 0.0
        if isinstance(score_data, dict):
            raw_score = score_data.get("score", 0)
            confidence = min(1.0, float(raw_score) / 5.0)

        if isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if sub_key.startswith("_") or sub_key.endswith("_citations"):
                    continue
                pairs.append(KeyValuePair(
                    key=f"{key}.{sub_key}",
                    value=str(sub_val) if sub_val is not None else "",
                    confidence=confidence,
                    page_num=page_num,
                ))
        elif isinstance(val, list):
            for idx, item in enumerate(val):
                if isinstance(item, dict):
                    for sub_key, sub_val in item.items():
                        if sub_key.startswith("_") or sub_key.endswith("_citations"):
                            continue
                        pairs.append(KeyValuePair(
                            key=f"{key}[{idx}].{sub_key}",
                            value=str(sub_val) if sub_val is not None else "",
                            confidence=confidence,
                            page_num=page_num,
                        ))
                else:
                    pairs.append(KeyValuePair(
                        key=f"{key}[{idx}]",
                        value=str(item) if item is not None else "",
                        confidence=confidence,
                        page_num=page_num,
                    ))
        else:
            pairs.append(KeyValuePair(
                key=key,
                value=str(val) if val is not None else "",
                confidence=confidence,
                page_num=page_num,
            ))
    return pairs


# ── Attestation & critical-step enrichment ───────────────────────


def _is_attested(value: str) -> bool | None:
    """Interpret a Done By / Checked By value as an attestation boolean.

    Returns ``True`` if a name/initials appear to be present, ``False`` if
    only a dash placeholder, and ``None`` if the field is completely empty.
    """
    if not value or not value.strip():
        return None
    return not _DASH_ONLY_RE.fullmatch(value.strip())


def _enrich_attestations(pairs: list[KeyValuePair]) -> list[KeyValuePair]:
    """Derive ``is_operator_attested`` / ``is_reviewer_attested`` booleans.

    Scans KV pairs for keys ending in ``Done By`` or ``Checked By`` (at any
    nesting depth) and appends synthetic boolean KV pairs.  The original
    pairs are returned unmodified.
    """
    _ATTESTATION_MAP = {
        "done by": "Is Operator Attested",
        "checked by": "Is Reviewer Attested",
        "verified by": "Is QA Attested",
    }
    extra: list[KeyValuePair] = []
    for kv in pairs:
        key_lower = kv.key.lower()
        for suffix, att_label in _ATTESTATION_MAP.items():
            if key_lower.endswith(suffix):
                attested = _is_attested(kv.value)
                if attested is not None:
                    original_suffix_len = len(suffix)
                    prefix = kv.key[: -original_suffix_len].rstrip(". ")
                    label = f"{prefix}.{att_label}" if prefix else att_label
                    extra.append(KeyValuePair(
                        key=label,
                        value=str(attested).lower(),
                        confidence=kv.confidence,
                        page_num=kv.page_num,
                    ))
                break
    return pairs + extra


def _enrich_critical_steps(pairs: list[KeyValuePair]) -> list[KeyValuePair]:
    """Flag manufacturing steps as critical based on operation content.

    A step is critical if its Operation field contains:
      - Numeric quantities with units (e.g. 150 kg, 800 L, 7.0 RPM)
      - Inspection/verification keywords (inspect, verify, weigh, measure ...)
      - Bold/emphasis formatting (<b>, <strong>, **...**)
    """
    step_ops: dict[str, KeyValuePair] = {}
    for kv in pairs:
        if ".Operation" in kv.key or kv.key == "Operation":
            step_ops[kv.key] = kv

    extra: list[KeyValuePair] = []
    for op_key, op_kv in step_ops.items():
        text = op_kv.value
        is_critical = bool(
            _NUMERIC_QTY_RE.search(text)
            or _INSPECTION_KW_RE.search(text)
            or _BOLD_TAG_RE.search(text)
        )
        crit_key = op_key.replace(".Operation", ".Is Critical Step").replace(
            "Operation", "Is Critical Step"
        )
        extra.append(KeyValuePair(
            key=crit_key,
            value=str(is_critical).lower(),
            confidence=op_kv.confidence,
            page_num=op_kv.page_num,
        ))
    return pairs + extra


# ── Schema loader ────────────────────────────────────────────────

_EXTRACTION_SCHEMAS_FILE = Path(__file__).resolve().parents[2] / "compliance" / "rules" / "extraction_schemas.yaml"


def _load_extraction_schema(family: str, override: dict | None = None) -> dict:
    """Resolve the extraction schema for a given template family.

    Priority:
      1. Explicit ``extraction_schema`` dict in config (non-empty)
      2. Family entry in ``extraction_schemas.yaml``
      3. Empty dict (skips Extract API)
    """
    if override:
        return override
    if not _EXTRACTION_SCHEMAS_FILE.exists():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(_EXTRACTION_SCHEMAS_FILE.read_text("utf-8")) or {}
        families = raw.get("families", {})
        entry = families.get(family, {})
        return entry.get("schema", {})
    except Exception:
        logger.warning("Failed to load extraction schemas from %s", _EXTRACTION_SCHEMAS_FILE, exc_info=True)
        return {}


# ══════════════════════════════════════════════════════════════════
#  Adapter
# ══════════════════════════════════════════════════════════════════


class DatalabOCRAdapter:
    """Data Lab (Chandra) OCR adapter implementing the OCREngine protocol."""

    def __init__(self, config: DatalabConfig) -> None:
        self._config = config
        self._client: Any = None
        self._extraction_schema = _load_extraction_schema(
            config.extraction_schema_family,
            config.extraction_schema or None,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from datalab_sdk import AsyncDatalabClient

        self._client = AsyncDatalabClient(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout,
        )
        return self._client

    def _build_convert_options(self, page_range: str | None = None) -> Any:
        from datalab_sdk import ConvertOptions

        opts = ConvertOptions(
            mode=self._config.mode,
            paginate=self._config.paginate,
            output_format=self._config.output_format,
            disable_image_extraction=self._config.disable_image_extraction,
            disable_image_captions=self._config.disable_image_captions,
            token_efficient_markdown=self._config.token_efficient_markdown,
        )
        if self._config.extras:
            opts.extras = self._config.extras
        if page_range:
            opts.page_range = page_range
        if self._config.max_pages is not None:
            opts.max_pages = self._config.max_pages
        if self._config.use_llm:
            opts.use_llm = True
        if self._config.force_ocr:
            opts.force_ocr = True
        if self._config.strip_existing_ocr:
            opts.strip_existing_ocr = True
        if self._config.save_checkpoint:
            opts.save_checkpoint = True
        return opts

    async def _convert_with_heartbeat(
        self,
        client: Any,
        pdf_path: str,
        opts: Any,
        on_tick: Any,
    ) -> Any:
        """Run ``client.convert`` and tick a heartbeat once per poll interval.

        The Datalab SDK's ``convert()`` blocks until the request reaches
        ``status=complete``; the SDK polls internally on
        ``poll_interval`` seconds but does not surface those polls to
        callers. To keep the user-visible progress alive during a long
        chunk, we run ``convert`` on a background task and emit a
        heartbeat tick every ``poll_interval`` while it's still
        running. The percent stays under our control (only advances on
        chunk completion, see ``_process_single_chunk``); the tick
        carries an elapsed-time label so the user sees activity even
        when the percent doesn't move.

        Doing it this way (rather than reimplementing submit + poll
        against the SDK's private ``_submit_with_retry`` /
        ``_poll_result`` methods) keeps us SDK-version-tolerant: an
        upstream rename can't silently break the heartbeat.
        """

        convert_task = asyncio.create_task(
            client.convert(
                file_path=pdf_path,
                options=opts,
                max_polls=self._config.max_polls,
                poll_interval=self._config.poll_interval,
            )
        )
        elapsed_s = 0.0
        try:
            while True:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(convert_task),
                        timeout=self._config.poll_interval,
                    )
                except asyncio.TimeoutError:
                    elapsed_s += self._config.poll_interval
                    on_tick(elapsed_s)
        except Exception:
            # Surface SDK exceptions verbatim — caller already wraps
            # them in retry logic.
            if not convert_task.done():
                convert_task.cancel()
            raise

    async def _submit_with_retry(
        self,
        pdf_path: str,
        page_range: str | None,
        progress_callback: ProgressCallback | None,
        chunk_label: str | None = None,
        baseline_provider: Callable[[], int] | None = None,
    ) -> Any:
        """Submit to Data Lab with exponential-backoff retry.

        ``chunk_label`` (e.g. ``"Chunk 3/5 (pages 21-30)"``) shows up
        in the heartbeat tick so multi-chunk batches can identify
        which chunk is currently in flight.

        ``baseline_provider`` is a callable returning the current
        floor percent for the heartbeat — read **fresh on every
        tick**, not captured once at start. With concurrent chunks
        this matters: chunk A's heartbeat can keep firing while
        chunks B and C complete, and the bar should reflect the
        completed-chunks total in those still-running chunks'
        labels rather than freezing at A's start-of-chunk reading.
        Defaults to a constant 5% for callers that don't track
        completed chunks.
        """
        from datalab_sdk.exceptions import DatalabAPIError, DatalabTimeoutError

        opts = self._build_convert_options(page_range)
        client = self._get_client()
        last_exc: Exception | None = None

        provider = baseline_provider if baseline_provider is not None else (lambda: 5)

        for attempt in range(self._config.submit_max_retries):
            try:
                if progress_callback:
                    progress_callback(
                        max(provider(), min(5 + attempt * 2, 15)),
                        (
                            f"Submitting {chunk_label} (attempt {attempt + 1})"
                            if chunk_label
                            else f"Submitting to Data Lab (attempt {attempt + 1})"
                        ),
                    )

                def _heartbeat(elapsed: float) -> None:
                    if progress_callback is None:
                        return
                    label = (
                        f"{chunk_label} — analyzing ({elapsed:.0f}s)"
                        if chunk_label
                        else f"Data Lab analyzing ({elapsed:.0f}s)"
                    )
                    # Read the baseline on every tick so a chunk
                    # that finishes during chunk A's poll loop
                    # advances A's heartbeat percent on the next
                    # tick. The actual UI bar is gated by the
                    # frontend store's strict monotone reducer, so
                    # this can never go backwards even if the
                    # provider — for whatever reason — returns a
                    # stale lower value.
                    progress_callback(provider(), label)

                result = await self._convert_with_heartbeat(
                    client, pdf_path, opts, _heartbeat,
                )
                if not getattr(result, "success", True):
                    err = getattr(result, "error", "Unknown error")
                    raise RuntimeError(f"Data Lab conversion failed: {err}")
                return result
            except (DatalabAPIError, DatalabTimeoutError, RuntimeError) as exc:
                last_exc = exc
                delay = self._config.submit_retry_base_delay * (2**attempt)
                logger.warning(
                    "Data Lab submit attempt %d failed (%s), retrying in %.1fs",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"Data Lab conversion failed after {self._config.submit_max_retries} attempts"
        ) from last_exc

    async def _run_extraction(
        self,
        pdf_path: str,
        checkpoint_id: str | None,
        page_range: str | None,
    ) -> dict:
        """Run the Extract API for structured KV pair extraction."""
        if not self._config.enable_extraction or not self._extraction_schema:
            return {}
        try:
            client = self._get_client()
            from datalab_sdk.models import ExtractOptions

            if checkpoint_id:
                ext_opts = ExtractOptions(
                    mode=self._config.mode,
                    page_schema=json.dumps(self._extraction_schema),
                    save_checkpoint=self._config.save_checkpoint,
                    checkpoint_id=checkpoint_id,
                )
                result = await client.extract(
                    options=ext_opts,
                    max_polls=self._config.max_polls,
                    poll_interval=self._config.poll_interval,
                )
            else:
                ext_opts = ExtractOptions(
                    mode=self._config.mode,
                    page_schema=json.dumps(self._extraction_schema),
                    save_checkpoint=self._config.save_checkpoint,
                )
                if page_range:
                    ext_opts.page_range = page_range
                result = await client.extract(
                    file_path=pdf_path,
                    options=ext_opts,
                    max_polls=self._config.max_polls,
                    poll_interval=self._config.poll_interval,
                )
            raw = getattr(result, "extraction_schema_json", None)
            if isinstance(raw, str):
                return json.loads(raw)
            return raw or {}
        except Exception:
            logger.warning("Data Lab extraction API failed; KV pairs will be empty", exc_info=True)
            return {}

    async def _fetch_json_bboxes(
        self,
        pdf_path: str,
        checkpoint_id: str | None,
        page_range: str | None,
    ) -> dict[int, list[tuple[str, str, list[list[float]]]]]:
        """Fetch JSON output for bounding box data."""
        if not self._config.fetch_block_bboxes:
            return {}
        try:
            client = self._get_client()
            from datalab_sdk import ConvertOptions

            json_opts = ConvertOptions(
                mode=self._config.mode,
                output_format="json",
                paginate=self._config.paginate,
            )
            if checkpoint_id:
                json_opts.checkpoint_id = checkpoint_id
            elif page_range:
                json_opts.page_range = page_range

            result = await client.convert(
                file_path=pdf_path,
                options=json_opts,
                max_polls=self._config.max_polls,
                poll_interval=self._config.poll_interval,
            )
            json_data = getattr(result, "json", None)
            if not json_data:
                return {}

            first_page_0 = 0
            if page_range:
                first_page_0 = int(page_range.split(",")[0].split("-")[0])
            return _extract_bboxes_from_json(json_data, first_page_0)
        except Exception:
            logger.warning("Data Lab JSON bbox fetch failed; using zero-coord placeholders", exc_info=True)
            return {}

    def _process_result(
        self,
        result: Any,
        page_offset: int,
        bbox_data: dict[int, list[tuple[str, str, list[list[float]]]]] | None = None,
        extraction_data: dict | None = None,
    ) -> tuple[list[OCRPageResult], str, list[dict], list[SignatureRegion], list[KeyValuePair]]:
        """Convert a Data Lab ConversionResult into OCR port models."""
        raw_md: str = getattr(result, "markdown", "") or ""
        sanitized_md, _repairs = sanitize_layout_markdown(raw_md)

        all_images = _decode_images(getattr(result, "images", None))

        if _PAGE_SPLIT_RE.search(sanitized_md):
            page_texts = _PAGE_SPLIT_RE.split(sanitized_md)
        elif PAGE_SEPARATOR in sanitized_md:
            page_texts = sanitized_md.split(PAGE_SEPARATOR)
        else:
            page_texts = [sanitized_md]

        # Drop empty leading segment produced by `{0}---` at start of text
        if page_texts and not page_texts[0].strip():
            page_texts = page_texts[1:]

        pages: list[OCRPageResult] = []
        all_table_meta: list[dict] = []
        all_signatures: list[SignatureRegion] = []
        all_kv_pairs: list[KeyValuePair] = []

        for i, page_md in enumerate(page_texts):
            page_num = page_offset + i + 1
            page_md_stripped = page_md.strip()

            page_images: dict[str, bytes] = {
                k: v for k, v in all_images.items() if k in page_md_stripped
            }

            sel_marks = _parse_selection_marks(page_md_stripped, page_num)
            hw_words = _parse_handwriting_words(page_md, page_num)
            sigs = _parse_signatures(page_md, page_num)
            table_meta = _parse_table_metadata(page_md_stripped, page_num)
            formulas = _parse_formulas(page_md_stripped, page_num)

            # Enrich words with bounding boxes from JSON output
            if bbox_data and page_num in bbox_data:
                for block_type, text, polygon in bbox_data[page_num]:
                    br = _polygon_to_bounding_region(polygon, page_num)
                    is_hw = block_type == "Handwriting"
                    for w in text.split():
                        hw_words.append(OCRWord(
                            text=w,
                            confidence=0.85 if is_hw else 0.90,
                            is_handwritten=is_hw,
                            bounding_region=br,
                        ))

            all_table_meta.extend(table_meta)
            all_signatures.extend(sigs)

            pages.append(
                OCRPageResult(
                    page_num=page_num,
                    markdown=page_md_stripped,
                    words=hw_words,
                    selection_marks=sel_marks,
                    formulas=formulas,
                    images=page_images,
                )
            )

        if extraction_data:
            all_kv_pairs = _map_extraction_to_kv_pairs(extraction_data, page_offset + 1)
            all_kv_pairs = _enrich_attestations(all_kv_pairs)
            all_kv_pairs = _enrich_critical_steps(all_kv_pairs)

        return pages, sanitized_md, all_table_meta, all_signatures, all_kv_pairs

    async def _process_single_chunk(
        self,
        chunk_idx: int,
        page_range: str,
        total_chunks: int,
        pdf_path: str,
        sem: asyncio.Semaphore,
        completed_counter: dict,
        progress_callback: ProgressCallback | None,
    ) -> tuple[
        list[OCRPageResult], str, list[dict], list[SignatureRegion],
        list[KeyValuePair], float | None, int,
    ]:
        """Process a single chunk: convert -> extract+bboxes -> parse. Semaphore-gated."""
        async with sem:
            logger.info(
                "Chunk %d/%d (pages %s) — acquired semaphore slot",
                chunk_idx + 1, total_chunks, page_range,
            )

            # Baseline for this chunk's heartbeat: pinned to the
            # progress already earned by *currently completed* chunks,
            # read fresh on every tick. With ``max_concurrent_chunks``
            # > 1 this matters — chunk A's heartbeat keeps firing while
            # chunks B and C complete, and reading the counter live
            # lets A's heartbeat reflect the running total instead of
            # freezing at A's start-of-chunk reading. The frontend
            # store enforces a strict monotone-only invariant so even
            # a stale read can never cause a backwards snap.
            chunk_label = f"Chunk {chunk_idx + 1}/{total_chunks} (pages {page_range})"

            def _baseline_pct(
                _counter: dict = completed_counter,
                _total: int = total_chunks,
            ) -> int:
                return int((_counter["n"] / _total) * 90)

            result = await self._submit_with_retry(
                pdf_path,
                page_range,
                progress_callback,
                chunk_label=chunk_label,
                baseline_provider=_baseline_pct,
            )

            pqs = getattr(result, "parse_quality_score", None)
            quality = float(pqs) if pqs is not None else None

            checkpoint_id = getattr(result, "checkpoint_id", None)

            extraction_data, bbox_data = await asyncio.gather(
                self._run_extraction(pdf_path, checkpoint_id, page_range),
                self._fetch_json_bboxes(pdf_path, checkpoint_id, page_range),
            )

            first_page_0 = int(page_range.split(",")[0].split("-")[0]) if page_range else 0
            chunk_pages, chunk_md, t_meta, sigs, kv_pairs = self._process_result(
                result, first_page_0, bbox_data, extraction_data,
            )

            completed_counter["n"] += 1
            if progress_callback:
                pct = int((completed_counter["n"] / total_chunks) * 90)
                progress_callback(pct, f"Completed chunk {completed_counter['n']}/{total_chunks}")

            return chunk_pages, chunk_md, t_meta, sigs, kv_pairs, quality, chunk_idx

    async def extract(
        self,
        pdf_path: str,
        pages: list[int] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> OCRResult:
        total_pages = await asyncio.get_event_loop().run_in_executor(
            None, _count_pdf_pages, pdf_path,
        )
        if total_pages == 0:
            total_pages = 200
            logger.warning("Could not count pages for %s, assuming %d", pdf_path, total_pages)

        page_ranges = _build_page_ranges(total_pages, self._config.chunk_pages, pages)
        total_chunks = len(page_ranges)

        if progress_callback:
            progress_callback(
                2,
                f"Starting {total_chunks} chunk(s) with concurrency "
                f"{min(self._config.max_concurrent_chunks, total_chunks)}",
            )

        sem = asyncio.Semaphore(self._config.max_concurrent_chunks)
        completed_counter: dict[str, int] = {"n": 0}

        chunk_results = await asyncio.gather(
            *(
                self._process_single_chunk(
                    idx, pr, total_chunks, pdf_path,
                    sem, completed_counter, progress_callback,
                )
                for idx, pr in enumerate(page_ranges)
            )
        )

        all_pages: list[OCRPageResult] = []
        full_markdowns: list[tuple[int, str]] = []
        all_table_meta: list[dict] = []
        all_signatures: list[SignatureRegion] = []
        all_kv_pairs: list[KeyValuePair] = []
        quality_scores: list[float] = []

        for chunk_pages, chunk_md, t_meta, sigs, kv_pairs, quality, cidx in chunk_results:
            all_pages.extend(chunk_pages)
            full_markdowns.append((cidx, chunk_md))
            all_table_meta.extend(t_meta)
            all_signatures.extend(sigs)
            all_kv_pairs.extend(kv_pairs)
            if quality is not None:
                quality_scores.append(quality)

        all_pages.sort(key=lambda p: p.page_num)
        full_markdowns.sort(key=lambda t: t[0])
        full_md = PAGE_SEPARATOR.join(md for _, md in full_markdowns)

        if progress_callback:
            progress_callback(100, "Data Lab extraction complete")

        raw_resp: dict[str, Any] = {}
        if quality_scores:
            raw_resp["parse_quality_score"] = sum(quality_scores) / len(quality_scores)
            raw_resp["quality_0_1"] = raw_resp["parse_quality_score"] / 5.0

        return OCRResult(
            pages=all_pages,
            full_markdown=full_md,
            table_metadata=all_table_meta,
            key_value_pairs=all_kv_pairs,
            signatures=all_signatures,
            raw_response=raw_resp,
        )

    def supports_handwriting(self) -> bool:
        return True

    def supports_barcodes(self) -> bool:
        return False

    def supports_selection_marks(self) -> bool:
        return "new_block_types" in (self._config.extras or "")
