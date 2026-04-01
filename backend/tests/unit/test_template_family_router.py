from app.core.services.template_family_router import route_template_family


def test_template_family_router_matches_aliases():
    packet_sections = [{"name": "Batch Production and Control Record", "start_page": 1, "end_page": 20}]
    out = route_template_family(packet_sections, extraction_family="bpr_core")
    assert out["template_family"] == "bpr_core"
    assert out["enable_custom_model"] is True
