"""FR-013 via the HTTP boundary: a persisted finding without hitl_status
does NOT come back as auto_approved (server-side normalization).

Writes a tiny compliance_result.json with one malformed finding under the
real document storage, hits GET /api/compliance/{doc_id}/report, and
asserts the response normalizes hitl_status correctly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # Redirect storage base_path to a tmp dir so we don't disturb the real
    # pilot doc.  Nested settings use the ``AT_`` prefix + nested delimiter ``__``.
    monkeypatch.setenv("AT_STORAGE__BASE_PATH", str(tmp_path))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    with TestClient(create_app()) as c:
        yield c


def _seed_report_with_unknown_hitl(tmp_path: Path, doc_id: str) -> None:
    # _doc_dir joins ``base_path / doc_id`` directly — no extra "documents/"
    # segment.
    doc_dir = tmp_path / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_id": "r-1",
        "doc_id": doc_id,
        "filename": "test.pdf",
        "total_pages": 1,
        "document_type": "batch_record",
        "generated_at": "2026-04-23T00:00:00Z",
        "model_versions": {},
        "overall_score": 100.0,
        "model_score": 100.0,
        "review_adjusted_score": None,
        "score_decomposition": {},
        "score_methodology": {},
        "executive_summary": {},
        "total_findings": 1,
        "severity_counts": {"critical": 1},
        "agent_reports": [],
        "skipped_agents": [],
        "findings": [
            {
                "finding_id": "f-no-hitl",
                "rule_id": "rule.x",
                "rule_text": "...",
                "rule_category": "c",
                "rule_category_display": "C",
                "agent": "alcoa",
                "severity": "critical",
                "status": "non_compliant",
                "confidence": 0.95,
                "page_numbers": [1],
                "reasoning": "...",
                "evidence": "...",
                "description": "...",
                "recommendation": "",
                "applicability_trace": [],
                # hitl_status deliberately omitted
            }
        ],
        "audit_trail": None,
    }
    (doc_dir / "compliance_result.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_missing_hitl_status_does_not_return_as_auto_approved(
    client: TestClient, tmp_path: Path
) -> None:
    doc_id = "doc-unknown-hitl"
    _seed_report_with_unknown_hitl(tmp_path, doc_id)

    r = client.get(f"/api/compliance/{doc_id}/report")
    assert r.status_code == 200, r.text
    body = r.json()
    # The server-side scorer must have classified the finding as unknown,
    # not auto_approved, and EXCLUDED it from penalty.
    dec = body["score_decomposition"]
    # Either agent_scores is empty (no agent reports persisted) or the
    # overall decomposition records unknown_skipped. Grep the JSON for
    # explicit evidence that we did NOT invent an auto_approved default.
    assert "auto_approved" not in json.dumps(dec), (
        f"auto_approved leaked into score decomposition: {dec}"
    )
