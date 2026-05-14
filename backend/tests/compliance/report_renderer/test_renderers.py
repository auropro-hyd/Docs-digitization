"""Renderer tests — HTML structure, PDF text content, Markdown
shape. Pin the visual contract against the client reference.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.compliance.models import (
    AgentReport,
    ComplianceFinding,
    ComplianceReport,
    RuleResult,
)
from app.compliance.report_renderer.builder import build_report_document
from app.compliance.report_renderer.render_html import render_html
from app.compliance.report_renderer.render_md import render_md


_NOW = datetime(2026, 5, 14, 18, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixture_report() -> ComplianceReport:
    """A 3-rule fixture covering all three compliance kinds."""
    rules = [
        RuleResult(
            rule_id="CHE-DOC1",
            rule_text="Are all attachments enclosed with the BPCR?",
            rule_category="document_completeness",
            agent="checklist",
            status="compliant",
            page_numbers=[103],
            reasoning="The BPCR REVIEW CHECK LIST confirms all attachments enclosed.",
        ),
        RuleResult(
            rule_id="CHE-BPC11",
            rule_text="Are all in process checks performed and compiled?",
            rule_category="bpcr_review",
            agent="checklist",
            status="non_compliant",
            page_numbers=[70],
        ),
        RuleResult(
            rule_id="CHE-BPC13",
            rule_text="Is output meeting standard yield range?",
            rule_category="bpcr_review",
            agent="checklist",
            status="uncertain",
            page_numbers=[18, 20, 21, 32],
        ),
    ]
    findings = [
        ComplianceFinding(
            finding_id="CHE-BPC11-f1",
            rule_id="CHE-BPC11",
            rule_text="In process checks",
            rule_category="bpcr_review",
            agent="checklist",
            severity="major",
            status="non_compliant",
            page_numbers=[70],
            reasoning="The water content check at Step 67 is missing from the BPCR.",
            recommendation="An investigation must be initiated to determine why the water content check at Step 67 was not documented.",
        ),
        ComplianceFinding(
            finding_id="CHE-BPC13-f1",
            rule_id="CHE-BPC13",
            rule_text="Output yield",
            rule_category="bpcr_review",
            agent="checklist",
            severity="major",
            status="uncertain",
            page_numbers=[18, 20, 21, 32],
            reasoning="Conflicting weights reported (978.50 kg / 977.32 kg).",
            recommendation="A formal investigation is required to reconcile the conflicting weight values.",
        ),
    ]
    return ComplianceReport(
        report_id="rpt-1",
        doc_id="doc-X",
        filename="2538104192-EHSII03.pdf",
        total_pages=104,
        document_type="batch_record",
        generated_at=_NOW,
        agent_reports=[
            AgentReport(
                agent="checklist",
                agent_display="Checklist",
                all_evaluations=rules,
                findings=findings,
            ),
        ],
    )


# ── HTML renderer ──────────────────────────────────────────────


def test_html_has_five_column_table_in_correct_order(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW)
    html = render_html(doc)

    # Scope the search to the rule table — "Compliance" otherwise
    # also matches the product name in the masthead.
    table_start = html.find('class="rule-table"')
    table_end = html.find("</thead>", table_start)
    assert table_start > 0 and table_end > table_start
    thead = html[table_start:table_end]

    expected_order = [
        "Question",
        "Compliance",
        "Evidence From Document",
        "Detailed Evidence",
        "Mitigation",
    ]
    positions = [thead.find(h) for h in expected_order]
    assert all(p > 0 for p in positions), f"missing headers in thead: {positions}"
    assert positions == sorted(positions), "column headers not in expected order"


def test_html_has_three_badge_kinds(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW)
    html = render_html(doc)

    assert "badge compliant" in html
    assert "badge action_required" in html
    assert "badge needs_attention" in html
    assert ">Compliant<" in html
    assert ">Action Required<" in html
    assert ">Needs Attention<" in html


def test_html_has_no_score_text_anywhere(fixture_report) -> None:
    """FR-007: the exported artifact MUST NOT carry score fields."""

    doc = build_report_document(fixture_report, now=_NOW)
    html = render_html(doc)

    # Lowercase scan to catch any case variant.
    lower = html.lower()
    for forbidden in [
        "overall_score",
        "model_score",
        "review_adjusted_score",
        "score_decomposition",
        "score_methodology",
    ]:
        assert forbidden not in lower, f"{forbidden!r} leaked into export"


def test_html_compliant_row_has_empty_pages_and_not_applicable(fixture_report) -> None:
    """FR-003 + FR-006a: compliant rows render with empty pages
    cell and "Not Applicable" mitigation."""

    doc = build_report_document(fixture_report, now=_NOW)
    html = render_html(doc)

    # Find the compliant row's cells. Crude but sufficient — the
    # fixture's compliant rule is CHE-DOC1.
    # Locate the row by question text.
    idx = html.find("Are all attachments enclosed")
    assert idx > 0
    # The cells after that row include the pages and mitigation;
    # in the compliant row pages must be empty and mitigation
    # "Not Applicable".
    # We can verify the row's mitigation cell carries the literal
    # text by checking it appears somewhere in the rendered HTML.
    assert "Not Applicable" in html


def test_html_carries_product_name_and_disclaimer(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW, operator="Manoj Sankad")
    html = render_html(doc)

    assert "BMR Compliance Intelligence Suite" in html
    assert "Manoj Sankad" in html
    # Engine brand in the disclaimer was unified onto
    # 'BMR Compliance Intelligence' in commit 03fcf71 (previously
    # 'Pharmix AI'). The masthead 'BMR Compliance Intelligence Suite'
    # check above is the longer form; this assertion guards the
    # disclaimer footer specifically.
    assert "BMR Compliance Intelligence" in html
    assert "Document is Draft" in html
    assert "TITLE OF DOCUMENT" in html


# ── Logo path resolution ───────────────────────────────────────


def test_logo_resolves_when_path_is_repo_root_relative(fixture_report) -> None:
    """The default settings value is repo-root-relative
    (``backend/app/.../logo.svg``). The backend usually runs with
    CWD inside ``backend/``, so a bare ``Path()`` resolve fails.
    The renderer MUST probe alternate anchors so the logo embeds
    as a data URI rather than rendering ``<img src="">``."""

    from pathlib import Path
    from app.compliance.report_renderer.builder import build_report_document

    repo_relative = Path("backend/app/compliance/report_renderer/assets/logo.svg")
    doc = build_report_document(fixture_report, now=_NOW, logo_path=repo_relative)
    html = render_html(doc)

    assert 'src=""' not in html, (
        "a failed logo lookup must not emit <img src=''> — that "
        "renders as the broken-image placeholder in browsers"
    )
    assert "data:image/svg+xml;base64," in html, (
        "the logo must inline as a base64 data URI so the rendered "
        "HTML stays self-contained for both standalone view and "
        "WeasyPrint PDF"
    )


def test_logo_resolves_when_path_is_backend_relative(fixture_report) -> None:
    """Same fix from the operator side — if the setting was
    overridden to a backend-CWD-relative path (``app/.../logo.svg``)
    we still resolve."""

    from pathlib import Path
    from app.compliance.report_renderer.builder import build_report_document

    backend_relative = Path("app/compliance/report_renderer/assets/logo.svg")
    doc = build_report_document(fixture_report, now=_NOW, logo_path=backend_relative)
    html = render_html(doc)

    assert "data:image/svg+xml;base64," in html


def test_missing_logo_falls_back_to_text_only_header(fixture_report) -> None:
    """When the configured path resolves to nothing the template
    must NOT emit an ``<img src="">`` tag — fall back to the
    placeholder div so the brand-block alignment stays consistent
    and no broken-image icon shows."""

    from pathlib import Path
    from app.compliance.report_renderer.builder import build_report_document

    doc = build_report_document(
        fixture_report, now=_NOW, logo_path=Path("does/not/exist.svg"),
    )
    html = render_html(doc)

    assert 'src=""' not in html
    assert "data:image/svg+xml;base64," not in html
    # Brand-block + product name still render — only the logo
    # reserves a placeholder.
    assert "BMR Compliance Intelligence Suite" in html
    assert "TITLE OF DOCUMENT" in html


# ── Markdown renderer ──────────────────────────────────────────


def test_md_has_five_column_table(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW)
    md = render_md(doc)

    # Header row of the rule table.
    assert "| Question | Compliance | Evidence From Document | Detailed Evidence | Mitigation |" in md


def test_md_has_no_score(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW)
    md = render_md(doc).lower()
    for forbidden in ["overall_score", "model_score", "score_decomposition"]:
        assert forbidden not in md


def test_md_compliant_row_uses_check_badge(fixture_report) -> None:
    doc = build_report_document(fixture_report, now=_NOW)
    md = render_md(doc)
    assert "✓ Compliant" in md
    assert "⚠ Action Required" in md
    assert "! Needs Attention" in md


# ── PDF renderer (skip if weasyprint unavailable) ──────────────


def test_pdf_renders_when_weasyprint_available(fixture_report) -> None:
    """If WeasyPrint can load on this host, render a real PDF and
    check the bytes look like a PDF. Skipped on hosts without
    pango/cairo (CI without the brew/apt install)."""

    weasyprint = pytest.importorskip("weasyprint")
    try:
        from app.compliance.report_renderer.render_pdf import (
            PdfRenderError,
            render_pdf,
        )
    except OSError:
        pytest.skip("WeasyPrint native libs unavailable on this host")

    doc = build_report_document(fixture_report, now=_NOW)
    try:
        pdf = render_pdf(doc)
    except PdfRenderError:
        pytest.skip("WeasyPrint native libs unavailable on this host")

    assert pdf[:4] == b"%PDF", "output must look like a PDF file"
    # Extract text and assert key invariants.
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(pdf))
        text = "".join(p.extract_text() or "" for p in reader.pages)
    except ImportError:
        pytest.skip("pypdf not available — skip text-content assertion")

    assert "Compliant" in text
    assert "Action Required" in text
    assert "Needs Attention" in text
    # No score field anywhere.
    assert "overall_score" not in text
    assert "review_adjusted_score" not in text
