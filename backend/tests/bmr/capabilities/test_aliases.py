"""Tests for the alias table and name normalisation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.bmr.capabilities.aliases import AliasTable, load_alias_table, normalise_name

PILOT = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "rules"
    / "pilot"
    / "aliases"
    / "materials.yaml"
)


def test_normalise_strips_punctuation_and_lowercases():
    assert normalise_name("Lactose-Mono") == "lactose mono"
    assert normalise_name("Mg_Stearate") == "mg stearate"
    assert normalise_name("  Avicel  ") == "avicel"


def test_case_sensitive_option():
    assert normalise_name("Avicel", case_insensitive=False) == "Avicel"


def test_pilot_alias_file_loads():
    table = load_alias_table(PILOT)
    assert table.scope == "materials"
    assert "Lactose Monohydrate" in table.canonical_names()
    assert table.resolve("Lactose Mono") == "Lactose Monohydrate"
    assert table.resolve("LACTOSE MONOHYDRATE") == "Lactose Monohydrate"
    assert table.resolve("Avicel") == "Microcrystalline Cellulose"
    assert table.resolve("MCC") == "Microcrystalline Cellulose"
    assert table.resolve("totally unknown material") is None


def test_alias_table_rejects_conflicting_mapping(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    data = {
        "scope": "materials",
        "entries": [
            {"canonical": "A", "aliases": ["x"]},
            {"canonical": "B", "aliases": ["x"]},
        ],
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_alias_table(path)


def test_alias_table_rejects_missing_canonical(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    data = {"scope": "materials", "entries": [{"aliases": ["x"]}]}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_alias_table(path)


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_alias_table(tmp_path / "no-such-file.yaml")


def test_alias_table_empty_entries_is_ok(tmp_path: Path):
    path = tmp_path / "empty.yaml"
    path.write_text(yaml.safe_dump({"scope": "materials", "entries": []}), encoding="utf-8")
    table = load_alias_table(path)
    assert isinstance(table, AliasTable)
    assert table.canonical_names() == []
