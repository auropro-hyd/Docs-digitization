from app.core.services.review_priority import build_review_priority_queue


def test_review_priority_ranks_low_confidence_critical_fields_first():
    extractions = [
        {
            "page_num": 1,
            "parser_repair_severity_score": 1,
            "packet_anchor_issues": [],
            "key_value_pairs": [{"field_id": "batch_no", "criticality": "critical", "calibrated_confidence": 0.42}],
        },
        {
            "page_num": 2,
            "parser_repair_severity_score": 0,
            "packet_anchor_issues": [],
            "key_value_pairs": [{"field_id": "remarks", "criticality": "minor", "calibrated_confidence": 0.91}],
        },
    ]
    queue = build_review_priority_queue(extractions, {"discrepancies": []})
    assert len(queue) >= 2
    assert queue[0]["page_num"] == 1
    assert queue[0]["priority_score"] > queue[1]["priority_score"]
