"""HITL review queue management.

Tracks pages awaiting human review, ordered by confidence (lowest first).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    EDITED = "edited"
    FLAGGED = "flagged"


@dataclass
class ReviewItem:
    doc_id: str
    page_num: int
    confidence: float
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    original_extraction: dict | None = None
    corrected_extraction: dict | None = None
    correction_reason: str | None = None


class ReviewQueue:
    """In-memory review queue. Replace with DB-backed implementation for production."""

    def __init__(self):
        self._items: dict[str, list[ReviewItem]] = {}

    def add_pages(self, doc_id: str, pages: list[dict]):
        items = []
        for page in pages:
            items.append(
                ReviewItem(
                    doc_id=doc_id,
                    page_num=page["page_num"],
                    confidence=page.get("confidence", 0.0),
                    original_extraction=page.get("extraction"),
                )
            )
        items.sort(key=lambda x: x.confidence)
        self._items[doc_id] = items

    def get_next(self, doc_id: str) -> ReviewItem | None:
        items = self._items.get(doc_id, [])
        for item in items:
            if item.status == ReviewStatus.PENDING:
                return item
        return None

    def get_all(self, doc_id: str) -> list[ReviewItem]:
        return self._items.get(doc_id, [])

    def approve(self, doc_id: str, page_num: int, reviewer: str) -> ReviewItem | None:
        item = self._find(doc_id, page_num)
        if item:
            item.status = ReviewStatus.APPROVED
            item.reviewer = reviewer
            item.reviewed_at = datetime.utcnow()
        return item

    def edit(
        self,
        doc_id: str,
        page_num: int,
        reviewer: str,
        corrected: dict,
        reason: str,
    ) -> ReviewItem | None:
        item = self._find(doc_id, page_num)
        if item:
            item.status = ReviewStatus.EDITED
            item.reviewer = reviewer
            item.reviewed_at = datetime.utcnow()
            item.corrected_extraction = corrected
            item.correction_reason = reason
        return item

    def flag(self, doc_id: str, page_num: int, reviewer: str, reason: str) -> ReviewItem | None:
        item = self._find(doc_id, page_num)
        if item:
            item.status = ReviewStatus.FLAGGED
            item.reviewer = reviewer
            item.reviewed_at = datetime.utcnow()
            item.correction_reason = reason
        return item

    def get_progress(self, doc_id: str) -> dict:
        items = self._items.get(doc_id, [])
        total = len(items)
        reviewed = sum(1 for i in items if i.status != ReviewStatus.PENDING)
        corrections = sum(1 for i in items if i.status == ReviewStatus.EDITED)
        return {
            "total": total,
            "reviewed": reviewed,
            "remaining": total - reviewed,
            "corrections": corrections,
        }

    def _find(self, doc_id: str, page_num: int) -> ReviewItem | None:
        for item in self._items.get(doc_id, []):
            if item.page_num == page_num:
                return item
        return None
