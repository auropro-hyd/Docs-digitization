from app.core.services.extraction_family_policy import enrich_packet_sections_with_family, resolve_family


def test_resolve_family_matches_checklist_keywords():
    fam, conf, reason = resolve_family("Checklist for vacuum tray drier operation")
    assert fam == "manufacturing_checklists"
    assert conf > 0
    assert "matched_keywords" in reason


def test_enrich_packet_sections_adds_family_fields():
    sections = [
        {"section_id": "pkt_sec_001", "name": "Batch Production and Control Record", "start_page": 1, "end_page": 35},
        {"section_id": "pkt_sec_002", "name": "Sieving Analysis Report", "start_page": 169, "end_page": 185},
    ]
    out = enrich_packet_sections_with_family(sections)
    assert len(out) == 2
    assert out[0]["extraction_family"] == "bpr_core"
    assert out[1]["extraction_family"] == "sieving_reports"
