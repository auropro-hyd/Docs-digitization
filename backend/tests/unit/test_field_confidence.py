from app.core.services.field_confidence import calibrate_kv_confidence


def test_calibrated_confidence_penalizes_disallowed_placeholder():
    kv = {
        "field_id": "batch_no",
        "confidence": 0.9,
        "is_placeholder": True,
        "placeholder_allowed": False,
    }
    out = calibrate_kv_confidence(
        kv,
        parser_repair_severity_score=2,
        selection_ambiguity=False,
        anchor_issue_count=0,
        critical_fields=["batch_no"],
    )
    assert out["criticality"] == "critical"
    assert out["calibrated_confidence"] < 0.7
    assert out["confidence_decomposition"]["placeholder_penalty"] > 0
