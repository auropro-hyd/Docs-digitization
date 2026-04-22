"""Tests for the manifest loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.bmr.ingest.manifest import (
    Manifest,
    ManifestValidationError,
    load_manifest,
)

PILOT_DIR = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "bmr"
    / "pilot"
    / "manifests"
)


def test_pilot_manifest_loads():
    manifest = load_manifest(PILOT_DIR / "default.yaml")
    assert isinstance(manifest, Manifest)
    assert manifest.id == "pilot.default"
    assert manifest.canonical_role_id == "BPCR"
    assert "BMR" in manifest.role_ids()
    assert "BPCR" in manifest.role_ids()
    assert "RawMaterialPage" in manifest.role_ids()


def test_missing_manifest_raises(tmp_path: Path):
    with pytest.raises(ManifestValidationError):
        load_manifest(tmp_path / "nope.yaml")


def test_duplicate_role_ids_rejected(
    tmp_path: Path, pilot_manifest_dict: dict
):
    bad = dict(pilot_manifest_dict)
    bad["required_roles"] = list(bad["required_roles"]) + [
        {"id": "BMR", "label": "Dup", "cardinality": "exactly_one"}
    ]
    target = tmp_path / "dup.yaml"
    target.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(target)
    assert "duplicate role" in str(exc_info.value).lower()


def test_multiple_canonical_roles_rejected(
    tmp_path: Path, pilot_manifest_dict: dict
):
    bad = dict(pilot_manifest_dict)
    roles = []
    for r in bad["required_roles"]:
        roles.append({**r, "canonical": True})
    bad["required_roles"] = roles
    target = tmp_path / "multi_canonical.yaml"
    target.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(target)
    assert "canonical" in str(exc_info.value).lower()


def test_classifier_pattern_unknown_role_rejected(
    tmp_path: Path, pilot_manifest_dict: dict
):
    bad = dict(pilot_manifest_dict)
    bad["classifier"] = dict(bad["classifier"])
    bad["classifier"]["filename_patterns"] = {
        "NotARealRole": ["*unknown*.pdf"],
    }
    target = tmp_path / "bad_patterns.yaml"
    target.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ManifestValidationError) as exc_info:
        load_manifest(target)
    assert "unknown role" in str(exc_info.value).lower()
