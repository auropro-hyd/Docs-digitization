from app.core.services.custom_model_shadow import summarize_shadow_delta


def test_custom_model_shadow_summarizes_changed_fields():
    baseline = [
        {"field_id": "batch_no", "normalized_value": "B-001"},
        {"field_id": "sample_sent_to_qcd", "normalized_value": "-"},
    ]
    custom = [
        {"field_id": "batch_no", "normalized_value": "B-001"},
        {"field_id": "sample_sent_to_qcd", "normalized_value": "yes"},
    ]
    out = summarize_shadow_delta(baseline, custom)
    assert out["changed_fields"] == 1
    assert out["custom_field_count"] == 2
