"""Azure Document Intelligence OCR adapter.

Uses Azure AI Foundry cloud API for dev/staging and disconnected container
for on-prem production. Same adapter class, different endpoint in config.

Provides: per-word handwriting detection, per-field confidence scores,
barcode reading (17+ types), selection mark detection, cross-page table support,
key-value pair extraction, font/style analysis, formula detection (LaTeX),
language detection, and signature identification.

Requests markdown output format so that result.content contains structured
markdown with headings, paragraphs, HTML tables, and page-break markers.
Per-page markdown is sliced from the full content using page span offsets.

Tables in the markdown are reconstructed from result.tables cell data to
preserve rowspan/colspan, rowHeader/stubHead semantics, and repeat headers
on cross-page table continuations.

Post-processing strips noise (empty figures, page numbers, redundant headers/
footers) using result.paragraphs roles, and normalises selection mark symbols.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from html import escape as html_escape

from app.config.settings import AzureDIConfig
from app.core.ports.ocr import (
    BarcodeResult,
    BoundingRegion,
    FormulaResult,
    KeyValuePair,
    LanguageSpan,
    OCRPageResult,
    OCRResult,
    OCRWord,
    ProgressCallback,
    SelectionMark,
    SignatureRegion,
    StyleSpan,
)
from app.core.services.layout_markdown_sanitizer import sanitize_layout_markdown

logger = logging.getLogger(__name__)

_TABLE_CORRUPTION_REPAIR_TAGS = {
    "fixed_fragment_t_table",
    "fixed_fragment_abl_table",
    "fixed_broken_table_join",
    "fixed_broken_table_open",
    "fixed_missing_angle_table_tag",
    "fixed_missing_angle_close_table_tag",
    "fixed_stranded_table_close_token",
    "removed_broken_pagenumber_comment",
    "fixed_broken_table_join_no_angle_close",
}


# ═══════════════════════════════════════════════════════════════
#  LRO progress extraction
# ═══════════════════════════════════════════════════════════════


def _read_percent_completed(poller) -> int | None:
    """Extract percentCompleted from the poller's internal pipeline response.

    The Azure SDK's ``LROPoller`` does not expose ``percentCompleted`` in its
    public API, but the value is present in the raw JSON body of each status
    poll response.  We access it via the internal ``_pipeline_response`` of the
    polling method — wrapped in a try/except so that SDK upgrades never crash.
    """
    try:
        pm = poller.polling_method()
        resp = getattr(pm, "_pipeline_response", None)
        if resp is None:
            return None
        http_resp = resp.http_response
        body = None
        if hasattr(http_resp, "json"):
            body = http_resp.json()
        elif hasattr(http_resp, "text"):
            body = json.loads(http_resp.text())
        if body and isinstance(body, dict):
            pct = body.get("percentCompleted")
            if pct is not None:
                return int(pct)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
#  Geometry helpers
# ═══════════════════════════════════════════════════════════════


def _to_bounding_region(regions: list | None, page_num: int) -> BoundingRegion | None:
    if not regions:
        return None
    r = regions[0]
    polygon = getattr(r, "polygon", None) or []
    if len(polygon) >= 4:
        x_coords = [polygon[i] for i in range(0, len(polygon), 2)]
        y_coords = [polygon[i] for i in range(1, len(polygon), 2)]
        return BoundingRegion(
            page_num=page_num,
            x=min(x_coords),
            y=min(y_coords),
            width=max(x_coords) - min(x_coords),
            height=max(y_coords) - min(y_coords),
        )
    return None


def _cell_pages(cell) -> set[int]:
    """Return the set of page numbers a cell appears on."""
    regions = getattr(cell, "bounding_regions", None) or []
    return {getattr(r, "page_number", 0) for r in regions}


def _span_page(span, pages) -> int:
    """Determine which page a span belongs to based on offset overlap."""
    offset = getattr(span, "offset", 0)
    for pg in pages or []:
        for ps in getattr(pg, "spans", None) or []:
            pg_start = getattr(ps, "offset", 0)
            pg_len = getattr(ps, "length", 0)
            if pg_start <= offset < pg_start + pg_len:
                return pg.page_number
    return 1


# ═══════════════════════════════════════════════════════════════
#  Table reconstruction from result.tables cells
# ═══════════════════════════════════════════════════════════════

_HEADER_KINDS = {"columnHeader", "stubHead"}
_ROW_HEADER_KINDS = {"rowHeader"}


def _build_table_html(table, page_num: int | None = None) -> str:
    """Reconstruct an HTML table from structured cell data.

    Handles columnHeader, rowHeader, stubHead, and description cell kinds.
    """
    cells = getattr(table, "cells", None) or []
    if not cells:
        return ""

    col_count = int(getattr(table, "column_count", 0) or 0)
    if col_count <= 0:
        # Fallback when SDK omits/inflates metadata: infer from cells.
        col_count = max(
            (int(getattr(c, "column_index", 0) or 0) + int(getattr(c, "column_span", 1) or 1))
            for c in cells
        )

    header_cells = [c for c in cells if getattr(c, "kind", "") in _HEADER_KINDS]
    data_cells = [c for c in cells if getattr(c, "kind", "") not in _HEADER_KINDS]

    header_row_indices = sorted({getattr(c, "row_index", 0) for c in header_cells}) if header_cells else []

    if page_num is not None:
        data_cells = [c for c in data_cells if page_num in _cell_pages(c)]

    data_row_indices = sorted({getattr(c, "row_index", 0) for c in data_cells})
    if not data_row_indices and not header_cells:
        return ""

    all_rows_on_page = set(header_row_indices) | set(data_row_indices)

    cell_map: dict[tuple[int, int], dict] = {}
    occupied: set[tuple[int, int]] = set()

    for cell in header_cells + data_cells:
        ri = getattr(cell, "row_index", 0)
        ci = getattr(cell, "column_index", 0)
        rs = max(1, int(getattr(cell, "row_span", 1) or 1))
        cs = max(1, int(getattr(cell, "column_span", 1) or 1))
        if ci >= col_count:
            # Ignore invalid cells that start past declared table width.
            continue
        if ci + cs > col_count:
            # Keep table geometry stable: clamp noisy spans that overflow width.
            cs = col_count - ci
            if cs <= 0:
                continue
        content = getattr(cell, "content", "")
        kind = getattr(cell, "kind", "content")

        effective_rs = sum(1 for i in range(ri, ri + rs) if i in all_rows_on_page)
        effective_rs = max(effective_rs, 1)

        cell_map[(ri, ci)] = {
            "content": content,
            "row_span": effective_rs,
            "col_span": cs,
            "kind": kind,
        }
        for dr in range(rs):
            for dc in range(cs):
                if dr > 0 or dc > 0:
                    occupied.add((ri + dr, ci + dc))

    parts: list[str] = ["<table>"]

    caption = getattr(table, "caption", None)
    if caption:
        cap_text = getattr(caption, "content", "")
        if cap_text:
            parts.append(f"<caption>{html_escape(cap_text)}</caption>")

    if header_row_indices:
        parts.append("<thead>")
        for ri in header_row_indices:
            parts.append("<tr>")
            for ci in range(col_count):
                if (ri, ci) in occupied:
                    continue
                cd = cell_map.get((ri, ci))
                if cd is None:
                    parts.append("<th></th>")
                    continue
                attrs = ""
                if cd["row_span"] > 1:
                    attrs += f' rowspan="{cd["row_span"]}"'
                if cd["col_span"] > 1:
                    attrs += f' colspan="{cd["col_span"]}"'
                parts.append(f'<th{attrs}>{html_escape(cd["content"])}</th>')
            parts.append("</tr>")
        parts.append("</thead>")

    if data_row_indices:
        parts.append("<tbody>")
        for ri in data_row_indices:
            parts.append("<tr>")
            for ci in range(col_count):
                if (ri, ci) in occupied:
                    continue
                cd = cell_map.get((ri, ci))
                if cd is None:
                    parts.append("<td></td>")
                    continue
                attrs = ""
                if cd["row_span"] > 1:
                    attrs += f' rowspan="{cd["row_span"]}"'
                if cd["col_span"] > 1:
                    attrs += f' colspan="{cd["col_span"]}"'
                is_row_header = cd["kind"] in _ROW_HEADER_KINDS
                tag = "th" if is_row_header else "td"
                if is_row_header:
                    attrs += ' scope="row"'
                parts.append(f'<{tag}{attrs}>{html_escape(cd["content"])}</{tag}>')
            parts.append("</tr>")
        parts.append("</tbody>")

    footnotes = getattr(table, "footnotes", None) or []
    if footnotes:
        parts.append('<tfoot><tr><td colspan="' + str(col_count) + '">')
        for fn in footnotes:
            fn_text = getattr(fn, "content", "")
            if fn_text:
                parts.append(f"<small>{html_escape(fn_text)}</small><br/>")
        parts.append("</td></tr></tfoot>")

    parts.append("</table>")
    return "\n".join(parts)


def _build_page_tables(result) -> dict[int, dict[int, str]]:
    """Build reconstructed HTML tables grouped by page and source table index."""
    page_tables: dict[int, dict[int, str]] = {}

    for table_idx, table in enumerate(getattr(result, "tables", None) or []):
        cells = getattr(table, "cells", None) or []
        if not cells:
            continue

        all_pages: set[int] = set()
        for cell in cells:
            all_pages |= _cell_pages(cell)
        all_pages.discard(0)

        for pg in sorted(all_pages):
            html = _build_table_html(table, page_num=pg)
            if html:
                page_tables.setdefault(pg, {})[table_idx] = html

    return page_tables


def _compute_table_ranges(result) -> list[tuple[int, int, int]]:
    """Map each table to its character offset range in result.content."""
    ranges: list[tuple[int, int, int]] = []

    for idx, table in enumerate(getattr(result, "tables", None) or []):
        spans = getattr(table, "spans", None) or []
        if not spans:
            continue
        offsets = []
        for span in spans:
            off = getattr(span, "offset", 0)
            lng = getattr(span, "length", 0)
            offsets.append((off, off + lng))
        if offsets:
            start = min(o[0] for o in offsets)
            end = max(o[1] for o in offsets)
            ranges.append((start, end, idx))

    ranges.sort(key=lambda r: r[0])
    return ranges


# ═══════════════════════════════════════════════════════════════
#  Full-markdown post-processing
# ═══════════════════════════════════════════════════════════════

_PAGE_NUMBER_RE = re.compile(r'<!-- PageNumber="[^"]*" -->\s*')
_EMPTY_FIGURE_RE = re.compile(
    r'<figure>\s*(?:\S[^<]{0,60}\s*)?</figure>\s*\n*',
    re.DOTALL,
)
_SELECTION_SELECTED_RE = re.compile(r':selected:')
_SELECTION_UNSELECTED_RE = re.compile(r':unselected:')
_PLACEHOLDER_VALUES = {"-", "n/a", "na", "not applicable", "nil"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def _reconstruct_full_markdown(content: str, result) -> str:
    """Replace Azure DI's native table HTML in the full markdown with
    reconstructed tables that have proper <thead>/<tbody>/rowspan/colspan."""
    tables = getattr(result, "tables", None) or []
    if not tables:
        return content

    table_ranges = _compute_table_ranges(result)
    if not table_ranges:
        return content

    full_table_html: dict[int, str] = {}
    for idx, table in enumerate(tables):
        html = _build_table_html(table, page_num=None)
        if html:
            full_table_html[idx] = html

    rebuilt = content
    for tbl_start, tbl_end, tbl_idx in reversed(table_ranges):
        replacement = full_table_html.get(tbl_idx)
        if replacement:
            rebuilt = rebuilt[:tbl_start] + replacement + rebuilt[tbl_end:]

    return rebuilt


def _build_paragraph_strip_ranges(result) -> list[tuple[int, int]]:
    """Identify character ranges to strip using paragraph roles.

    Strips: pageHeader, pageFooter, pageNumber paragraphs.
    """
    strip_roles = {"pageHeader", "pageFooter", "pageNumber"}
    ranges: list[tuple[int, int]] = []

    for para in getattr(result, "paragraphs", None) or []:
        role = getattr(para, "role", None)
        if role not in strip_roles:
            continue
        for span in getattr(para, "spans", None) or []:
            off = getattr(span, "offset", 0)
            lng = getattr(span, "length", 0)
            if lng > 0:
                ranges.append((off, off + lng))

    ranges.sort(key=lambda r: r[0])
    return ranges


def _strip_paragraph_ranges(content: str, ranges: list[tuple[int, int]]) -> str:
    """Remove character ranges from content (headers, footers, page numbers)."""
    if not ranges:
        return content
    parts: list[str] = []
    prev_end = 0
    for start, end in ranges:
        if start > prev_end:
            parts.append(content[prev_end:start])
        prev_end = max(prev_end, end)
    if prev_end < len(content):
        parts.append(content[prev_end:])
    return "".join(parts)


def _cleanup_markdown(md: str) -> str:
    """Remove noise from Azure DI markdown output."""
    md, _ = sanitize_layout_markdown(md)
    md = _PAGE_NUMBER_RE.sub("", md)
    md = _EMPTY_FIGURE_RE.sub("", md)
    md = _SELECTION_SELECTED_RE.sub("☑", md)
    md = _SELECTION_UNSELECTED_RE.sub("☐", md)
    md = re.sub(r'\n{4,}', '\n\n\n', md)
    return md.strip()


# ═══════════════════════════════════════════════════════════════
#  Per-page markdown extraction with table replacement
# ═══════════════════════════════════════════════════════════════


def _extract_page_markdown(
    content: str,
    az_page,
    page_tables: dict[int, str] | None = None,
    table_ranges: list[tuple[int, int, int]] | None = None,
) -> str | None:
    """Slice the full markdown content for a single page using Azure DI span offsets."""
    spans = getattr(az_page, "spans", None)
    if not spans:
        return None

    try:
        page_start = getattr(spans[0], "offset", 0)
        page_length = getattr(spans[0], "length", 0)
        if len(spans) > 1:
            last = spans[-1]
            end = getattr(last, "offset", 0) + getattr(last, "length", 0)
            page_start = min(page_start, getattr(spans[0], "offset", 0))
            page_length = end - page_start

        if page_length <= 0:
            return None

        page_end = page_start + page_length
        page_md = content[page_start:page_end]

        if page_tables and table_ranges:
            overlapping = []
            for tbl_start, tbl_end, tbl_idx in table_ranges:
                if tbl_start < page_end and tbl_end > page_start:
                    overlap_start = max(tbl_start, page_start) - page_start
                    overlap_end = min(tbl_end, page_end) - page_start
                    overlapping.append((overlap_start, overlap_end, tbl_idx))

            if overlapping:
                overlapping.sort(key=lambda x: x[0], reverse=True)
                for overlap_start, overlap_end, tbl_idx in overlapping:
                    replacement = page_tables.get(tbl_idx, "")
                    page_md = page_md[:overlap_start] + replacement + page_md[overlap_end:]

        return page_md.strip()

    except Exception as e:
        logger.warning(f"Failed to extract page markdown from spans: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Enriched metadata extractors
# ═══════════════════════════════════════════════════════════════


def _extract_key_value_pairs(result, pages) -> list[KeyValuePair]:
    """Extract key-value pairs from form fields."""
    pairs: list[KeyValuePair] = []
    for kvp in getattr(result, "key_value_pairs", None) or []:
        key_elem = getattr(kvp, "key", None)
        val_elem = getattr(kvp, "value", None)
        if not key_elem:
            continue

        key_text = getattr(key_elem, "content", "").strip()
        val_text = getattr(val_elem, "content", "").strip() if val_elem else ""
        confidence = getattr(kvp, "confidence", 0.0)

        page_num = 1
        key_spans = getattr(key_elem, "bounding_regions", None) or []
        if key_spans:
            page_num = getattr(key_spans[0], "page_number", 1)

        br = _to_bounding_region(
            getattr(key_elem, "bounding_regions", None), page_num
        )

        if key_text:
            pairs.append(KeyValuePair(
                key=key_text,
                value=val_text,
                confidence=confidence,
                page_num=page_num,
                bounding_region=br,
            ))
    return pairs


def _extract_styles(result) -> list[StyleSpan]:
    """Extract font/handwriting style spans."""
    spans: list[StyleSpan] = []
    for style in getattr(result, "styles", None) or []:
        for span in getattr(style, "spans", None) or []:
            spans.append(StyleSpan(
                is_handwritten=getattr(style, "is_handwritten", False) or False,
                font_family=getattr(style, "similar_font_family", "") or "",
                font_style=str(getattr(style, "font_style", "normal") or "normal"),
                font_weight=str(getattr(style, "font_weight", "normal") or "normal"),
                color=getattr(style, "color", "") or "",
                confidence=getattr(style, "confidence", 0.0),
                offset=getattr(span, "offset", 0),
                length=getattr(span, "length", 0),
            ))
    return spans


def _extract_languages(result) -> list[LanguageSpan]:
    """Extract per-span language detection results."""
    langs: list[LanguageSpan] = []
    for lang in getattr(result, "languages", None) or []:
        for span in getattr(lang, "spans", None) or []:
            langs.append(LanguageSpan(
                locale=getattr(lang, "locale", "") or "",
                confidence=getattr(lang, "confidence", 0.0),
                offset=getattr(span, "offset", 0),
                length=getattr(span, "length", 0),
            ))
    return langs


def _extract_formulas(result) -> dict[int, list[FormulaResult]]:
    """Extract formulas grouped by page number."""
    page_formulas: dict[int, list[FormulaResult]] = {}
    for az_page in getattr(result, "pages", None) or []:
        pn = az_page.page_number
        for formula in getattr(az_page, "formulas", None) or []:
            page_formulas.setdefault(pn, []).append(FormulaResult(
                kind=str(getattr(formula, "kind", "inline")),
                value=getattr(formula, "value", ""),
                confidence=getattr(formula, "confidence", 0.0),
                page_num=pn,
            ))
    return page_formulas


def _detect_signatures(result, styles: list[StyleSpan], pages) -> list[SignatureRegion]:
    """Detect signature regions using multiple signals.

    1. key_value_pairs where the key mentions "signature" and has a value
    2. Handwriting style spans that overlap with signature-related key areas
    3. Style spans with is_handwritten near bottom of page
    """
    signatures: list[SignatureRegion] = []
    sig_keywords = {"signature", "signed by", "authorized by", "sign", "approved by", "verified by"}

    # Precompute handwriting presence per page once (instead of scanning all styles per KV row).
    handwritten_pages: set[int] = set()
    for style in styles:
        if not (style.is_handwritten and style.confidence > 0.5):
            continue
        handwritten_pages.update(_style_pages(style, pages))

    for kvp in getattr(result, "key_value_pairs", None) or []:
        key_elem = getattr(kvp, "key", None)
        val_elem = getattr(kvp, "value", None)
        if not key_elem:
            continue

        key_text = getattr(key_elem, "content", "").strip().lower()
        if not any(kw in key_text for kw in sig_keywords):
            continue

        val_text = getattr(val_elem, "content", "").strip() if val_elem else ""

        page_num = 1
        key_regions = getattr(key_elem, "bounding_regions", None) or []
        if key_regions:
            page_num = getattr(key_regions[0], "page_number", 1)

        val_regions = getattr(val_elem, "bounding_regions", None) if val_elem else None
        br = _to_bounding_region(val_regions, page_num) if val_regions else None

        value_norm = val_text.strip().lower()
        has_value_text = bool(value_norm) and value_norm not in _PLACEHOLDER_VALUES
        confidence = float(getattr(kvp, "confidence", 0.0) or 0.0)

        hw_near = page_num in handwritten_pages
        score = 0.0
        reason_codes: list[str] = []
        if any(kw in key_text for kw in sig_keywords):
            score += 0.45
            reason_codes.append("signature_keyword_match")
        if has_value_text:
            score += 0.35
            reason_codes.append("non_placeholder_value_present")
        if hw_near:
            score += 0.2
            reason_codes.append("nearby_handwriting_detected")

        status = "signed" if score >= 0.55 and (has_value_text or hw_near) else "unsigned"
        confidence = round(min(1.0, max(confidence, score)), 3)

        label = getattr(key_elem, "content", "").strip()
        signatures.append(SignatureRegion(
            page_num=page_num,
            status=status,
            confidence=confidence,
            bounding_region=br,
            label=label,
            reason_codes=reason_codes,
            evidence={
                "source": "kv_signature_key",
                "key_text": label,
                "has_value_text": has_value_text,
                "value_length": len(val_text),
                "keyword_matched": any(kw in key_text for kw in sig_keywords),
                "handwriting_nearby": hw_near,
                "region_present": br is not None,
                "decision_score": round(score, 3),
            },
        ))

    hw_by_page: dict[int, list[StyleSpan]] = {}
    for s in styles:
        if s.is_handwritten and s.confidence > 0.6 and s.length > 10:
            for pg in getattr(result, "pages", None) or []:
                for ps in getattr(pg, "spans", None) or []:
                    pg_start = getattr(ps, "offset", 0)
                    pg_len = getattr(ps, "length", 0)
                    if pg_start <= s.offset < pg_start + pg_len:
                        hw_by_page.setdefault(pg.page_number, []).append(s)

    sig_pages = {s.page_num for s in signatures}
    for pn, hw_spans in hw_by_page.items():
        if pn in sig_pages:
            continue
        total_hw_len = sum(s.length for s in hw_spans)
        if total_hw_len > 20:
            avg_conf = sum(s.confidence for s in hw_spans) / len(hw_spans)
            score = min(0.8, 0.45 + min(0.35, total_hw_len / 120.0))
            signatures.append(SignatureRegion(
                page_num=pn,
                status="signed" if score >= 0.55 else "unsigned",
                confidence=round(max(avg_conf, score), 3),
                label="Handwritten content detected",
                reason_codes=["handwriting_density_trigger"],
                evidence={
                    "source": "handwriting_fallback",
                    "handwriting_span_count": len(hw_spans),
                    "handwritten_content_density": total_hw_len,
                    "decision_score": round(score, 3),
                },
            ))

    return signatures


def _spans_overlap_page(style: StyleSpan, page_num: int, pages) -> bool:
    """Check if a style span overlaps with a given page."""
    for pg in pages or []:
        if pg.page_number != page_num:
            continue
        for ps in getattr(pg, "spans", None) or []:
            pg_start = getattr(ps, "offset", 0)
            pg_len = getattr(ps, "length", 0)
            if pg_start <= style.offset < pg_start + pg_len:
                return True
    return False


def _style_pages(style: StyleSpan, pages) -> set[int]:
    """Return all pages that overlap with a style span."""
    hits: set[int] = set()
    for pg in pages or []:
        for ps in getattr(pg, "spans", None) or []:
            pg_start = getattr(ps, "offset", 0)
            pg_len = getattr(ps, "length", 0)
            if pg_start <= style.offset < pg_start + pg_len:
                hits.add(pg.page_number)
                break
    return hits


# ═══════════════════════════════════════════════════════════════
#  Main adapter class
# ═══════════════════════════════════════════════════════════════


class AzureDIOCRAdapter:
    def __init__(self, config: AzureDIConfig):
        self._config = config
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client

        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        self._client = DocumentIntelligenceClient(
            endpoint=self._config.endpoint,
            credential=AzureKeyCredential(self._config.api_key),
        )
        return self._client

    async def extract(
        self,
        pdf_path: str,
        pages: list[int] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> OCRResult:
        client = self._get_client()
        loop = asyncio.get_event_loop()
        timeout_seconds = max(60, int(getattr(self._config, "analyze_timeout_seconds", 900) or 900))
        poll_interval = max(1, int(getattr(self._config, "progress_poll_interval_seconds", 2) or 2))
        heartbeat_seconds = max(5, int(getattr(self._config, "progress_heartbeat_seconds", 30) or 30))
        extract_started = time.monotonic()
        logger.info("Azure DI extract started for %s", pdf_path)

        def _analyze_with_progress():
            from azure.ai.documentintelligence.models import (
                AnalyzeDocumentRequest,
                DocumentContentFormat,
            )

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            poller = client.begin_analyze_document(
                "prebuilt-layout",
                body=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                features=self._config.features,
                output_content_format=DocumentContentFormat.MARKDOWN,
            )

            if progress_callback is None:
                return poller.result()

            progress_callback(0, "Submitted for OCR analysis")
            last_pct = -1
            started = time.monotonic()
            last_heartbeat = started

            pm = poller.polling_method()

            while not poller.done():
                elapsed = int(time.monotonic() - started)
                if elapsed >= timeout_seconds:
                    raise TimeoutError(
                        f"OCR analysis exceeded timeout of {timeout_seconds}s; "
                        "please retry with a smaller file or increase timeout."
                    )
                time.sleep(poll_interval)

                try:
                    pm.update_status()
                except Exception:
                    logger.debug("Progress poll failed, will retry", exc_info=True)

                pct = _read_percent_completed(poller)
                status_str = poller.status()

                if pct is not None and pct != last_pct:
                    last_pct = pct
                    progress_callback(pct, f"Analyzing ({pct}%)")
                elif last_pct < 0:
                    label = "Analyzing..." if status_str == "running" else f"Status: {status_str}"
                    progress_callback(5, label)

                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_seconds:
                    last_heartbeat = now
                    fallback_pct = max(5, last_pct if last_pct >= 0 else 5)
                    logger.info("Azure DI polling heartbeat: status=%s elapsed=%ss pct=%s", status_str, elapsed, pct)
                    progress_callback(fallback_pct, f"Still analyzing ({elapsed}s elapsed)")

            progress_callback(100, "Analysis complete")
            return poller.result()

        result = await loop.run_in_executor(None, _analyze_with_progress)
        analyze_elapsed = int(time.monotonic() - extract_started)
        logger.info("Azure DI analyze call completed in %ss for %s", analyze_elapsed, pdf_path)

        ocr_pages: list[OCRPageResult] = []
        content = result.content or ""

        table_meta = self._extract_table_metadata(result)
        page_tables = _build_page_tables(result)
        table_ranges = _compute_table_ranges(result)

        all_styles = _extract_styles(result)
        all_kv_pairs = _extract_key_value_pairs(result, result.pages)
        all_languages = _extract_languages(result)
        all_formulas = _extract_formulas(result)
        all_signatures = _detect_signatures(result, all_styles, result.pages)

        para_strip_ranges = _build_paragraph_strip_ranges(result)

        for az_page in result.pages or []:
            page_num = az_page.page_number
            if pages and page_num not in pages:
                continue

            words: list[OCRWord] = []
            for word in az_page.words or []:
                words.append(
                    OCRWord(
                        text=word.content,
                        confidence=getattr(word, "confidence", 0.0),
                        is_handwritten=getattr(word, "is_handwritten", False) or False,
                        bounding_region=_to_bounding_region(getattr(word, "bounding_regions", None), page_num),
                    )
                )

            barcodes: list[BarcodeResult] = []
            for bc in getattr(az_page, "barcodes", None) or []:
                barcodes.append(
                    BarcodeResult(
                        barcode_type=getattr(bc, "kind", "unknown"),
                        value=getattr(bc, "value", ""),
                        page_num=page_num,
                    )
                )

            selection_marks: list[SelectionMark] = []
            for sm in az_page.selection_marks or []:
                selection_marks.append(
                    SelectionMark(
                        state=sm.state or "unselected",
                        confidence=getattr(sm, "confidence", 0.0),
                        page_num=page_num,
                        bounding_region=_to_bounding_region(getattr(sm, "bounding_regions", None), page_num),
                    )
                )

            page_markdown = _extract_page_markdown(content, az_page)
            if page_markdown is None:
                page_markdown = " ".join(w.text for w in words)
                logger.info(f"Page {page_num}: fell back to word concatenation (no spans)")
            page_markdown, page_repairs = sanitize_layout_markdown(page_markdown)

            # If markdown appears table-corrupted, retry with reconstructed table HTML
            # to preserve grid semantics while keeping the default path lightweight.
            if any(tag in _TABLE_CORRUPTION_REPAIR_TAGS for tag in page_repairs):
                rebuilt_page_md = _extract_page_markdown(
                    content,
                    az_page,
                    page_tables.get(page_num),
                    table_ranges,
                )
                if rebuilt_page_md:
                    rebuilt_page_md, rebuilt_repairs = sanitize_layout_markdown(rebuilt_page_md)
                    prefer_rebuilt = (
                        rebuilt_page_md.count("<table") >= page_markdown.count("<table")
                        and len(rebuilt_repairs) <= len(page_repairs)
                    )
                    if prefer_rebuilt:
                        page_markdown = rebuilt_page_md
                        page_repairs = rebuilt_repairs

            ocr_pages.append(
                OCRPageResult(
                    page_num=page_num,
                    markdown=page_markdown,
                    page_width=getattr(az_page, "width", None),
                    page_height=getattr(az_page, "height", None),
                    page_unit=getattr(az_page, "unit", None),
                    parser_repairs=page_repairs,
                    words=words,
                    barcodes=barcodes,
                    selection_marks=selection_marks,
                    formulas=all_formulas.get(page_num, []),
                )
            )

        processed_full = content
        processed_full = _strip_paragraph_ranges(processed_full, para_strip_ranges)
        processed_full = _cleanup_markdown(processed_full)

        for page in ocr_pages:
            page.markdown = _cleanup_markdown(page.markdown)

        logger.info(
            f"Extraction complete: {len(ocr_pages)} pages, "
            f"{len(all_kv_pairs)} KV pairs, {len(all_styles)} style spans, "
            f"{len(all_signatures)} signatures, {len(all_languages)} language spans, "
            f"{sum(len(v) for v in all_formulas.values())} formulas"
        )
        total_elapsed = int(time.monotonic() - extract_started)
        logger.info("Azure DI full extraction pipeline completed in %ss for %s", total_elapsed, pdf_path)

        return OCRResult(
            pages=ocr_pages,
            full_markdown=processed_full,
            table_metadata=table_meta,
            key_value_pairs=all_kv_pairs,
            styles=all_styles,
            signatures=all_signatures,
            languages=all_languages,
        )

    @staticmethod
    def _extract_table_metadata(result) -> list[dict]:
        """Extract table metadata from Azure DI result, including cross-page detection."""
        tables_meta: list[dict] = []
        for table in getattr(result, "tables", None) or []:
            regions = getattr(table, "bounding_regions", None) or []
            page_numbers = sorted({getattr(r, "page_number", 0) for r in regions})
            is_cross_page = len(page_numbers) > 1
            tables_meta.append({
                "row_count": getattr(table, "row_count", 0),
                "column_count": getattr(table, "column_count", 0),
                "page_numbers": page_numbers,
                "is_cross_page": is_cross_page,
            })
        return tables_meta

    def supports_handwriting(self) -> bool:
        return True

    def supports_barcodes(self) -> bool:
        return True

    def supports_selection_marks(self) -> bool:
        return True

    async def extract_query_fields(self, pdf_path: str, query_fields: list[str]) -> list[dict]:
        """Optional targeted extraction path using Azure query_fields."""
        if not query_fields:
            return []
        client = self._get_client()
        loop = asyncio.get_event_loop()

        def _analyze_query_fields():
            from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            try:
                poller = client.begin_analyze_document(
                    "prebuilt-layout",
                    body=AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                    query_fields=query_fields,
                )
                result = poller.result()
            except TypeError:
                logger.warning("Azure SDK does not support query_fields on this version/endpoint")
                return []
            except Exception:
                logger.exception("Query fields extraction failed")
                return []

            rows: list[dict] = []
            for doc in getattr(result, "documents", None) or []:
                fields = getattr(doc, "fields", None) or {}
                for key, val in fields.items():
                    content = getattr(val, "content", "") or ""
                    conf = float(getattr(val, "confidence", 0.0) or 0.0)
                    regions = getattr(val, "bounding_regions", None) or []
                    page_num = getattr(regions[0], "page_number", 0) if regions else 0
                    rows.append({
                        "field_id": _slug(key),
                        "key": key,
                        "value": content,
                        "normalized_value": content.strip(),
                        "confidence": conf,
                        "page_num": page_num,
                        "bounding_region": _to_bounding_region(regions, page_num) if regions else None,
                    })
            return rows

        return await loop.run_in_executor(None, _analyze_query_fields)
