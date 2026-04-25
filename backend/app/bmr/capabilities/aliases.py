"""Alias-table loader and name normalisation helpers.

An alias file maps a canonical name to a list of accepted variants. After
loading, ``AliasTable`` supports fast ``resolve(name) -> canonical`` lookup
with the normalisation strategy defined by the rule's ``entity_match``
block.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_MULTISPACE_RE = re.compile(r"\s+")


def normalise_name(
    name: str,
    *,
    case_insensitive: bool = True,
    punctuation_strip: Iterable[str] = ("-", "_", "."),
) -> str:
    """Return a canonical-ish form of ``name`` for matching.

    Steps: optional punctuation stripping → whitespace collapse → optional
    lowercasing. This is the function invoked by the ``normalise`` and
    ``alias`` entity-match strategies.
    """

    s = name
    for ch in punctuation_strip:
        s = s.replace(ch, " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    if case_insensitive:
        s = s.lower()
    return s


@dataclass(frozen=True)
class AliasTable:
    scope: str
    entries: dict[str, str] = field(default_factory=dict)  # normalised variant -> canonical
    source_path: str | None = None

    def resolve(
        self,
        name: str,
        *,
        case_insensitive: bool = True,
        punctuation_strip: Iterable[str] = ("-", "_", "."),
    ) -> str | None:
        """Return the canonical form for ``name``, or ``None`` if not known."""

        key = normalise_name(
            name,
            case_insensitive=case_insensitive,
            punctuation_strip=punctuation_strip,
        )
        return self.entries.get(key)

    def canonical_names(self) -> list[str]:
        return sorted(set(self.entries.values()))


def load_alias_table(path: Path) -> AliasTable:
    """Load an alias YAML file. Missing or malformed files raise ValueError."""

    if not path.exists():
        raise FileNotFoundError(f"alias file not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"alias file {path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"alias file {path} must be a mapping at the top level")

    scope = data.get("scope", path.stem)
    entries_raw = data.get("entries", [])
    if not isinstance(entries_raw, list):
        raise ValueError(f"alias file {path} 'entries' must be a list")

    entries: dict[str, str] = {}
    for idx, entry in enumerate(entries_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"alias entry #{idx} in {path} must be a mapping")
        canonical = entry.get("canonical")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ValueError(
                f"alias entry #{idx} in {path} is missing a non-empty 'canonical'"
            )
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError(
                f"alias entry #{idx} in {path} 'aliases' must be a list"
            )
        names = [canonical, *[a for a in aliases if isinstance(a, str)]]
        for variant in names:
            key = normalise_name(variant)
            if not key:
                continue
            existing = entries.get(key)
            if existing and existing != canonical:
                raise ValueError(
                    f"alias {variant!r} in {path} would map to both "
                    f"{existing!r} and {canonical!r}; resolve the conflict"
                )
            entries[key] = canonical

    return AliasTable(
        scope=str(scope),
        entries=entries,
        source_path=str(path),
    )


__all__ = ["AliasTable", "load_alias_table", "normalise_name"]
