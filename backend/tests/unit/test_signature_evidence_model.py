from types import SimpleNamespace

from app.adapters.ocr.azure_di import _detect_signatures


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_signature_detection_emits_evidence_and_reason_codes():
    key = _ns(
        content="Reviewed By Signature",
        bounding_regions=[_ns(page_number=1, polygon=[0.1, 0.1, 0.3, 0.1, 0.3, 0.2, 0.1, 0.2])],
    )
    value = _ns(
        content="John Doe",
        bounding_regions=[_ns(page_number=1, polygon=[0.35, 0.1, 0.55, 0.1, 0.55, 0.2, 0.35, 0.2])],
    )
    kvp = _ns(key=key, value=value, confidence=0.74)
    page = _ns(page_number=1, spans=[_ns(offset=0, length=100)])
    style = _ns(is_handwritten=True, confidence=0.8, offset=20, length=14)
    result = _ns(key_value_pairs=[kvp], pages=[page])

    signatures = _detect_signatures(result, [style], [page])
    assert len(signatures) == 1
    sig = signatures[0]
    assert sig.status == "signed"
    assert "signature_keyword_match" in sig.reason_codes
    assert "decision_score" in sig.evidence
    assert sig.evidence["source"] == "kv_signature_key"
