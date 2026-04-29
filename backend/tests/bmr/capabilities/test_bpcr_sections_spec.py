"""Spec-loader tests for ``bpcr-section-spec.yaml`` (Spec 007 contracts/section-spec-config)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.bmr.capabilities.bpcr_sections_spec import (
    BPCRSectionsSpecError,
    load_spec,
)


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "spec.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    return target


def test_loader_accepts_valid_spec(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        spec_version: "1.0"
        sections:
          - section_id: cover
            display_name: "Cover"
            regex:
              - "^\\\\s*Cover\\\\b"
            bands: [top_of_page]
        """,
    )
    spec = load_spec(path)
    assert spec.spec_version == "1.0"
    assert spec.sections[0].section_id == "cover"


def test_loader_rejects_unsectioned_sentinel(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        spec_version: "1.0"
        sections:
          - section_id: unsectioned
            display_name: "Reserved"
        """,
    )
    with pytest.raises(BPCRSectionsSpecError):
        load_spec(path)


def test_loader_rejects_duplicate_section_ids(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        spec_version: "1.0"
        sections:
          - section_id: cover
            display_name: "Cover A"
          - section_id: cover
            display_name: "Cover B"
        """,
    )
    with pytest.raises(BPCRSectionsSpecError):
        load_spec(path)


def test_loader_rejects_invalid_section_id_pattern(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        spec_version: "1.0"
        sections:
          - section_id: "Cover Page"
            display_name: "Cover"
        """,
    )
    with pytest.raises(BPCRSectionsSpecError):
        load_spec(path)


def test_loader_rejects_uncompilable_regex(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        spec_version: "1.0"
        sections:
          - section_id: cover
            display_name: "Cover"
            regex:
              - "(unbalanced"
        """,
    )
    with pytest.raises(BPCRSectionsSpecError):
        load_spec(path)


def test_loader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BPCRSectionsSpecError):
        load_spec(tmp_path / "missing.yaml")


def test_pilot_spec_on_disk_loads() -> None:
    """The shipped pilot spec MUST always validate."""

    spec = load_spec(
        Path(__file__).resolve().parents[3]
        / "config"
        / "bmr"
        / "pilot"
        / "bpcr-section-spec.yaml"
    )
    assert spec.spec_version
    section_ids = {s.section_id for s in spec.sections}
    assert "yield_calculation" in section_ids
