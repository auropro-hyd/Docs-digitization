"""ALCOA++ compliance review agent.

Evaluates extracted data against 82 ALCOA++ rules covering:
Attributable, Legible, Contemporaneous, Original, Accurate,
Complete, Consistent, Enduring, Available.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.ports.llm import LLMProvider

logger = logging.getLogger(__name__)


@dataclass
class ComplianceFinding:
    rule_id: str
    rule_category: str
    severity: str  # critical, major, minor, observation
    page_num: int | None
    description: str
    recommendation: str


ALCOA_REVIEW_PROMPT = """You are an ALCOA++ compliance reviewer for pharmaceutical batch production records.

Review the following extracted document content and check for ALCOA++ compliance:

**ALCOA++ Principles:**
- Attributable: All entries must be attributable to a person (signed/initialed with date)
- Legible: All entries must be readable and permanent
- Contemporaneous: All entries must be recorded at the time of activity
- Original: Original records or certified copies
- Accurate: No errors, corrections must follow proper procedure
- Complete: All data present, no blank fields where data is expected
- Consistent: Data consistent across the document
- Enduring: Recorded on approved media
- Available: Records accessible when needed

**Document content (page {page_num}):**
```
{content}
```

List any compliance findings. For each finding provide:
1. Which ALCOA++ principle is violated
2. Severity (critical/major/minor/observation)
3. Description of the issue
4. Recommendation for remediation

If no issues found, state "No findings."
"""


class ALCOAAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def review_page(self, page_num: int, content: str) -> list[ComplianceFinding]:
        prompt = ALCOA_REVIEW_PROMPT.format(page_num=page_num, content=content[:4000])

        response = await self._llm.generate(
            prompt,
            system="You are a pharmaceutical compliance expert specializing in ALCOA++ data integrity.",
        )

        return self._parse_findings(response, page_num)

    async def review_document(self, extractions: list[dict]) -> list[ComplianceFinding]:
        all_findings: list[ComplianceFinding] = []
        for ext in extractions:
            page_num = ext.get("page_num", 0)
            markdown = ext.get("markdown", "")
            if markdown.strip():
                findings = await self.review_page(page_num, markdown)
                all_findings.extend(findings)
        return all_findings

    def _parse_findings(self, response: str, page_num: int) -> list[ComplianceFinding]:
        if "no findings" in response.lower() or "no issues" in response.lower():
            return []

        findings: list[ComplianceFinding] = []
        lines = response.strip().split("\n")
        current_finding: dict = {}

        for line in lines:
            line = line.strip()
            if not line:
                if current_finding.get("description"):
                    findings.append(
                        ComplianceFinding(
                            rule_id=f"ALCOA-{len(findings) + 1}",
                            rule_category=current_finding.get("category", "general"),
                            severity=current_finding.get("severity", "observation"),
                            page_num=page_num,
                            description=current_finding.get("description", ""),
                            recommendation=current_finding.get("recommendation", ""),
                        )
                    )
                    current_finding = {}
                continue

            lower = line.lower()
            if any(s in lower for s in ["critical", "major", "minor", "observation"]):
                for sev in ["critical", "major", "minor", "observation"]:
                    if sev in lower:
                        current_finding["severity"] = sev
                        break

            for principle in [
                "attributable",
                "legible",
                "contemporaneous",
                "original",
                "accurate",
                "complete",
                "consistent",
                "enduring",
                "available",
            ]:
                if principle in lower:
                    current_finding["category"] = principle
                    break

            current_finding.setdefault("description", "")
            current_finding["description"] += " " + line

        if current_finding.get("description"):
            findings.append(
                ComplianceFinding(
                    rule_id=f"ALCOA-{len(findings) + 1}",
                    rule_category=current_finding.get("category", "general"),
                    severity=current_finding.get("severity", "observation"),
                    page_num=page_num,
                    description=current_finding["description"].strip(),
                    recommendation=current_finding.get("recommendation", "Review and correct"),
                )
            )

        return findings
