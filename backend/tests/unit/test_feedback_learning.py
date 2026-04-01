from app.core.services.feedback_learning import build_correction_artifacts, evaluate_retraining_trigger


def test_build_correction_artifacts_creates_confusion_map():
    corrections = [
        {"field_id": "batch_no", "before_value": "B-009", "after_value": "B-001", "criticality": "critical"},
        {"field_id": "batch_no", "before_value": "B-009", "after_value": "B-001", "criticality": "critical"},
        {"field_id": "product_name", "before_value": "Paracitamol", "after_value": "Paracetamol", "criticality": "major"},
    ]
    out = build_correction_artifacts(corrections)
    assert out["summary"]["total_corrections"] == 3
    assert "B-009 -> B-001" in out["ocr_confusion_map"]
    assert out["correction_dictionary"]["field_updates"]["batch_no"] == 2


def test_retraining_trigger_fires_on_volume_or_critical_rate():
    corrections = [
        {"field_id": "batch_no", "before_value": "A", "after_value": "B", "criticality": "critical"}
        for _ in range(25)
    ]
    trigger = evaluate_retraining_trigger(
        corrections,
        threshold_correction_rate=0.05,
        threshold_critical_rate=0.02,
        min_corrections_for_trigger=20,
    )
    assert trigger["should_trigger_retraining"] is True
