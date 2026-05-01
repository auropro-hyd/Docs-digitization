# Quickstart: Verify Per-Section Document Type

## Run the tests

```bash
cd backend
pytest tests/compliance/test_per_section_doc_type.py -v
```

All tests should pass.

## Verify on the sample document

```bash
# 1. Bust the segmentation cache
rm backend/data/documents/90ec18f4-1f29-4613-92e8-c2325bec9968/segmentation.json

# 2. Re-run compliance pipeline (via API or directly)
# The next audit run will regenerate segmentation.json

# 3. Inspect the new segmentation output
cat backend/data/documents/90ec18f4-1f29-4613-92e8-c2325bec9968/segmentation.json | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data['sections']:
    print(f\"pages {s['start_page']:3d}-{s['end_page']:3d}  doc_type={s.get('document_type','MISSING')!r:25s}  section_type={s['section_type']}\")
"
```

## Expected output

Each section should have a non-empty `document_type`. Example:

```
pages   1- 35  doc_type='batch_record'           section_type=batch_production_and_control_record
pages  36- 46  doc_type='raw_material_request'   section_type=raw_material_request_and_issue
pages  48- 67  doc_type='scada_report'           section_type=equipment_monitoring_data
pages  70- 70  doc_type='ipc_report'             section_type=in_process_samples_report
pages  71- 79  doc_type='analysis_report'        section_type=instrument_analysis_report
pages  80- 97  doc_type='operation_checklist'    section_type=manufacturing_checklist
pages  98-101  doc_type='certificate'            section_type=certificate_of_analysis
pages 102-115  doc_type='qc_analytical_package'  section_type=qc_analytical_data_review_checklist
```

## Check the prompt (quick sanity)

```python
from app.compliance.segmentation import _build_segmentation_prompt
prompt = _build_segmentation_prompt(extractions=[{"page_num": 1, "markdown": "test"}], filename="test.pdf")
assert "batch_record" in prompt
assert "operation_checklist" in prompt
assert "section_aliases" not in prompt
assert "key_value_pairs" not in prompt.lower()
print("Prompt check passed")
```
