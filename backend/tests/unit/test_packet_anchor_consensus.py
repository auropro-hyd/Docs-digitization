from app.core.services.packet_anchor_consensus import evaluate_packet_anchor_consensus, page_anchor_issues


def test_packet_anchor_consensus_flags_conflicting_batch_number():
    extractions = [
        {
            "page_num": 1,
            "key_value_pairs": [
                {"key": "Batch No", "value": "B-001"},
                {"key": "Product Name", "value": "Paracetamol Tablets"},
            ],
        },
        {
            "page_num": 2,
            "key_value_pairs": [
                {"key": "Batch Number", "value": "B-001"},
            ],
        },
        {
            "page_num": 3,
            "key_value_pairs": [
                {"key": "Batch No", "value": "B-009"},
            ],
        },
    ]

    summary = evaluate_packet_anchor_consensus(extractions)
    assert summary["status"] == "warning"
    assert "batch_no" in summary["anchors"]
    assert summary["anchors"]["batch_no"]["consistent"] is False

    issues = page_anchor_issues(3, summary)
    assert len(issues) == 1
    assert issues[0]["anchor_id"] == "batch_no"
    assert issues[0]["observed"] == "B-009"
