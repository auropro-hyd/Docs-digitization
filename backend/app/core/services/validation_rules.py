"""Custom validation rules for extraction plausibility checks.

These rules catch errors that OCR engines won't flag -- logical inconsistencies,
format violations, and missing required data.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.core.services.confidence import ValidationResults


def validate_page_extraction(extraction: dict) -> ValidationResults:
    """Run all validation rules against a page's extracted data."""
    results = ValidationResults()
    markdown = extraction.get("markdown", "")

    _check_date_plausibility(markdown, results)
    _check_quantity_ranges(markdown, results)
    _check_not_empty(markdown, results)

    return results


def _check_date_plausibility(markdown: str, results: ValidationResults):
    """Flag dates that look implausible (too old, too future)."""
    results.rules_checked += 1
    date_pattern = r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b"
    matches = re.findall(date_pattern, markdown)

    current_year = datetime.now().year
    has_bad_date = False

    for match in matches:
        year_str = match[2]
        year = int(year_str)
        if len(year_str) == 2:
            year += 2000 if year < 50 else 1900
        if year < 2000 or year > current_year + 1:
            has_bad_date = True
            results.failures.append(f"Implausible date year: {year}")

    if not has_bad_date:
        results.rules_passed += 1


def _check_quantity_ranges(markdown: str, results: ValidationResults):
    """Flag impossibly large or negative quantities."""
    results.rules_checked += 1
    qty_pattern = r"(?:qty|quantity|weight|volume)[:\s]*(-?[\d,.]+)\s*(?:kg|g|ml|l|mg)\b"
    matches = re.findall(qty_pattern, markdown, re.IGNORECASE)

    has_bad_qty = False
    for match in matches:
        try:
            val = float(match.replace(",", ""))
            if val < 0 or val > 1_000_000:
                has_bad_qty = True
                results.failures.append(f"Suspicious quantity: {val}")
        except ValueError:
            pass

    if not has_bad_qty:
        results.rules_passed += 1


def _check_not_empty(markdown: str, results: ValidationResults):
    """Flag pages with suspiciously little content."""
    results.rules_checked += 1
    stripped = markdown.strip()
    if len(stripped) > 20:
        results.rules_passed += 1
    else:
        results.failures.append(f"Page content too short ({len(stripped)} chars)")
