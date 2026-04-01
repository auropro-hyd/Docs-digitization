import json
from pathlib import Path

import pytest

from tests.benchmark.label_schema import GoldLabelDocument


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "extraction_benchmark"
    / "labels"
    / "sample_bpr_packet_01.labels.json"
)


def test_gold_label_schema_accepts_sample_fixture():
    payload = json.loads(FIXTURE.read_text())
    doc = GoldLabelDocument.model_validate(payload)
    assert doc.sample_id == "sample_bpr_packet_01"
    assert len(doc.fields) >= 1


def test_gold_label_schema_rejects_invalid_region():
    payload = json.loads(FIXTURE.read_text())
    payload["fields"][0]["expected_region"] = [0.6, 0.2, 0.1, 0.3]
    with pytest.raises(ValueError):
        GoldLabelDocument.model_validate(payload)
