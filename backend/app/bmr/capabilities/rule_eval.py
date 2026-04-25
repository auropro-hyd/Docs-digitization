"""Rule evaluation capabilities: same-page, cross-document, page-aggregate.

All three functions share the same contract:

    (rule, extracted, aliases) -> list[FindingDraft]

They are pure — no I/O, no RNG — and deterministic modulo input ordering.
Every returned :class:`FindingDraft` MUST carry a non-empty ``evidence``
list (Constitution V — Evidence-Anchored Findings).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from typing import Any

from app.bmr.capabilities.aliases import AliasTable, normalise_name
from app.bmr.capabilities.evidence import (
    EvidenceRegion,
    FindingDraft,
    FindingSource,
    FindingStatus,
)
from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)

CAPABILITY_VERSION = "1"


# ── Shared helpers ───────────────────────────────────────────────────────────


def _finding_source_for_rule(rule: dict[str, Any]) -> FindingSource:
    alcoa = rule.get("alcoa_tag")
    if alcoa:
        return FindingSource.ALCOA
    if rule.get("gmp_category"):
        return FindingSource.GMP
    return FindingSource.CHECKLIST


def _ref_field_name(field_ref: dict[str, Any] | None) -> str | None:
    if not field_ref:
        return None
    name = field_ref.get("field")
    return name if isinstance(name, str) else None


def _entity_key(
    name: str | None,
    *,
    strategy: str,
    case_insensitive: bool,
    punctuation_strip: Iterable[str],
    alias_table: AliasTable | None,
) -> str | None:
    """Compute the comparison key for an entity name under ``strategy``."""

    if not name:
        return None
    if strategy == "exact":
        return name
    if strategy == "normalise":
        return normalise_name(
            name,
            case_insensitive=case_insensitive,
            punctuation_strip=punctuation_strip,
        )
    if strategy == "alias":
        if alias_table is None:
            return None
        canonical = alias_table.resolve(
            name,
            case_insensitive=case_insensitive,
            punctuation_strip=punctuation_strip,
        )
        return canonical
    if strategy in {"step_number", "batch_id"}:
        return normalise_name(name, case_insensitive=True, punctuation_strip=("-", "_"))
    # 'custom' not implemented in v0 — treat as exact.
    return name


def _tolerance_applied(tol: dict[str, Any] | None) -> dict[str, Any] | None:
    if not tol:
        return None
    return {k: v for k, v in tol.items() if v is not None}


def _compare_numeric(
    source_value: Any, target_value: Any, tol: dict[str, Any] | None
) -> tuple[FindingStatus, str]:
    """Return status + detail when comparing numeric values under ``tol``.

    If either value cannot coerce to float, returns INDETERMINATE.
    """

    try:
        s = float(source_value)
        t = float(target_value)
    except (TypeError, ValueError):
        return FindingStatus.INDETERMINATE, (
            f"cannot compare non-numeric values: source={source_value!r}, target={target_value!r}"
        )

    if tol is None:
        return (
            (FindingStatus.PASS if s == t else FindingStatus.OPEN),
            f"exact compare: source={s}, target={t}",
        )

    kind = tol.get("kind")
    value = tol.get("value")
    if value is None:
        return FindingStatus.INDETERMINATE, "tolerance.value missing"

    diff = abs(s - t)
    if kind == "absolute":
        allowed = float(value)
        passed = diff <= allowed
        detail = f"|{s} - {t}| = {diff} {'≤' if passed else '>'} {allowed}"
    elif kind == "percent":
        base = max(abs(s), abs(t))
        if base == 0:
            return FindingStatus.INDETERMINATE, (
                "percent tolerance cannot be applied when both values are zero "
                "(would divide by zero)"
            )
        allowed_pct = float(value)
        pct = (diff / base) * 100.0
        passed = pct <= allowed_pct
        detail = (
            f"|{s} - {t}| / max(|s|,|t|) = {pct:.4f}% "
            f"{'≤' if passed else '>'} {allowed_pct}%"
        )
    elif kind == "relative":
        if t == 0:
            return FindingStatus.INDETERMINATE, (
                "relative tolerance cannot be applied when target is zero"
            )
        allowed_rel = float(value)
        rel = diff / abs(t)
        passed = rel <= allowed_rel
        detail = (
            f"|{s} - {t}| / |{t}| = {rel:.4f} "
            f"{'≤' if passed else '>'} {allowed_rel}"
        )
    else:
        return FindingStatus.INDETERMINATE, f"unknown tolerance.kind {kind!r}"

    return (FindingStatus.PASS if passed else FindingStatus.OPEN), detail


def _evidence_from_field(f: FieldValue | None, *, note: str | None = None) -> EvidenceRegion | None:
    if f is None or f.source_doc_id is None or f.source_page_index is None:
        return None
    return EvidenceRegion(
        doc_id=f.source_doc_id,
        page_index=f.source_page_index,
        field=f.field,
        value=f.value,
        bbox=f.page_bbox,
        note=note,
    )


def _rule_core(rule: dict[str, Any]) -> tuple[str, str, str, str | None, str | None]:
    rule_id = str(rule["id"])
    rule_version = str(rule.get("version", "0.0.0"))
    severity = str(rule.get("severity", "observation"))
    alcoa = rule.get("alcoa_tag")
    gmp = rule.get("gmp_category")
    return (
        rule_id,
        rule_version,
        severity,
        alcoa if isinstance(alcoa, str) else None,
        gmp if isinstance(gmp, str) else None,
    )


def _rule_source_evidence(rule: dict[str, Any]) -> list[EvidenceRegion]:
    """Synthetic evidence anchoring an UNEVALUATED finding to its rule.

    Constitution V requires every finding to carry an evidence trail.
    Findings the evaluator cannot tie to a document (missing inputs,
    degenerate config) still need to answer "why does this finding
    exist?" — we point them at the rule itself via a reserved
    ``doc_id="__rule__"`` marker so downstream projection, rendering,
    and audits can distinguish them without losing provenance.
    """

    return [
        EvidenceRegion(
            doc_id="__rule__",
            page_index=1,
            field="rule_source",
            note=f"rule:{rule.get('id', '<unknown>')}@{rule.get('version', '0.0.0')}",
        )
    ]


def _unevaluated_finding(
    rule: dict[str, Any], summary: str, detail: str = ""
) -> FindingDraft:
    rule_id, rule_version, severity, alcoa, gmp = _rule_core(rule)
    return FindingDraft(
        rule_id=rule_id,
        rule_version=rule_version,
        status=FindingStatus.UNEVALUATED,
        severity=severity,
        alcoa_tag=alcoa,
        gmp_category=gmp,
        summary=summary,
        detail=detail,
        source=_finding_source_for_rule(rule),
        evidence=_rule_source_evidence(rule),
    )


# ── same_page_eval.v1 ────────────────────────────────────────────────────────


def same_page_eval_v1(
    *,
    rule: dict[str, Any],
    extracted: ExtractedPackage,
    alias_tables: dict[str, AliasTable] | None = None,  # unused for same_page
) -> list[FindingDraft]:
    """Evaluate a ``same_page`` rule across every page tagged by scope_hint.

    v0 semantics: the rule passes on a page when the source field exists and
    its value is truthy; otherwise emits an OPEN finding. This covers the
    "operator signature present" rule in the pilot bank.
    """

    del alias_tables  # unused for same_page

    ctx = rule.get("context_object", {})
    if ctx.get("scope") != "same_page":
        raise ValueError(
            f"same_page_eval_v1 received rule with scope={ctx.get('scope')!r}"
        )

    source_ref = rule.get("source", {})
    field_name = _ref_field_name(source_ref)
    if field_name is None:
        return [
            _unevaluated_finding(
                rule, "rule has no source.field; cannot evaluate", ""
            )
        ]

    scope_hint = source_ref.get("scope_hint")
    target_pages = _filter_pages_by_tag(extracted.pages, scope_hint)
    if not target_pages:
        return [
            _unevaluated_finding(
                rule,
                "no pages match scope hint for same_page rule",
                f"scope_hint={scope_hint!r}",
            )
        ]

    rule_id, rule_version, severity, alcoa, gmp = _rule_core(rule)
    findings: list[FindingDraft] = []
    for page in target_pages:
        values = page.get_fields(field_name)
        present = any(_is_truthy(v.value) for v in values)
        if present:
            continue
        evidence = EvidenceRegion(
            doc_id=page.doc_id,
            page_index=page.page_index,
            field=field_name,
            value=None,
            note="field missing or empty",
        )
        findings.append(
            FindingDraft(
                rule_id=rule_id,
                rule_version=rule_version,
                status=FindingStatus.OPEN,
                severity=severity,
                alcoa_tag=alcoa,
                gmp_category=gmp,
                summary=f"Missing {field_name} on {page.document_role} p.{page.page_index}",
                detail=(
                    f"Rule {rule_id} requires a value for {field_name!r} on every "
                    f"{page.document_role} page matching scope_hint={scope_hint!r}; "
                    f"page {page.page_index} has none."
                ),
                source=_finding_source_for_rule(rule),
                evidence=[evidence],
            )
        )
    return findings


# ── cross_doc_rule_eval.v1 ───────────────────────────────────────────────────


def cross_doc_rule_eval_v1(
    *,
    rule: dict[str, Any],
    extracted: ExtractedPackage,
    alias_tables: dict[str, AliasTable] | None = None,
) -> list[FindingDraft]:
    """Evaluate a ``cross_document`` rule.

    For each page matching ``source.scope_hint``, pull the source entity
    and field value, then locate the counterpart in the role declared by
    ``context_object.role`` using the configured entity_match strategy.
    Apply tolerance, emit findings per comparison.
    """

    ctx = rule.get("context_object", {})
    if ctx.get("scope") != "cross_document":
        raise ValueError(
            f"cross_doc_rule_eval_v1 received rule with scope={ctx.get('scope')!r}"
        )
    counterpart_role = ctx.get("role")
    if not counterpart_role:
        return [_unevaluated_finding(rule, "cross_document rule has no role")]

    entity_match = ctx.get("entity_match", {}) or {}
    strategy = entity_match.get("strategy", "exact")
    case_insensitive = bool(entity_match.get("case_insensitive", True))
    punctuation_strip = entity_match.get("punctuation_strip", ("-", "_", "."))
    aliases_file = entity_match.get("aliases_file")
    alias_table = None
    if aliases_file and alias_tables:
        alias_table = alias_tables.get(str(aliases_file))

    source_ref = rule.get("source", {})
    target_ref = rule.get("target", {})
    source_field = _ref_field_name(source_ref)
    target_field = _ref_field_name(target_ref)
    if source_field is None or target_field is None:
        return [
            _unevaluated_finding(
                rule,
                "cross_document rule requires both source.field and target.field",
            )
        ]

    source_pages = _filter_pages_by_tag(
        extracted.pages, source_ref.get("scope_hint")
    )
    counterpart_pages = extracted.pages_by_role(counterpart_role)

    if not source_pages:
        return [
            _unevaluated_finding(
                rule,
                "no source pages match scope_hint; cross-doc rule cannot evaluate",
            )
        ]
    if not counterpart_pages:
        return [
            _unevaluated_finding(
                rule,
                f"no counterpart pages with role {counterpart_role!r} in package",
            )
        ]

    # Index counterpart fields by entity key.
    counterpart_index: dict[str, list[tuple[ExtractedPage, FieldValue]]] = {}
    for page in counterpart_pages:
        for fv in page.get_fields(target_field):
            key = _entity_key(
                fv.entity_name,
                strategy=strategy,
                case_insensitive=case_insensitive,
                punctuation_strip=punctuation_strip,
                alias_table=alias_table,
            )
            if key is None:
                continue
            counterpart_index.setdefault(key, []).append((page, fv))

    tolerance = rule.get("tolerance")
    multiplicity = rule.get("multiplicity", "error")
    fallback = rule.get("fallback", "flag_as_unevaluated")

    findings: list[FindingDraft] = []
    rule_id, rule_version, severity, alcoa, gmp = _rule_core(rule)

    for page in source_pages:
        for source_fv in page.get_fields(source_field):
            source_key = _entity_key(
                source_fv.entity_name,
                strategy=strategy,
                case_insensitive=case_insensitive,
                punctuation_strip=punctuation_strip,
                alias_table=alias_table,
            )
            source_evidence = _evidence_from_field(source_fv, note="source")

            if source_key is None:
                findings.append(
                    _make_fallback_finding(
                        rule,
                        rule_id,
                        rule_version,
                        severity,
                        alcoa,
                        gmp,
                        fallback,
                        summary=(
                            f"Cannot resolve entity for {source_field}="
                            f"{source_fv.value!r} on {page.document_role} p.{page.page_index}"
                        ),
                        detail=(
                            f"entity_match strategy={strategy!r} could not produce a key "
                            f"for entity_name={source_fv.entity_name!r}"
                        ),
                        evidence=[e for e in [source_evidence] if e is not None],
                    )
                )
                continue

            matches = counterpart_index.get(source_key, [])
            if not matches:
                findings.append(
                    _make_fallback_finding(
                        rule,
                        rule_id,
                        rule_version,
                        severity,
                        alcoa,
                        gmp,
                        fallback,
                        summary=(
                            f"No counterpart {target_field} for entity "
                            f"{source_fv.entity_name!r} on {counterpart_role}"
                        ),
                        detail=(
                            f"source entity_key={source_key!r} has no match "
                            f"in role {counterpart_role!r}"
                        ),
                        evidence=[e for e in [source_evidence] if e is not None],
                    )
                )
                continue

            if len(matches) > 1 and multiplicity == "error":
                findings.append(
                    FindingDraft(
                        rule_id=rule_id,
                        rule_version=rule_version,
                        status=FindingStatus.INDETERMINATE,
                        severity=severity,
                        alcoa_tag=alcoa,
                        gmp_category=gmp,
                        summary=(
                            f"Multiple counterparts for entity {source_fv.entity_name!r} "
                            f"in role {counterpart_role!r} ({len(matches)} matches)"
                        ),
                        detail=(
                            f"rule.multiplicity={multiplicity!r} disallows tied matches; "
                            "reviewer must disambiguate."
                        ),
                        source=_finding_source_for_rule(rule),
                        evidence=[e for e in [source_evidence] if e is not None],
                    )
                )
                continue
            chosen = [matches[0]] if multiplicity == "first" else matches

            for target_page, target_fv in chosen:
                target_evidence = _evidence_from_field(target_fv, note="target")
                status, detail = _compare_numeric(
                    source_fv.value, target_fv.value, tolerance
                )
                summary = _compare_summary(
                    rule_id, page, source_fv, target_page, target_fv, status
                )
                findings.append(
                    FindingDraft(
                        rule_id=rule_id,
                        rule_version=rule_version,
                        status=status,
                        severity=severity,
                        alcoa_tag=alcoa,
                        gmp_category=gmp,
                        summary=summary,
                        detail=detail,
                        source=_finding_source_for_rule(rule),
                        evidence=[
                            e
                            for e in [source_evidence, target_evidence]
                            if e is not None
                        ],
                        tolerance_applied=_tolerance_applied(tolerance),
                        fields={
                            "source_value": source_fv.value,
                            "target_value": target_fv.value,
                            "entity_key": source_key,
                        },
                    )
                )

    return findings


# ── page_aggregate_eval.v1 ───────────────────────────────────────────────────


def page_aggregate_eval_v1(
    *,
    rule: dict[str, Any],
    extracted: ExtractedPackage,
    alias_tables: dict[str, AliasTable] | None = None,  # unused for aggregate
) -> list[FindingDraft]:
    """Evaluate a ``page_aggregate`` rule by aggregating source values."""

    del alias_tables

    ctx = rule.get("context_object", {})
    if ctx.get("scope") != "page_aggregate":
        raise ValueError(
            f"page_aggregate_eval_v1 received rule with scope={ctx.get('scope')!r}"
        )

    selector = ctx.get("page_selector", {}) or {}
    aggregation = ctx.get("aggregation")
    document_role = selector.get("document_role")
    if not document_role or not aggregation:
        return [
            _unevaluated_finding(
                rule,
                "page_aggregate rule requires page_selector.document_role and aggregation",
            )
        ]

    source_field = _ref_field_name(rule.get("source"))
    expected_field = _ref_field_name(rule.get("expected"))
    if source_field is None:
        return [_unevaluated_finding(rule, "page_aggregate rule requires source.field")]

    target_pages = extracted.pages_by_role(document_role)
    target_pages = _filter_pages_by_selector(target_pages, selector)
    if not target_pages:
        return [
            _unevaluated_finding(
                rule,
                f"no pages in role {document_role!r} match selector",
            )
        ]

    values: list[FieldValue] = []
    for p in target_pages:
        values.extend(p.get_fields(source_field))
    if not values:
        return [
            _unevaluated_finding(
                rule, f"no values found for {source_field!r} under aggregate selector"
            )
        ]

    numeric: list[float] = []
    for v in values:
        with contextlib.suppress(TypeError, ValueError):
            numeric.append(float(v.value))
    if aggregation != "count" and len(numeric) != len(values):
        return [
            _unevaluated_finding(
                rule,
                f"aggregation={aggregation} requires numeric values; some are non-numeric",
            )
        ]

    aggregated = _aggregate(aggregation, numeric if aggregation != "count" else values)

    # Resolve expected value (may be a literal in rule, a field, or missing).
    expected_value: Any = None
    expected_evidence: EvidenceRegion | None = None
    if expected_field:
        exp_fv = _find_expected(extracted, expected_field, rule.get("expected", {}))
        if exp_fv is None:
            return [
                _unevaluated_finding(
                    rule, f"expected.field {expected_field!r} not found in package"
                )
            ]
        expected_value = exp_fv.value
        expected_evidence = _evidence_from_field(exp_fv, note="expected")

    tolerance = rule.get("tolerance")
    rule_id, rule_version, severity, alcoa, gmp = _rule_core(rule)

    source_evidence = [
        _evidence_from_field(v, note="source_aggregated") for v in values
    ]
    source_evidence = [e for e in source_evidence if e is not None]

    if expected_value is None:
        return [
            _unevaluated_finding(
                rule,
                f"aggregate {aggregation}({source_field})={aggregated} has no expected value",
            )
        ]

    status, detail = _compare_numeric(aggregated, expected_value, tolerance)
    summary = (
        f"{aggregation}({source_field}) across {document_role} = {aggregated}"
        f" vs expected {expected_value}"
    )
    evidence = source_evidence + ([expected_evidence] if expected_evidence else [])
    return [
        FindingDraft(
            rule_id=rule_id,
            rule_version=rule_version,
            status=status,
            severity=severity,
            alcoa_tag=alcoa,
            gmp_category=gmp,
            summary=summary,
            detail=detail,
            source=_finding_source_for_rule(rule),
            evidence=evidence,
            tolerance_applied=_tolerance_applied(tolerance),
            fields={
                "aggregate_value": aggregated,
                "expected_value": expected_value,
                "aggregation": aggregation,
                "sample_count": len(values),
            },
        )
    ]


# ── tiny helpers ─────────────────────────────────────────────────────────────


def _filter_pages_by_tag(pages: Iterable[ExtractedPage], tag: str | None) -> list[ExtractedPage]:
    if not tag:
        return list(pages)
    return [p for p in pages if tag in p.tags]


def _filter_pages_by_selector(
    pages: list[ExtractedPage], selector: dict[str, Any]
) -> list[ExtractedPage]:
    page_filter = selector.get("page_filter", "all_bpcr_step_pages")
    if page_filter == "all_bpcr_step_pages":
        return [p for p in pages if "bpcr_step_page" in p.tags]
    if page_filter == "first_page":
        return pages[:1]
    if page_filter == "last_page":
        return pages[-1:]
    if page_filter == "by_index":
        raw_indices = selector.get("page_indices") or []
        # Empty ``page_indices`` with ``page_filter=by_index`` is almost
        # always an authoring mistake: matching zero pages causes the
        # rule to silently UNEVALUATE on every document. Treat the
        # selector as degenerate by returning no pages — the caller
        # will then emit an UNEVALUATED finding with a clear summary,
        # which is what the reviewer needs to see the mistake.
        wanted = {int(i) for i in raw_indices if isinstance(i, int) and i >= 1}
        if not wanted:
            return []
        return [p for p in pages if p.page_index in wanted]
    if page_filter == "by_tag":
        tag = selector.get("page_tag")
        return [p for p in pages if tag and tag in p.tags]
    return pages


def _aggregate(kind: str, values: list[Any]) -> float:
    if kind == "sum":
        return float(sum(values))
    if kind == "count":
        return float(len(values))
    if kind == "min":
        return float(min(values))
    if kind == "max":
        return float(max(values))
    if kind == "avg":
        return float(sum(values) / len(values)) if values else 0.0
    raise ValueError(f"unknown aggregation kind {kind!r}")


def _find_expected(
    extracted: ExtractedPackage,
    field_name: str,
    expected_ref: dict[str, Any],
) -> FieldValue | None:
    document_ref_hint = expected_ref.get("document_ref_hint")
    candidates: Iterable[ExtractedPage] = (
        extracted.pages_by_role(document_ref_hint)
        if document_ref_hint
        else extracted.pages
    )

    for page in candidates:
        match = page.find_single(field_name)
        if match is not None:
            return match
    return None


def _is_truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return bool(value)


def _make_fallback_finding(
    rule: dict[str, Any],
    rule_id: str,
    rule_version: str,
    severity: str,
    alcoa: str | None,
    gmp: str | None,
    fallback: str,
    *,
    summary: str,
    detail: str,
    evidence: list[EvidenceRegion],
) -> FindingDraft:
    if fallback == "treat_as_pass":
        status = FindingStatus.PASS
    elif fallback == "flag_as_indeterminate":
        status = FindingStatus.INDETERMINATE
    else:  # flag_as_unevaluated
        status = FindingStatus.UNEVALUATED
    return FindingDraft(
        rule_id=rule_id,
        rule_version=rule_version,
        status=status,
        severity=severity,
        alcoa_tag=alcoa,
        gmp_category=gmp,
        summary=summary,
        detail=detail,
        source=_finding_source_for_rule(rule),
        evidence=evidence,
        fallback_applied=fallback,
    )


def _compare_summary(
    rule_id: str,
    source_page: ExtractedPage,
    source_fv: FieldValue,
    target_page: ExtractedPage,
    target_fv: FieldValue,
    status: FindingStatus,
) -> str:
    tag = {
        FindingStatus.PASS: "matches",
        FindingStatus.OPEN: "mismatch",
        FindingStatus.INDETERMINATE: "cannot compare",
        FindingStatus.UNEVALUATED: "unevaluated",
    }[status]
    entity = source_fv.entity_name or target_fv.entity_name or "value"
    return (
        f"{entity}: {tag} between {source_page.document_role} "
        f"p.{source_page.page_index} ({source_fv.field}={source_fv.value}) "
        f"and {target_page.document_role} p.{target_page.page_index} "
        f"({target_fv.field}={target_fv.value})"
    )


__all__ = [
    "CAPABILITY_VERSION",
    "cross_doc_rule_eval_v1",
    "page_aggregate_eval_v1",
    "same_page_eval_v1",
]
