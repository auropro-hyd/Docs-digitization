"""Checklist-based compliance review agent.

Auto-verifies completed checklists, flags missing checkboxes and signatures.
"""

from __future__ import annotations

from app.compliance.alcoa import ComplianceFinding
from app.core.ports.llm import LLMProvider

CHECKLIST_REVIEW_PROMPT = """You are a checklist verification specialist for pharmaceutical documents.

Review the following document content and check:
- Are all checklist items marked (checked/unchecked)?
- Are all required signatures present?
- Are all date fields filled?
- Are there any blank fields that should have been completed?

**Document content (page {page_num}):**
```
{content}
```

List any incomplete items with severity and recommendations.
If all items are complete, state "No findings."
"""


class ChecklistAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def review_page(self, page_num: int, content: str) -> list[ComplianceFinding]:
        prompt = CHECKLIST_REVIEW_PROMPT.format(page_num=page_num, content=content[:4000])
        response = await self._llm.generate(
            prompt,
            system="You are a pharmaceutical documentation checklist reviewer.",
        )

        if "no findings" in response.lower() or "all items" in response.lower():
            return []

        return [
            ComplianceFinding(
                rule_id=f"CHK-P{page_num}",
                rule_category="checklist",
                severity="major",
                page_num=page_num,
                description=response[:500],
                recommendation="Complete all required checklist items",
            )
        ]

    async def review_document(self, extractions: list[dict]) -> list[ComplianceFinding]:
        all_findings: list[ComplianceFinding] = []
        for ext in extractions:
            page_num = ext.get("page_num", 0)
            markdown = ext.get("markdown", "")
            if markdown.strip():
                findings = await self.review_page(page_num, markdown)
                all_findings.extend(findings)
        return all_findings
