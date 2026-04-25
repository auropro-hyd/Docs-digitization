"""Tests for the ``bmr-rules`` CLI entry point."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.bmr.cli.__main__ import main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "rules"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _run_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)
    exit_code = main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_cli_version(monkeypatch):
    code, out, _ = _run_cli(["--version"], monkeypatch)
    assert code == 0
    assert "bmr-rules" in out
    assert "1.0" in out


def test_cli_validate_valid_dir_exits_zero(monkeypatch):
    code, out, _ = _run_cli(["validate", str(FIXTURES / "valid")], monkeypatch)
    assert code == 0, out
    assert "OK" in out
    assert "0 failed" in out


def test_cli_validate_invalid_dir_exits_nonzero(monkeypatch):
    code, out, _ = _run_cli(["validate", str(FIXTURES / "invalid")], monkeypatch)
    assert code == 1
    assert "FAIL" in out


def test_cli_validate_json_output(monkeypatch):
    code, out, _ = _run_cli(
        ["validate", str(FIXTURES / "valid"), "--format", "json"], monkeypatch
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["ok"] >= 3
    for report in payload["reports"]:
        assert report["schema_version"] == "1.0"
        assert report["rule_id"] is not None
        assert report["ok"] is True


def test_cli_validate_missing_path_returns_exit_2(monkeypatch):
    code, _, err = _run_cli(
        ["validate", "/nonexistent/path/abc123"], monkeypatch
    )
    assert code == 2
    assert "does not exist" in err


def test_cli_validate_empty_dir_returns_exit_2(monkeypatch, tmp_path: Path):
    code, _, err = _run_cli(["validate", str(tmp_path)], monkeypatch)
    assert code == 2
    assert "no rule YAML files" in err


def test_cli_help_returns_exit_2(monkeypatch):
    code, _, _ = _run_cli([], monkeypatch)
    assert code == 2


# ── fixture-run subcommand ───────────────────────────────────────────────────


def _fixture_run_argv(
    rule_name: str,
    fixture_name: str,
    *,
    expect: str | None = None,
    fmt: str = "human",
) -> list[str]:
    argv = [
        "fixture-run",
        str(FIXTURES / "valid" / rule_name),
        "--fixture",
        str(FIXTURES / "fixtures" / fixture_name),
        "--repo-root",
        str(REPO_ROOT),
        "--format",
        fmt,
        "--color",
        "never",
    ]
    if expect is not None:
        argv.extend(["--expect", expect])
    return argv


def test_cli_fixture_run_fires_on_mismatch(monkeypatch):
    code, out, _ = _run_cli(
        _fixture_run_argv(
            "cross_doc_weight_match.yaml",
            "bpcr_weight_mismatch.json",
            expect="fires",
        ),
        monkeypatch,
    )
    assert code == 0, out
    assert "OK" in out
    assert "scope:   cross_document" in out
    assert "findings" in out
    assert "fired=True" in out


def test_cli_fixture_run_respects_not_fires_expectation(monkeypatch):
    code, out, _ = _run_cli(
        _fixture_run_argv(
            "cross_doc_weight_match.yaml",
            "bpcr_weight_match.json",
            expect="not_fires",
        ),
        monkeypatch,
    )
    assert code == 0, out
    assert "OK" in out


def test_cli_fixture_run_reports_expectation_mismatch(monkeypatch):
    code, out, _ = _run_cli(
        _fixture_run_argv(
            "cross_doc_weight_match.yaml",
            "bpcr_weight_match.json",
            expect="fires",  # fixture matches so rule stays silent
        ),
        monkeypatch,
    )
    assert code == 1, out
    assert "FAIL" in out
    assert "NOT met" in out


def test_cli_fixture_run_json_payload(monkeypatch):
    code, out, _ = _run_cli(
        _fixture_run_argv(
            "cross_doc_weight_match.yaml",
            "bpcr_weight_mismatch.json",
            expect="fires",
            fmt="json",
        ),
        monkeypatch,
    )
    assert code == 0, out
    payload = json.loads(out)
    assert payload["rule_id"] == "alcoa.accurate.bpcr-raw-material-weight-match"
    assert payload["fired"] is True
    assert payload["ok"] is True
    assert payload["findings"], "expected at least one finding"
    for finding in payload["findings"]:
        assert "evidence" in finding


def test_cli_fixture_run_missing_rule_exits_2(monkeypatch, tmp_path: Path):
    code, _, err = _run_cli(
        [
            "fixture-run",
            str(tmp_path / "nope.yaml"),
            "--fixture",
            str(FIXTURES / "fixtures" / "bpcr_weight_match.json"),
        ],
        monkeypatch,
    )
    assert code == 2
    assert "rule file not found" in err


def test_cli_fixture_run_missing_fixture_exits_2(monkeypatch, tmp_path: Path):
    code, _, err = _run_cli(
        [
            "fixture-run",
            str(FIXTURES / "valid" / "cross_doc_weight_match.yaml"),
            "--fixture",
            str(tmp_path / "nope.json"),
        ],
        monkeypatch,
    )
    assert code == 2
    assert "fixture not found" in err


# ── diff subcommand ──────────────────────────────────────────────────────────


def _write_tuned_copy(src: Path, tmp_path: Path, *, tolerance: float) -> Path:
    import yaml

    mapping = yaml.safe_load(src.read_text(encoding="utf-8"))
    mapping["tolerance"]["value"] = tolerance
    mapping["version"] = "1.1.0"
    target = tmp_path / "tuned.yaml"
    target.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return target


def test_cli_diff_reports_no_changes_for_identical(monkeypatch):
    rule = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    code, out, _ = _run_cli(
        ["diff", str(rule), str(rule), "--color", "never"], monkeypatch
    )
    assert code == 0
    assert "no changes" in out


def test_cli_diff_reports_tuning_edits(monkeypatch, tmp_path: Path):
    base = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    tuned = _write_tuned_copy(base, tmp_path, tolerance=0.5)
    code, out, _ = _run_cli(
        ["diff", str(base), str(tuned), "--color", "never"], monkeypatch
    )
    assert code == 0, out
    assert "/tolerance/value" in out
    assert "tolerance_relaxed" in out
    assert "version_bumped" in out


def test_cli_diff_exit_on_change(monkeypatch, tmp_path: Path):
    base = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    tuned = _write_tuned_copy(base, tmp_path, tolerance=0.2)
    code, _, _ = _run_cli(
        ["diff", str(base), str(tuned), "--exit-on-change", "--color", "never"],
        monkeypatch,
    )
    assert code == 1


def test_cli_diff_json_output(monkeypatch, tmp_path: Path):
    base = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    tuned = _write_tuned_copy(base, tmp_path, tolerance=0.5)
    code, out, _ = _run_cli(
        ["diff", str(base), str(tuned), "--format", "json"], monkeypatch
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["left_version"] == "1.0.0"
    assert payload["right_version"] == "1.1.0"
    assert any(e["path"] == "/tolerance/value" for e in payload["entries"])
    assert "tolerance_relaxed" in payload["tags"]


def test_cli_diff_missing_file_exits_2(monkeypatch, tmp_path: Path):
    base = FIXTURES / "valid" / "cross_doc_weight_match.yaml"
    code, _, err = _run_cli(
        ["diff", str(base), str(tmp_path / "missing.yaml")], monkeypatch
    )
    assert code == 2
    assert "right rule file not found" in err
