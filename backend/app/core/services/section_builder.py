"""Section continuity detection, header deduplication, and document structure tree builder.

Analyzes page markdown to detect repeating headers, section boundaries,
and build a hierarchical structure: Document -> Sections -> Subsections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class DetectedHeader:
    text: str
    page_nums: list[int] = field(default_factory=list)
    frequency: int = 0


@dataclass
class SectionSpan:
    name: str
    section_type: str
    start_page: int
    end_page: int
    subsections: list[SectionSpan] = field(default_factory=list)


class SectionBuilder:
    """Builds document structure from per-page markdown."""

    def __init__(self, header_similarity_threshold: float = 0.8):
        self._threshold = header_similarity_threshold

    def detect_repeating_headers(self, pages: dict[int, str]) -> list[DetectedHeader]:
        """Find headers that repeat across pages (e.g. document title, batch number)."""
        first_lines: dict[str, list[int]] = {}
        for page_num, markdown in sorted(pages.items()):
            lines = markdown.strip().split("\n")[:3]
            for line in lines:
                cleaned = line.strip().strip("#").strip()
                if len(cleaned) > 5:
                    first_lines.setdefault(cleaned, []).append(page_num)

        headers = []
        for text, page_nums in first_lines.items():
            if len(page_nums) >= 3:
                headers.append(DetectedHeader(text=text, page_nums=page_nums, frequency=len(page_nums)))
        return sorted(headers, key=lambda h: h.frequency, reverse=True)

    def detect_section_boundaries(self, pages: dict[int, str]) -> list[SectionSpan]:
        """Detect section starts from markdown headings."""
        section_pattern = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
        sections: list[SectionSpan] = []
        current_section: SectionSpan | None = None

        for page_num in sorted(pages.keys()):
            markdown = pages[page_num]
            matches = section_pattern.findall(markdown)

            if matches:
                heading = matches[0].strip()
                if current_section:
                    current_section.end_page = page_num - 1
                    sections.append(current_section)
                current_section = SectionSpan(
                    name=heading,
                    section_type="detected",
                    start_page=page_num,
                    end_page=page_num,
                )
            elif current_section:
                current_section.end_page = page_num

        if current_section:
            sections.append(current_section)

        if not sections and pages:
            sorted_pages = sorted(pages.keys())
            sections.append(
                SectionSpan(
                    name="Document",
                    section_type="full_document",
                    start_page=sorted_pages[0],
                    end_page=sorted_pages[-1],
                )
            )

        return sections

    def build_structure(self, pages: dict[int, str]) -> tuple[list[SectionSpan], list[DetectedHeader]]:
        """Build complete document structure with header dedup."""
        headers = self.detect_repeating_headers(pages)
        sections = self.detect_section_boundaries(pages)
        return sections, headers
