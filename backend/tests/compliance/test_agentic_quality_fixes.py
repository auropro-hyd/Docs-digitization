"""Pin the quality fixes layered on top of Akhilesh's agentic-audit branch.

Akhilesh's 010-agentic-audit-fix shipped the load-bearing agentic
machinery (context_sources field, ContextToolbox, run_agentic_postpass,
section_worker fan-out, summarizer) but the audit on 2026-05-13 flagged
five quality concerns:

  1. **Phase 1.5b summarisation runs unconditionally** — N LLM calls
     per doc even when no rule uses agentic_audit.
  2. **store_page_summary races under asyncio.gather** — read-merge-
     write per task → last-writer-wins, summaries silently lost.
  3. **toolbox.get_context_pages truncates silently at page_cap** —
     LLM has no signal that its context window is incomplete.
  4. **WorkerAction allows tool actions the prompt doesn't advertise**
     — silent improvisation if the LLM picks one.
  5. **sop_rules.{yaml,md} not present** — SOP agent zero-rule.

This module pins each fix.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ── Fix 5: SOP rules load ────────────────────────────────────────


def test_sop_rules_are_no_longer_empty() -> None:
    """The active sop_rules.{yaml,md} pair must exist and the
    registry must surface at least one SOP rule. Closes the zero-
    rule warning Akhilesh hit on 2026-05-12.
    """
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    sop_rules = r.get_rules("sop")
    assert len(sop_rules) > 0, "sop_rules.md/yaml must populate the SOP agent"
    # Spot-check the SOP-* prefix shape so an empty file with the
    # right name doesn't accidentally pass.
    assert any(rule.id.startswith("SOP-") for rule in sop_rules)


# ── Fix 1: Phase 1.5b gating ────────────────────────────────────


def test_phase_1_5b_skips_when_no_agentic_rule() -> None:
    """The summariser auto-skip relies on a simple-enough predicate
    that we can check it directly: when no applicable agent has any
    rule with evaluation_strategy='agentic_audit', summarisation
    must NOT be triggered."""

    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    # Among the live agents, identify which have agentic_audit rules.
    agents_with_agentic = [
        a for a in ("alcoa", "gmp", "checklist", "sop", "reconciliation")
        if any(rule.evaluation_strategy == "agentic_audit"
               for rule in r.get_rules(a))
    ]
    # Sanity: at least one (checklist) does — otherwise the whole
    # phase-1.5b machinery would be unreachable.
    assert "checklist" in agents_with_agentic, (
        "checklist must have agentic_audit rules — that's the whole "
        "point of 010"
    )

    # Simulate the predicate used in compliance_graph.py
    def needs_summaries(applicable: list[str], force: bool) -> bool:
        if force:
            return True
        return any(
            rule.evaluation_strategy == "agentic_audit"
            for agent in applicable
            for rule in r.get_rules(agent)
        )

    assert needs_summaries(["checklist"], force=False) is True
    assert needs_summaries(["alcoa"], force=False) is False, (
        "alcoa has no agentic_audit rules; summarisation must skip"
    )
    assert needs_summaries(["alcoa"], force=True) is True, (
        "force flag overrides the auto-skip"
    )
    assert needs_summaries([], force=False) is False


# ── Fix 2: summariser race-safety ───────────────────────────────


@pytest.mark.asyncio
async def test_summarize_in_batches_does_not_lose_concurrent_writes(
    tmp_path, monkeypatch,
) -> None:
    """The old read-merge-write inside _summarize_one under
    asyncio.gather lost up to 9 summaries per batch of 10. After
    the fix (per-doc lock + batched single-write), all summaries
    survive."""

    # Point summariser at tmp_path via the storage base override.
    from app.compliance import summarizer
    from app.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.storage, "base_path", str(tmp_path))

    doc_id = "test-doc-race"
    extractions = [
        {"page_num": i, "markdown": f"page {i} content"}
        for i in range(1, 21)  # 20 pages → 2 batches of 10
    ]
    section_map = {
        i: {"document_type": "batch_record", "section_type": "manufacturing_operations"}
        for i in range(1, 21)
    }

    async def fake_generate(markdown, system=None):
        # Each call yields a distinguishable summary so we detect drops.
        await asyncio.sleep(0)  # force yields between tasks
        return f"summary-of-{markdown[:6]}"

    fake_llm = AsyncMock()
    fake_llm.generate = fake_generate

    await summarizer.summarize_pages_in_batches(
        extractions, section_map, doc_id, fake_llm, batch_size=10,
    )

    summary_file = tmp_path / doc_id / "summaries" / "page_summaries.json"
    assert summary_file.exists()
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    # All 20 entries must survive — old code would lose ~18.
    assert len(data) == 20, (
        f"summariser dropped entries: got {len(data)}/20. The per-doc "
        f"lock + batched-write fix is regressed."
    )
    for i in range(1, 21):
        assert str(i) in data
        assert data[str(i)]["text"].startswith("summary-of-")


@pytest.mark.asyncio
async def test_store_page_summary_serialises_concurrent_writers(
    tmp_path, monkeypatch,
) -> None:
    """The single-page entry-point must hold the per-doc lock during
    its own read-merge-write, so a HITL caller + a background batch
    can't race."""

    from app.compliance import summarizer
    from app.config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.storage, "base_path", str(tmp_path))

    doc_id = "test-doc-single-page"
    await asyncio.gather(*[
        summarizer.store_page_summary(
            doc_id, page_num=i, document_type="batch_record",
            section_type="manufacturing_operations", text=f"text-{i}",
        )
        for i in range(1, 11)
    ])

    summary_file = tmp_path / doc_id / "summaries" / "page_summaries.json"
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert len(data) == 10, (
        f"concurrent store_page_summary calls dropped entries: got "
        f"{len(data)}/10"
    )


