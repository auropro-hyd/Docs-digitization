"""Auto-discovery: LLM proposes additional cross-page checks.

After predefined rules are evaluated, this module asks the LLM to identify
additional consistency checks specific to the document.  Discovered rules
are persisted for reproducibility and can be promoted to predefined rules.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.compliance.models import (
    DiscoveredRule,
    DocumentSegmentation,
)
from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a pharmaceutical compliance expert. You identify cross-page "
    "consistency checks that should be performed on a document packet but "
    "weren't covered by predefined rules. "
    "You MUST respond with valid JSON matching the provided schema."
)


class ProposedCheck(BaseModel):
    description: str = ""
    section_ids: list[str] = Field(default_factory=list)
    sections_semantic: list[str] = Field(default_factory=list)
    reasoning: str = ""
    priority: str = "medium"


class DiscoveryResult(BaseModel):
    checks: list[ProposedCheck] = Field(default_factory=list)


class AutoDiscovery:
    """Discovers additional cross-page checks via LLM."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def discover(
        self,
        segmentation: DocumentSegmentation,
        evaluated_rules_summary: str,
        dependency_tags: list[dict] | None = None,
    ) -> list[ProposedCheck]:
        sections_desc = "\n".join(
            f"- {s.section_id} ({s.section_type}): \"{s.name}\" pages {s.start_page}-{s.end_page}"
            for s in segmentation.sections
        )

        dep_text = "None available"
        if dependency_tags:
            dep_text = "\n".join(
                f"- [{d.get('ref_type', '?')}] {d.get('identifier', '?')} "
                f"(page {d.get('page_num', '?')})"
                for d in dependency_tags[:40]
            )

        prompt = (
            f"You just completed evaluating these predefined cross-page rules:\n"
            f"{evaluated_rules_summary}\n\n"
            f"Document structure:\n{sections_desc}\n\n"
            f"Dependency cues from per-page analysis:\n{dep_text}\n\n"
            f"Are there ADDITIONAL cross-page consistency checks that should be "
            f"performed but weren't covered by the predefined rules above?\n\n"
            f"For each proposed check, provide:\n"
            f"- description: what to verify\n"
            f"- section_ids: which section_ids from the document to compare\n"
            f"- sections_semantic: semantic type names (e.g. ['manufacturing', 'qc_report'])\n"
            f"- reasoning: why this check matters\n"
            f"- priority: high / medium / low\n\n"
            f"Only propose checks DIFFERENT from the already-evaluated rules.\n"
            f"If no additional checks are needed, return an empty list."
        )

        try:
            result = await self._llm.generate_structured(
                prompt, DiscoveryResult, system=_SYSTEM,
            )
            if not isinstance(result, DiscoveryResult):
                result = DiscoveryResult.model_validate(result)
            return result.checks
        except Exception:
            logger.exception("Auto-discovery failed")
            return []


def load_discovered_rules(doc_dir: Path) -> list[DiscoveredRule]:
    """Load previously discovered rules from disk."""
    path = doc_dir / "auto_discovered_rules.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [DiscoveredRule.model_validate(d) for d in data]
    except Exception:
        logger.warning("Failed to load discovered rules from %s", path)
        return []


def store_discovered_rules(doc_dir: Path, rules: list[DiscoveredRule]) -> None:
    """Persist discovered rules to disk."""
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / "auto_discovered_rules.json"
    data = [r.model_dump() for r in rules]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def checks_to_discovered_rules(
    checks: list[ProposedCheck],
    existing: list[DiscoveredRule],
) -> list[DiscoveredRule]:
    """Merge newly discovered checks with existing persisted rules."""
    existing_descs = {r.description.lower().strip() for r in existing}
    now = datetime.now(timezone.utc).isoformat()

    merged = list(existing)
    for check in checks:
        if check.description.lower().strip() not in existing_descs:
            merged.append(DiscoveredRule(
                description=check.description,
                sections_semantic=check.sections_semantic,
                section_ids=check.section_ids,
                reasoning=check.reasoning,
                priority=check.priority,
                discovered_at=now,
            ))
    return merged
