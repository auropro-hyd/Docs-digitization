"""Structured diff between two rule YAML files (Spec 005 follow-up).

Plain text diff (``diff -u``) works poorly for rule YAML because YAML
key order, quoting, and block style all change without changing
semantics. Tuning a rule typically produces a handful of targeted
edits (tolerance widening, alias file swap, severity nudge, new
``synthesises_from`` entry), and rule authors need to see *those*
edits, not a noisy reshuffle.

This module compares two rule mappings field-by-field and emits a
:class:`RuleDiffReport` listing only the semantic deltas:

- ``ADDED`` — key present in the "right" rule but not the "left".
- ``REMOVED`` — key present in the "left" but not the "right".
- ``CHANGED`` — key present on both sides with a different value.

The diff is aware of common tuning patterns and tags those deltas so
callers (the ``bmr-rules diff`` CLI, future lint bots, the
``bmr-rule-author`` skill) can render them with the correct severity
(e.g. "severity downgrade without rationale" should be flagged).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class ChangeTag(StrEnum):
    """Semantic annotations applied to specific keys.

    These are purely informational — the diff is correct without them,
    but they let the UI layer highlight tune-mode hotspots instantly.
    """

    ID_RENAMED = "id_renamed"
    VERSION_BUMPED = "version_bumped"
    SEVERITY_CHANGED = "severity_changed"
    TOLERANCE_RELAXED = "tolerance_relaxed"
    TOLERANCE_TIGHTENED = "tolerance_tightened"
    SCOPE_CHANGED = "scope_changed"
    ALIAS_FILE_CHANGED = "alias_file_changed"
    ALIAS_FILE_SET = "alias_file_set"
    SYNTHESIS_SOURCES_CHANGED = "synthesis_sources_changed"
    FALLBACK_CHANGED = "fallback_changed"
    ALCOA_TAG_CHANGED = "alcoa_tag_changed"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class RuleDiffEntry:
    """One change between the left and right rule."""

    path: str
    kind: ChangeKind
    left: Any = None
    right: Any = None
    tags: tuple[ChangeTag, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["tags"] = [t.value for t in self.tags]
        return payload


@dataclass
class RuleDiffReport:
    """Aggregate diff for one pair of rules."""

    left_source: str
    right_source: str
    left_id: str | None
    right_id: str | None
    left_version: str | None
    right_version: str | None
    entries: list[RuleDiffEntry] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.entries)

    @property
    def tags(self) -> set[ChangeTag]:
        seen: set[ChangeTag] = set()
        for entry in self.entries:
            seen.update(entry.tags)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "left_source": self.left_source,
            "right_source": self.right_source,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "left_version": self.left_version,
            "right_version": self.right_version,
            "entries": [e.to_dict() for e in self.entries],
            "tags": sorted(t.value for t in self.tags),
        }


# ── Diff engine ──────────────────────────────────────────────────────────────


_SENTINEL = object()


def _walk(
    left: Any, right: Any, *, path: str, entries: list[RuleDiffEntry]
) -> None:
    if left == right:
        return

    if isinstance(left, dict) and isinstance(right, dict):
        keys = sorted(set(left) | set(right))
        for key in keys:
            sub_path = f"{path}/{key}"
            l_val = left.get(key, _SENTINEL)
            r_val = right.get(key, _SENTINEL)
            if l_val is _SENTINEL:
                entries.append(
                    RuleDiffEntry(
                        path=sub_path, kind=ChangeKind.ADDED, right=r_val
                    )
                )
            elif r_val is _SENTINEL:
                entries.append(
                    RuleDiffEntry(
                        path=sub_path, kind=ChangeKind.REMOVED, left=l_val
                    )
                )
            else:
                _walk(l_val, r_val, path=sub_path, entries=entries)
        return

    if isinstance(left, list) and isinstance(right, list):
        # Rule YAML lists are usually short enum-like sequences
        # (``synthesises_from``, ``punctuation_strip``). Treat the list
        # as a single value so the author sees "the whole list changed"
        # rather than noisy per-index edits.
        entries.append(
            RuleDiffEntry(
                path=path, kind=ChangeKind.CHANGED, left=left, right=right
            )
        )
        return

    entries.append(
        RuleDiffEntry(path=path, kind=ChangeKind.CHANGED, left=left, right=right)
    )


def _tag_entries(
    entries: list[RuleDiffEntry], *, left: dict[str, Any], right: dict[str, Any]
) -> list[RuleDiffEntry]:
    tagged: list[RuleDiffEntry] = []
    for entry in entries:
        tags: list[ChangeTag] = list(entry.tags)
        path = entry.path

        if path == "/id" and entry.kind is ChangeKind.CHANGED:
            tags.append(ChangeTag.ID_RENAMED)
        if path == "/version" and entry.kind is ChangeKind.CHANGED:
            tags.append(ChangeTag.VERSION_BUMPED)
        if path == "/severity" and entry.kind is ChangeKind.CHANGED:
            tags.append(ChangeTag.SEVERITY_CHANGED)
        if path == "/alcoa_tag" and entry.kind is ChangeKind.CHANGED:
            tags.append(ChangeTag.ALCOA_TAG_CHANGED)
        if path == "/fallback" and entry.kind is ChangeKind.CHANGED:
            tags.append(ChangeTag.FALLBACK_CHANGED)
        if (
            path == "/deprecated"
            and entry.kind in {ChangeKind.ADDED, ChangeKind.CHANGED}
            and entry.right is True
        ):
            tags.append(ChangeTag.DEPRECATED)
        if path.startswith("/context_object/scope"):
            tags.append(ChangeTag.SCOPE_CHANGED)
        if path.endswith("/entity_match/aliases_file"):
            if entry.kind is ChangeKind.ADDED:
                tags.append(ChangeTag.ALIAS_FILE_SET)
            elif entry.kind is ChangeKind.CHANGED:
                tags.append(ChangeTag.ALIAS_FILE_CHANGED)
        if path == "/synthesises_from" and entry.kind in {
            ChangeKind.ADDED,
            ChangeKind.CHANGED,
            ChangeKind.REMOVED,
        }:
            tags.append(ChangeTag.SYNTHESIS_SOURCES_CHANGED)
        if path == "/tolerance/value":
            left_val = entry.left
            right_val = entry.right
            if isinstance(left_val, (int, float)) and isinstance(
                right_val, (int, float)
            ):
                if right_val > left_val:
                    tags.append(ChangeTag.TOLERANCE_RELAXED)
                elif right_val < left_val:
                    tags.append(ChangeTag.TOLERANCE_TIGHTENED)

        tagged.append(
            RuleDiffEntry(
                path=entry.path,
                kind=entry.kind,
                left=entry.left,
                right=entry.right,
                tags=tuple(tags),
            )
        )
    return tagged


def diff_rule_mappings(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_source: str = "<left>",
    right_source: str = "<right>",
) -> RuleDiffReport:
    """Diff two rule mappings and tag semantically meaningful deltas."""

    entries: list[RuleDiffEntry] = []
    _walk(left, right, path="", entries=entries)
    tagged = _tag_entries(entries, left=left, right=right)
    return RuleDiffReport(
        left_source=left_source,
        right_source=right_source,
        left_id=left.get("id") if isinstance(left.get("id"), str) else None,
        right_id=right.get("id") if isinstance(right.get("id"), str) else None,
        left_version=(
            left.get("version") if isinstance(left.get("version"), str) else None
        ),
        right_version=(
            right.get("version") if isinstance(right.get("version"), str) else None
        ),
        entries=tagged,
    )


def diff_rule_files(left_path: Path, right_path: Path) -> RuleDiffReport:
    """Load two rule YAMLs and diff them."""

    left = yaml.safe_load(left_path.read_text(encoding="utf-8")) or {}
    right = yaml.safe_load(right_path.read_text(encoding="utf-8")) or {}
    if not isinstance(left, dict):
        left = {}
    if not isinstance(right, dict):
        right = {}
    return diff_rule_mappings(
        left,
        right,
        left_source=str(left_path),
        right_source=str(right_path),
    )


__all__ = [
    "ChangeKind",
    "ChangeTag",
    "RuleDiffEntry",
    "RuleDiffReport",
    "diff_rule_files",
    "diff_rule_mappings",
]
