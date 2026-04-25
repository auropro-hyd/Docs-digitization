"""YAML rule loader for the BMR rule engine.

Reads ``*.yaml`` files from a rule directory, validates each against its
declared schema via :mod:`app.bmr.rules.validator`, and returns
:class:`LoadedRule` records. The loader is side-effect free.

Every loaded rule carries a ``content_hash`` computed over the canonical
JSON form of its body (Spec 005 FR-005). The hash is the pipeline's
fingerprint of the rule at load time: two YAMLs with identical bodies
share a hash; any change produces a new one. Findings emitted by the
compliance stage stamp this hash so prior audit runs can be replayed
deterministically — even if the author never bumped the semver
``version`` field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.bmr.rules.validator import (
    RuleValidationError,
    RuleValidationReport,
    validate_rule_mapping,
)

# Keys computed at load time / not part of the authored rule body. They
# are stripped before hashing so the hash is stable across loads and
# independent of whatever the loader chooses to inject.
_HASH_EXCLUDED_KEYS = frozenset({"content_hash", "source_path"})


def _canonicalize_for_hash(value: Any) -> Any:
    """Recursively normalize a rule body for hashing.

    YAML happily parses ``1`` as ``int`` and ``1.0`` as ``float`` —
    semantically identical but producing different JSON output and
    therefore different SHA-256 digests. For the content hash to be a
    stable fingerprint of the rule's *meaning*, every numeric leaf is
    projected onto a single representation (``float``, then formatted
    with ``repr``), booleans are kept distinct from numbers, and lists
    preserve order (rule authors do rely on list order).
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return f"__num__:{float(value)!r}"
    if isinstance(value, float):
        return f"__num__:{value!r}"
    if isinstance(value, dict):
        return {k: _canonicalize_for_hash(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize_for_hash(v) for v in value]
    return value


def compute_rule_content_hash(mapping: dict[str, Any]) -> str:
    """Return a deterministic SHA-256 of the rule's canonical body.

    The body is serialised with sorted keys, stable separators, and
    numeric-type normalization so the hash is identical across
    platforms and across equivalent int/float encodings. Keys listed in
    :data:`_HASH_EXCLUDED_KEYS` are removed first — they are metadata,
    not rule content, and including them would make every load produce
    a new hash.
    """

    body = {k: v for k, v in mapping.items() if k not in _HASH_EXCLUDED_KEYS}
    canonical = json.dumps(
        _canonicalize_for_hash(body),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoadedRule:
    """A rule YAML file that parsed and validated successfully."""

    rule: dict[str, Any]
    source_path: str
    schema_version: str
    content_hash: str

    @property
    def id(self) -> str:
        return str(self.rule["id"])

    @property
    def version(self) -> str:
        return str(self.rule["version"])

    @property
    def scope(self) -> str:
        return str(self.rule["context_object"]["scope"])

    @property
    def deprecated(self) -> bool:
        return bool(self.rule.get("deprecated", False))

    @property
    def superseded_by(self) -> str | None:
        value = self.rule.get("superseded_by")
        return value if isinstance(value, str) else None

    @property
    def stamped_version(self) -> str:
        """Author-facing compound version (``<semver>+<hash12>``).

        Used wherever a single string identifying "this rule at this
        content" is needed — e.g. the ``rule_version`` column on
        findings persisted by prior runs. The suffix makes it
        unmistakable that the reproducibility anchor is the content
        hash, not the semver.
        """

        return f"{self.version}+{self.content_hash[:12]}"


@dataclass
class RuleBank:
    """A collection of rules loaded from a directory.

    Callers typically treat the bank as immutable after construction.
    """

    rules: list[LoadedRule] = field(default_factory=list)
    reports: list[RuleValidationReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.reports)

    @property
    def errors(self) -> list[RuleValidationError]:
        flat: list[RuleValidationError] = []
        for report in self.reports:
            flat.extend(report.errors)
        return flat

    def by_id(self, rule_id: str) -> LoadedRule | None:
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        return None


def _iter_yaml_files(target: Path) -> list[Path]:
    if target.is_file():
        if target.suffix.lower() in {".yaml", ".yml"}:
            return [target]
        return []
    if target.is_dir():
        return sorted(p for p in target.rglob("*.yaml") if p.is_file()) + sorted(
            p for p in target.rglob("*.yml") if p.is_file()
        )
    return []


def load_rule_file(path: Path) -> tuple[LoadedRule | None, RuleValidationReport]:
    """Load and validate a single rule YAML.

    Returns ``(loaded, report)``. ``loaded`` is ``None`` iff the report has
    blocking errors.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        report = RuleValidationReport(
            rule_id=None,
            schema_version=None,
            source_path=str(path),
            errors=[
                RuleValidationError(
                    path="/",
                    message=f"cannot read rule file: {exc}",
                    severity="blocking",
                )
            ],
        )
        return None, report

    try:
        mapping = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        report = RuleValidationReport(
            rule_id=None,
            schema_version=None,
            source_path=str(path),
            errors=[
                RuleValidationError(
                    path="/",
                    message=f"invalid YAML: {exc}",
                    severity="blocking",
                )
            ],
        )
        return None, report

    report = validate_rule_mapping(mapping, source_path=path)
    if not report.ok:
        return None, report

    assert isinstance(mapping, dict)  # validator guarantees this if report is ok
    content_hash = compute_rule_content_hash(mapping)
    loaded = LoadedRule(
        rule=mapping,
        source_path=str(path),
        schema_version=str(mapping["schema_version"]),
        content_hash=content_hash,
    )
    return loaded, report


def _strip_version_suffix(token: str) -> str:
    """``rule_id@1.2.3`` → ``rule_id`` (stamped_version → bare id)."""

    return token.split("@", 1)[0]


def _validate_supersession_chains(bank: RuleBank) -> None:
    """Attach cycle errors to bank reports.

    ``superseded_by`` forms a directed graph across rules. A cycle would
    make prior-run replay tools loop forever, so any cycle is reported
    as a blocking error keyed to the offending rule's source path.

    A ``superseded_by`` pointing at a rule that is *not* in the current
    bank is **not** an error: rules can legitimately retire towards a
    successor that lives in a different bank or has not been loaded
    together — we only fail loud on structural loops.
    """

    by_id = {r.id: r for r in bank.rules}

    def _report_for(rule: LoadedRule) -> RuleValidationReport:
        for rep in bank.reports:
            if rep.source_path == rule.source_path:
                return rep
        raise AssertionError(
            f"missing report for loaded rule {rule.id} at {rule.source_path}"
        )

    for rule in bank.rules:
        if rule.superseded_by is None:
            continue
        visited: list[str] = [rule.id]
        current: LoadedRule | None = rule
        while current is not None and current.superseded_by is not None:
            successor_id = _strip_version_suffix(current.superseded_by)
            if successor_id in visited:
                _report_for(rule).errors.append(
                    RuleValidationError(
                        path="/superseded_by",
                        message=(
                            "superseded_by chain forms a cycle: "
                            + " -> ".join([*visited, successor_id])
                        ),
                        severity="blocking",
                    )
                )
                break
            visited.append(successor_id)
            current = by_id.get(successor_id)


def load_rule_bank(target: Path) -> RuleBank:
    """Load every rule YAML under ``target`` (file or directory)."""

    bank = RuleBank()
    files = _iter_yaml_files(target)
    for path in files:
        loaded, report = load_rule_file(path)
        bank.reports.append(report)
        if loaded is not None:
            bank.rules.append(loaded)
    _validate_supersession_chains(bank)
    # Any rule whose supersession chain failed must be removed from the
    # active set so downstream evaluators do not run it.
    bad_paths = {rep.source_path for rep in bank.reports if not rep.ok}
    if bad_paths:
        bank.rules = [r for r in bank.rules if r.source_path not in bad_paths]
    return bank


__all__ = [
    "LoadedRule",
    "RuleBank",
    "compute_rule_content_hash",
    "load_rule_bank",
    "load_rule_file",
]
