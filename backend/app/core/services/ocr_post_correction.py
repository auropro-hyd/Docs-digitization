"""OCR post-correction: apply learned corrections to new OCR output.

Tier 1 of the OCR correction learning pipeline.  Aggregates reviewer
corrections across all processed documents into a global correction
store, then applies high-confidence rules to newly-extracted text.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config.settings import FeedbackConfig

logger = logging.getLogger(__name__)


class CorrectionRule(BaseModel):
    """A single learned OCR correction rule."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    pattern: str
    replacement: str
    field_context: str = "any"
    occurrences: int = 0
    confidence: float = 0.0
    source_docs: int = 0
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GlobalCorrectionStore(BaseModel):
    """Aggregated corrections across all reviewed documents."""

    rules: list[CorrectionRule] = Field(default_factory=list)
    last_updated: str = ""
    total_corrections_processed: int = 0


def _store_path(config: FeedbackConfig) -> Path:
    """Resolve the store path relative to the backend working directory."""
    return Path(config.correction_store_path)


def load_global_corrections(config: FeedbackConfig) -> GlobalCorrectionStore:
    """Load the global correction store from disk, returning empty if absent."""
    p = _store_path(config)
    if not p.exists():
        return GlobalCorrectionStore()
    try:
        return GlobalCorrectionStore.model_validate_json(p.read_text("utf-8"))
    except Exception:
        logger.warning("Failed to load correction store from %s", p, exc_info=True)
        return GlobalCorrectionStore()


def save_global_corrections(store: GlobalCorrectionStore, config: FeedbackConfig) -> None:
    p = _store_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(store.model_dump_json(indent=2), encoding="utf-8")


def rebuild_global_corrections(
    storage_base: str,
    config: FeedbackConfig,
) -> GlobalCorrectionStore:
    """Scan all result.json files and build a global correction store.

    Aggregates ``review_corrections`` from every document, filters by
    occurrence and source-document thresholds, computes consistency-based
    confidence, and persists the store.
    """
    base = Path(storage_base)
    if not base.exists():
        return GlobalCorrectionStore()

    pair_counts: Counter[tuple[str, str, str]] = Counter()
    pair_sources: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    total_processed = 0

    for result_file in base.rglob("result.json"):
        try:
            data = json.loads(result_file.read_text("utf-8"))
        except Exception:
            continue
        corrections = data.get("review_corrections") or []
        if not corrections:
            continue
        doc_id = result_file.parent.name
        for c in corrections:
            before = str(c.get("before_value", "") or "").strip()
            after = str(c.get("after_value", "") or "").strip()
            if not before or not after or before == after:
                continue
            field_id = str(c.get("field_id", "")).strip() or "any"
            ctx = "page_markdown" if c.get("source") == "page_edit" else field_id
            key = (before, after, ctx)
            pair_counts[key] += 1
            pair_sources[key].add(doc_id)
            total_processed += 1

    # For each (before, after, ctx): check if the same before text maps
    # to different replacements to compute consistency-based confidence.
    before_total: Counter[tuple[str, str]] = Counter()
    for (before, _after, ctx), count in pair_counts.items():
        before_total[(before, ctx)] += count

    rules: list[CorrectionRule] = []
    for (before, after, ctx), count in pair_counts.items():
        n_docs = len(pair_sources[(before, after, ctx)])
        if count < config.min_correction_occurrences:
            continue
        if n_docs < config.min_correction_source_docs:
            continue
        total_for_pattern = before_total[(before, ctx)]
        confidence = count / total_for_pattern if total_for_pattern else 0.0
        rules.append(
            CorrectionRule(
                pattern=before,
                replacement=after,
                field_context=ctx,
                occurrences=count,
                confidence=round(confidence, 4),
                source_docs=n_docs,
            )
        )

    rules.sort(key=lambda r: (-r.confidence, -r.occurrences))

    store = GlobalCorrectionStore(
        rules=rules[:500],
        last_updated=datetime.now(timezone.utc).isoformat(),
        total_corrections_processed=total_processed,
    )
    save_global_corrections(store, config)
    return store


class OCRPostCorrector:
    """Apply learned OCR corrections to text.

    Only rules meeting the configured confidence threshold are used.
    Corrections are applied via exact string replacement, longest-first
    to avoid partial matches.
    """

    def __init__(self, store: GlobalCorrectionStore, config: FeedbackConfig) -> None:
        min_conf = config.min_correction_confidence
        eligible = [r for r in store.rules if r.confidence >= min_conf and r.is_active]
        eligible.sort(key=lambda r: -len(r.pattern))
        self._rules = eligible
        self._any_rules = [r for r in eligible if r.field_context == "any" or r.field_context == "page_markdown"]
        self._field_index: dict[str, list[CorrectionRule]] = defaultdict(list)
        for r in eligible:
            if r.field_context not in ("any", "page_markdown"):
                self._field_index[r.field_context].append(r)

    def correct_markdown(self, markdown: str) -> tuple[str, list[dict[str, Any]]]:
        """Apply corrections to page markdown, returning corrected text and log."""
        applied: list[dict[str, Any]] = []
        for rule in self._any_rules:
            if rule.pattern in markdown:
                markdown = markdown.replace(rule.pattern, rule.replacement)
                applied.append(
                    {
                        "pattern": rule.pattern,
                        "replacement": rule.replacement,
                        "confidence": rule.confidence,
                        "occurrences": rule.occurrences,
                        "source_docs": rule.source_docs,
                    }
                )
        return markdown, applied

    def correct_kv_value(self, field_key: str, value: str) -> tuple[str, list[dict[str, Any]]]:
        """Apply field-context-specific corrections to a KV value."""
        applied: list[dict[str, Any]] = []
        candidates = self._field_index.get(field_key, []) + self._any_rules
        for rule in candidates:
            if rule.pattern in value:
                value = value.replace(rule.pattern, rule.replacement)
                applied.append(
                    {
                        "pattern": rule.pattern,
                        "replacement": rule.replacement,
                        "confidence": rule.confidence,
                        "field_context": rule.field_context,
                    }
                )
        return value, applied
