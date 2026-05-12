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

        # Allowlist of block types we lift out of Datalab's JSON
        # tree for downstream consumers. The original list was
        # ``Text + Handwriting + SectionHeader + ListItem + Span +
        # Line`` — purely the textual blocks.
        #
        # ``Signature``, ``Form``, ``TableCell``, and ``Table``
        # were added on 2026-05-05 to support the signature
        # enricher (``app.adapters.ocr.signature_enricher``)
        # which needs to know about signature-classified blocks
        # and table-cell bboxes to synthesize missing
        # ``[Signature]`` markers in cells where Datalab's
        # classifier missed the stroke but context makes
        # presence obvious. Without these, the enricher's L2
        # path is starved of evidence and recall drops by an
        # order of magnitude on docs with sparse initial-only
        # signatures.
        if polygon and block_type in (
            "Text",
            "Handwriting",
            "SectionHeader",
            "ListItem",
            "Span",
            "Line",
            "Signature",
            "Form",
            "TableCell",
            "Table",
        ):
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

        # Fail fast on a bad key so the operator sees an actionable
        # error at startup rather than hours of "OCR progress 0% —
        # analyzing" with 401s buried in the warning log. Mirrors the
        # Gemini adapter's pattern. The key never appears verbatim in
        # the message; we surface only its length and last 4 chars to
        # help the operator confirm which secret was loaded.
        api_key = (config.api_key or "").strip().strip('"').strip("'")
        if not api_key:
            raise RuntimeError(
                "DatalabOCRAdapter: AT_DATALAB__api_key is empty. "
                "Set it in backend/.env (get one from "
                "https://www.datalab.to/app/keys); restart the server "
                "after updating."
            )
        if api_key.startswith(("your-", "REPLACE", "<")) or api_key.endswith(("-here", ">")):
            raise RuntimeError(
                f"DatalabOCRAdapter: AT_DATALAB__api_key looks like a "
                f"placeholder ({api_key[:6]}…). Replace with a real key from "
                "https://www.datalab.to/app/keys."
            )
        if len(api_key) < 16:
            raise RuntimeError(
                f"DatalabOCRAdapter: AT_DATALAB__api_key is suspiciously short "
                f"({len(api_key)} chars). Verify the key was copied in full."
            )
        # Stash the cleaned key so ``_get_client`` doesn't re-parse the
        # raw config value (which may carry stray quotes from .env).
        self._cleaned_api_key = api_key

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from datalab_sdk import AsyncDatalabClient

        self._client = AsyncDatalabClient(
            api_key=self._cleaned_api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout,
        )
        logger.info(
            "Data Lab adapter ready: key=***%s (length=%d) base_url=%s",
            self._cleaned_api_key[-4:],
            len(self._cleaned_api_key),
            self._config.base_url,
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
        on_tick: Any | None = None,
    ) -> Any:
        """Run ``client.convert`` to completion.

        Originally this method emitted per-chunk heartbeats via
        ``on_tick`` while the SDK polled internally. Concurrent chunks
        each running their own heartbeat raced against the upstream
        WS throttle (1 broadcast/second/doc), producing a UX where
        only one chunk's label ever showed — making it look like the
        other chunks were stuck. The aggregate heartbeat now lives at
        the ``extract()`` level (see ``_run_aggregate_heartbeat``);
        ``on_tick`` is kept for backwards compatibility with tests
        that exercised the per-tick callback in isolation, but the
        production path no longer passes it.
        """

        convert_task = asyncio.create_task(
            client.convert(
                file_path=pdf_path,
                options=opts,
                max_polls=self._config.max_polls,
                poll_interval=self._config.poll_interval,
            )
        )
        if on_tick is None:
            return await convert_task

        # Backwards-compatible per-tick path retained for unit tests.
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
            if not convert_task.done():
                convert_task.cancel()
            raise

    async def _run_aggregate_heartbeat(
        self,
        *,
        in_flight: dict[int, tuple[str, float]],
        completed_counter: dict[str, int],
        total_chunks: int,
        progress_callback: ProgressCallback | None,
    ) -> None:
        """Single coordinating heartbeat — one broadcast per poll interval.

        Replaces the previous per-chunk heartbeat where N concurrent
        chunks each fired their own tick and only one survived the
        downstream WS throttle. By aggregating here, every tick
        carries the full picture (chunks active / oldest age / newest
        age / completed count) so a reviewer can tell the system is
        working even when the percent hasn't moved yet.

        Stops when cancelled (``extract()`` cancels it once
        ``asyncio.gather`` over all chunks resolves).
        """

        if progress_callback is None:
            return

        loop = asyncio.get_running_loop()

        try:
            while True:
                await asyncio.sleep(self._config.poll_interval)
                snapshot = list(in_flight.values())
                if not snapshot:
                    # All chunks done; nothing to report. We don't
                    # emit a 100% here — chunk-completion path owns
                    # the percent floor.
                    continue

                now = loop.time()
                ages = sorted(now - start for _, start in snapshot)
                completed = completed_counter["n"]
                baseline = int((completed / total_chunks) * 90)
                label = (
                    f"Datalab • {len(snapshot)}/{total_chunks} chunks analyzing"
                    f" • oldest {ages[-1]:.0f}s, newest {ages[0]:.0f}s"
                )
                if completed:
                    label += f" • {completed} completed"
                progress_callback(baseline, label)
        except asyncio.CancelledError:
            # Normal shutdown when extract() finishes.
            return

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
            except DatalabAPIError as exc:
                # 401/403 are not transient — the key is wrong, expired,
                # revoked, or the workspace is over quota. Retrying just
                # produces hours of misleading "OCR progress 0% —
                # analyzing" labels while the bar can't move. Re-raise
                # immediately with an actionable RuntimeError so the
                # outer pipeline marks the run failed and the UI shows
                # the auth error instead of a stuck progress bar.
                if getattr(exc, "status_code", None) in (401, 403):
                    masked = f"***{self._cleaned_api_key[-4:]}"
                    raise RuntimeError(
                        f"Data Lab returned {exc.status_code} Unauthorized — "
                        f"the configured AT_DATALAB__api_key (key={masked}, "
                        f"length={len(self._cleaned_api_key)}) was rejected. "
                        "Get a fresh key from https://www.datalab.to/app/keys "
                        "and restart the server."
                    ) from exc
                last_exc = exc
                delay = self._config.submit_retry_base_delay * (2**attempt)
                logger.warning(
                    "Data Lab submit attempt %d failed (%s), retrying in %.1fs",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except (DatalabTimeoutError, RuntimeError) as exc:
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
        pdf_path: str | None = None,
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
        sig_enrichment_telemetry: dict[int, dict] = {}

        # Signature-enrichment dependencies — loaded once per
        # adapter call so we don't re-parse the YAML or hit the
        # config cache per-page. The kill switch
        # (DatalabConfig.signature_enrichment=False) short-circuits
        # injection but still emits telemetry, which is exactly
        # the surface we need for a flag-off A/B diagnostic.
        sig_columns: tuple[str, ...] = ()
        sig_enrich_enabled = bool(getattr(self._config, "signature_enrichment", True))
        sig_enrich_aggressive = bool(
            getattr(self._config, "signature_enrichment_aggressive", True)
        )
        try:
            from app.compliance.rules.profiles import load_profiles
            sig_columns = tuple(load_profiles().signature_column_headers)
        except Exception:  # pragma: no cover — defensive
            logger.warning(
                "signature enricher: failed to load column headers from "
                "document_profiles.yaml; falling back to no enrichment",
                exc_info=True,
            )

        for i, page_md in enumerate(page_texts):
            page_num = page_offset + i + 1
            page_md_stripped = page_md.strip()

            page_images: dict[str, bytes] = {
                k: v for k, v in all_images.items() if k in page_md_stripped
            }

            # Signature enrichment runs BEFORE _parse_signatures so
            # the synthesized markers flow through the same regex
            # path L0/L1 already use. Result: a uniform
            # ``[Signature]`` wire shape regardless of which layer
            # produced it. We call the enricher even when bbox_data
            # is empty/absent because the L4 path needs no JSON-tree
            # evidence — column-header + date-only content alone
            # triggers injection (this is the only layer that
            # works on docs where Datalab returns no Handwriting
            # blocks even though [Signature] markers ARE in the
            # markdown — diagnostic on May 4 doc, all 13 signed
            # pages had handwritten_count=0).
            enriched_md = page_md_stripped
            if sig_columns:
                from app.adapters.ocr.signature_enricher import (
                    JsonBlock,
                    enrich_page,
                )
                page_json_blocks: list[JsonBlock] = []
                for block_type, _text, polygon in (bbox_data or {}).get(page_num, []):
                    try:
                        poly = tuple((float(p[0]), float(p[1])) for p in polygon)
                    except (TypeError, ValueError):
                        continue
                    page_json_blocks.append(
                        JsonBlock(
                            block_type=block_type,
                            polygon=poly,
                            page_num=page_num,
                            text=_text,
                        )
                    )
                enrichment = enrich_page(
                    page_md_stripped,
                    page_json_blocks,
                    page_num,
                    sig_columns,
                    enabled=sig_enrich_enabled,
                    aggressive=sig_enrich_aggressive,
                )
                enriched_md = enrichment.markdown
                if enrichment.telemetry.injected_count or any(
                    enrichment.telemetry.layer_counts.values()
                ):
                    sig_enrichment_telemetry[page_num] = enrichment.telemetry.to_dict()

            sel_marks = _parse_selection_marks(enriched_md, page_num)
            hw_words = _parse_handwriting_words(page_md, page_num)
            sigs = _parse_signatures(enriched_md, page_num)
            table_meta = _parse_table_metadata(enriched_md, page_num)
            formulas = _parse_formulas(enriched_md, page_num)

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
                    markdown=enriched_md,
                    words=hw_words,
                    selection_marks=sel_marks,
                    formulas=formulas,
                    images=page_images,
                )
            )

        # ── Cell-bbox crops for L_HWTEXT / L_TEXT cells ─────────
        # Where Datalab transcribed handwritten initials as text
        # (italic ``<i>FE</i>`` or plain ``N089``) instead of
        # cropping them as ``<img data-bbox>`` regions, the cell
        # now has ``[Signature]`` text but no visual signature.
        # Akhilesh reported this gap on 2538105061.pdf: 29 pages
        # affected. We close it by rendering the PDF page at the
        # cell's TableCell bbox and writing the crop next to the
        # other ``HASH_img.jpg`` files, then rewriting the
        # markdown to include the ``<img>`` tag alongside the
        # text marker.
        #
        # Fail-open: any crop failure is logged and the cell
        # keeps its ``[Signature]`` text marker without an image.
        if pdf_path and bbox_data and sig_columns:
            try:
                self._inject_signature_crops(
                    pages, bbox_data, sig_columns, pdf_path,
                )
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "signature crop injection failed; markdown unchanged"
                )

        if sig_enrichment_telemetry:
            logger.info(
                "signature enricher: enriched %d page(s); per-page telemetry=%s",
                len(sig_enrichment_telemetry),
                sig_enrichment_telemetry,
            )
            # Also emit a structured run-telemetry event per page so
            # the on-disk ``telemetry.json`` carries the per-layer
            # counts as queryable structured data (not just a log
            # line). Auto-no-ops when no sink is bound (e.g. tests
            # exercising the adapter directly outside a pipeline).
            try:
                from app.observability.run_telemetry import record_event
                for page_num, tel in sig_enrichment_telemetry.items():
                    record_event(
                        "signature.page_enriched",
                        page_num=page_num,
                        layer_counts=tel.get("layer_counts", {}),
                        injected_count=tel.get("injected_count", 0),
                        skipped_idempotent=tel.get("skipped_idempotent", 0),
                        signature_columns_detected=tel.get(
                            "signature_columns_detected", 0
                        ),
                        tables_scanned=tel.get("tables_scanned", 0),
                    )
            except Exception:  # pragma: no cover — never break OCR
                pass

        if extraction_data:
            all_kv_pairs = _map_extraction_to_kv_pairs(extraction_data, page_offset + 1)
            all_kv_pairs = _enrich_attestations(all_kv_pairs)
            all_kv_pairs = _enrich_critical_steps(all_kv_pairs)

        return pages, sanitized_md, all_table_meta, all_signatures, all_kv_pairs

    def _inject_signature_crops(
        self,
        pages: list[OCRPageResult],
        bbox_data: dict[int, list[tuple[str, str, list[list[float]]]]],
        sig_columns: tuple[str, ...],
        pdf_path: str,
    ) -> None:
        """For each ``[Signature]``-marked cell with no ``<img>``,
        find the matching TableCell bbox in the JSON tree, crop
        the PDF page at that bbox, save as
        ``<doc_dir>/images/p{page}_sigcrop_{hash}.jpg``, and
        rewrite the markdown to embed the ``<img>`` tag alongside
        the ``[Signature]`` marker.

        Matching strategy: text-based. The JSON-tree ``TableCell``
        blocks carry both a polygon and an HTML-stripped text
        content. We clean the markdown cell text the same way and
        match by exact equality. When the match is unique we use
        that bbox; when it's ambiguous (multiple cells share the
        same text — common with ``N089`` repeated across rows) we
        match by reading order.

        Mutates ``pages[i].markdown`` in place. Fail-open: any
        single-cell failure is logged and the cell keeps its
        text marker without an image.
        """
        from pathlib import Path
        from app.adapters.ocr.cell_image_crop import crop_cell_regions
        from app.adapters.ocr.signature_enricher import (
            EXISTING_MARKER_RE,
            IMG_TAG_RE,
            _is_separator_row,
            _is_signature_column_header,
            _split_table_row,
            _normalize_for_match,
        )
        try:
            from app.observability.run_telemetry import record_event
        except Exception:  # pragma: no cover — defensive
            def record_event(*a, **kw):  # type: ignore[no-redef]
                return None

        doc_dir = Path(pdf_path).parent
        images_dir = doc_dir / "images"
        doc_id = doc_dir.name

        # Emit an always-fires "attempted" event at entry so post-run
        # validation can see the crop pipeline ran. Captures counts
        # of TableCells / sig-marker cells / unmatched cells per page
        # so silent no-crop runs are no longer black-box.
        total_table_cells = sum(
            1 for bs in bbox_data.values() for (bt, _t, _p) in bs
            if bt == "TableCell"
        )
        record_event(
            "signature.crop_pipeline_attempted",
            pages_in_chunk=len(pages),
            total_table_cells_in_bbox_data=total_table_cells,
            sig_columns_loaded=len(sig_columns),
        )

        if total_table_cells == 0:
            # Datalab's JSON tree had no TableCell blocks for this
            # chunk — common when the run uses Datalab's "fast" or
            # "balanced" mode rather than "accurate", or when the
            # API is in a flaky state. Without TableCells we have
            # no cell-bbox info to crop. Bail loudly to telemetry.
            record_event(
                "signature.crop_pipeline_skipped",
                level="warning",
                reason="no TableCell blocks in bbox_data",
                pages_in_chunk=len(pages),
            )
            return

        # Collect per-page cell crop work: which bboxes to crop +
        # which markdown positions to rewrite.
        crop_bboxes: dict[int, list[tuple[float, float, float, float]]] = {}
        # page_num -> [(cell_text_to_replace, bbox_key_index)]
        per_page_replacements: dict[int, list[tuple[str, int]]] = {}
        # Per-page diagnostic counters
        per_page_diag: dict[int, dict[str, int]] = {}

        # Build OCR-pixel page size map for the cropper.
        page_pixel_sizes: dict[int, tuple[float, float]] = {}
        for page in pages:
            w = getattr(page, "page_width", None) or 0
            h = getattr(page, "page_height", None) or 0
            if w and h:
                page_pixel_sizes[page.page_num] = (float(w), float(h))

        for page in pages:
            page_num = page.page_num
            md = page.markdown
            if not md or "[Signature]" not in md:
                continue
            page_blocks = bbox_data.get(page_num, []) or []
            # All TableCell blocks on this page, in JSON order.
            table_cells = [
                (txt, poly) for (bt, txt, poly) in page_blocks
                if bt == "TableCell" and poly
            ]
            if not table_cells:
                continue

            # Find each [Signature] cell without an <img>; collect
            # its cleaned text content so we can match against
            # TableCell texts.
            cell_specs: list[tuple[str, str]] = []  # (raw_cell, cleaned)
            rows = md.split("\n")
            i = 0
            while i < len(rows):
                if not rows[i].strip().startswith("|"):
                    i += 1; continue
                ts = i
                while i < len(rows) and rows[i].strip().startswith("|"):
                    i += 1
                table = rows[ts:i]
                hdr_idx = next(
                    (k for k, r in enumerate(table)
                     if not _is_separator_row(r)),
                    None,
                )
                if hdr_idx is None:
                    continue
                hdr_cells = _split_table_row(table[hdr_idx])
                sig_col_idx = {
                    ci for ci, h in enumerate(hdr_cells)
                    if _is_signature_column_header(h, sig_columns)
                }
                if not sig_col_idx:
                    continue
                for k, row in enumerate(table):
                    if k == hdr_idx or _is_separator_row(row):
                        continue
                    data_cells = _split_table_row(row)
                    for ci in sig_col_idx:
                        if ci >= len(data_cells):
                            continue
                        cell = data_cells[ci]
                        if not EXISTING_MARKER_RE.search(cell):
                            continue
                        if IMG_TAG_RE.search(cell):
                            continue  # already has crop
                        # Normalized text used for matching against
                        # the JSON-tree TableCell.text field. Keep
                        # dates and filler intact — they're part
                        # of the cell's identity. We previously
                        # used ``_clean_for_signature_check`` here
                        # which strips dates, leaving an empty
                        # match key for L4 cells (just a date) and
                        # losing the entire crop opportunity.
                        normalized = _normalize_for_match(cell)
                        if not normalized:
                            continue
                        cell_specs.append((cell, normalized))

            if not cell_specs:
                continue

            # Index TableCell texts under the same normalization
            # we applied to the markdown cells. Match by exact
            # normalized equality; when multiple TableCells share
            # the same text (e.g. every Done-by cell in the
            # dispensing table just has a date), pair by
            # reading-order so the Nth signature cell maps to
            # the Nth matching TableCell.
            cleaned_index: dict[str, list[
                tuple[float, float, float, float]
            ]] = {}
            for txt, poly in table_cells:
                t_norm = _normalize_for_match(txt or "")
                if not t_norm:
                    continue
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                cleaned_index.setdefault(t_norm, []).append(bbox)

            crop_list: list[tuple[float, float, float, float]] = []
            replacements: list[tuple[str, int]] = []
            used_indices: dict[str, int] = {}
            unmatched_keys: list[str] = []
            for raw_cell, cleaned in cell_specs:
                # Pull the next bbox for this cleaned text
                # (reading-order match for duplicates).
                bboxes = cleaned_index.get(cleaned)
                if not bboxes:
                    unmatched_keys.append(cleaned)
                    continue
                idx = used_indices.get(cleaned, 0)
                if idx >= len(bboxes):
                    unmatched_keys.append(cleaned + f"#{idx}")
                    continue  # ran out — mismatched
                bbox = bboxes[idx]
                used_indices[cleaned] = idx + 1
                bbox_idx_in_crop = len(crop_list)
                crop_list.append(bbox)
                replacements.append((raw_cell, bbox_idx_in_crop))

            per_page_diag[page_num] = {
                "sig_marker_cells_without_img": len(cell_specs),
                "table_cells_in_bbox_data": len(table_cells),
                "table_cells_with_matchable_text": sum(
                    len(v) for v in cleaned_index.values()
                ),
                "matched_to_bbox": len(crop_list),
                "unmatched_sig_cells": len(unmatched_keys),
                "sample_unmatched_keys": unmatched_keys[:5],
                "sample_table_cell_keys": list(cleaned_index.keys())[:5],
            }

            if crop_list:
                crop_bboxes[page_num] = crop_list
                per_page_replacements[page_num] = replacements

        if not crop_bboxes:
            return

        # Run all crops in one pypdfium2 session.
        crop_results = crop_cell_regions(
            pdf_path=pdf_path,
            cell_bboxes=crop_bboxes,
            output_dir=images_dir,
            page_pixel_sizes=page_pixel_sizes,
        )

        api_prefix = f"/api/documents/{doc_id}/images/"
        total_injected = 0

        # Rewrite each page's markdown to embed the <img> tag.
        for page in pages:
            page_num = page.page_num
            replacements = per_page_replacements.get(page_num)
            if not replacements:
                continue
            crops = crop_results.get(page_num) or []
            if not crops:
                continue
            md = page.markdown
            for raw_cell, bbox_idx in replacements:
                if bbox_idx >= len(crops):
                    continue
                _, filename = crops[bbox_idx]
                # Inject <img> immediately after [Signature] for
                # the matched cell. Use a single replace so
                # idempotency on rerun is preserved (the new
                # cell content already has an <img> so a second
                # pass would skip it via the IMG_TAG_RE guard).
                new_cell = re.sub(
                    r"(\[Signature\])(\s*)",
                    rf'\1 <img data-sigcrop="1" src="{api_prefix}{filename}"/>\2',
                    raw_cell,
                    count=1,
                )
                # Replace only the first occurrence per row (raw_cell
                # may appear elsewhere on the page if Datalab
                # produces identical cell content).
                if new_cell != raw_cell:
                    md = md.replace(raw_cell, new_cell, 1)
                    total_injected += 1
            page.markdown = md

        # Always emit a completion event so post-run validation can
        # see the matcher's per-page diagnostics regardless of how
        # many crops were generated. ``per_page_diag`` shows
        # exactly where the matching falls off when zero crops
        # land — most often "table_cells_with_matchable_text=0"
        # (JSON tree was empty) or "matched_to_bbox=0" (cell
        # text didn't match any TableCell text).
        record_event(
            "signature.crop_pipeline_completed",
            total_injected=total_injected,
            pages_touched=len(crop_bboxes),
            pages_with_signatures_to_match=len(per_page_diag),
            per_page_diag=per_page_diag,
        )

        if total_injected:
            logger.info(
                "signature crop: injected %d <img> tags across %d pages",
                total_injected, len(crop_bboxes),
            )
            record_event(
                "signature.crops_injected",
                total_injected=total_injected,
                pages_touched=len(crop_bboxes),
            )

    async def _process_single_chunk(
        self,
        chunk_idx: int,
        page_range: str,
        total_chunks: int,
        pdf_path: str,
        sem: asyncio.Semaphore,
        completed_counter: dict,
        progress_callback: ProgressCallback | None,
        in_flight: dict[int, tuple[str, float]] | None = None,
    ) -> tuple[
        list[OCRPageResult], str, list[dict], list[SignatureRegion],
        list[KeyValuePair], float | None, int,
    ]:
        """Process a single chunk: convert -> extract+bboxes -> parse. Semaphore-gated.

        Registers the chunk in the shared ``in_flight`` map on entry
        and removes it on exit. The aggregate heartbeat
        (``_run_aggregate_heartbeat``) reads this map every poll
        interval to emit one combined progress label for all
        concurrent chunks; per-chunk heartbeats are no longer
        emitted here so the upstream WS throttle isn't flooded.
        """
        async with sem:
            logger.info(
                "Chunk %d/%d (pages %s) — acquired semaphore slot",
                chunk_idx + 1, total_chunks, page_range,
            )

            chunk_label = f"Chunk {chunk_idx + 1}/{total_chunks} (pages {page_range})"
            loop = asyncio.get_running_loop()
            if in_flight is not None:
                in_flight[chunk_idx] = (chunk_label, loop.time())

            try:
                result = await self._submit_with_retry(
                    pdf_path,
                    page_range,
                    None,  # per-chunk heartbeat is gone; aggregate covers it
                    chunk_label=chunk_label,
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
                    pdf_path=pdf_path,
                )
            finally:
                if in_flight is not None:
                    in_flight.pop(chunk_idx, None)

            completed_counter["n"] += 1
            if progress_callback:
                pct = int((completed_counter["n"] / total_chunks) * 90)
                if in_flight is not None and len(in_flight) > 0:
                    suffix = f" • {len(in_flight)} chunk(s) still analyzing"
                else:
                    suffix = ""
                progress_callback(
                    pct,
                    f"Completed chunk {completed_counter['n']}/{total_chunks}{suffix}",
                )

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
        # Shared map of currently-in-flight chunks. The aggregate
        # heartbeat reads this every poll interval and emits a
        # single combined label so a reviewer watching the bar can
        # tell how many chunks are running, the oldest age, and how
        # many have finished — without the throttle having to pick
        # one chunk's per-tick label to surface and dropping the
        # rest.
        in_flight: dict[int, tuple[str, float]] = {}

        heartbeat_task = asyncio.create_task(
            self._run_aggregate_heartbeat(
                in_flight=in_flight,
                completed_counter=completed_counter,
                total_chunks=total_chunks,
                progress_callback=progress_callback,
            )
        )

        try:
            chunk_results = await asyncio.gather(
                *(
                    self._process_single_chunk(
                        idx, pr, total_chunks, pdf_path,
                        sem, completed_counter, progress_callback,
                        in_flight=in_flight,
                    )
                    for idx, pr in enumerate(page_ranges)
                )
            )
        finally:
            heartbeat_task.cancel()
            # Suppress the CancelledError that ``cancel()`` raises;
            # we don't care about its return value, only that it
            # stops before the function returns.
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

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
