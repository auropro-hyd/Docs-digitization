"""Build enriched page context for compliance evaluation.

Assembles OCR markdown with structured metadata from Azure Document
Intelligence (signatures, key-value pairs, handwriting flags, selection
marks) into a single prompt-ready string that gives the LLM much richer
context than raw markdown alone.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MAX_MARKDOWN_CHARS = 6000
_MAX_KV_PAIRS = 40
_MAX_SIGNATURES = 20


def build_enriched_context(
    extraction: dict,
    page_num: int,
    *,
    global_kv_pairs: list[dict] | None = None,
) -> str:
    """Return a prompt-ready string combining markdown + structured metadata.

    Parameters
    ----------
    extraction:
        Per-page extraction dict (from result.json) containing markdown,
        signatures, key_value_pairs, selection_marks, handwritten_count, etc.
    page_num:
        1-based page number.
    global_kv_pairs:
        Optional document-level key-value pairs (from top-level result.json).
    """
    markdown = extraction.get("markdown", "")[:_MAX_MARKDOWN_CHARS]

    sections = [f"PAGE CONTENT (page {page_num}):\n```\n{markdown}\n```"]

    metadata_lines: list[str] = []

    # --- Signatures ---
    sigs = extraction.get("signatures", [])
    if sigs:
        sig_details = []
        for s in sigs[:_MAX_SIGNATURES]:
            label = s.get("label", "").strip()
            status = s.get("status", "unsigned")
            conf = s.get("confidence", 0.0)
            desc = f"{status}"
            if label:
                desc = f'"{label}": {status}'
            if conf:
                desc += f" (conf={conf:.2f})"
            sig_details.append(desc)
        metadata_lines.append(
            f"Signatures detected: {len(sigs)} — {'; '.join(sig_details)}"
        )
    else:
        metadata_lines.append("Signatures detected: 0 (no signature fields found on this page)")

    # --- Handwriting ---
    hw_count = extraction.get("handwritten_count", 0)
    if hw_count > 0:
        metadata_lines.append(
            f"Handwritten regions: {hw_count} words identified as handwritten "
            f"(NOTE: handwritten text is often garbled by OCR — unusual text "
            f"in signature/date columns is likely valid handwriting, not errors)"
        )
    else:
        metadata_lines.append(
            "Handwritten regions: 0 (all text appears to be printed/typed)"
        )

    # --- Key-Value Pairs (page-level) ---
    page_kv = extraction.get("key_value_pairs", [])
    if page_kv:
        kv_lines = []
        for kv in page_kv[:_MAX_KV_PAIRS]:
            key = kv.get("key", "").strip()
            value = kv.get("value", "").strip()
            if key:
                if value:
                    is_dash = value.replace("-", "").replace("—", "").strip() == ""
                    if is_dash:
                        entry = f'  "{key}": "{value}" [dash annotation = not applicable]'
                    else:
                        entry = f'  "{key}": "{value}"'
                else:
                    entry = f'  "{key}": [empty/blank]'
                kv_lines.append(entry)
        if kv_lines:
            metadata_lines.append("Key-value pairs (form fields):\n" + "\n".join(kv_lines))
    else:
        metadata_lines.append("Key-value pairs: none detected on this page")

    # --- Global Key-Value Pairs (document-level, for context) ---
    if global_kv_pairs:
        global_for_page = [
            kv for kv in global_kv_pairs
            if kv.get("page_num", 0) == page_num
        ]
        if global_for_page:
            gkv_lines = []
            for kv in global_for_page[:_MAX_KV_PAIRS]:
                key = kv.get("key", "").strip()
                value = kv.get("value", "").strip()
                if key:
                    entry = f'  "{key}": "{value}"' if value else f'  "{key}": [empty/blank]'
                    gkv_lines.append(entry)
            if gkv_lines:
                metadata_lines.append(
                    "Additional document-level fields for this page:\n" + "\n".join(gkv_lines)
                )

    # --- Selection Marks ---
    sel_marks = extraction.get("selection_marks", [])
    if sel_marks:
        selected = sum(1 for m in sel_marks if m.get("state") == "selected")
        unselected = len(sel_marks) - selected
        metadata_lines.append(
            f"Selection marks (checkboxes): {len(sel_marks)} total — "
            f"{selected} checked (☑), {unselected} unchecked (☐)"
        )
    else:
        metadata_lines.append("Selection marks: none on this page")

    # --- Tables ---
    tables = extraction.get("tables", [])
    if tables:
        metadata_lines.append(f"Tables: {len(tables)} table(s) detected on this page")
    else:
        metadata_lines.append("Tables: none detected on this page")

    sections.append(
        "STRUCTURED METADATA (from document intelligence extraction):\n"
        + "\n".join(f"- {line}" for line in metadata_lines)
    )

    return "\n\n".join(sections)


def classify_page_type(extraction: dict) -> str:
    """Classify a page based on its metadata for rule applicability filtering.

    Returns one of: "form", "printed", "cover", "index", "content".
    """
    hw_count = extraction.get("handwritten_count", 0)
    kv_pairs = extraction.get("key_value_pairs", [])
    sigs = extraction.get("signatures", [])
    sel_marks = extraction.get("selection_marks", [])
    markdown = extraction.get("markdown", "")
    page_num = extraction.get("page_num", 0)

    if page_num == 1:
        md_lower = markdown[:500].lower()
        if any(kw in md_lower for kw in ["batch", "product", "manufacturing", "record"]):
            return "cover"

    if hw_count > 0 or len(kv_pairs) > 2 or len(sigs) > 0 or len(sel_marks) > 2:
        return "form"

    md_lower = markdown[:300].lower()
    if any(kw in md_lower for kw in ["table of contents", "index", "list of"]):
        return "index"

    if hw_count == 0 and len(kv_pairs) == 0 and len(sigs) == 0:
        return "printed"

    return "content"
