from app.core.services.cross_field_consistency import evaluate_cross_field_consistency


def test_cross_field_consistency_detects_anchor_mismatch():
    extractions = [
        {"page_num": 1, "key_value_pairs": [{"field_id": "batch_no", "normalized_value": "B-001"}]},
        {"page_num": 4, "key_value_pairs": [{"field_id": "batch_no", "normalized_value": "B-009"}]},
    ]
    out = evaluate_cross_field_consistency(extractions)
    assert out["status"] == "warning"
    assert out["discrepancy_count"] >= 1
    assert any(d["type"] == "anchor_mismatch" for d in out["discrepancies"])
