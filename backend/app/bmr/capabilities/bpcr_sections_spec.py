"""Canonical BPCR section spec — loader and Pydantic models (Spec 007).

The canonical list of BPCR sections is data, not code. This module
loads the YAML at ``backend/config/bmr/pilot/bpcr-section-spec.yaml``
(or wherever ``AT_BMR__BPCR_SECTIONS_SPEC`` points) and returns a
validated :class:`BPCRSectionsSpec`.

Validation contract (Spec 007 contracts/section-spec-config.md):

- ``spec_version`` is a non-empty string.
- ``sections`` is a non-empty list.
- Each ``section_id`` matches ``^[a-z][a-z0-9_]*$`` and is globally
  unique within the file.
- The reserved sentinel ``unsectioned`` cannot be authored.
- Every ``regex`` pattern compiles. A bad pattern fails the load with
  a message naming the offending section.

The loader is pure: same path + same bytes → same returned spec
object. No filesystem side-effects.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

UNSECTIONED_ID = "unsectioned"
"""Reserved sentinel ``section_id`` emitted by the detector when no
canonical section matches a page. Authors cannot use this value in the
spec file (the loader rejects it)."""

DEFAULT_SPEC_PATH_ENV = "AT_BMR__BPCR_SECTIONS_SPEC"

_DEFAULT_SPEC_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "bmr"
    / "pilot"
    / "bpcr-section-spec.yaml"
)

_SECTION_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BANDS = ("top_of_page", "top_of_table", "mid_page")
Band = Literal["top_of_page", "top_of_table", "mid_page"]


class BPCRSectionsSpecError(ValueError):
    """Raised by :func:`load_spec` when the YAML file is invalid.

    Subclasses ``ValueError`` so ``pydantic.ValidationError`` and our
    own structural errors share a single exception type for callers
    that want to catch "bad spec" generically.
    """


class BPCRSectionEntry(BaseModel):
    """A single canonical section in the BPCR section spec."""

    section_id: str
    display_name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)
    bands: list[Band] = Field(default_factory=lambda: ["top_of_page"])
    requires_emphasis_for_mid_page: bool = True

    model_config = ConfigDict(frozen=True)

    @field_validator("section_id")
    @classmethod
    def _validate_section_id(cls, value: str) -> str:
        if not _SECTION_ID_RE.match(value):
            raise ValueError(
                f"section_id {value!r} must match ^[a-z][a-z0-9_]*$"
            )
        if value == UNSECTIONED_ID:
            raise ValueError(
                f"section_id {value!r} is reserved for the detector and "
                "cannot appear in the canonical spec"
            )
        return value

    @field_validator("bands")
    @classmethod
    def _bands_non_empty_and_known(cls, value: list[Band]) -> list[Band]:
        if not value:
            raise ValueError("bands must be a non-empty list")
        seen: set[str] = set()
        for band in value:
            if band in seen:
                raise ValueError(f"duplicate band {band!r}")
            seen.add(band)
        return value


class BPCRSectionsSpec(BaseModel):
    """Versioned, validated canonical list of BPCR sections."""

    spec_version: str = Field(min_length=1)
    sections: list[BPCRSectionEntry] = Field(min_length=1)

    model_config = ConfigDict(frozen=True)

    @field_validator("sections")
    @classmethod
    def _section_ids_are_unique(
        cls, value: list[BPCRSectionEntry]
    ) -> list[BPCRSectionEntry]:
        seen: set[str] = set()
        for entry in value:
            if entry.section_id in seen:
                raise ValueError(
                    f"duplicate section_id {entry.section_id!r}"
                )
            seen.add(entry.section_id)
        return value

    def get(self, section_id: str) -> BPCRSectionEntry | None:
        for entry in self.sections:
            if entry.section_id == section_id:
                return entry
        return None


def default_spec_path() -> Path:
    """Return the on-disk path to the default canonical spec file.

    Resolution order matches FR-007:
    1. ``AT_BMR__BPCR_SECTIONS_SPEC`` environment variable (absolute
       path or path relative to cwd).
    2. ``backend/config/bmr/pilot/bpcr-section-spec.yaml`` shipped
       with the repo.
    """

    override = os.getenv(DEFAULT_SPEC_PATH_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_SPEC_PATH


def load_spec(path: Path | None = None) -> BPCRSectionsSpec:
    """Load and validate the canonical BPCR section spec.

    Args:
        path: Override path. Defaults to
            :func:`default_spec_path` (env-aware).

    Returns:
        A validated :class:`BPCRSectionsSpec`.

    Raises:
        BPCRSectionsSpecError: structural problem (file missing, bad
            YAML, schema violation, regex compile failure). The
            message names the offending field and value where
            possible.
    """

    target = path or default_spec_path()
    if not target.exists():
        raise BPCRSectionsSpecError(
            f"BPCR sections spec not found at {target}"
        )
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise BPCRSectionsSpecError(
            f"BPCR sections spec at {target} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise BPCRSectionsSpecError(
            f"BPCR sections spec at {target} must be a mapping, got "
            f"{type(raw).__name__}"
        )

    try:
        spec = BPCRSectionsSpec.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError or ValueError
        raise BPCRSectionsSpecError(
            f"BPCR sections spec at {target} failed validation: {exc}"
        ) from exc

    # Compile-check every regex up-front so a bad pattern surfaces at
    # load time (Constitution VI — config errors are blocking).
    for entry in spec.sections:
        for pattern in entry.regex:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise BPCRSectionsSpecError(
                    f"BPCR sections spec at {target}: section "
                    f"{entry.section_id!r} has an invalid regex "
                    f"{pattern!r}: {exc}"
                ) from exc

    return spec


__all__ = [
    "DEFAULT_SPEC_PATH_ENV",
    "UNSECTIONED_ID",
    "BPCRSectionEntry",
    "BPCRSectionsSpec",
    "BPCRSectionsSpecError",
    "Band",
    "default_spec_path",
    "load_spec",
]
