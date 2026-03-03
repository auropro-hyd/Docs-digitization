"""LLM-based page/document type classifier.

Given a page's markdown content, classifies it into a type enum that drives
which validation rules and extraction strategy to apply.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from app.core.ports.llm import LLMProvider


class PageType(StrEnum):
    MANUFACTURING_INSTRUCTION = "manufacturing_instruction"
    CHECKLIST = "checklist"
    CERTIFICATE_OF_ANALYSIS = "certificate_of_analysis"
    RAW_MATERIAL_FORM = "raw_material_form"
    WEIGHING_RECORD = "weighing_record"
    EQUIPMENT_LOG = "equipment_log"
    SIGNATURE_PAGE = "signature_page"
    COVER_PAGE = "cover_page"
    TABLE_OF_CONTENTS = "table_of_contents"
    BLANK_PAGE = "blank_page"
    GENERIC = "generic"


class ClassificationResult(BaseModel):
    page_type: PageType
    reasoning: str


CLASSIFICATION_PROMPT = """Classify this document page into one of these categories:
- manufacturing_instruction: Steps for manufacturing a product
- checklist: Verification checklist with checkboxes or sign-off fields
- certificate_of_analysis: Lab test results and certificates
- raw_material_form: Raw material dispensing or receiving records
- weighing_record: Weight measurements and balances
- equipment_log: Equipment usage, cleaning, or calibration logs
- signature_page: Page primarily containing signatures and dates
- cover_page: Title page or document header
- table_of_contents: Index or contents listing
- blank_page: Mostly empty page
- generic: Does not fit other categories

Page content:
```
{content}
```

Respond with the category name and a one-sentence reasoning."""


class PageClassifier:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def classify(self, page_markdown: str) -> ClassificationResult:
        truncated = page_markdown[:3000]
        prompt = CLASSIFICATION_PROMPT.format(content=truncated)

        try:
            result = await self._llm.generate_structured(
                prompt,
                ClassificationResult,
                system="You are a document analysis expert. Classify document pages accurately.",
            )
            return result  # type: ignore[return-value]
        except Exception:
            raw = await self._llm.generate(prompt)
            for pt in PageType:
                if pt.value in raw.lower():
                    return ClassificationResult(page_type=pt, reasoning=raw[:200])
            return ClassificationResult(page_type=PageType.GENERIC, reasoning=raw[:200])
