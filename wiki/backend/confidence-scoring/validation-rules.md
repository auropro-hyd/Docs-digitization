# Validation Rules

> **Code reference:** [`backend/app/core/services/validation_rules.py`](../../../backend/app/core/services/validation_rules.py)

Custom validation rules catch **logical and domain-specific errors** that OCR engines will never flag. An OCR engine might perfectly recognize the text "32/13/2025" — but that date is impossible. These rules provide the reality-check layer that turns raw OCR confidence into actionable trust.

---

## Purpose

OCR engines measure how confident they are that they *read* something correctly. Validation rules measure whether what was read *makes sense*. The two are complementary:

| Layer | Question | Example Catch |
|---|---|---|
| OCR confidence | "Did I read this correctly?" | Blurry "8" vs "3" |
| Validation rules | "Does this value make sense?" | Date of "2098", negative quantity, empty page |

Together they feed into the [composite confidence scorer](./composite-scorer.md) where validation carries **30% weight**.

---

## Entry Point

```python
def validate_page_extraction(extraction: dict) -> ValidationResults:
```

Takes a single page extraction dict (must contain a `"markdown"` key) and runs all registered rules against it. Returns a `ValidationResults` object.

---

## ValidationResults

```python
@dataclass
class ValidationResults:
    rules_checked: int = 0
    rules_passed: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.rules_checked == 0:
            return 1.0
        return self.rules_passed / self.rules_checked
```

| Field | Description |
|---|---|
| `rules_checked` | Total number of rules evaluated |
| `rules_passed` | Number of rules that passed |
| `failures` | Human-readable descriptions of each failure |
| `pass_rate` | `rules_passed / rules_checked` (1.0 if no rules checked) |

The `pass_rate` is the signal consumed by the composite scorer.

---

## Current Rules

### 1. Date Plausibility

> `_check_date_plausibility(markdown, results)`

**Regex:** `\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b`

Scans the page markdown for date patterns and flags any date where:

- **Year < 2000** — pharmaceutical manufacturing records before 2000 are extremely unlikely in this system.
- **Year > current year + 1** — future dates beyond next year are implausible.

Two-digit year handling:
- `< 50` → interpreted as `2000 + year` (e.g. `25` → `2025`)
- `≥ 50` → interpreted as `1900 + year` (e.g. `98` → `1998`, which would fail)

**Example failures:**
- `"Batch date: 15/06/1998"` — year before 2000
- `"Expiry: 01/01/2098"` — year too far in the future

### 2. Quantity Range

> `_check_quantity_ranges(markdown, results)`

**Regex:** `(?:qty|quantity|weight|volume)[:\s]*(-?[\d,.]+)\s*(?:kg|g|ml|l|mg)\b`

Looks for labeled quantities with units and flags values that are:

- **Negative** — negative quantities indicate OCR misread or data error.
- **Greater than 1,000,000** — impossibly large values for pharmaceutical batch records.

**Example failures:**
- `"Quantity: -50 kg"` — negative value
- `"Weight: 9,999,999 g"` — exceeds 1M threshold

### 3. Content Not Empty

> `_check_not_empty(markdown, results)`

Flags pages where the stripped markdown content has **≤ 20 characters**. This catches:

- Blank pages the OCR returned near-empty results for.
- Pages where the OCR produced only whitespace or a few stray characters.
- Separator pages or cover sheets with no meaningful content.

**Example failure:**
- A page containing only `"Page 5"` (7 characters < 20 threshold)

---

## How Validation Feeds Into Confidence

The validation `pass_rate` is one of four signals in the [composite confidence scorer](./composite-scorer.md):

```
composite = 0.30 * docling_mean
           + 0.25 * azure_di_min_word
           + 0.15 * marker_table_norm
           + 0.30 * validation_pass_rate   ← this
```

With 30% weight, validation has significant influence:

| Rules Passed | pass_rate | Impact on Composite |
|---|---|---|
| 3/3 | 1.0 | +0.30 |
| 2/3 | 0.67 | +0.20 |
| 1/3 | 0.33 | +0.10 |
| 0/3 | 0.0 | +0.00 |

A page failing all three rules loses the entire 30% validation contribution, which alone can drop it from `high` to `medium` confidence tier.

---

## Future Rules

The following rules are planned for upcoming iterations:

### Batch Number Consistency

Check that the batch number appearing on each page matches the batch number declared on the first page (or cover sheet). Flag any page where the batch number differs — this could indicate a scanning error where pages from different batches were interleaved.

### Required Fields Not Blank

For critical document steps (e.g. weighing, mixing, compression), verify that mandatory fields are not blank:
- Operator signature
- Verification signature
- Date and time
- Equipment ID

This requires section-type awareness — the rule needs to know which fields are mandatory for which section type.

### Cross-Reference Validation

Verify that quantities referenced in different sections are consistent:
- Input material quantities in the dispensing section should match the quantities consumed in the manufacturing section.
- Yield calculations should be arithmetically correct given the input quantities.
- In-process check results should fall within specified acceptance criteria.

---

## Adding a New Rule

To add a new validation rule:

1. Create a private function following the pattern:

```python
def _check_new_rule(markdown: str, results: ValidationResults) -> None:
    results.rules_checked += 1
    # ... perform check ...
    if passes:
        results.rules_passed += 1
    else:
        results.failures.append("Description of what failed")
```

2. Call it from `validate_page_extraction`:

```python
def validate_page_extraction(extraction: dict) -> ValidationResults:
    results = ValidationResults()
    markdown = extraction.get("markdown", "")
    _check_date_plausibility(markdown, results)
    _check_quantity_ranges(markdown, results)
    _check_not_empty(markdown, results)
    _check_new_rule(markdown, results)  # ← add here
    return results
```

The rule will automatically be included in the pass_rate and feed into the composite confidence score.

---

## Related Documentation

- [Composite Confidence Scorer](./composite-scorer.md) — consumes validation results as 30% of the composite score
- [Document Processing Workflow](../workflow/document-processing.md) — where validation is invoked during `merge_ocr_results`
- [HITL Flow](../workflow/hitl-flow.md) — how low confidence (including validation failures) triggers human review
