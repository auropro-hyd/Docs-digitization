"""Schema discovery + loading for BMR audit rules.

The schema files live alongside the rule bank under
``backend/config/rules/schema/rule.schema.v{MAJOR}.{MINOR}.json``. Each
rule YAML declares its ``schema_version``; the loader dispatches to the
matching schema.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR_ENV = "BMR_RULE_SCHEMA_DIR"

_SCHEMA_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)$")
_SCHEMA_FILENAME_RE = re.compile(r"^rule\.schema\.v(?P<major>\d+)\.(?P<minor>\d+)\.json$")

_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "config" / "rules" / "schema"


class SchemaNotFoundError(LookupError):
    """Raised when a requested schema version cannot be located on disk."""


class SchemaMalformedError(ValueError):
    """Raised when a schema file exists but is not parseable JSON."""


def default_schema_dir() -> Path:
    """Return the on-disk directory containing BMR rule schemas."""

    import os

    override = os.environ.get(SCHEMA_DIR_ENV)
    if override:
        return Path(override).resolve()
    return _DEFAULT_SCHEMA_DIR


def available_schema_versions(schema_dir: Path | None = None) -> list[str]:
    """List schema versions present in ``schema_dir`` in sort order."""

    directory = schema_dir or default_schema_dir()
    if not directory.is_dir():
        return []
    versions: list[tuple[int, int, str]] = []
    for child in directory.iterdir():
        match = _SCHEMA_FILENAME_RE.match(child.name)
        if not match:
            continue
        versions.append((int(match["major"]), int(match["minor"]), f"{match['major']}.{match['minor']}"))
    versions.sort()
    return [v for _, _, v in versions]


def schema_path_for(version: str, schema_dir: Path | None = None) -> Path:
    """Return the path for a given schema version string (e.g. ``"1.0"``)."""

    match = _SCHEMA_VERSION_RE.match(version)
    if not match:
        msg = (
            f"schema_version {version!r} is not valid; expected 'MAJOR.MINOR' "
            f"(e.g. '1.0')"
        )
        raise SchemaNotFoundError(msg)
    directory = schema_dir or default_schema_dir()
    path = directory / f"rule.schema.v{match['major']}.{match['minor']}.json"
    if not path.exists():
        known = ", ".join(available_schema_versions(directory)) or "(none)"
        msg = (
            f"schema_version {version!r} is not available at {path}. "
            f"Known versions: {known}."
        )
        raise SchemaNotFoundError(msg)
    return path


@lru_cache(maxsize=16)
def _load_schema_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    # mtime_ns is part of the cache key so edits during dev invalidate cleanly.
    del mtime_ns
    try:
        raw = Path(path_str).read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaMalformedError(f"cannot read schema at {path_str}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaMalformedError(f"schema at {path_str} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SchemaMalformedError(f"schema at {path_str} must be a JSON object")
    return parsed


def load_schema(version: str, schema_dir: Path | None = None) -> dict[str, Any]:
    """Load and cache the schema for ``version``.

    Cache entries are keyed on path + mtime so dev edits take effect on next
    call without manual invalidation.
    """

    path = schema_path_for(version, schema_dir=schema_dir)
    mtime_ns = path.stat().st_mtime_ns
    # Copy to prevent callers from mutating the cached dict.
    cached = _load_schema_cached(str(path), mtime_ns)
    return json.loads(json.dumps(cached))


__all__ = [
    "SCHEMA_DIR_ENV",
    "SchemaMalformedError",
    "SchemaNotFoundError",
    "available_schema_versions",
    "default_schema_dir",
    "load_schema",
    "schema_path_for",
]
