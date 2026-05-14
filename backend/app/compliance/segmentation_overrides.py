"""HITL-edit preservation for Spec 011 / US4.

The compliance pipeline re-runs segmentation whenever
``POST /api/compliance/{doc_id}/segment`` is hit (re-OCR, operator
disagrees with the LLM, etc.). Today's behaviour overwrites
``segmentation.json`` with the fresh LLM output, wiping any
operator edits made via ``PUT /segmentation``.

This module stores operator edits in a sidecar
``segmentation.overrides.json`` so they survive re-segmentation.

Storage shape — JSON list of records, one per (section_id, field)
change. Append-only in spirit; we keep history for audit
(``recorded_at`` + ``actor``). On apply, the LAST record per
``(section_id, field)`` wins.

Apply policy — operator intent is sacred:

* Apply runs AT THE END of the segmentation pipeline (after the
  geometric / vocabulary post-processes).
* When an override's target ``section_id`` is missing from the
  fresh LLM output, emit ``segmentation.override_orphaned`` and
  drop the override (HITL has to re-decide).
* When an override introduces a geometric anomaly (overlap,
  out-of-range page), the validators flag it as a warning but the
  override stands — the operator's word is final.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.compliance.models import DocumentSection, DocumentSegmentation

logger = logging.getLogger(__name__)

_OVERRIDE_FILE: str = "segmentation.overrides.json"

OverrideField = Literal[
    "section_type",
    "document_type",
    "start_page",
    "end_page",
    "name",
]

_PAGE_FIELDS: frozenset[str] = frozenset({"start_page", "end_page"})


class SegmentationOverride(BaseModel):
    """One operator edit to a specific field of a specific section."""

    section_id: str
    field: OverrideField
    value: str | int
    recorded_at: datetime
    actor: str

    def model_post_init(self, _ctx: object) -> None:  # noqa: D401
        # Type-coerce numeric fields written as strings via JSON.
        # Pydantic doesn't auto-coerce when the field type is a
        # union ``str | int`` — we want page fields to be ints.
        if self.field in _PAGE_FIELDS and isinstance(self.value, str):
            try:
                object.__setattr__(self, "value", int(self.value))
            except ValueError:
                pass


def load_overrides(doc_dir: Path) -> list[SegmentationOverride]:
    """Read the sidecar; return [] when absent or malformed.

    Malformed files (corrupted JSON, schema drift) are logged and
    treated as empty rather than raising — segmentation must never
    fail because an override file is bad."""

    path = doc_dir / _OVERRIDE_FILE
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "segmentation overrides at %s is unreadable; treating as empty",
            path, exc_info=True,
        )
        return []
    if not isinstance(raw, list):
        logger.warning(
            "segmentation overrides at %s is not a list; treating as empty",
            path,
        )
        return []
    out: list[SegmentationOverride] = []
    for entry in raw:
        try:
            out.append(SegmentationOverride.model_validate(entry))
        except Exception:
            logger.warning(
                "skipping malformed override entry at %s: %r",
                path, entry, exc_info=True,
            )
    return out


def save_override(doc_dir: Path, override: SegmentationOverride) -> None:
    """Append one record to the sidecar via tmp+rename for atomicity.

    Reads the existing file, appends the new record, writes through
    a ``.tmp`` then renames — so a crash mid-write doesn't leave a
    corrupt sidecar.
    """

    doc_dir.mkdir(parents=True, exist_ok=True)
    existing = load_overrides(doc_dir)
    existing.append(override)
    path = doc_dir / _OVERRIDE_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            [o.model_dump(mode="json") for o in existing],
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def diff_for_overrides(
    baseline: DocumentSegmentation,
    incoming: DocumentSegmentation,
    *,
    actor: str,
    now: datetime | None = None,
) -> list[SegmentationOverride]:
    """Compute the per-(section_id, field) diff from a PUT body
    against the current ``segmentation.json``.

    Only fields the operator can change via the editor are diffed
    (name / section_type / document_type / start_page / end_page).
    Sections present in ``incoming`` but absent from ``baseline``
    (operator added a section) are NOT recorded as overrides —
    those are creations, not edits; if the LLM re-emits a
    different shape on the next run, the operator's add is lost.
    Today's editor doesn't allow add/delete; we defer the
    add/delete case to a later spec.
    """

    timestamp = now or datetime.now(UTC)
    baseline_by_id = {s.section_id: s for s in baseline.sections}
    overrides: list[SegmentationOverride] = []
    for sec in incoming.sections:
        base = baseline_by_id.get(sec.section_id)
        if base is None:
            continue
        for field in ("name", "section_type", "document_type", "start_page", "end_page"):
            new_value = getattr(sec, field)
            old_value = getattr(base, field)
            if new_value != old_value:
                overrides.append(SegmentationOverride(
                    section_id=sec.section_id,
                    field=field,  # type: ignore[arg-type]
                    value=new_value,
                    recorded_at=timestamp,
                    actor=actor,
                ))
    return overrides


def apply_overrides(
    seg: DocumentSegmentation,
    overrides: list[SegmentationOverride],
) -> tuple[DocumentSegmentation, list[dict]]:
    """Apply overrides on top of a fresh segmentation.

    Returns ``(patched_segmentation, orphaned_records)``. Each
    orphaned record is a dict ready to render as a
    ``segmentation.override_orphaned`` validation issue (the
    caller wraps it into ``SegmentationIssue``).

    Last-write-wins per ``(section_id, field)``: the overrides
    list is walked in order, so a later override on the same
    section/field replaces an earlier one. This matches
    operator intuition — the last save is the binding one.
    """

    if not overrides:
        return seg, []

    # Resolve last-write-wins per (section_id, field).
    resolved: dict[tuple[str, str], SegmentationOverride] = {}
    for ov in overrides:
        resolved[(ov.section_id, ov.field)] = ov

    sections_by_id: dict[str, DocumentSection] = {s.section_id: s for s in seg.sections}
    orphans: list[dict] = []
    updated: dict[str, dict] = {}

    for (section_id, field), ov in resolved.items():
        target = sections_by_id.get(section_id)
        if target is None:
            orphans.append({
                "section_id": section_id,
                "field": field,
                "value": ov.value,
                "actor": ov.actor,
            })
            continue
        updated.setdefault(section_id, {})[field] = ov.value

    if not updated:
        return seg, orphans

    new_sections = [
        sec.model_copy(update=updated[sec.section_id])
        if sec.section_id in updated else sec
        for sec in seg.sections
    ]
    return seg.model_copy(update={"sections": new_sections}), orphans
