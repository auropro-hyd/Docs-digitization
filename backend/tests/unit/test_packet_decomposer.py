from app.core.services.packet_decomposer import decompose_packet, sections_as_dicts


def test_decompose_packet_splits_on_page_counter_reset():
    pages = {
        1: "# Batch Production Record\nPage 1 of 35\nProduct Name",
        2: "Batch Production Record\nPage 2 of 35\nOperations",
        3: "# Packing Material Request\nPage 1 of 1\nRequest No.",
        4: "Packing Material Request\nPage 1 of 1\nContinuation",
    }
    sections = decompose_packet(pages)
    assert len(sections) >= 2
    assert sections[0].start_page == 1
    assert sections[0].end_page == 2
    assert sections[1].start_page == 3


def test_decompose_packet_returns_dicts():
    pages = {1: "Header A\nPage 1 of 1", 2: "Header B\nPage 1 of 1"}
    sections = decompose_packet(pages)
    payload = sections_as_dicts(sections)
    assert isinstance(payload, list)
    assert all("section_id" in item for item in payload)
