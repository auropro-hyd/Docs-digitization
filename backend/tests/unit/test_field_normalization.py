from app.core.services.field_normalization import evaluate_placeholder, normalize_field_value, normalize_kv_record


def test_normalize_field_value_applies_date_and_identifier_rules():
    normalized_date, date_reasons = normalize_field_value("effective_date", "09-04-2025")
    assert normalized_date == "09/04/2025"
    assert "normalized_date_format" in date_reasons

    normalized_id, id_reasons = normalize_field_value("batch_no", " ab - 123 ")
    assert normalized_id == "AB-123"
    assert "normalized_identifier" in id_reasons


def test_placeholder_policy_allows_and_rejects_by_field():
    is_placeholder, allowed, reason = evaluate_placeholder("sample_sent_to_qcd", "-", family="manufacturing_checklists")
    assert is_placeholder is True
    assert allowed is True
    assert reason == "allowed_by_field_policy"

    is_placeholder, allowed, reason = evaluate_placeholder("batch_no", "-", family="bpr_core")
    assert is_placeholder is True
    assert allowed is False
    assert reason == "not_allowed_for_field"


def test_normalize_kv_record_emits_raw_normalized_and_policy_metadata():
    kv = {"key": "Batch No", "value": " ab - 123 "}
    out = normalize_kv_record(kv, family="bpr_core")
    assert out["field_id"] == "batch_no"
    assert out["raw_value"] == " ab - 123 "
    assert out["normalized_value"] == "AB-123"
    assert isinstance(out["normalization_reason_codes"], list)
    assert out["is_placeholder"] is False
