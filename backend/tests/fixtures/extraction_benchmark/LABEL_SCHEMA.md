# Extraction Benchmark Label Schema

Each benchmark sample must define labels as:

```json
{
  "sample_id": "sample_bpr_packet_01",
  "document_type": "batch_record",
  "fields": [
    {
      "field_id": "batch_no",
      "raw_value": "2538104192",
      "normalized_value": "2538104192",
      "expected_page": 1,
      "expected_region": [0.52, 0.18, 0.76, 0.22],
      "criticality": "critical",
      "placeholder_allowed": false,
      "handwriting_expected": false,
      "notes": "Header row field"
    }
  ]
}
```

Field requirements:

- `field_id`: canonical field identifier used by benchmark matching.
- `raw_value`: expected value as seen in document.
- `normalized_value`: canonical normalized value used for exact-match metric.
- `expected_page`: expected page index (1-based) when location-sensitive.
- `expected_region`: normalized box `[x1, y1, x2, y2]` for wrong-region checks.
- `criticality`: `critical|major|minor|observation`.
- `placeholder_allowed`: marks allowed placeholder semantics.
- `handwriting_expected`: when `true`, missing handwriting evidence is taxonomy `handwriting`.
