from app.core.services.localized_corruption_risk import compute_packet_corruption_risk


def test_compute_packet_corruption_risk_marks_high_for_multiple_signals():
    extractions = [
        {
            "page_num": 1,
            "parser_repair_severity_score": 0,
            "selection_semantics": {"has_ambiguity": False},
            "packet_anchor_issues": [],
            "handwritten_count": 0,
        },
        {
            "page_num": 2,
            "parser_repair_severity_score": 10,
            "selection_semantics": {"has_ambiguity": True},
            "packet_anchor_issues": [{"anchor_id": "batch_no"}],
            "handwritten_count": 25,
        },
    ]
    confidence_scores = {1: 0.92, 2: 0.41}

    risk = compute_packet_corruption_risk(extractions, confidence_scores)
    assert risk["status"] == "needs_attention"
    assert risk["pages"][1]["level"] == "low"
    assert risk["pages"][2]["level"] == "high"
    assert risk["pages"][2]["score"] > risk["pages"][1]["score"]
