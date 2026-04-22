"""Tests for :mod:`app.bmr.rules.docgen` (Spec 005 FR-012).

The generator exists so non-programmer rule authors can read the rule
contract as Markdown rather than JSON Schema. That only works if the
committed ``rule.schema.vX.Y.md`` files stay in sync with the JSON
Schema, so CI asserts the markdown is byte-identical to what
:func:`render_schema_markdown` produces from the current JSON.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.rules.docgen import render_schema_markdown, write_schema_markdown
from app.bmr.rules.schema import available_schema_versions, default_schema_dir


def test_generated_markdown_is_deterministic():
    first = render_schema_markdown("1.0")
    second = render_schema_markdown("1.0")
    assert first.markdown == second.markdown, (
        "docgen is supposed to be pure; running it twice produced two "
        "different markdowns."
    )


def test_committed_markdown_is_in_sync_with_schema():
    for version in available_schema_versions():
        result = render_schema_markdown(version)
        if not result.target_path.exists():
            pytest.fail(
                f"{result.target_path.name} is missing. "
                "Run `python -m app.bmr.rules.docgen` and commit the result."
            )
        committed = result.target_path.read_text(encoding="utf-8")
        assert committed == result.markdown, (
            f"{result.target_path.name} is stale. "
            "Run `python -m app.bmr.rules.docgen` and commit the refreshed "
            "file."
        )


def test_write_schema_markdown_writes_target(tmp_path: Path):
    # Copy the JSON schema to a scratch directory so we can exercise
    # ``write_schema_markdown`` without mutating the tree.
    source_dir = default_schema_dir()
    for path in source_dir.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())

    result = write_schema_markdown("1.0", schema_dir=tmp_path)

    assert result.target_path.parent == tmp_path
    assert result.target_path.exists()
    assert result.target_path.read_text(encoding="utf-8") == result.markdown
    # First section is always the schema title + version banner.
    assert result.markdown.startswith("# BMR Audit Rule (v1.0)")
    # Required top-level fields must appear as headings so authors can
    # skim the contract without reading tables.
    for required in ("id", "version", "severity", "alcoa_tag", "context_object"):
        assert f"### `{required}` (**required**)" in result.markdown


def test_conditional_section_describes_scope_branches():
    result = render_schema_markdown("1.0")
    assert "## Conditional requirements" in result.markdown
    # Each of the five branches in the schema's allOf block should
    # produce a prose bullet.
    for branch in (
        "CrossDocumentRequirements",
        "PageAggregateRequirements",
        "SourceRequiredForLeafScopes",
        "SamePageForbidsCrossScopeKeys",
        "ChecklistSynthesisRequirements",
    ):
        assert f"**{branch}**" in result.markdown
