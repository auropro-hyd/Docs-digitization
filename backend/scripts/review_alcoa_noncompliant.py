"""Quick-review script: ALCOA non-compliant findings vs actual page content.

Usage:
    python3 scripts/review_alcoa_noncompliant.py <doc_id>

    # or with the hardcoded default:
    python3 scripts/review_alcoa_noncompliant.py

Reads:
    data/documents/<doc_id>/page_eval_debug.json  — raw LLM evaluations
    data/documents/<doc_id>/result.json           — page extractions (markdown + metadata)

Prints each non-compliant / uncertain ALCOA finding alongside the actual
page markdown so you can spot hallucinated evidence at a glance.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

DOC_DIR = Path(__file__).parent.parent / "data" / "documents"
DEFAULT_DOC_ID = "90ec18f4-1f29-4613-92e8-c2325bec9968"

SEPARATOR = "=" * 80
SUBSEP = "-" * 60

NON_COMPLIANT_STATUSES = {"non_compliant", "uncertain"}

# How many chars of page markdown to show (set to 0 for unlimited)
PAGE_PREVIEW_CHARS = 3000


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_page_map(result: dict) -> dict[int, dict]:
    """Return {page_num: extraction} from result.json."""
    return {ext["page_num"]: ext for ext in result.get("extractions", [])}


def print_finding(finding: dict, extraction: dict | None) -> None:
    print(SEPARATOR)
    print(f"  PAGE {finding['page_num']}  |  {finding['rule_id']}  |  {finding['status'].upper()}  |  severity: {finding['severity']}")
    print(SEPARATOR)

    print("\n[ LLM REASONING ]")
    print(textwrap.fill(finding["reasoning"] or "(none)", width=80, initial_indent="  ", subsequent_indent="  "))

    print("\n[ LLM EVIDENCE (claimed) ]")
    print(textwrap.fill(finding["evidence"] or "(none)", width=80, initial_indent="  ", subsequent_indent="  "))

    print(f"\n  confidence: {finding['confidence']}")
    print(f"  applicability_trace: {', '.join(finding['applicability_trace']) or '(none)'}")

    if extraction is None:
        print("\n[ PAGE CONTENT ]  *** extraction not found ***")
        return

    markdown = extraction.get("markdown", "").strip()
    kv_pairs = extraction.get("key_value_pairs", [])
    sigs = extraction.get("signatures", [])
    hw_count = extraction.get("handwritten_count", 0)
    selection_marks = extraction.get("selection_marks", [])

    print(f"\n[ PAGE METADATA ]")
    print(f"  handwritten_count: {hw_count}")
    print(f"  signatures detected: {len(sigs)}")
    print(f"  key_value_pairs: {len(kv_pairs)}")
    print(f"  selection_marks: {len(selection_marks)}")
    if kv_pairs:
        print("  key-value pairs:")
        for kv in kv_pairs[:20]:
            key = kv.get("key", {}).get("content", "") if isinstance(kv.get("key"), dict) else kv.get("key", "")
            val = kv.get("value", {}).get("content", "") if isinstance(kv.get("value"), dict) else kv.get("value", "")
            print(f"    {key!r}: {val!r}")
        if len(kv_pairs) > 20:
            print(f"    ... ({len(kv_pairs) - 20} more)")

    print(f"\n[ PAGE MARKDOWN (first {PAGE_PREVIEW_CHARS} chars) ]")
    preview = markdown[:PAGE_PREVIEW_CHARS] if PAGE_PREVIEW_CHARS else markdown
    if not preview:
        print("  (empty)")
    else:
        for line in preview.splitlines():
            print(f"  {line}")
        if PAGE_PREVIEW_CHARS and len(markdown) > PAGE_PREVIEW_CHARS:
            print(f"  ... [{len(markdown) - PAGE_PREVIEW_CHARS} chars truncated]")

    print()


def main() -> None:
    doc_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DOC_ID
    doc_dir = DOC_DIR / doc_id

    debug_path = doc_dir / "page_eval_debug.json"
    result_path = doc_dir / "result.json"

    if not debug_path.exists():
        print(f"ERROR: {debug_path} not found. Run a compliance audit with debug_page_eval=true first.")
        sys.exit(1)
    if not result_path.exists():
        print(f"ERROR: {result_path} not found.")
        sys.exit(1)

    debug = load_json(debug_path)
    result = load_json(result_path)
    page_map = build_page_map(result)

    alcoa_entries = debug.get("agents", {}).get("alcoa", [])
    non_compliant = [e for e in alcoa_entries if e["status"] in NON_COMPLIANT_STATUSES]

    print(f"\nDoc:    {doc_id}")
    print(f"Total ALCOA evaluations: {len(alcoa_entries)}")
    print(f"Non-compliant / uncertain: {len(non_compliant)}")

    if not non_compliant:
        print("Nothing to review.")
        return

    # Group summary
    print(f"\nSummary:")
    for e in non_compliant:
        print(f"  page={e['page_num']}  {e['rule_id']}  {e['status']}  [{e['severity']}]")

    print()
    for finding in non_compliant:
        extraction = page_map.get(finding["page_num"])
        print_finding(finding, extraction)


if __name__ == "__main__":
    main()