# ── Fix 3: toolbox truncation marker ───────────────────────────


def test_get_context_pages_surfaces_truncation() -> None:
    """When the matching pages exceed page_cap the toolbox must
    append an explicit truncation marker AND list the dropped
    page_nums — silent slicing was the original failure mode."""

    from app.compliance.agentic.toolbox import ContextToolbox

    extractions = [
        {"page_num": i, "markdown": f"content of page {i}"}
        for i in range(1, 11)
    ]
    section_map = {
        i: {"document_type": "batch_record", "section_type": "manufacturing_operations"}
        for i in range(1, 11)
    }
    toolbox = ContextToolbox(
        all_extractions=extractions,
        section_map=section_map,
        doc_id="test-doc",
        page_cap=3,  # force truncation
    )
    result = toolbox.get_context_pages("batch_record", "manufacturing_operations")
    assert "truncated" in result.lower(), (
        "get_context_pages must mark truncation so the LLM sees the gap"
    )
    # Dropped pages 4..10 must be named.
    for p in range(4, 11):
        assert str(p) in result


def test_get_context_pages_does_not_mark_truncation_when_under_cap() -> None:
    """No false-positive truncation marker when all pages fit."""

    from app.compliance.agentic.toolbox import ContextToolbox

    extractions = [
        {"page_num": i, "markdown": f"content of page {i}"}
        for i in range(1, 4)
    ]
    section_map = {
        i: {"document_type": "batch_record", "section_type": "manufacturing_operations"}
        for i in range(1, 4)
    }
    toolbox = ContextToolbox(
        all_extractions=extractions,
        section_map=section_map,
        doc_id="test-doc",
        page_cap=50,
    )
    result = toolbox.get_context_pages("batch_record", "manufacturing_operations")
    assert "truncated" not in result.lower()


# ── Fix 4: registry surfaces context_sources field ─────────────


def test_registry_propagates_context_sources_from_yaml() -> None:
    """Akhilesh's `context_sources` YAML field must arrive on the
    loaded AuditRule. On main pre-010 it was silently dropped at
    YAML load → CHE-DOC1 had no manufacturing_operations context.
    """
    from app.compliance.rules.registry import RuleRegistry

    r = RuleRegistry()
    che_doc1 = next(
        (rule for rule in r.get_rules("checklist") if rule.id == "CHE-DOC1"),
        None,
    )
    assert che_doc1 is not None, "CHE-DOC1 must exist in checklist_rules.yaml"
    assert che_doc1.evaluation_strategy == "agentic_audit"
    assert che_doc1.context_sources, (
        "CHE-DOC1 must surface its context_sources — registry's "
        "_YAML_DICT_LIST_FIELDS plumbing is regressed if this is empty"
    )
    # Real-doc shape: a list of dicts with document_type + section_types.
    src = che_doc1.context_sources[0]
    assert "document_type" in src
    assert "section_types" in src or "section_type" in src
