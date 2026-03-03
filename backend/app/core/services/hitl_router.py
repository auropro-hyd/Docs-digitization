"""HITL routing service.

Determines which pages need human review based on composite confidence scores
and configurable thresholds. Supports tiered review (critical first, then batch).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import HITLConfig


@dataclass
class ReviewDecision:
    page_num: int
    confidence: float
    action: str  # "auto_approve" | "review_required" | "batch_review"
    priority: int  # lower = higher priority


class HITLRouter:
    def __init__(self, config: HITLConfig):
        self._auto_threshold = config.auto_approve_threshold
        self._review_threshold = config.review_threshold
        self._batch_review = config.batch_review_enabled

    def route_pages(self, confidence_scores: dict[int, float]) -> list[ReviewDecision]:
        """Route each page to the appropriate review action."""
        decisions: list[ReviewDecision] = []
        priority = 0

        for page_num in sorted(confidence_scores.keys()):
            score = confidence_scores[page_num]

            if score >= self._auto_threshold:
                decisions.append(
                    ReviewDecision(
                        page_num=page_num,
                        confidence=score,
                        action="auto_approve",
                        priority=999,
                    )
                )
            elif score >= self._review_threshold and self._batch_review:
                decisions.append(
                    ReviewDecision(
                        page_num=page_num,
                        confidence=score,
                        action="batch_review",
                        priority=priority + 100,
                    )
                )
            else:
                decisions.append(
                    ReviewDecision(
                        page_num=page_num,
                        confidence=score,
                        action="review_required",
                        priority=priority,
                    )
                )
                priority += 1

        decisions.sort(key=lambda d: d.priority)
        return decisions

    def needs_human_review(self, confidence_scores: dict[int, float]) -> bool:
        """Quick check: does any page need review?"""
        return any(s < self._auto_threshold for s in confidence_scores.values())

    def get_review_summary(self, confidence_scores: dict[int, float]) -> dict:
        decisions = self.route_pages(confidence_scores)
        return {
            "total_pages": len(decisions),
            "auto_approved": sum(1 for d in decisions if d.action == "auto_approve"),
            "needs_review": sum(1 for d in decisions if d.action == "review_required"),
            "batch_review": sum(1 for d in decisions if d.action == "batch_review"),
        }
