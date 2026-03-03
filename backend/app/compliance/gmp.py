"""GMP compliance review agent.

Validates documentation practices, equipment ID matching,
and SOP reference validation.
"""

from __future__ import annotations

from app.compliance.alcoa import ComplianceFinding
from app.core.ports.llm import LLMProvider

GMP_REVIEW_PROMPT = """You are a GMP (Good Manufacturing Practice) compliance reviewer.

Review the following pharmaceutical document content for GMP compliance:

**Key GMP checks:**
- Equipment IDs properly documented and consistent
- SOP references present and properly formatted
- Environmental conditions recorded where required
- Proper correction procedures followed (single line strikethrough, initials, date)
- Material reconciliation present
- Yield calculations documented
- In-process controls recorded

**Document content (page {page_num}):**
```
{content}
```

List any GMP compliance findings with severity and recommendations.
If no issues found, state "No findings."
"""


class GMPAgent:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def review_page(self, page_num: int, content: str) -> list[ComplianceFinding]:
        prompt = GMP_REVIEW_PROMPT.format(page_num=page_num, content=content[:4000])
        response = await self._llm.generate(
            prompt,
            system="You are a pharmaceutical GMP compliance expert.",
        )

        if "no findings" in response.lower() or "no issues" in response.lower():
            return []

        return [
            ComplianceFinding(
                rule_id=f"GMP-P{page_num}",
                rule_category="gmp",
                severity="observation",
                page_num=page_num,
                description=response[:500],
                recommendation="Review GMP documentation requirements",
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
