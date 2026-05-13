# Quickstart: Client-Aligned Compliance Report (Spec 008)

Goal: from a clean checkout to seeing the new rule-centric PDF on disk.

## Prerequisites

- A `data/documents/{doc_id}/compliance_result.json` produced by a recent compliance run on `main`. If you don't have one, run a compliance pipeline against any fixture doc first.
- `weasyprint` installed (`pip install weasyprint` in the backend venv; brings in cairo + pango). macOS: `brew install cairo pango gdk-pixbuf libffi`.
- A generic compliance-suite logo PNG at `backend/app/compliance/report_renderer/assets/logo.png` (or set `AT_COMPLIANCE__REPORT_LOGO_PATH` to your own).

## End-to-end smoke test (after implementation)

```bash
# 1. Pick a doc that already has compliance_result.json on disk.
DOC_ID=$(ls backend/data/documents/ | head -1)

# 2. (Optional) Warm the mitigation cache so the export has populated
#    Mitigation cells on every non-compliant / uncertain row.
curl -sS -X POST http://localhost:8100/api/compliance/$DOC_ID/mitigation/synthesize \
  -H 'Content-Type: application/json' \
  -d '{}' | jq

# 3. Render the PDF.
curl -sS -o /tmp/report.pdf \
  "http://localhost:8100/api/compliance/$DOC_ID/export?format=pdf"

# 4. Confirm shape — text-extract should NOT contain "Score:" or "overall_score".
pdftotext /tmp/report.pdf - | grep -iE 'score' && echo "FAIL: score leaked" || echo "OK"

# 5. Confirm three statuses present in the rendered text.
pdftotext /tmp/report.pdf - | grep -E 'Compliant|Action Required|Needs Attention'

# 6. Open it in a viewer.
open /tmp/report.pdf
```

## Visual diff against the reference

The reference PDF lives at:
```
context/2538104192 1/Checklist based Review_Compliance_Report (3).pdf
```

Manual 9-of-10 check (SC-002):

| Check | Pass condition |
|---|---|
| Header band | Generic logo top-left, product name ("BMR Compliance Intelligence Suite" or `AT_COMPLIANCE__REPORT_PRODUCT_NAME`) centered, "TITLE OF DOCUMENT" top-left text, "Document is Draft" top-right |
| Metadata table | Document / Product / Batch No / Date Of Validation rows |
| Column count | Exactly 5 |
| Column order | Question · Compliance · Evidence From Document · Detailed Evidence · Mitigation |
| Badge wording | "Compliant", "Action Required", "Needs Attention" (NOT "Non-Compliant" / "Uncertain") |
| Page-range syntax | "PAGE:N", "PAGE:N, M, K", "PAGE:N to M" (range when ≥3 contiguous) |
| Compliant rows | Evidence-from-doc cell EMPTY; Detailed Evidence is a summary; Mitigation says "Not Applicable" |
| Non-compliant / Attention rows | Pages populated; Mitigation populated with 1-4 sentences of action |
| Footer | Disclaimer line on every page; operator name + generation timestamp |
| No-score guarantee | Word "Score" appears nowhere in the rendered PDF |

## Frontend smoke test (after Phase 4)

```bash
# Backend running on :8100; frontend on :3100.
open http://localhost:3100/compliance?doc=$DOC_ID
```

- Top section: `AgentScorecard` panels + `ExecutiveSummary` render with scores (unchanged from today — scores are internal-use, kept on-screen).
- Body section: the new `RuleTable` renders below, replacing the previous flat findings list.
- Click any rule row → expand drawer opens with per-page detail + HITL controls.
- "Approve" on a Needs Attention row → badge flips to Compliant in place.
- Click "Preview" → modal opens with the iframed PDF preview — note the PDF has NO scores even though the screen behind it does.
- Click "Download" → dropdown lets you choose `PDF` (default) / `HTML` / `Markdown`. The downloaded file is score-free.

## API smoke test (after Phase 2)

```bash
# Returns the rule-centric JSON shape that the frontend consumes.
curl -sS "http://localhost:8100/api/compliance/$DOC_ID/report-rows" | jq '.stats'
# Expected:
# {
#   "row_count": 21,
#   "compliant_count": 18,
#   "action_required_count": 2,
#   "needs_attention_count": 1,
#   "excluded_not_applicable_count": 4
# }
```

## Round-trip equality (SC-003)

```bash
.venv/bin/python -c "
import json
from pypdf import PdfReader

data = json.load(open('backend/data/documents/$DOC_ID/compliance_result.json'))
expected_rules = {
    ev['rule_id']
    for ar in data['agent_reports']
    for ev in ar['all_evaluations']
    if ev['status'] != 'not_applicable'
}

text = ''.join(p.extract_text() for p in PdfReader('/tmp/report.pdf').pages)
pdf_rules = {rid for rid in expected_rules if rid in text}

missing = expected_rules - pdf_rules
print(f'rules in JSON (applicable): {len(expected_rules)}')
print(f'rules in PDF text: {len(pdf_rules)}')
print(f'missing from PDF: {missing}')
"
```

`missing` must be empty.

## Telemetry sanity (SC-004 / SC-006)

After running an export:

```bash
.venv/bin/python -c "
import json
ev = [
    e for e in json.load(open('backend/data/documents/$DOC_ID/telemetry-compliance.json'))['events']
    if e['event'] in ('compliance.report_rendered', 'compliance.mitigation_synthesised')
]
for e in ev[-5:]:
    print(e['event'], e['fields'])
"
```

Expect one `report_rendered` event per export call with `row_count`, `compliant_count`, `action_required_count`, `needs_attention_count`, `format`, `render_duration_ms`. Mitigation events appear only when the synthesise endpoint was called.

## Known limitations (v1)

- Compliant-row summary text is a deterministic boilerplate (`"Evaluated across N pages..."`). Rich per-rule templates are a follow-up (requires `AuditRule.summary_template` field, out of scope here).
- All-English only.
- No DOCX / XLSX export.
- No multi-doc bulk export.
- A11y tagging exists (weasyprint default) but not WCAG-audited.
