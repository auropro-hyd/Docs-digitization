"""Deterministic packet decomposition from per-page markdown."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_PAGE_COUNTER_RE = re.compile(r"\bpage\s+(\d+)\s+of\s+(\d+)\b", re.IGNORECASE)
_MD_TAG_RE = re.compile(r"[#*_`>|]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NON_TEXT_RE = re.compile(r"[^a-z0-9\s:/.-]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_IGNORED_PREFIXES = (
    "format no",
    "printed by",
    "printed on",
    "this document is electronically issued",
)


@dataclass
class PacketSection:
    section_id: str
    name: str
    start_page: int
    end_page: int
    boundary_confidence: float
    boundary_reason: str


def _clean_line(line: str) -> str:
    x = _HTML_TAG_RE.sub(" ", line)
    x = _MD_TAG_RE.sub(" ", x)
    x = _NON_TEXT_RE.sub(" ", x).strip().lower()
    x = _WS_RE.sub(" ", x)
    return x


def _extract_heading(markdown: str) -> str:
    for raw in (markdown or "").splitlines()[:20]:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            return _clean_line(line)
    return ""


def _extract_signature_lines(markdown: str) -> list[str]:
    sig: list[str] = []
    for raw in (markdown or "").splitlines()[:24]:
        line = _clean_line(raw)
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in _IGNORED_PREFIXES):
            continue
        if _PAGE_COUNTER_RE.search(line):
            continue
        sig.append(line)
        if len(sig) >= 3:
            break
    return sig


def _extract_page_counter(markdown: str) -> tuple[int, int] | None:
    m = _PAGE_COUNTER_RE.search(markdown or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tokens(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        out.update(tok for tok in line.split() if len(tok) > 2)
    return out


def _section_name(markdown: str, fallback_lines: list[str], seq: int) -> str:
    heading = _extract_heading(markdown)
    if heading:
        return heading[:120]
    if fallback_lines:
        return fallback_lines[0][:120]
    return f"section_{seq:03d}"


def decompose_packet(pages: dict[int, str]) -> list[PacketSection]:
    """Split a packet into coarse sections using deterministic heuristics."""
    if not pages:
        return []

    ordered = sorted(pages.items(), key=lambda x: x[0])
    sections: list[PacketSection] = []
    seq = 1

    start_page = ordered[0][0]
    prev_counter = _extract_page_counter(ordered[0][1])
    prev_sig_lines = _extract_signature_lines(ordered[0][1])
    prev_tokens = _tokens(prev_sig_lines)
    cur_name = _section_name(ordered[0][1], prev_sig_lines, seq)
    boundary_reason = "initial_section"
    boundary_conf = 1.0

    for page_num, md in ordered[1:]:
        counter = _extract_page_counter(md)
        sig_lines = _extract_signature_lines(md)
        sig_tokens = _tokens(sig_lines)
        similarity = _jaccard(prev_tokens, sig_tokens)

        new_section = False
        reason = ""
        confidence = 0.0

        if counter and prev_counter:
            cur_idx, cur_total = counter
            prev_idx, prev_total = prev_counter
            if cur_idx == 1 and (prev_idx > 1 or cur_total != prev_total):
                new_section = True
                reason = "page_counter_reset"
                confidence = 0.92

        if not new_section and similarity < 0.22 and len(sig_tokens) >= 3:
            new_section = True
            reason = "header_signature_shift"
            confidence = 0.76

        if new_section:
            sections.append(
                PacketSection(
                    section_id=f"pkt_sec_{seq:03d}",
                    name=cur_name,
                    start_page=start_page,
                    end_page=page_num - 1,
                    boundary_confidence=boundary_conf,
                    boundary_reason=boundary_reason,
                )
            )
            seq += 1
            start_page = page_num
            cur_name = _section_name(md, sig_lines, seq)
            boundary_reason = reason
            boundary_conf = confidence

        prev_counter = counter or prev_counter
        prev_sig_lines = sig_lines or prev_sig_lines
        prev_tokens = _tokens(prev_sig_lines)

    sections.append(
        PacketSection(
            section_id=f"pkt_sec_{seq:03d}",
            name=cur_name,
            start_page=start_page,
            end_page=ordered[-1][0],
            boundary_confidence=boundary_conf,
            boundary_reason=boundary_reason,
        )
    )
    return sections


def sections_as_dicts(sections: list[PacketSection]) -> list[dict]:
    return [asdict(s) for s in sections]
