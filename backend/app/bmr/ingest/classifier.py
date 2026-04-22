"""Hybrid document classifier (filename + header-text tiers).

The v0 slice implements two deterministic tiers — filename glob match, then
first-page header keyword match. VLM tiebreak is a named extension point
(``HybridClassifierConfig.header_text_extractor`` + future ``vlm_port``)
but is not required to land a working pipeline.

Design rules (from Spec 002):

- Each tier emits a per-role score in ``[0, 1]``.
- The winning role must have a score strictly greater than the runner-up by
  ``confidence_margin`` (default 0.15); otherwise the outcome is ``None``
  and the file is flagged for reviewer attention.
- The classifier is pure: no file I/O, no DB, no network. Callers pass in
  filenames and header text via the ``header_text_extractor`` hook.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.bmr.ingest.manifest import Manifest
from app.bmr.ingest.models import ClassificationDecisionSource

# Header-extractor takes ``(file_bytes, filename)`` and returns normalised text
# (usually first page, top region, lowercased).
HeaderTextExtractor = Callable[[bytes, str], str]


@dataclass(frozen=True)
class HybridClassifierConfig:
    confidence_margin: float = 0.15
    min_confidence: float = 0.55
    filename_weight: float = 1.0
    header_weight: float = 0.8
    # Multiple keyword hits saturate gently to avoid runaway scores.
    header_saturation_hits: int = 3


@dataclass
class ClassifierOutcome:
    """Result of classifying a single file."""

    role: str | None
    confidence: float
    decision_source: ClassificationDecisionSource
    tier_scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_KEYWORD_CLEAN_RE = re.compile(r"\s+")


def _normalise_text(text: str) -> str:
    return _KEYWORD_CLEAN_RE.sub(" ", text.lower()).strip()


def _score_filename(filename: str, patterns_by_role: dict[str, list[str]]) -> dict[str, float]:
    """Return 1.0 for the first role whose pattern matches (case-insensitive), else 0."""

    lower = filename.lower()
    scores: dict[str, float] = {}
    for role, patterns in patterns_by_role.items():
        match = any(fnmatch.fnmatchcase(lower, p.lower()) for p in patterns)
        if match:
            scores[role] = 1.0
    return scores


def _score_header(
    header_text: str,
    keywords_by_role: dict[str, list[str]],
    saturation_hits: int,
) -> dict[str, float]:
    """Score each role based on how many of its header keywords appear.

    Score ranges ``[0, 1]`` and saturates at ``saturation_hits`` matches.
    """

    if not header_text:
        return {}
    normalised = _normalise_text(header_text)
    scores: dict[str, float] = {}
    for role, keywords in keywords_by_role.items():
        hits = sum(1 for kw in keywords if _normalise_text(kw) in normalised)
        if hits == 0:
            continue
        scores[role] = min(1.0, hits / max(1, saturation_hits))
    return scores


class HybridClassifier:
    """Classifier driven by manifest-declared patterns + keyword lists."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        config: HybridClassifierConfig | None = None,
        header_text_extractor: HeaderTextExtractor | None = None,
    ) -> None:
        self._manifest = manifest
        self._config = config or HybridClassifierConfig()
        self._extractor = header_text_extractor

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def classify_file(self, *, filename: str, content: bytes) -> ClassifierOutcome:
        """Classify a single file against the manifest.

        The winning role must beat the runner-up by ``confidence_margin`` AND
        exceed ``min_confidence``; otherwise the outcome is unclassified and
        the caller must route the file to reviewer attention.
        """

        notes: list[str] = []
        policy = self._manifest.classifier
        tier_scores: dict[str, float] = {}

        filename_scores: dict[str, float] = {}
        header_scores: dict[str, float] = {}

        if "filename" in policy.tiers:
            filename_scores = _score_filename(filename, policy.filename_patterns)

        if "header" in policy.tiers:
            header_text = ""
            if self._extractor is not None:
                try:
                    header_text = self._extractor(content, filename) or ""
                except Exception as exc:
                    notes.append(f"header_extractor_failed: {exc}")
                    header_text = ""
            else:
                notes.append("header_extractor_not_configured")
            header_scores = _score_header(
                header_text,
                policy.header_keywords,
                self._config.header_saturation_hits,
            )

        normalised: dict[str, float] = {}
        for role in set(filename_scores) | set(header_scores):
            contributions: list[tuple[float, float]] = []  # (score, weight)
            if role in filename_scores:
                contributions.append((filename_scores[role], self._config.filename_weight))
            if role in header_scores:
                contributions.append((header_scores[role], self._config.header_weight))
            total_weight = sum(w for _, w in contributions)
            if total_weight <= 0:
                continue
            weighted = sum(s * w for s, w in contributions)
            normalised[role] = weighted / total_weight

        if filename_scores:
            tier_scores["filename"] = max(filename_scores.values())
        if header_scores:
            tier_scores["header"] = max(header_scores.values())

        if not normalised:
            return ClassifierOutcome(
                role=None,
                confidence=0.0,
                decision_source=ClassificationDecisionSource.UNKNOWN,
                tier_scores=tier_scores,
                notes=notes + ["no_tier_matched"],
            )

        ranked = sorted(normalised.items(), key=lambda kv: kv[1], reverse=True)
        top_role, top_score = ranked[0]
        runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0

        margin = top_score - runner_up_score
        if top_score < self._config.min_confidence:
            notes.append(
                f"top_confidence_below_threshold: {top_score:.2f} < "
                f"{self._config.min_confidence:.2f}"
            )
            return ClassifierOutcome(
                role=None,
                confidence=top_score,
                decision_source=ClassificationDecisionSource.UNKNOWN,
                tier_scores=tier_scores,
                notes=notes,
            )

        if margin < self._config.confidence_margin and len(ranked) > 1:
            notes.append(
                f"ambiguous_between_{top_role}_and_{ranked[1][0]}: "
                f"margin={margin:.2f} < {self._config.confidence_margin:.2f}"
            )
            return ClassifierOutcome(
                role=None,
                confidence=top_score,
                decision_source=ClassificationDecisionSource.UNKNOWN,
                tier_scores=tier_scores,
                notes=notes,
            )

        decision_source = (
            ClassificationDecisionSource.FILENAME
            if filename_scores.get(top_role, 0.0) >= header_scores.get(top_role, 0.0)
            else ClassificationDecisionSource.HEADER
        )

        return ClassifierOutcome(
            role=top_role,
            confidence=top_score,
            decision_source=decision_source,
            tier_scores=tier_scores,
            notes=notes,
        )


__all__ = [
    "ClassifierOutcome",
    "HeaderTextExtractor",
    "HybridClassifier",
    "HybridClassifierConfig",
    "ClassificationDecision",  # re-export for convenience
]


ClassificationDecision = ClassifierOutcome  # alias per spec naming
