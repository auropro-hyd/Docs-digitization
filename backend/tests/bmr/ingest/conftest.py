"""Shared fixtures for BMR ingestion tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.bmr.ingest.manifest import Manifest


@pytest.fixture
def pilot_manifest_dict() -> dict:
    path = (
        Path(__file__).resolve().parents[3]
        / "config"
        / "bmr"
        / "pilot"
        / "manifests"
        / "default.yaml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def pilot_manifest(pilot_manifest_dict: dict) -> Manifest:
    return Manifest.model_validate(pilot_manifest_dict)
