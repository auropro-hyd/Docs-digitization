"""``feedback_seed.v1`` — turn a resolution into a :class:`FeedbackSample`.

Constitution IX (rule-as-data): every reviewer decision must leave an
immutable trace so Spec 005's authoring skill can tune or migrate rules
without re-mining raw OCR. The sample embeds a snapshot of the finding,
the action, and a digest of the rule inputs — the rule itself is
referenced by id/version (not inlined) because the rule YAML is already
content-addressable.

Pure function: no I/O. Caller is responsible for persistence.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from app.bmr.hitl.models import FeedbackSample, StructuredResolution, now_utc
from app.bmr.workflow.models import FindingRecord

CAPABILITY_VERSION = "1"


def _digest_inputs(finding: FindingRecord) -> str:
    """Deterministic digest of the evidence + fields used by the finding."""

    payload = {
        "rule_id": finding.rule_id,
        "rule_version": finding.rule_version,
        "status": finding.status.value,
        "tolerance_applied": finding.tolerance_applied,
        "fields": finding.fields,
        "evidence": [e.model_dump(mode="json") for e in finding.evidence],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def feedback_seed_v1(
    *,
    resolution: StructuredResolution,
    finding: FindingRecord,
) -> FeedbackSample:
    """Create a FeedbackSample for the given resolution + finding pair."""

    return FeedbackSample(
        sample_id=f"fbs_{uuid.uuid4().hex}",
        run_id=resolution.run_id,
        finding_id=resolution.finding_id,
        resolution_id=resolution.resolution_id,
        rule_id=finding.rule_id,
        rule_version=finding.rule_version,
        action=resolution.action,
        reason_type=resolution.reason_type,
        finding_snapshot=finding.model_copy(deep=True),
        input_context_digest=_digest_inputs(finding),
        created_at=now_utc(),
    )


__all__ = ["CAPABILITY_VERSION", "feedback_seed_v1"]
