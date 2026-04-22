"""Deterministic JSON-Schema-backed validator for BMR rule mappings.

Converts raw ``jsonschema`` errors into author-facing
:class:`RuleValidationError` records with JSON pointers and ``fix_hint``
snippets. Never calls an LLM, network, or DB (Constitution IX — rule
validation is a correctness boundary).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as _JsonSchemaError

from app.bmr.rules.schema import (
    SchemaNotFoundError,
    available_schema_versions,
    load_schema,
)


@dataclass(frozen=True)
class RuleValidationError:
    """Single author-facing validation error for a rule mapping."""

    path: str
    message: str
    fix_hint: str | None = None
    severity: str = "blocking"  # "blocking" | "warning"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleValidationReport:
    """Aggregate validation outcome for a single rule mapping."""

    rule_id: str | None
    schema_version: str | None
    source_path: str | None = None
    errors: list[RuleValidationError] = field(default_factory=list)
    warnings: list[RuleValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


# ── Error shaping ────────────────────────────────────────────────────────────


def _json_pointer(path_parts: list[Any]) -> str:
    if not path_parts:
        return "/"
    return "/" + "/".join(str(p) for p in path_parts)


def _shape_required_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    missing = err.message.rsplit("'", 2)
    property_name = missing[1] if len(missing) >= 3 else None

    ctx = _required_context(err)
    if property_name == "entity_match" and ctx == "cross_document":
        return (
            "context_object.scope = cross_document requires an entity_match block.",
            (
                "entity_match:\n"
                "  strategy: normalise\n"
                "  normalise: true\n"
                "  aliases_file: backend/config/rules/pilot/aliases/materials.yaml"
            ),
        )
    if property_name == "role" and ctx == "cross_document":
        return (
            "context_object.scope = cross_document requires a role "
            "(e.g. the counterpart document role from the manifest).",
            "role: RawMaterialPage",
        )
    if property_name == "target" and ctx == "cross_document":
        return (
            "context_object.scope = cross_document requires a target field reference.",
            "target:\n  field: weight_kg",
        )
    if property_name == "page_selector" and ctx == "page_aggregate":
        return (
            "context_object.scope = page_aggregate requires a page_selector.",
            "page_selector:\n  document_role: BPCR\n  page_filter: all_bpcr_step_pages",
        )
    if property_name == "aggregation" and ctx == "page_aggregate":
        return (
            "context_object.scope = page_aggregate requires an aggregation.",
            "aggregation: sum",
        )
    if property_name:
        return (f"missing required field '{property_name}'.", None)
    return (err.message, None)


def _required_context(err: _JsonSchemaError) -> str | None:
    """Infer the scope ('cross_document' | 'page_aggregate' | 'same_page') for a required error.

    Conditional schema branches (``if``/``then``) attach the error to
    ``/context_object`` or ``/`` on the root; we inspect the current
    ``instance`` to pull the scope out.
    """

    parts = [str(p) for p in err.absolute_path]
    instance = err.instance

    # Case: error on /context_object — instance IS the context_object dict.
    if parts == ["context_object"] and isinstance(instance, dict):
        scope = instance.get("scope")
        return scope if isinstance(scope, str) else None

    # Case: error on root / — instance is the full rule; walk into context_object.
    if not parts and isinstance(instance, dict):
        ctx = instance.get("context_object")
        if isinstance(ctx, dict):
            scope = ctx.get("scope")
            return scope if isinstance(scope, str) else None

    return None


def _shape_tolerance_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    if err.validator == "exclusiveMinimum":
        return (
            f"tolerance.value must be a positive number; got {err.instance!r}.",
            "tolerance:\n  kind: absolute\n  value: 0.1\n  unit: kg",
        )
    return (err.message, None)


def _shape_enum_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    allowed = err.validator_value
    if isinstance(allowed, list):
        return (
            f"value {err.instance!r} is not one of {allowed}.",
            None,
        )
    return (err.message, None)


def _shape_unevaluated_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    # jsonschema reports message like "Unevaluated properties are not allowed ('foo' was unexpected)"
    return (
        f"unknown property in rule ({err.message}). Check for typos or fields that don't belong in this scope.",
        None,
    )


def _shape_not_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    parts = [str(p) for p in err.absolute_path]
    if "context_object" in parts or not parts:
        return (
            "context_object.scope = same_page must not declare role, entity_match, "
            "page_selector, or aggregation; those belong to cross_document or "
            "page_aggregate scopes.",
            None,
        )
    return (err.message, None)


def _shape_pattern_error(err: _JsonSchemaError) -> tuple[str, str | None]:
    parts = [str(p) for p in err.absolute_path]
    if parts == ["id"]:
        return (
            f"id {err.instance!r} does not match required pattern "
            "(lowercase letters, digits, '.', '_', '-'; must start with a letter).",
            "id: alcoa.accurate.bpcr-raw-material-weight-match",
        )
    if parts == ["version"]:
        return (
            f"version {err.instance!r} must be semver (e.g. '1.0.0').",
            "version: 1.0.0",
        )
    return (err.message, None)


def _shape_error(err: _JsonSchemaError) -> RuleValidationError:
    if err.validator == "required":
        message, fix_hint = _shape_required_error(err)
    elif err.validator == "exclusiveMinimum":
        message, fix_hint = _shape_tolerance_error(err)
    elif err.validator in {"enum", "const"}:
        message, fix_hint = _shape_enum_error(err)
    elif err.validator == "unevaluatedProperties":
        message, fix_hint = _shape_unevaluated_error(err)
    elif err.validator == "not":
        message, fix_hint = _shape_not_error(err)
    elif err.validator == "pattern":
        message, fix_hint = _shape_pattern_error(err)
    else:
        message = err.message
        fix_hint = None

    return RuleValidationError(
        path=_json_pointer(list(err.absolute_path)),
        message=message,
        fix_hint=fix_hint,
        severity="blocking",
    )


# ── Public API ───────────────────────────────────────────────────────────────


def validate_rule_mapping(
    mapping: object,
    *,
    source_path: str | Path | None = None,
) -> RuleValidationReport:
    """Validate a single rule mapping (post-YAML-load) against its declared schema.

    ``mapping`` must be a dict; anything else is a blocking error.
    """

    source = str(source_path) if source_path else None
    if not isinstance(mapping, dict):
        return RuleValidationReport(
            rule_id=None,
            schema_version=None,
            source_path=source,
            errors=[
                RuleValidationError(
                    path="/",
                    message=(
                        "rule YAML must be a mapping at the top level "
                        f"(got {type(mapping).__name__})."
                    ),
                    severity="blocking",
                ),
            ],
        )

    rule_id = mapping.get("id") if isinstance(mapping.get("id"), str) else None
    declared_version = (
        mapping.get("schema_version")
        if isinstance(mapping.get("schema_version"), str)
        else None
    )

    if declared_version is None:
        return RuleValidationReport(
            rule_id=rule_id,
            schema_version=None,
            source_path=source,
            errors=[
                RuleValidationError(
                    path="/schema_version",
                    message=(
                        "rule is missing 'schema_version'; every rule YAML must "
                        "declare the schema it was authored against."
                    ),
                    fix_hint='schema_version: "1.0"',
                    severity="blocking",
                ),
            ],
        )

    try:
        schema = load_schema(declared_version)
    except SchemaNotFoundError as exc:
        known = ", ".join(available_schema_versions()) or "(none)"
        return RuleValidationReport(
            rule_id=rule_id,
            schema_version=declared_version,
            source_path=source,
            errors=[
                RuleValidationError(
                    path="/schema_version",
                    message=(
                        f"declared schema_version {declared_version!r} is not available; "
                        f"{exc}. Known versions: {known}."
                    ),
                    severity="blocking",
                ),
            ],
        )

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(mapping), key=lambda e: list(e.absolute_path))

    shaped: list[RuleValidationError] = [_shape_error(err) for err in errors]

    # Supplement: numeric-field rules without tolerance are a hard error
    # (Constitution VIII — Accurate; Spec 003 FR-006). The schema permits
    # this shape so authors of same-page categorical rules don't get a
    # false blocker; we enforce it semantically here when the source field
    # name hints at numeric content.
    shaped.extend(_semantic_checks(mapping))

    return RuleValidationReport(
        rule_id=rule_id,
        schema_version=declared_version,
        source_path=source,
        errors=shaped,
    )


_NUMERIC_FIELD_HINTS = (
    "weight",
    "mass",
    "volume",
    "quantity",
    "count",
    "temperature",
    "pressure",
    "yield",
    "percent",
    "ph_",
    "_ph",
    "duration",
    "time_s",
    "time_min",
    "kg",
    "ml",
    "liters",
    "litres",
)


def _looks_numeric(field_name: str) -> bool:
    name = field_name.lower()
    return any(hint in name for hint in _NUMERIC_FIELD_HINTS)


def _semantic_checks(mapping: dict[str, Any]) -> list[RuleValidationError]:
    errors: list[RuleValidationError] = []
    source = mapping.get("source")
    tolerance = mapping.get("tolerance")
    if isinstance(source, dict):
        field_name = source.get("field")
        if (
            isinstance(field_name, str)
            and _looks_numeric(field_name)
            and tolerance is None
        ):
            errors.append(
                RuleValidationError(
                    path="/tolerance",
                    message=(
                        f"source.field {field_name!r} looks numeric but no tolerance "
                        "is declared. Numeric comparisons without an explicit "
                        "tolerance are forbidden (Constitution VIII — Accurate)."
                    ),
                    fix_hint=(
                        "tolerance:\n"
                        "  kind: absolute\n"
                        "  value: 0.1\n"
                        "  unit: <unit>"
                    ),
                    severity="blocking",
                )
            )

    # page_selector.page_filter == "by_index" without a non-empty
    # page_indices list matches zero pages and produces UNEVALUATED
    # findings on every document — almost always a copy-paste bug.
    selector = mapping.get("page_selector")
    if isinstance(selector, dict) and selector.get("page_filter") == "by_index":
        indices = selector.get("page_indices")
        if not isinstance(indices, list) or not any(
            isinstance(i, int) and i >= 1 for i in indices
        ):
            errors.append(
                RuleValidationError(
                    path="/page_selector/page_indices",
                    message=(
                        "page_filter='by_index' requires a non-empty list of "
                        "positive page_indices; otherwise every page is skipped."
                    ),
                    fix_hint="page_indices: [1, 2, 3]",
                    severity="blocking",
                )
            )
    return errors


__all__ = [
    "RuleValidationError",
    "RuleValidationReport",
    "validate_rule_mapping",
]
