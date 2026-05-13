"""Mitigation-text picker and synthesis for non-compliant /
uncertain rule rows.

Priority chain (Spec 008 data-model.md §"_pick_mitigation"):

  1. The longest non-empty ``ComplianceFinding.recommendation``
     across the rule's findings — rule authors who wrote
     remediation guidance into the YAML put thought into it; that's
     the most rule-specific text we have.
  2. The first non-empty ``ComplianceFinding.mitigation_text`` —
     the cache populated by ``POST /mitigation/synthesize``.
  3. A category-aware boilerplate fallback.

The compliant case is not handled here — the renderer hard-codes
"Not Applicable" for compliant rows directly.

``synthesize_mitigations()`` is the cache-warming path: an operator
hits ``POST /mitigation/synthesize`` and we walk every
non-compliant / uncertain finding lacking both a rule-author
``recommendation`` and a cached ``mitigation_text``, asking the
evaluator LLM for one or two sentences of actionable remediation.
The cost ceiling guards against a runaway loop spending more than
``compliance.mitigation_synth_cost_ceiling_usd`` per export.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.compliance.models import ComplianceFinding
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

# Boilerplate fallback. Used when neither the rule author nor the
# synthesis cache has any mitigation text. Per Akhilesh's pointer
# every non-compliant / uncertain row MUST have a non-empty
# mitigation, so this is the floor — never empty.
_FALLBACK_MITIGATION: str = (
    "Review and remediate. Initiate a CAPA if the underlying gap "
    "persists; document the corrective steps and any operator "
    "interventions in the batch record."
)


def pick_mitigation(findings: list[ComplianceFinding]) -> str:
    """Return the mitigation cell text for a non-compliant / uncertain
    rule row.

    Pure function; no I/O, no LLM. Operators eagerly call the
    synthesis endpoint to populate ``mitigation_text`` before
    export when they want LLM-generated guidance.
    """

    if not findings:
        return _FALLBACK_MITIGATION

    # Layer 1: rule-author's recommendation. Pick the longest one
    # (most-developed thought across the findings on this rule).
    recs = [f.recommendation.strip() for f in findings if f.recommendation.strip()]
    if recs:
        return max(recs, key=len)

    # Layer 2: cached LLM-synthesised mitigation text.
    cached = [f.mitigation_text.strip() for f in findings if f.mitigation_text.strip()]
    if cached:
        return max(cached, key=len)

    # Layer 3: boilerplate.
    return _FALLBACK_MITIGATION


# ── Synthesis ────────────────────────────────────────────────


# Status values mirrored in tests + frontend toast copy.
SynthesisStatus = str  # "synthesized" | "skipped_has_mitigation" | "skipped_compliant" | "skipped_cost_ceiling" | "error"

# Per-1M-token cost estimates for the evaluator model (gpt-4.1-mini
# pricing as of 2026-05). These don't need to be exact — the
# ceiling check is a guardrail, not a billing source of truth.
_INPUT_USD_PER_MTOK: float = 0.30
_OUTPUT_USD_PER_MTOK: float = 1.20
# A typical mitigation completion is ~80 output tokens. Bound the
# estimate's pessimism by assuming 120.
_ESTIMATED_OUTPUT_TOKENS: int = 120


@dataclass
class SynthesisResult:
    """Per-finding outcome from a synthesis run."""

    rule_id: str
    finding_id: str
    agent: str
    status: SynthesisStatus
    cost_estimate_usd: float
    duration_ms: float
    error: str | None = None


_SYNTH_SYSTEM_PROMPT: str = (
    "You write pharmaceutical compliance mitigation guidance. "
    "Given a non-compliant or uncertain rule observation, respond "
    "with one to three sentences of concrete remediation steps "
    "the operator should take. No preamble, no markdown, no "
    "bullet points — just the remediation text directly. Keep it "
    "under 80 words."
)


def _estimate_tokens(text: str) -> int:
    """Char-based token estimate (~4 chars per token).

    Avoids a tiktoken dependency; the resulting cost figure is a
    guardrail, not a billing source of truth, so a ~25% margin of
    error is fine.
    """
    return max(1, len(text) // 4)


def _estimate_cost_usd(prompt: str) -> float:
    """Rough USD cost of one synthesis call."""
    in_tokens = _estimate_tokens(prompt) + _estimate_tokens(_SYNTH_SYSTEM_PROMPT)
    return (
        in_tokens * _INPUT_USD_PER_MTOK / 1_000_000
        + _ESTIMATED_OUTPUT_TOKENS * _OUTPUT_USD_PER_MTOK / 1_000_000
    )


def _build_synth_prompt(finding: dict) -> str:
    """Compose the per-finding prompt."""

    rule_text = finding.get("rule_text") or finding.get("description") or finding.get("rule_id", "")
    reasoning = finding.get("reasoning", "") or finding.get("description", "")
    severity = finding.get("severity", "observation")
    pages = finding.get("page_numbers") or []
    page_str = ", ".join(str(p) for p in pages) if pages else "n/a"

    return (
        f"Rule: {rule_text}\n"
        f"Severity: {severity}\n"
        f"Pages: {page_str}\n"
        f"Observation: {reasoning}\n\n"
        "Write the mitigation text now."
    )


def _needs_synthesis(finding: dict, *, force: bool) -> bool:
    """True when this finding should be sent to the LLM.

    Compliant findings never need mitigation. Non-compliant /
    uncertain findings need it unless they already have a
    rule-author recommendation or a cached synthesised text — and
    ``force=True`` bypasses both caches.
    """
    status = (finding.get("status") or "").lower()
    if status == "compliant" or status == "not_applicable":
        return False
    if force:
        return True
    if (finding.get("recommendation") or "").strip():
        return False
    if (finding.get("mitigation_text") or "").strip():
        return False
    return True


def _classify_skip(finding: dict) -> SynthesisStatus:
    status = (finding.get("status") or "").lower()
    if status == "compliant" or status == "not_applicable":
        return "skipped_compliant"
    return "skipped_has_mitigation"


def _iter_findings_with_owner(report_data: dict):
    """Yield (finding_dict, owning_lists) tuples.

    ``compliance_result.json`` carries findings in two places —
    ``agent_reports[i].findings`` and the top-level ``findings``
    list. Synthesis must update both so the export and any
    downstream consumer see the same mitigation text. The owner
    list lets the caller mutate the same dict reference in place
    (Python dicts are reference-typed, so mutation flows through).
    """
    seen: set[str] = set()
    for ar in report_data.get("agent_reports", []):
        agent = ar.get("agent", "")
        for f in ar.get("findings", []):
            fid = f.get("finding_id", "")
            if fid in seen:
                continue
            seen.add(fid)
            f.setdefault("agent", agent)
            yield f


async def synthesize_mitigations(
    report_data: dict,
    *,
    llm: LLMProvider,
    cost_ceiling_usd: float,
    rule_ids: set[str] | None = None,
    force: bool = False,
) -> list[SynthesisResult]:
    """Run LLM synthesis across the report's findings.

    Mutates ``report_data`` in place — caller is responsible for
    persisting it (atomically) when this returns.

    Stops dispatching new calls as soon as the next call's
    estimated cost would push cumulative spend over the ceiling;
    already-issued calls are not cancelled. Each skipped finding
    is still recorded in the result list so the caller can report
    "1 synthesised, 7 over ceiling" rather than swallowing the
    skip silently.
    """

    results: list[SynthesisResult] = []
    cumulative_cost = 0.0
    ceiling_hit = False

    for finding in _iter_findings_with_owner(report_data):
        rule_id = finding.get("rule_id", "")
        finding_id = finding.get("finding_id", "")
        agent = finding.get("agent", "")

        if rule_ids is not None and rule_id not in rule_ids:
            continue

        if not _needs_synthesis(finding, force=force):
            results.append(SynthesisResult(
                rule_id=rule_id,
                finding_id=finding_id,
                agent=agent,
                status=_classify_skip(finding),
                cost_estimate_usd=0.0,
                duration_ms=0.0,
            ))
            continue

        prompt = _build_synth_prompt(finding)
        est = _estimate_cost_usd(prompt)
        if cumulative_cost + est > cost_ceiling_usd:
            ceiling_hit = True
            results.append(SynthesisResult(
                rule_id=rule_id,
                finding_id=finding_id,
                agent=agent,
                status="skipped_cost_ceiling",
                cost_estimate_usd=est,
                duration_ms=0.0,
            ))
            continue

        t0 = time.perf_counter()
        try:
            text = await llm.generate(prompt, system=_SYNTH_SYSTEM_PROMPT)
        except Exception as exc:  # the LLM call is best-effort
            logger.warning(
                "Mitigation synthesis failed for %s/%s: %s",
                rule_id, finding_id, exc,
            )
            results.append(SynthesisResult(
                rule_id=rule_id,
                finding_id=finding_id,
                agent=agent,
                status="error",
                cost_estimate_usd=est,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                error=str(exc),
            ))
            continue

        finding["mitigation_text"] = text.strip()
        cumulative_cost += est
        results.append(SynthesisResult(
            rule_id=rule_id,
            finding_id=finding_id,
            agent=agent,
            status="synthesized",
            cost_estimate_usd=est,
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        ))

    # Mirror mitigation_text into the top-level findings list so any
    # downstream consumer reading from there (legacy frontend, JSON
    # API users) sees the same cached text.
    by_id: dict[str, str] = {}
    for ar in report_data.get("agent_reports", []):
        for f in ar.get("findings", []):
            if f.get("mitigation_text"):
                by_id[f.get("finding_id", "")] = f["mitigation_text"]
    for f in report_data.get("findings", []):
        fid = f.get("finding_id", "")
        if fid in by_id:
            f["mitigation_text"] = by_id[fid]

    if ceiling_hit:
        logger.warning(
            "Mitigation synthesis stopped at $%.4f (ceiling $%.4f) — "
            "%d findings skipped",
            cumulative_cost,
            cost_ceiling_usd,
            sum(1 for r in results if r.status == "skipped_cost_ceiling"),
        )

    return results
