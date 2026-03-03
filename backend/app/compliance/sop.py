"""SOP compliance review agent.

Compares manufacturing steps against SOP requirements and flags deviations.
"""

from __future__ import annotations

from app.compliance.alcoa import ComplianceFinding
from app.core.ports.llm import LLMProvider

SOP_REVIEW_PROMPT = """You are an SOP compliance reviewer for pharmaceutical manufacturing.

Review the following document content and check:
- Are SOP references properly cited?
- Do manufacturing steps align with documented procedures?
- Are any deviations from standard procedures documented?
- Are deviation reports referenced where deviations occurred?

**Document content (page {page_num}):**
```
{content}
```

List any SOP compliance findings with severity and recommendations.
If no issues found, state "No findings."
"""


class SOPAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def review_page(self, page_num: int, content: str) -> list[ComplianceFinding]:
        prompt = SOP_REVIEW_PROMPT.format(page_num=page_num, content=content[:4000])
        response = await self._llm.generate(
            prompt,
            system="You are a pharmaceutical SOP compliance expert.",
        )

        if "no findings" in response.lower() or "no issues" in response.lower():
            return []

        return [
            ComplianceFinding(
                rule_id=f"SOP-P{page_num}",
                rule_category="sop",
                severity="observation",
                page_num=page_num,
                description=response[:500],
                recommendation="Review SOP compliance requirements",
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
