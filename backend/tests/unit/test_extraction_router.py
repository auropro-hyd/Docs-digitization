from app.core.services.extraction_router import route_extraction_strategy


def test_route_extraction_strategy_picks_primary_family_by_weighted_pages():
    packet_sections = [
        {
            "section_id": "pkt_1",
            "extraction_family": "bpr_core",
            "extraction_family_confidence": 0.9,
            "start_page": 1,
            "end_page": 40,
        },
        {
            "section_id": "pkt_2",
            "extraction_family": "sieving_reports",
            "extraction_family_confidence": 0.9,
            "start_page": 170,
            "end_page": 185,
        },
    ]
    route = route_extraction_strategy(packet_sections)
    assert route["primary_family"] == "bpr_core"
    assert "batch_no" in route["critical_fields"]
    assert route["fallback_order"][0] == "bpr_core"
