"""Integration tests for ``GET /api/compliance/{doc_id}/export``.

Pins the Spec 008 export contract end-to-end through the FastAPI
route:

* Default ``format=pdf``; HTML and Markdown also reachable.
* Response shape: Content-Type / Content-Disposition / body bytes.
* Score fields stripped (FR-007) — never leak into the artifact.
* PDF→HTML fallback when WeasyPrint native deps aren't loadable.
* Disk cache at ``data/documents/{doc_id}/exports/`` with mtime
  invalidation and ``?nocache=1`` bypass.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import compliance as compliance_route


_DOC_ID = "exp-doc-1"


def _seed_compliance_report(doc_dir: Path) -> None:
    """Write a minimal but complete ``compliance_result.json``."""

    doc_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_id": "rpt-int-1",
        "doc_id": _DOC_ID,
        "filename": "fixture.pdf",
        "total_pages": 12,
        "document_type": "batch_record",
        "generated_at": datetime(2026, 5, 14, 10, 0, 0, tzinfo=UTC).isoformat(),
        "agent_reports": [
            {
                "agent": "checklist",
                "agent_display": "Checklist",
                "total_rules": 2,
                "total_findings": 1,
                "all_evaluations": [
                    {
                        "rule_id": "CHE-1",
                        "rule_text": "Are all attachments enclosed?",
                        "rule_category": "document_completeness",
                        "agent": "checklist",
                        "status": "compliant",
                        "page_numbers": [3],
                        "reasoning": "Attachments verified.",
                    },
                    {
                        "rule_id": "CHE-2",
                        "rule_text": "Are all checks compiled?",
                        "rule_category": "bpcr_review",
                        "agent": "checklist",
                        "status": "non_compliant",
                        "page_numbers": [7],
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
                        "reasoning": "Water content check at Step 67 is missing.",
                        "recommendation": "Investigate the missing check.",
                    },
                ],
            },
        ],
    }
    (doc_dir / "compliance_result.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A TestClient with storage rooted at ``tmp_path`` and a fixture
    compliance report seeded for ``_DOC_ID``."""

    _seed_compliance_report(tmp_path / _DOC_ID)

    # Pin the storage root by overriding the route's ``_doc_dir`` —
    # avoids reaching into the LRU-cached settings instance.
    def fake_doc_dir(doc_id: str) -> Path:
        d = tmp_path / doc_id
        if not d.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        return d

    monkeypatch.setattr(compliance_route, "_doc_dir", fake_doc_dir)

    from app.main import create_app

    app = create_app()
    return TestClient(app)


def test_default_format_is_pdf_or_html_fallback(client: TestClient) -> None:
    """Default request returns PDF when WeasyPrint is loadable;
    falls back to HTML with the fallback header otherwise."""

    resp = client.get(f"/api/compliance/{_DOC_ID}/export")
    assert resp.status_code == 200

    if resp.headers.get("X-Render-Fallback") == "html":
        # Fallback path — WeasyPrint native deps missing on host.
        assert resp.headers["content-type"].startswith("text/html")
        assert "compliance.html" in resp.headers["content-disposition"]
        assert resp.headers.get("X-Render-Fallback-Reason") == "weasyprint_unavailable"
    else:
        assert resp.headers["content-type"] == "application/pdf"
        assert "compliance.pdf" in resp.headers["content-disposition"]
        assert resp.content[:4] == b"%PDF"


def test_html_format_returns_html_with_no_scores(client: TestClient) -> None:
    """FR-007: exported artifact MUST NOT carry score fields."""

    resp = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "html"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "compliance.html" in resp.headers["content-disposition"]

    body = resp.text.lower()
    for forbidden in [
        "overall_score",
        "model_score",
        "review_adjusted_score",
        "score_decomposition",
    ]:
        assert forbidden not in body, f"{forbidden!r} leaked into export"

    # The three client-aligned compliance kinds must be present.
    assert "compliant" in body
    assert "action required" in body


def test_md_format_returns_markdown_table(client: TestClient) -> None:
    resp = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "md"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "compliance.md" in resp.headers["content-disposition"]
    assert "| Question | Compliance |" in resp.text


def test_invalid_format_returns_422(client: TestClient) -> None:
    resp = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "docx"})
    assert resp.status_code == 422


def test_unknown_agent_returns_404(client: TestClient) -> None:
    resp = client.get(
        f"/api/compliance/{_DOC_ID}/export",
        params={"format": "html", "agent": "nonexistent"},
    )
    assert resp.status_code == 404


def test_missing_report_returns_404(client: TestClient, tmp_path: Path) -> None:
    """Documents without a compliance_result.json yield 404."""

    # Create the doc dir but no compliance_result.json.
    (tmp_path / "no-report-doc").mkdir()
    resp = client.get("/api/compliance/no-report-doc/export")
    assert resp.status_code == 404


def test_cache_hit_on_second_request(client: TestClient, tmp_path: Path) -> None:
    """Second request returns ``X-Cache: hit`` and the cached file."""

    r1 = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "html"})
    assert r1.status_code == 200
    assert r1.headers.get("X-Cache") == "miss"

    cache_path = tmp_path / _DOC_ID / "exports" / "report.html"
    assert cache_path.exists(), "first request must populate the cache"

    r2 = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "html"})
    assert r2.status_code == 200
    assert r2.headers.get("X-Cache") == "hit"
    assert r2.content == r1.content


def test_nocache_bypasses_cache(client: TestClient) -> None:
    """``?nocache=1`` re-renders even when a fresh cache exists."""

    client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "html"})
    r = client.get(
        f"/api/compliance/{_DOC_ID}/export",
        params={"format": "html", "nocache": "1"},
    )
    assert r.status_code == 200
    assert r.headers.get("X-Cache") == "miss"


def test_mtime_change_invalidates_cache(
    client: TestClient, tmp_path: Path,
) -> None:
    """When ``compliance_result.json`` is rewritten (e.g. by a HITL
    review), the next export must re-render rather than serve stale
    bytes."""

    import os
    import time

    client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "md"})
    cache_path = tmp_path / _DOC_ID / "exports" / "report.md"
    result_path = tmp_path / _DOC_ID / "compliance_result.json"
    assert cache_path.exists()

    # Bump the report's mtime past the cache's mtime. A 2 s gap keeps
    # us above filesystem mtime resolution on every common OS.
    future = time.time() + 2
    os.utime(result_path, (future, future))

    r = client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "md"})
    assert r.status_code == 200
    assert r.headers.get("X-Cache") == "miss"
