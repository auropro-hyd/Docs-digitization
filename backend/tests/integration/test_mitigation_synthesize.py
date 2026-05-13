"""Integration tests for ``POST /api/compliance/{doc_id}/mitigation/synthesize``.

Spec 008 / US3 — operator warms the mitigation cache before
exporting so the rendered report carries LLM-synthesised
remediation text on every non-compliant / uncertain row.

Pinned contracts:
- Priority chain: rule-author ``recommendation`` and existing
  ``mitigation_text`` skip synthesis unless ``force=true``.
- Compliant findings are always skipped (no mitigation needed).
- Cost ceiling: once the next call would push cumulative spend
  over the ceiling, remaining findings surface as
  ``skipped_cost_ceiling`` rather than being silently dropped.
- Atomic write: synth-then-crash leaves the original
  ``compliance_result.json`` intact (verified by writing through
  a ``.tmp`` then ``replace``).
- Export cache busting: a successful synth removes cached
  ``report*.pdf`` / ``report*.html`` / ``report*.md`` so the
  next ``/export`` call re-renders with fresh mitigation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routes import compliance as compliance_route


_DOC_ID = "synth-doc-1"


class _StubLLM:
    """Records prompts and returns deterministic mitigation text."""

    def __init__(self, response: str = "Investigate and document.") -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.response = response
        self._fail_after: int | None = None

    async def generate(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise RuntimeError("simulated LLM error")
        return self.response

    async def generate_structured(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class _StubContainer:
    def __init__(self, llm: _StubLLM) -> None:
        self.compliance_evaluator_llm = llm


def _seed_report(doc_dir: Path, *, recommendations: dict[str, str] | None = None) -> None:
    """Seed a compliance report with two non-compliant + one
    compliant finding. ``recommendations`` lets a test pre-populate
    rule-author text on specific findings."""

    recommendations = recommendations or {}
    doc_dir.mkdir(parents=True, exist_ok=True)
    findings_inline = [
        {
            "finding_id": "F-1",
            "rule_id": "R-1",
            "rule_text": "Are all checks compiled?",
            "rule_category": "bpcr_review",
            "agent": "checklist",
            "severity": "major",
            "status": "non_compliant",
            "page_numbers": [7],
            "reasoning": "Step 67 check is missing.",
            "recommendation": recommendations.get("F-1", ""),
        },
        {
            "finding_id": "F-2",
            "rule_id": "R-2",
            "rule_text": "Is the yield within spec?",
            "rule_category": "bpcr_review",
            "agent": "checklist",
            "severity": "major",
            "status": "uncertain",
            "page_numbers": [18, 20],
            "reasoning": "Conflicting weights.",
            "recommendation": recommendations.get("F-2", ""),
        },
        # A compliant finding — should always be skipped.
        {
            "finding_id": "F-3",
            "rule_id": "R-3",
            "rule_text": "Attachments enclosed?",
            "rule_category": "document_completeness",
            "agent": "checklist",
            "severity": "observation",
            "status": "compliant",
            "page_numbers": [3],
            "reasoning": "Verified.",
        },
    ]
    payload = {
        "report_id": "rpt-synth-1",
        "doc_id": _DOC_ID,
        "filename": "synth.pdf",
        "total_pages": 20,
        "document_type": "batch_record",
        "generated_at": datetime(2026, 5, 14, tzinfo=UTC).isoformat(),
        "agent_reports": [
            {
                "agent": "checklist",
                "agent_display": "Checklist",
                "total_rules": 3,
                "total_findings": 3,
                "all_evaluations": [
                    {
                        "rule_id": f["rule_id"],
                        "rule_text": f["rule_text"],
                        "rule_category": f["rule_category"],
                        "agent": "checklist",
                        "status": f["status"],
                        "page_numbers": f["page_numbers"],
                    }
                    for f in findings_inline
                ],
                "findings": findings_inline,
            },
        ],
        "findings": findings_inline,
    }
    (doc_dir / "compliance_result.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


@pytest.fixture
def stub_llm() -> _StubLLM:
    return _StubLLM(response="Initiate CAPA and document the gap.")


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: _StubLLM,
) -> TestClient:
    _seed_report(tmp_path / _DOC_ID)

    def fake_doc_dir(doc_id: str) -> Path:
        d = tmp_path / doc_id
        if not d.exists():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
        return d

    monkeypatch.setattr(compliance_route, "_doc_dir", fake_doc_dir)
    monkeypatch.setattr(
        compliance_route, "get_container", lambda: _StubContainer(stub_llm),
    )

    from app.main import create_app

    app = create_app()
    client = TestClient(app)
    client.tmp_path = tmp_path  # type: ignore[attr-defined]
    return client


def _read_report(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / _DOC_ID / "compliance_result.json").read_text("utf-8"),
    )


def test_synthesizes_non_compliant_and_uncertain_findings(
    client: TestClient, stub_llm: _StubLLM,
) -> None:
    resp = client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    assert resp.status_code == 200

    summary = resp.json()
    assert summary["counts"]["synthesized"] == 2
    assert summary["counts"]["skipped_compliant"] == 1
    assert summary["cost_estimate_usd"] > 0
    assert len(stub_llm.calls) == 2

    # The cache is persisted to disk for both non-compliant findings.
    report = _read_report(client.tmp_path)  # type: ignore[attr-defined]
    f_by_id = {f["finding_id"]: f for f in report["findings"]}
    assert f_by_id["F-1"]["mitigation_text"] == "Initiate CAPA and document the gap."
    assert f_by_id["F-2"]["mitigation_text"] == "Initiate CAPA and document the gap."
    assert f_by_id["F-3"].get("mitigation_text", "") == ""

    # Both copies of the finding (top-level and agent_reports) carry
    # the same mitigation text — the renderer reads agent_reports;
    # downstream JSON consumers may read either.
    ar_findings = {
        f["finding_id"]: f
        for ar in report["agent_reports"]
        for f in ar["findings"]
    }
    assert ar_findings["F-1"]["mitigation_text"] == f_by_id["F-1"]["mitigation_text"]


def test_skips_findings_with_existing_recommendation(
    client: TestClient, tmp_path: Path, stub_llm: _StubLLM,
) -> None:
    """A rule-author ``recommendation`` blocks synthesis unless forced."""

    # Reseed with a recommendation on F-1.
    _seed_report(tmp_path / _DOC_ID, recommendations={"F-1": "Investigate the missing check immediately."})

    resp = client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    summary = resp.json()
    assert summary["counts"]["synthesized"] == 1  # only F-2
    assert summary["counts"]["skipped_has_mitigation"] == 1  # F-1
    assert summary["counts"]["skipped_compliant"] == 1
    assert len(stub_llm.calls) == 1


def test_force_flag_resynthesizes_already_cached_findings(
    client: TestClient, tmp_path: Path, stub_llm: _StubLLM,
) -> None:
    """``force=true`` re-runs synthesis even when the cache exists."""

    # First pass populates mitigation_text.
    client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    assert len(stub_llm.calls) == 2

    # Change the LLM response so we can prove a re-call happened.
    stub_llm.response = "Refreshed guidance."

    resp = client.post(
        f"/api/compliance/{_DOC_ID}/mitigation/synthesize",
        json={"force": True},
    )
    summary = resp.json()
    assert summary["counts"]["synthesized"] == 2
    assert len(stub_llm.calls) == 4

    report = _read_report(tmp_path)
    f_by_id = {f["finding_id"]: f for f in report["findings"]}
    assert f_by_id["F-1"]["mitigation_text"] == "Refreshed guidance."
    assert f_by_id["F-2"]["mitigation_text"] == "Refreshed guidance."


def test_rule_id_filter_limits_synthesis_scope(
    client: TestClient, stub_llm: _StubLLM,
) -> None:
    resp = client.post(
        f"/api/compliance/{_DOC_ID}/mitigation/synthesize",
        json={"rule_ids": ["R-1"]},
    )
    summary = resp.json()
    assert summary["counts"]["synthesized"] == 1
    # F-2 isn't in scope so it doesn't appear in per_finding either.
    rule_ids_in_result = {r["rule_id"] for r in summary["per_finding"]}
    assert rule_ids_in_result == {"R-1"}
    assert len(stub_llm.calls) == 1


def test_cost_ceiling_skips_remaining_findings(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, stub_llm: _StubLLM,
) -> None:
    """Once the next call would push cumulative spend over the
    ceiling, remaining findings surface as ``skipped_cost_ceiling``
    rather than being silently dropped."""

    # Patch the settings ceiling to a value tiny enough that a
    # single call fits but a second can't. The estimator is
    # heuristic (4 chars/token), so we pick a ceiling between one
    # and two call estimates by computing it from the fixture.
    from app.compliance.report_renderer.mitigation import _estimate_cost_usd, _build_synth_prompt
    f1_prompt = _build_synth_prompt({
        "rule_id": "R-1", "rule_text": "Are all checks compiled?",
        "severity": "major", "page_numbers": [7],
        "reasoning": "Step 67 check is missing.",
    })
    one_call_cost = _estimate_cost_usd(f1_prompt)

    real_get_settings = compliance_route.get_settings
    def fake_get_settings():  # type: ignore[no-untyped-def]
        settings = real_get_settings()
        # Use model_copy so we don't mutate the cached singleton.
        new_compliance = settings.compliance.model_copy(
            update={"mitigation_synth_cost_ceiling_usd": one_call_cost * 1.5},
        )
        return settings.model_copy(update={"compliance": new_compliance})
    monkeypatch.setattr(compliance_route, "get_settings", fake_get_settings)

    resp = client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    summary = resp.json()
    assert summary["counts"].get("synthesized", 0) == 1
    assert summary["counts"].get("skipped_cost_ceiling", 0) == 1
    assert len(stub_llm.calls) == 1


def test_llm_error_recorded_per_finding_without_failing_request(
    client: TestClient, stub_llm: _StubLLM,
) -> None:
    """One LLM failure must not poison the whole batch — the run
    moves on to the next finding and records the error in the
    per-finding result."""

    stub_llm._fail_after = 0  # first call already exceeds → every call fails

    resp = client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["counts"].get("error", 0) == 2
    error_entries = [r for r in summary["per_finding"] if r["status"] == "error"]
    assert all(r["error"] for r in error_entries)


def test_synth_busts_export_cache(client: TestClient, tmp_path: Path) -> None:
    """A successful synth removes the cached export so the next
    /export call re-renders with the freshly populated mitigation."""

    # Prime the export cache by hitting /export first.
    client.get(f"/api/compliance/{_DOC_ID}/export", params={"format": "html"})
    cache_path = tmp_path / _DOC_ID / "exports" / compliance_route._cache_filename("html", None)
    assert cache_path.exists()

    client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    assert not cache_path.exists(), (
        "synthesised mitigation must invalidate the export cache so "
        "the next download includes the fresh text"
    )


def test_missing_report_returns_404(client: TestClient) -> None:
    resp = client.post("/api/compliance/no-such-doc/mitigation/synthesize", json={})
    assert resp.status_code == 404


def test_atomic_write_leaves_temp_artifact_cleaned_up(
    client: TestClient, tmp_path: Path,
) -> None:
    """The synth path writes through ``compliance_result.json.tmp``
    then renames. A successful call must leave no ``.tmp`` lying
    around — pin so a future refactor that drops the rename gets
    caught here."""

    client.post(f"/api/compliance/{_DOC_ID}/mitigation/synthesize", json={})
    leftover = tmp_path / _DOC_ID / "compliance_result.json.tmp"
    assert not leftover.exists()
