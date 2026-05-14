"""Tests for Spec 011 / US4 — HITL-edit preservation via the
sidecar ``segmentation.overrides.json`` and the apply pipeline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.compliance.models import DocumentSection, DocumentSegmentation
from app.compliance.segmentation_overrides import (
    SegmentationOverride,
    apply_overrides,
    diff_for_overrides,
    load_overrides,
    save_override,
)


_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)


def _sec(
    section_id: str,
    *,
    section_type: str = "manufacturing_operations",
    document_type: str = "batch_record",
    start: int = 1,
    end: int = 10,
    name: str = "",
) -> DocumentSection:
    return DocumentSection(
        section_id=section_id,
        name=name or section_id.replace("_", " ").title(),
        section_type=section_type,
        document_type=document_type,
        start_page=start,
        end_page=end,
        description="",
    )


def _seg(*sections: DocumentSection) -> DocumentSegmentation:
    return DocumentSegmentation(sections=list(sections), document_type="batch_record", confidence=0.9)


# ── load / save ────────────────────────────────────────────────


def test_load_returns_empty_when_no_sidecar(tmp_path: Path) -> None:
    assert load_overrides(tmp_path) == []


def test_save_creates_and_appends(tmp_path: Path) -> None:
    """Two saves leave two records on disk; load preserves order."""

    ov1 = SegmentationOverride(
        section_id="rm",
        field="end_page",
        value=47,
        recorded_at=_NOW,
        actor="alice",
    )
    ov2 = SegmentationOverride(
        section_id="rm",
        field="end_page",
        value=49,
        recorded_at=_NOW,
        actor="bob",
    )
    save_override(tmp_path, ov1)
    save_override(tmp_path, ov2)

    loaded = load_overrides(tmp_path)
    assert [o.value for o in loaded] == [47, 49]
    assert [o.actor for o in loaded] == ["alice", "bob"]


def test_load_tolerates_corrupt_file(tmp_path: Path) -> None:
    """A corrupt sidecar is logged and treated as empty —
    segmentation must never fail because the overrides file is bad."""

    (tmp_path / "segmentation.overrides.json").write_text("{not json")
    assert load_overrides(tmp_path) == []


def test_load_skips_individual_malformed_entries(tmp_path: Path) -> None:
    """An entry missing required fields is skipped; the valid
    siblings still load."""

    (tmp_path / "segmentation.overrides.json").write_text(json.dumps([
        {"section_id": "ok", "field": "end_page", "value": 5,
         "recorded_at": _NOW.isoformat(), "actor": "x"},
        {"junk": "yes"},
    ]))
    loaded = load_overrides(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].section_id == "ok"


def test_save_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    ov = SegmentationOverride(
        section_id="rm",
        field="end_page",
        value=5,
        recorded_at=_NOW,
        actor="x",
    )
    save_override(tmp_path, ov)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ── diff_for_overrides ────────────────────────────────────────


def test_diff_extracts_changed_fields() -> None:
    """Operator extends end_page from 25 → 27 and changes
    section_type. The diff returns two overrides."""

    baseline = _seg(_sec("rm", end=25, section_type="material_request"))
    incoming = _seg(_sec("rm", end=27, section_type="material_issue"))
    overrides = diff_for_overrides(baseline, incoming, actor="alice", now=_NOW)
    fields = {(o.field, o.value) for o in overrides}
    assert fields == {("end_page", 27), ("section_type", "material_issue")}
    assert all(o.actor == "alice" for o in overrides)
    assert all(o.recorded_at == _NOW for o in overrides)


def test_diff_skips_unchanged_sections() -> None:
    baseline = _seg(_sec("rm", end=25))
    incoming = _seg(_sec("rm", end=25))
    assert diff_for_overrides(baseline, incoming, actor="alice", now=_NOW) == []


def test_diff_ignores_new_sections() -> None:
    """Operator adds a section that wasn't in the baseline — not
    recorded as an override (would be lost on next re-segment
    anyway; deferred to a later spec)."""

    baseline = _seg(_sec("rm", end=25))
    incoming = _seg(_sec("rm", end=25), _sec("new_section"))
    assert diff_for_overrides(baseline, incoming, actor="alice", now=_NOW) == []


# ── apply_overrides ────────────────────────────────────────────


def test_apply_patches_target_section() -> None:
    """Operator's end_page=47 survives even when the fresh LLM
    output emits end_page=25 for the same section_id."""

    seg = _seg(_sec("rm", end=25))
    overrides = [SegmentationOverride(
        section_id="rm",
        field="end_page",
        value=47,
        recorded_at=_NOW,
        actor="alice",
    )]
    patched, orphans = apply_overrides(seg, overrides)
    assert patched.sections[0].end_page == 47
    assert orphans == []


def test_apply_handles_multiple_fields() -> None:
    """One override per field; the patched section reflects all."""

    seg = _seg(_sec("rm", end=25, section_type="material_request"))
    overrides = [
        SegmentationOverride(section_id="rm", field="end_page", value=47,
                              recorded_at=_NOW, actor="x"),
        SegmentationOverride(section_id="rm", field="section_type",
                              value="material_issue",
                              recorded_at=_NOW, actor="x"),
    ]
    patched, _ = apply_overrides(seg, overrides)
    assert patched.sections[0].end_page == 47
    assert patched.sections[0].section_type == "material_issue"


def test_apply_last_write_wins_per_section_field() -> None:
    """Two overrides on the same (section, field) — the later one
    wins. Operator intuition: the last save is the binding one."""

    seg = _seg(_sec("rm", end=25))
    overrides = [
        SegmentationOverride(section_id="rm", field="end_page", value=40,
                              recorded_at=_NOW, actor="x"),
        SegmentationOverride(section_id="rm", field="end_page", value=47,
                              recorded_at=_NOW, actor="x"),
    ]
    patched, _ = apply_overrides(seg, overrides)
    assert patched.sections[0].end_page == 47


def test_apply_emits_orphan_for_missing_target() -> None:
    """Override target_section_id is no longer in the LLM output.
    The override is dropped and surfaced as an orphan."""

    seg = _seg(_sec("rm", end=25))
    overrides = [SegmentationOverride(
        section_id="vanished",
        field="end_page",
        value=10,
        recorded_at=_NOW,
        actor="alice",
    )]
    patched, orphans = apply_overrides(seg, overrides)
    # Patched is unchanged.
    assert patched.sections[0].end_page == 25
    # One orphan returned for the caller to surface as a
    # validation issue.
    assert len(orphans) == 1
    assert orphans[0]["section_id"] == "vanished"
    assert orphans[0]["field"] == "end_page"


def test_apply_no_overrides_passthrough() -> None:
    seg = _seg(_sec("rm", end=25))
    patched, orphans = apply_overrides(seg, [])
    assert patched.model_dump() == seg.model_dump()
    assert orphans == []
