from app.core.services.query_field_merge import merge_query_fields


def test_merge_query_fields_replaces_disallowed_placeholder():
    kv_records = [
        {
            "field_id": "batch_no",
            "normalized_value": "-",
            "confidence": 0.75,
            "is_placeholder": True,
            "placeholder_allowed": False,
        }
    ]
    query_records = [
        {
            "field_id": "batch_no",
            "value": "AB-001",
            "normalized_value": "AB-001",
            "confidence": 0.82,
        }
    ]

    merged, trace = merge_query_fields(kv_records, query_records)
    assert merged[0]["normalized_value"] == "AB-001"
    assert merged[0]["source"] == "query_fields_override"
    assert trace[0]["action"] == "replaced_with_query"


def test_merge_query_fields_adds_missing_field():
    merged, trace = merge_query_fields([], [{"field_id": "mpcr_number", "value": "M-100", "confidence": 0.9}])
    assert len(merged) == 1
    assert merged[0]["field_id"] == "mpcr_number"
    assert trace[0]["action"] == "added_from_query"
