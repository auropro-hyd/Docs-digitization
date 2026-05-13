"""Integration tests for ``GET /api/compliance/{doc_id}/report-rows``.

The endpoint feeds the on-screen rule table directly from the
backend builder so the TypeScript layer never re-derives the
client-aligned shape. Same transform as ``/export``, no file
caching (React Query handles UI caching).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import compliance as compliance_route


_DOC_ID = "rows-doc-1"


def _seed(doc_dir: Path) -> None:
    doc_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_id": "rpt-rows-1",
        "doc_id": _DOC_ID,
        "filename": "rows.pdf",
        "total_pages": 20,
        "document_type": "batch_record",
        "generated_at": datetime(2026, 5, 14, tzinfo=UTC).isoformat(),
        "agent_reports": [
            {
                "agent": "checklist",
                "agent_display": "Checklist",
                "total_rules": 3,
                "all_evaluations": [
                    {
                        "rule_id": "CHE-1",
                        "rule_text": "Are all attachments enclosed?",
                        "rule_category": "document_completeness",
                        "agent": "checklist",
                        "status": "compliant",
                        "page_numbers": [3],
                        "reasoning": "OK",
                    },
                    {
                        "rule_id": "CHE-2",
                        "rule_text": "Are all checks compiled?",
                        "rule_category": "bpcr_review",
                        "agent": "checklist",
                        "status": "non_compliant",
                        "page_numbers": [7],
                    },
                    {
                        "rule_id": "CHE-3",
                        "rule_text": "Is the yield within spec?",
                        "rule_category": "bpcr_review",
                        "agent": "checklist",
                        "status": "uncertain",
                        "page_numbers": [18, 20],
                    },
                ],
                "findings": [
                    {
                        "finding_id": "CHE-2-f1",
                        "rule_id": "CHE-2",
                        "rule_text": "In-process checks",
                        "rule_category": "bpcr_review",
                        "agent": "checklist",
                        "severity": "major",
                        "status": "non_compliant",
                        "page_numbers": [7],
                        "reasoning": "Step 67 missing.",
                        "recommendation": "Investigate.",
                    },
                ],
            },
            {
                "agent": "gmp",
                "agent_display": "GMP",
                "total_rules": 1,
                "all_evaluations": [
                    {
                        "rule_id": "GMP-1",
                        "rule_text": "GMP review",
                        "rule_category": "general",
                        "agent": "gmp",
                        "status": "compliant",
                        "page_numbers": [],
                        "reasoning": "Looks fine.",
                    },
                ],
                "findings": [],
            },
        ],
    }
    (doc_dir / "compliance_result.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    _seed(tmp_path / _DOC_ID)

    def fake_doc_dir(doc_id: str) -> Path:
        d = tmp_path / doc_id
        if not d.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        return d

    monkeypatch.setattr(compliance_route, "_doc_dir", fake_doc_dir)

    from app.main import create_app

    return TestClient(create_app())


def test_returns_full_report_document_shape(client: TestClient) -> None:
    resp = client.get(f"/api/compliance/{_DOC_ID}/report-rows")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level shape
    assert set(body.keys()) == {"header", "rows", "footer", "stats"}

    header = body["header"]
    assert header["product_name"] == "BMR Compliance Intelligence Suite"
    assert header["is_draft"] is True
    assert isinstance(header["metadata_rows"], list)

    rows = body["rows"]
    assert len(rows) == 4  # CHE-1 + CHE-2 + CHE-3 + GMP-1 (none excluded)

    kinds = {r["compliance_kind"] for r in rows}
    assert kinds == {"compliant", "action_required", "needs_attention"}

    # Sort order pin: action_required first, then needs_attention,
    # then compliant.
    first_kinds = [r["compliance_kind"] for r in rows]
    assert first_kinds[0] == "action_required"
    assert "compliant" in first_kinds[-2:]

    footer = body["footer"]
    assert "Pharmix AI" in footer["disclaimer"]

    stats = body["stats"]
    assert stats["row_count"] == 4
    assert stats["action_required_count"] == 1
    assert stats["needs_attention_count"] == 1
    assert stats["compliant_count"] == 2


def test_agent_filter_scopes_rows(client: TestClient) -> None:
    resp = client.get(
        f"/api/compliance/{_DOC_ID}/report-rows", params={"agent": "checklist"},
    )
    assert resp.status_code == 200
    body = resp.json()
    agents = {r["agent"] for r in body["rows"]}
    assert agents == {"Checklist"}  # display name from the builder map
    assert body["stats"]["row_count"] == 3


def test_carries_no_score_fields(client: TestClient) -> None:
    """FR-007 + FR-011: report-rows feeds the rule table, which is
    score-free; the scorecard fetches scores separately via /report.
    Pin so a future builder change can't sneak scores back into the
    rule-table payload."""

    resp = client.get(f"/api/compliance/{_DOC_ID}/report-rows")
    payload_text = resp.text.lower()
    for forbidden in ["overall_score", "model_score", "review_adjusted_score"]:
        assert forbidden not in payload_text, f"{forbidden!r} leaked into report-rows"


def test_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.get(
        f"/api/compliance/{_DOC_ID}/report-rows", params={"agent": "nope"},
    )
    assert resp.status_code == 404


def test_missing_report_returns_404(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "no-rows-doc").mkdir()
    resp = client.get("/api/compliance/no-rows-doc/report-rows")
    assert resp.status_code == 404


def test_route_is_in_quiet_routes() -> None:
    """The frontend refetches /report-rows on every HITL action;
    keeping it at INFO would flood the log."""
    from app.observability.middleware import _QUIET_ROUTES

    assert ("GET", "/api/compliance/{doc_id}/report-rows") in _QUIET_ROUTES
