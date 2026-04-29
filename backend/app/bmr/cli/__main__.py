"""``bmr-rules`` CLI entry point.

Subcommands:

- ``validate <path> [--format human|json]`` — validate a single rule YAML
  or a directory of rule YAMLs against the published schema.
- ``fixture-run <rule> --fixture <extraction.json> [--expect fires|not_fires]``
  — evaluate a single rule against a fixture and report findings +
  evidence. The ``bmr-rule-author`` skill (Spec 005) invokes this in
  Step 4 of its authoring flow.

Future subcommand ``diff`` is planned in spec 005 and will land
incrementally.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from app.bmr.capabilities.bpcr_section_detect import detect_bpcr_sections
from app.bmr.capabilities.bpcr_sections_spec import (
    BPCRSectionsSpecError,
    default_spec_path,
    load_spec,
)
from app.bmr.rules.diff import (
    ChangeKind,
    RuleDiffReport,
    diff_rule_files,
)
from app.bmr.rules.fixture_run import (
    ExpectOutcome,
    FixtureRunReport,
    run_rule_against_fixture,
)
from app.bmr.rules.loader import load_rule_bank
from app.bmr.rules.schema import available_schema_versions, default_schema_dir
from app.bmr.rules.validator import RuleValidationReport
from app.core.ports.ocr import OCRResult


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bmr-rules",
        description="BMR audit rule tooling (schema validation, diff, fixture runs).",
    )
    parser.add_argument(
        "--version", action="store_true", help="print CLI + schema versions and exit."
    )
    sub = parser.add_subparsers(dest="command")

    validate = sub.add_parser("validate", help="validate a rule YAML or directory")
    validate.add_argument("path", type=Path, help="rule YAML file or directory")
    validate.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format (default: human)",
    )
    validate.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize human output (default: auto)",
    )

    fixture = sub.add_parser(
        "fixture-run",
        help="evaluate a rule YAML against an extraction fixture",
    )
    fixture.add_argument("rule", type=Path, help="rule YAML file")
    fixture.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="extraction fixture (the same shape as extraction.json)",
    )
    fixture.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "repository root used to resolve relative aliases_file paths "
            "(defaults to the current working directory)"
        ),
    )
    fixture.add_argument(
        "--aliases-dir",
        type=Path,
        default=None,
        help="override directory to look up alias files by filename",
    )
    fixture.add_argument(
        "--expect",
        choices=("fires", "not_fires"),
        default=None,
        help=(
            "fail the run with exit code 1 if the rule's behaviour does "
            "not match the expectation"
        ),
    )
    fixture.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format (default: human)",
    )
    fixture.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize human output (default: auto)",
    )

    detect = sub.add_parser(
        "detect-sections",
        help="run BPCR layout-aware section detection against an OCR JSON",
    )
    detect.add_argument(
        "--ocr",
        type=Path,
        required=True,
        help="OCR result JSON (the same shape as OCRResult.model_dump_json()).",
    )
    detect.add_argument(
        "--spec",
        type=Path,
        default=None,
        help=(
            "BPCR section spec YAML. Defaults to "
            "AT_BMR__BPCR_SECTIONS_SPEC env var or the shipped pilot spec."
        ),
    )
    detect.add_argument(
        "--doc-id",
        default="bpcr",
        help="document id stamped onto the resulting BPCRSectionMap.",
    )
    detect.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format (default: human)",
    )
    detect.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize human output (default: auto)",
    )

    diff = sub.add_parser(
        "diff",
        help="structured diff between two rule YAML files",
    )
    diff.add_argument("left", type=Path, help="baseline rule YAML")
    diff.add_argument("right", type=Path, help="candidate rule YAML")
    diff.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="output format (default: human)",
    )
    diff.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="colorize human output (default: auto)",
    )
    diff.add_argument(
        "--exit-on-change",
        action="store_true",
        help=(
            "exit 1 when any change is detected (useful as a pre-commit "
            "guard to force a --version bump)"
        ),
    )

    return parser


# ── Output helpers ───────────────────────────────────────────────────────────


def _use_color(stream, mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return stream.isatty()


class _Style:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    DIM = "\033[2m"
    BOLD = "\033[1m"


def _color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{code}{text}{_Style.RESET}"


def _format_human(
    reports: list[RuleValidationReport], *, color: bool, stream
) -> int:
    total_errors = 0
    ok_count = 0
    fail_count = 0
    for report in reports:
        header = report.source_path or report.rule_id or "<unknown>"
        if report.ok:
            ok_count += 1
            ok_tag = _color("OK", _Style.GREEN, color)
            stream.write(f"{header}  {ok_tag}\n")
            continue
        fail_count += 1
        fail_tag = _color("FAIL", _Style.RED, color)
        stream.write(f"{header}  {fail_tag}\n")
        for err in report.errors:
            total_errors += 1
            ident = report.rule_id or "<rule>"
            label = _color(f"[{ident}]", _Style.BOLD, color)
            path_part = _color(err.path, _Style.DIM, color)
            stream.write(f"  {label} {path_part}: {err.message}\n")
            if err.fix_hint:
                stream.write(_color("      fix:\n", _Style.DIM, color))
                for line in err.fix_hint.splitlines():
                    stream.write(f"        {line}\n")
    total = len(reports)
    stream.write("\n")
    summary = (
        f"{total} rule{'s' if total != 1 else ''} checked, "
        f"{ok_count} ok, {fail_count} failed, {total_errors} errors."
    )
    stream.write(summary + "\n")
    return 0 if fail_count == 0 else 1


def _format_json(reports: list[RuleValidationReport], stream) -> int:
    payload = {
        "reports": [r.to_dict() for r in reports],
        "summary": {
            "total": len(reports),
            "ok": sum(1 for r in reports if r.ok),
            "failed": sum(1 for r in reports if not r.ok),
        },
    }
    stream.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return 0 if payload["summary"]["failed"] == 0 else 1


# ── Subcommand: validate ─────────────────────────────────────────────────────


def _run_validate(args: argparse.Namespace) -> int:
    target: Path = args.path
    if not target.exists():
        sys.stderr.write(f"bmr-rules: path does not exist: {target}\n")
        return 2

    bank = load_rule_bank(target)
    if not bank.reports:
        sys.stderr.write(
            f"bmr-rules: no rule YAML files found under {target} "
            f"(looked for *.yaml / *.yml).\n"
        )
        return 2

    if args.format == "json":
        return _format_json(bank.reports, sys.stdout)
    color = _use_color(sys.stdout, args.color)
    return _format_human(bank.reports, color=color, stream=sys.stdout)


# ── Subcommand: fixture-run ──────────────────────────────────────────────────


def _format_fixture_run_human(
    report: FixtureRunReport, *, color: bool, stream
) -> int:
    header = report.rule_id or report.rule_source_path
    tag = (
        _color("OK", _Style.GREEN, color)
        if report.ok
        else _color("FAIL", _Style.RED, color)
    )
    stream.write(f"{header}  {tag}\n")

    stream.write(f"  rule:    {report.rule_source_path}\n")
    stream.write(f"  fixture: {report.fixture_path}\n")
    if report.scope:
        stream.write(f"  scope:   {report.scope}\n")

    if report.validation is not None and not report.validation.ok:
        fail_tag = _color("schema FAIL", _Style.RED, color)
        stream.write(f"  {fail_tag}\n")
        for err in report.validation.errors:
            stream.write(f"    {err.path}: {err.message}\n")

    for err in report.errors:
        err_tag = _color("error", _Style.RED, color)
        stream.write(f"  {err_tag} {err.path}: {err.message}\n")

    if report.expected != "unspecified":
        verdict = (
            _color("met", _Style.GREEN, color)
            if report.expectation_met
            else _color("NOT met", _Style.RED, color)
        )
        stream.write(
            f"  expected {report.expected}, "
            f"fired={report.fired} — {verdict}\n"
        )
    else:
        stream.write(f"  fired:   {report.fired}\n")

    if report.findings:
        stream.write(f"  findings ({len(report.findings)}):\n")
        for i, finding in enumerate(report.findings, start=1):
            status = finding.status.value
            sev = finding.severity
            status_tag = _color(
                f"[{status}/{sev}]",
                _Style.YELLOW if status == "open" else _Style.DIM,
                color,
            )
            stream.write(f"    {i}. {status_tag} {finding.summary}\n")
            for ev in finding.evidence:
                stream.write(
                    f"       evidence: doc={ev.doc_id} "
                    f"page={ev.page_index} field={ev.field} value={ev.value!r}\n"
                )
    else:
        stream.write("  findings: (none)\n")

    return 0 if report.ok else 1


def _format_fixture_run_json(report: FixtureRunReport, stream) -> int:
    stream.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return 0 if report.ok else 1


def _run_fixture_run(args: argparse.Namespace) -> int:
    rule_path: Path = args.rule
    fixture_path: Path = args.fixture
    if not rule_path.exists():
        sys.stderr.write(f"bmr-rules: rule file not found: {rule_path}\n")
        return 2
    if not fixture_path.exists():
        sys.stderr.write(f"bmr-rules: fixture not found: {fixture_path}\n")
        return 2

    repo_root = (args.repo_root or Path.cwd()).resolve()
    expected: ExpectOutcome = args.expect or "unspecified"
    report = run_rule_against_fixture(
        rule_path=rule_path,
        fixture_path=fixture_path,
        repo_root=repo_root,
        aliases_dir=args.aliases_dir,
        expected=expected,
    )

    if args.format == "json":
        return _format_fixture_run_json(report, sys.stdout)
    color = _use_color(sys.stdout, args.color)
    return _format_fixture_run_human(report, color=color, stream=sys.stdout)


# ── Subcommand: diff ─────────────────────────────────────────────────────────


_KIND_GLYPH = {
    ChangeKind.ADDED: ("+", _Style.GREEN),
    ChangeKind.REMOVED: ("-", _Style.RED),
    ChangeKind.CHANGED: ("~", _Style.YELLOW),
}


def _format_diff_human(
    report: RuleDiffReport, *, color: bool, stream
) -> int:
    left = report.left_id or report.left_source
    right = report.right_id or report.right_source
    if not report.has_changes:
        stream.write(
            f"{left} vs {right}  {_color('no changes', _Style.GREEN, color)}\n"
        )
        return 0

    stream.write(f"{left} → {right}\n")
    if report.left_version or report.right_version:
        stream.write(
            f"  version: {report.left_version} → {report.right_version}\n"
        )
    for entry in report.entries:
        glyph, style = _KIND_GLYPH[entry.kind]
        glyph_txt = _color(glyph, style, color)
        stream.write(f"  {glyph_txt} {entry.path or '/'}\n")
        if entry.kind in (ChangeKind.CHANGED, ChangeKind.REMOVED):
            stream.write(
                _color(f"      - {entry.left!r}\n", _Style.DIM, color)
            )
        if entry.kind in (ChangeKind.CHANGED, ChangeKind.ADDED):
            stream.write(
                _color(f"      + {entry.right!r}\n", _Style.DIM, color)
            )
        if entry.tags:
            tag_text = ", ".join(t.value for t in entry.tags)
            stream.write(
                _color(f"      tags: {tag_text}\n", _Style.BOLD, color)
            )
    stream.write("\n")
    summary = (
        f"{len(report.entries)} change"
        f"{'s' if len(report.entries) != 1 else ''}."
    )
    if report.tags:
        summary += " semantic: " + ", ".join(sorted(t.value for t in report.tags))
    stream.write(summary + "\n")
    return 0


def _format_diff_json(report: RuleDiffReport, stream) -> int:
    stream.write(json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return 0


def _run_diff(args: argparse.Namespace) -> int:
    left: Path = args.left
    right: Path = args.right
    for label, path in (("left", left), ("right", right)):
        if not path.exists():
            sys.stderr.write(f"bmr-rules: {label} rule file not found: {path}\n")
            return 2

    report = diff_rule_files(left, right)

    if args.format == "json":
        code = _format_diff_json(report, sys.stdout)
    else:
        color = _use_color(sys.stdout, args.color)
        code = _format_diff_human(report, color=color, stream=sys.stdout)

    if args.exit_on_change and report.has_changes:
        return 1
    return code


# ── Subcommand: detect-sections ──────────────────────────────────────────────


def _format_detect_sections_human(report, *, color: bool, stream) -> int:
    outcome_color = {
        "ok": _Style.GREEN,
        "partial": _Style.YELLOW,
        "failed": _Style.RED,
    }.get(report.outcome, _Style.DIM)
    header = (
        f"doc_id={report.doc_id} method={report.method} "
        f"spec_version={report.spec_version} detector={report.detector_version}"
    )
    stream.write(header + "\n")
    stream.write(
        "outcome: "
        + _color(report.outcome, outcome_color, color)
        + f"  ({len(report.spans)} span"
        + ("s" if len(report.spans) != 1 else "")
        + ")\n"
    )
    if report.notes:
        stream.write(_color("notes: " + ", ".join(report.notes) + "\n", _Style.DIM, color))
    stream.write("\n")
    for span in report.spans:
        glyph = _color("●", _Style.GREEN, color) if span.section_id != "unsectioned" \
            else _color("○", _Style.DIM, color)
        line = (
            f"  {glyph} pages {span.start_page:>3}–{span.end_page:<3}  "
            f"{span.section_id}"
        )
        if span.display_name:
            line += f"  ({span.display_name})"
        line += f"  conf={span.confidence:.2f}  via={span.detection_method}"
        stream.write(line + "\n")
        if span.matched_text:
            preview = _color(f"      matched: “{span.matched_text}”\n", _Style.DIM, color)
            stream.write(preview)
    stream.write("\n")
    return 0 if report.outcome != "failed" else 1


def _format_detect_sections_json(report, stream) -> int:
    stream.write(json.dumps(report.model_dump(mode="json"), indent=2) + "\n")
    return 0 if report.outcome != "failed" else 1


def _run_detect_sections(args: argparse.Namespace) -> int:
    ocr_path: Path = args.ocr
    if not ocr_path.is_file():
        sys.stderr.write(f"bmr-rules: OCR JSON not found: {ocr_path}\n")
        return 2
    try:
        ocr = OCRResult.model_validate_json(ocr_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface the parse error verbatim
        sys.stderr.write(f"bmr-rules: failed to parse OCR JSON: {exc}\n")
        return 2

    spec_path = args.spec or default_spec_path()
    try:
        spec = load_spec(spec_path)
    except BPCRSectionsSpecError as exc:
        sys.stderr.write(f"bmr-rules: bad section spec: {exc}\n")
        return 2

    report = detect_bpcr_sections(doc_id=args.doc_id, ocr=ocr, sections_spec=spec)

    if args.format == "json":
        return _format_detect_sections_json(report, sys.stdout)
    color = _use_color(sys.stdout, args.color)
    return _format_detect_sections_human(report, color=color, stream=sys.stdout)


# ── Entry ────────────────────────────────────────────────────────────────────


def _print_version() -> None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        backend_version = version("auto-transcription")
    except PackageNotFoundError:
        backend_version = "unknown"
    versions = available_schema_versions() or ["(none found)"]
    sys.stdout.write(
        f"bmr-rules (backend {backend_version})\n"
        f"schema dir: {default_schema_dir()}\n"
        f"schema versions: {', '.join(versions)}\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        _print_version()
        return 0

    if args.command == "validate":
        return _run_validate(args)
    if args.command == "fixture-run":
        return _run_fixture_run(args)
    if args.command == "diff":
        return _run_diff(args)
    if args.command == "detect-sections":
        return _run_detect_sections(args)

    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
