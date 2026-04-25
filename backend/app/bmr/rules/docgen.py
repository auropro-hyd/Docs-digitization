"""Generate human-readable Markdown from the BMR rule JSON Schema.

Rule authors who are not programmers should not have to read JSON
Schema (FR-012). This module renders ``rule.schema.vX.Y.json`` into a
``rule.schema.vX.Y.md`` sibling document:

- a top-level section per required and optional property;
- an enum list rendered as a bullet list when present;
- conditional ``allOf`` + ``$ref`` + ``if/then`` blocks flattened into
  plain prose so the branching semantics of ``context_object.scope`` are
  discoverable without crawling JSON Pointers;
- a canonical example per scope sourced from the accompanying
  ``rule.schema.vX.Y.examples.json`` file if present, else omitted.

Determinism: the generator is pure and side-effect-free (Constitution I).
Running the same schema through the same generator on two machines
MUST produce byte-identical markdown, so CI can assert the committed
markdown is up to date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.bmr.rules.schema import default_schema_dir, schema_path_for


@dataclass(frozen=True)
class DocGenResult:
    """Byte-identical markdown output + its target path."""

    version: str
    source_path: Path
    target_path: Path
    markdown: str


# ── Rendering helpers ────────────────────────────────────────────────────────


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- `{v}`" for v in values)


def _render_type(schema: dict[str, Any]) -> str:
    if "const" in schema:
        return f"must equal `{schema['const']!r}`"
    if "enum" in schema:
        vals = schema["enum"]
        if len(vals) <= 6:
            return "one of: " + ", ".join(f"`{v}`" for v in vals)
        return "one of (see enum list below)"
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        return f"see [{ref}](#{ref.lower()})"
    base = schema.get("type")
    if isinstance(base, list):
        return " or ".join(f"`{t}`" for t in base)
    if isinstance(base, str):
        if base == "array":
            items = schema.get("items", {})
            if "type" in items:
                return f"`array` of `{items['type']}`"
            return "`array`"
        return f"`{base}`"
    return "(any)"


def _render_constraints(schema: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    for key in ("minLength", "maxLength", "pattern", "minimum", "maximum"):
        if key in schema:
            bits.append(f"{key} = `{schema[key]}`")
    if schema.get("exclusiveMinimum") is not None:
        bits.append(f"> `{schema['exclusiveMinimum']}`")
    return bits


def _render_property(
    name: str, prop: dict[str, Any], *, required: bool
) -> list[str]:
    lines: list[str] = []
    label = "**required**" if required else "optional"
    lines.append(f"### `{name}` ({label})")
    lines.append("")
    if "description" in prop:
        lines.append(prop["description"])
        lines.append("")
    lines.append(f"- Type: {_render_type(prop)}")
    constraints = _render_constraints(prop)
    for c in constraints:
        lines.append(f"- Constraint: {c}")
    if "enum" in prop and len(prop["enum"]) > 6:
        lines.append("- Allowed values:")
        lines.append("")
        lines.append(_bullets([str(v) for v in prop["enum"]]))
    lines.append("")
    return lines


def _render_def(name: str, definition: dict[str, Any]) -> list[str]:
    lines = [f"### `{name}`", ""]
    if "description" in definition:
        lines.append(definition["description"])
        lines.append("")
    if definition.get("type") == "object":
        required = set(definition.get("required", []))
        properties = definition.get("properties", {})
        if properties:
            lines.append("| Field | Required | Type | Notes |")
            lines.append("|-------|----------|------|-------|")
            for key in sorted(properties):
                prop = properties[key]
                req = "yes" if key in required else "no"
                typ = _render_type(prop)
                notes_parts = _render_constraints(prop)
                if "enum" in prop and len(prop["enum"]) <= 10:
                    notes_parts.append(
                        "enum: " + ", ".join(f"`{v}`" for v in prop["enum"])
                    )
                notes = "; ".join(notes_parts) or ""
                lines.append(f"| `{key}` | {req} | {typ} | {notes} |")
            lines.append("")
    elif "enum" in definition:
        lines.append(_bullets([str(v) for v in definition["enum"]]))
        lines.append("")
    return lines


def _render_conditionals(schema: dict[str, Any]) -> list[str]:
    """Flatten ``allOf`` branches into prose.

    The v1.0 schema uses five ``if/then`` blocks (keyed off
    ``context_object.scope``). We describe each as a short paragraph so
    non-programmers don't need to read JSON Schema's conditional
    grammar.
    """

    lines: list[str] = []
    defs = schema.get("$defs", {})
    all_of = schema.get("allOf", [])
    for entry in all_of:
        ref = entry.get("$ref", "")
        name = ref.split("/")[-1]
        branch = defs.get(name)
        if not branch or not isinstance(branch, dict):
            continue
        condition = _describe_if(branch.get("if"))
        action = _describe_then(branch.get("then"))
        if condition and action:
            lines.append(f"- **{name}** — if {condition}, {action}.")
    if lines:
        return ["## Conditional requirements", "", *lines, ""]
    return []


def _describe_if(if_schema: dict[str, Any] | None) -> str | None:
    if not isinstance(if_schema, dict):
        return None
    ctx = if_schema.get("properties", {}).get("context_object", {})
    scope = ctx.get("properties", {}).get("scope", {})
    const = scope.get("const")
    enum = scope.get("enum")
    if const:
        return f"`context_object.scope == \"{const}\"`"
    if enum:
        vals = ", ".join(f"`{v}`" for v in enum)
        return f"`context_object.scope` is one of {vals}"
    return None


def _describe_then(then_schema: dict[str, Any] | None) -> str | None:
    if not isinstance(then_schema, dict):
        return None
    parts: list[str] = []
    required_top = then_schema.get("required", [])
    if required_top:
        parts.append(
            "the rule must declare " + ", ".join(f"`{r}`" for r in required_top)
        )
    ctx_required = (
        then_schema.get("properties", {})
        .get("context_object", {})
        .get("required", [])
    )
    if ctx_required:
        parts.append(
            "`context_object` must declare "
            + ", ".join(f"`{r}`" for r in ctx_required)
        )
    ctx_forbid = (
        then_schema.get("properties", {})
        .get("context_object", {})
        .get("not", {})
        .get("anyOf")
    )
    if ctx_forbid:
        forbidden = [
            f"`{entry['required'][0]}`"
            for entry in ctx_forbid
            if isinstance(entry, dict) and entry.get("required")
        ]
        if forbidden:
            parts.append(
                "`context_object` must NOT declare " + ", ".join(forbidden)
            )
    return "; ".join(parts) or None


# ── Public API ───────────────────────────────────────────────────────────────


def render_schema_markdown(
    version: str, *, schema_dir: Path | None = None
) -> DocGenResult:
    """Render the Markdown for a schema version.

    Writes nothing on disk; callers decide whether to persist the
    generated markdown (``write_schema_markdown``) or just compare it
    to the committed file (CI freshness guard).
    """

    schema_path = schema_path_for(version, schema_dir=schema_dir)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    title = schema.get("title", f"BMR Audit Rule v{version}")
    description = schema.get("description", "")

    lines: list[str] = []
    lines.append(f"# {title} (v{version})")
    lines.append("")
    lines.append("> Generated from `" + schema_path.name + "`. Do not edit by hand;")
    lines.append("> run `python -m app.bmr.rules.docgen` to regenerate.")
    lines.append("")
    if description:
        lines.append(description)
        lines.append("")

    required = set(schema.get("required", []))
    properties = schema.get("properties", {})

    lines.append("## Top-level properties")
    lines.append("")
    for name in sorted(properties, key=lambda n: (n not in required, n)):
        lines.extend(_render_property(name, properties[name], required=name in required))

    lines.extend(_render_conditionals(schema))

    defs = schema.get("$defs", {})
    if defs:
        lines.append("## Referenced definitions")
        lines.append("")
        for name in sorted(defs):
            lines.extend(_render_def(name, defs[name]))

    markdown = "\n".join(lines).rstrip() + "\n"
    target = schema_path.with_suffix(".md")
    return DocGenResult(
        version=version,
        source_path=schema_path,
        target_path=target,
        markdown=markdown,
    )


def write_schema_markdown(
    version: str, *, schema_dir: Path | None = None
) -> DocGenResult:
    """Render and persist the Markdown next to the JSON Schema file."""

    result = render_schema_markdown(version, schema_dir=schema_dir)
    result.target_path.write_text(result.markdown, encoding="utf-8")
    return result


def main() -> int:
    """CLI entry: regenerate markdown for every advertised schema version."""

    from app.bmr.rules.schema import available_schema_versions

    directory = default_schema_dir()
    versions = available_schema_versions(directory)
    if not versions:
        print(f"no schema files found under {directory}")
        return 2
    for v in versions:
        result = write_schema_markdown(v, schema_dir=directory)
        print(f"wrote {result.target_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DocGenResult",
    "render_schema_markdown",
    "write_schema_markdown",
]
