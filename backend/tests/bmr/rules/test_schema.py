"""Tests for schema discovery and loading."""

from __future__ import annotations

import json

import pytest

from app.bmr.rules.schema import (
    SchemaNotFoundError,
    available_schema_versions,
    default_schema_dir,
    load_schema,
    schema_path_for,
)


def test_default_schema_dir_exists():
    assert default_schema_dir().is_dir()


def test_v1_0_schema_is_listed():
    versions = available_schema_versions()
    assert "1.0" in versions


def test_load_v1_0_schema_parses():
    schema = load_schema("1.0")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "BMR Audit Rule"
    assert "context_object" in schema["properties"]


def test_schema_path_round_trips():
    path = schema_path_for("1.0")
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["title"] == "BMR Audit Rule"


def test_unknown_version_raises():
    with pytest.raises(SchemaNotFoundError):
        load_schema("99.99")


def test_malformed_version_string_raises():
    with pytest.raises(SchemaNotFoundError):
        load_schema("not-semver")
