"""Corrections API — manage OCR correction rules learned from reviews."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config.settings import get_settings
from app.core.services.ocr_post_correction import (
    GlobalCorrectionStore,
    load_global_corrections,
    rebuild_global_corrections,
    save_global_corrections,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RuleToggleResponse(BaseModel):
    id: str
    is_active: bool


class RebuildResponse(BaseModel):
    total_rules: int
    total_corrections_processed: int
    last_updated: str


class CorrectionStatsResponse(BaseModel):
    total_rules: int
    active_rules: int
    inactive_rules: int
    total_corrections_processed: int
    last_updated: str
    rules_by_field_context: dict[str, int]
    top_confusion_pairs: list[dict]


class ConfusionPair(BaseModel):
    pattern: str
    replacement: str
    occurrences: int
    confidence: float
    field_context: str


def _load_store() -> GlobalCorrectionStore:
    settings = get_settings()
    return load_global_corrections(settings.feedback)


def _save_store(store: GlobalCorrectionStore) -> None:
    settings = get_settings()
    save_global_corrections(store, settings.feedback)


@router.get("/rules")
async def list_rules(
    active: Optional[bool] = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    store = _load_store()
    rules = store.rules

    if active is not None:
        rules = [r for r in rules if r.is_active == active]

    total = len(rules)
    page = rules[skip : skip + limit]

    return {
        "rules": [r.model_dump() for r in page],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/rules/{rule_id}")
async def get_rule(rule_id: str):
    store = _load_store()
    for rule in store.rules:
        if rule.id == rule_id:
            return rule.model_dump()
    raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")


@router.post("/rules/{rule_id}/toggle", response_model=RuleToggleResponse)
async def toggle_rule(rule_id: str):
    store = _load_store()
    for rule in store.rules:
        if rule.id == rule_id:
            rule.is_active = not rule.is_active
            _save_store(store)
            return RuleToggleResponse(id=rule.id, is_active=rule.is_active)
    raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")


@router.post("/rebuild", response_model=RebuildResponse)
async def rebuild():
    settings = get_settings()
    store = rebuild_global_corrections(
        storage_base=settings.storage.base_path,
        config=settings.feedback,
    )
    return RebuildResponse(
        total_rules=len(store.rules),
        total_corrections_processed=store.total_corrections_processed,
        last_updated=store.last_updated,
    )


@router.get("/stats", response_model=CorrectionStatsResponse)
async def get_stats():
    store = _load_store()
    rules = store.rules

    field_counts: Counter[str] = Counter()
    for r in rules:
        field_counts[r.field_context] += 1

    active = sum(1 for r in rules if r.is_active)

    top_pairs = sorted(rules, key=lambda r: (-r.occurrences, -r.confidence))[:20]

    return CorrectionStatsResponse(
        total_rules=len(rules),
        active_rules=active,
        inactive_rules=len(rules) - active,
        total_corrections_processed=store.total_corrections_processed,
        last_updated=store.last_updated,
        rules_by_field_context=dict(field_counts),
        top_confusion_pairs=[
            {
                "pattern": r.pattern,
                "replacement": r.replacement,
                "occurrences": r.occurrences,
                "confidence": r.confidence,
                "field_context": r.field_context,
            }
            for r in top_pairs
        ],
    )


@router.get("/confusion-matrix")
async def confusion_matrix(top_n: int = Query(20, ge=1, le=100)):
    store = _load_store()
    rules = store.rules

    sorted_rules = sorted(rules, key=lambda r: (-r.occurrences, -r.confidence))[:top_n]

    return {
        "pairs": [
            {
                "pattern": r.pattern,
                "replacement": r.replacement,
                "occurrences": r.occurrences,
                "confidence": round(r.confidence, 4),
                "field_context": r.field_context,
            }
            for r in sorted_rules
        ],
        "total_rules": len(rules),
    }
