"""Single-rule fixture execution (Spec 005 — authoring-side).

The :mod:`app.bmr.workflow.stages` compliance stage evaluates every rule
in a bank across the full 5-stage pipeline. Rule authors iterating on a
single YAML don't need that ceremony — they need a tight "load one rule,
run it against a tiny extraction, show me the findings" loop. That is
what this module provides.

It is deliberately decoupled from LangGraph, the run store, and the HITL
service so the `bmr-rules fixture-run` CLI and the `bmr-rule-author`
skill can both call it without standing up the full application.

Both inputs are files on disk:

1. A rule YAML (anywhere the author keeps their draft).
2. An extraction fixture — the same ``extraction.json`` shape the
   sidecar extractor consumes, so the author can reuse production
   fixtures or hand-craft a minimal one.

Output is a :class:`FixtureRunReport` carrying the findings produced,
the scope that dispatched, and — critically — whether the result matches
the author's expectations (``--expect fires | not_fires`` on the CLI).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.bmr.capabilities.aliases import AliasTable, load_alias_table
from app.bmr.capabilities.evidence import FindingDraft, FindingStatus
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.capabilities.rule_eval import (
    cross_doc_rule_eval_v1,
    page_aggregate_eval_v1,
    same_page_eval_v1,
)
from app.bmr.capabilities.synthesise import checklist_synthesise_v1
from app.bmr.rules.loader import LoadedRule, load_rule_file
from app.bmr.rules.validator import RuleValidationReport

ExpectOutcome = Literal["fires", "not_fires", "unspecified"]


@dataclass(frozen=True)
class FixtureRunError:
    """A problem preventing the fixture run from producing findings."""

    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixtureRunReport:
    """Structured output of a single fixture run.

    Consumers:

    - The ``bmr-rules fixture-run`` CLI renders this as human text or
      JSON for ``--format=json``.
    - The ``bmr-rule-author`` skill reads the JSON form and folds the
      findings + evidence references into its ``### Validation`` block
      (see SKILL.md Step 5).
    """

    rule_id: str | None
    rule_version: str | None
    rule_source_path: str
    fixture_path: str
    rule_content_hash: str | None = None
    scope: str | None = None
    # Spec 005 FR-013 — surface deprecation so authors testing a
    # retired rule know the pipeline would skip it in production.
    deprecated: bool = False
    superseded_by: str | None = None
    validation: RuleValidationReport | None = None
    findings: list[FindingDraft] = field(default_factory=list)
    errors: list[FixtureRunError] = field(default_factory=list)
    expected: ExpectOutcome = "unspecified"

    @property
    def fired(self) -> bool:
        """True if the rule emitted at least one ``open`` finding."""

        return any(f.status is FindingStatus.OPEN for f in self.findings)

    @property
    def expectation_met(self) -> bool | None:
        """``None`` if no expectation was supplied; else the verdict."""

        if self.expected == "fires":
            return self.fired
        if self.expected == "not_fires":
            return not self.fired
        return None

    @property
    def ok(self) -> bool:
        """Overall pass/fail used as the CLI exit code.

        A run is "ok" when there were no hard errors, the schema
        validation passed, and the expectation (if provided) was met.
        """

        if self.errors:
            return False
        if self.validation is not None and not self.validation.ok:
            return False
        met = self.expectation_met
        if met is not None:
            return met
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_content_hash": self.rule_content_hash,
            "rule_source_path": self.rule_source_path,
            "fixture_path": self.fixture_path,
            "scope": self.scope,
            "deprecated": self.deprecated,
            "superseded_by": self.superseded_by,
            "expected": self.expected,
            "fired": self.fired,
            "expectation_met": self.expectation_met,
            "ok": self.ok,
            "validation": (
                self.validation.to_dict() if self.validation is not None else None
            ),
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "rule_version": f.rule_version,
                    "rule_content_hash": f.rule_content_hash,
                    "status": f.status.value,
                    "severity": f.severity,
                    "alcoa_tag": f.alcoa_tag,
                    "gmp_category": f.gmp_category,
                    "source": f.source.value,
                    "summary": f.summary,
                    "detail": f.detail,
                    "tolerance_applied": f.tolerance_applied,
                    "fields": dict(f.fields),
                    "evidence": [
                        {
                            "doc_id": e.doc_id,
                            "page_index": e.page_index,
                            "field": e.field,
                            "value": e.value,
                            "note": e.note,
                        }
                        for e in f.evidence
                    ],
                }
                for f in self.findings
            ],
            "errors": [e.to_dict() for e in self.errors],
        }


_DISPATCH = {
    "same_page": same_page_eval_v1,
    "cross_document": cross_doc_rule_eval_v1,
    "page_aggregate": page_aggregate_eval_v1,
}


def _load_extracted(path: Path) -> ExtractedPackage:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("package_id", path.stem)
    return ExtractedPackage.model_validate(payload)


def _resolve_aliases(
    loaded: LoadedRule,
    *,
    repo_root: Path,
    aliases_dir: Path | None,
) -> tuple[dict[str, AliasTable], list[FixtureRunError]]:
    """Load every ``aliases_file`` referenced by ``loaded``.

    Mirrors the behaviour of :func:`app.bmr.workflow.stages._load_alias_tables`
    but scoped to a single rule so the authoring path doesn't depend on
    an entire rule bank. Missing alias files are surfaced as fixture-run
    errors rather than crashes so the author gets an actionable message.
    """

    context_object = loaded.rule.get("context_object") or {}
    entity_match = context_object.get("entity_match") or {}
    rel = entity_match.get("aliases_file")
    if not rel:
        return {}, []

    candidates: list[Path] = [repo_root / rel]
    if aliases_dir is not None:
        candidates.append(aliases_dir / Path(rel).name)
    for candidate in candidates:
        if candidate.is_file():
            return {str(rel): load_alias_table(candidate)}, []

    return {}, [
        FixtureRunError(
            path="/context_object/entity_match/aliases_file",
            message=(
                f"aliases_file {rel!r} not found. Looked in: "
                + ", ".join(str(c) for c in candidates)
                + "."
            ),
        )
    ]


def run_rule_against_fixture(
    *,
    rule_path: Path,
    fixture_path: Path,
    repo_root: Path,
    aliases_dir: Path | None = None,
    expected: ExpectOutcome = "unspecified",
    peer_findings: list[FindingDraft] | None = None,
) -> FixtureRunReport:
    """Load a rule + fixture and evaluate just that rule.

    ``peer_findings`` lets ``checklist_synthesis`` rules roll up findings
    from sibling rules the author already validated; leaf-rule fixture
    runs leave it empty.
    """

    report = FixtureRunReport(
        rule_id=None,
        rule_version=None,
        rule_source_path=str(rule_path),
        fixture_path=str(fixture_path),
    )

    if not rule_path.is_file():
        report.errors.append(
            FixtureRunError(
                path="/rule_path", message=f"rule file not found: {rule_path}"
            )
        )
        return report
    if not fixture_path.is_file():
        report.errors.append(
            FixtureRunError(
                path="/fixture_path",
                message=f"fixture file not found: {fixture_path}",
            )
        )
        return report

    loaded, validation = load_rule_file(rule_path)
    report.validation = validation
    report.rule_id = validation.rule_id
    if loaded is None:
        # Validation report carries the reasons; no need to re-shape
        # them into fixture-run errors.
        report.expected = expected
        return report

    report.rule_id = loaded.id
    report.rule_version = loaded.version
    report.rule_content_hash = loaded.content_hash
    report.scope = loaded.scope
    report.deprecated = loaded.deprecated
    report.superseded_by = loaded.superseded_by
    report.expected = expected

    try:
        extracted = _load_extracted(fixture_path)
    except (json.JSONDecodeError, ValueError) as exc:
        report.errors.append(
            FixtureRunError(
                path="/fixture_path",
                message=f"fixture is not a valid ExtractedPackage JSON: {exc}",
            )
        )
        return report

    if loaded.scope == "checklist_synthesis":
        synthesised = checklist_synthesise_v1(
            rule=loaded.rule, findings=list(peer_findings or [])
        )
        for draft in synthesised:
            draft.rule_content_hash = loaded.content_hash
        report.findings.extend(synthesised)
        return report

    evaluator = _DISPATCH.get(loaded.scope)
    if evaluator is None:
        report.errors.append(
            FixtureRunError(
                path="/context_object/scope",
                message=(
                    f"no evaluator registered for scope {loaded.scope!r}. "
                    f"Known scopes: {sorted(_DISPATCH)} + 'checklist_synthesis'."
                ),
            )
        )
        return report

    alias_tables, alias_errors = _resolve_aliases(
        loaded, repo_root=repo_root, aliases_dir=aliases_dir
    )
    report.errors.extend(alias_errors)
    if alias_errors:
        # Alias misses turn numeric rules into false negatives silently;
        # short-circuit instead of pretending to fire/not-fire.
        return report

    findings = evaluator(
        rule=loaded.rule, extracted=extracted, alias_tables=alias_tables
    )
    for draft in findings:
        draft.rule_content_hash = loaded.content_hash
    report.findings.extend(findings)
    return report


__all__ = [
    "ExpectOutcome",
    "FixtureRunError",
    "FixtureRunReport",
    "run_rule_against_fixture",
]
