"""Mitigation-text picker for non-compliant / uncertain rule rows.

Priority chain (Spec 008 data-model.md §"_pick_mitigation"):

  1. The longest non-empty ``ComplianceFinding.recommendation``
     across the rule's findings — rule authors who wrote
     remediation guidance into the YAML put thought into it; that's
     the most rule-specific text we have.
  2. The first non-empty ``ComplianceFinding.mitigation_text`` —
     the cache populated by ``POST /mitigation/synthesize`` (US3,
     not in this MVP).
  3. A category-aware boilerplate fallback.

The compliant case is not handled here — the renderer hard-codes
"Not Applicable" for compliant rows directly.

LLM-based synthesis is deliberately NOT in this module's MVP. It
lives in US3 / Phase 5 of the plan. When implemented it'll write
the result into ``ComplianceFinding.mitigation_text`` (additive
field added in this PR) so the renderer reads from the cache.
"""

from __future__ import annotations

from app.compliance.models import ComplianceFinding

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
