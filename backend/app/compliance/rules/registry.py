"""Rule registry: loads audit rules from markdown + YAML config files.

Markdown files hold rule text (human-readable). YAML files hold behavioral
metadata (scope, applicability, pass criteria, etc.) for easy tuning.

Supports read and write operations. Writes serialize structured data back to
markdown and persist agent metadata to agents_meta.json.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parent
_META_FILE = _RULES_DIR / "agents_meta.json"

_CATEGORY_RE = re.compile(r"^(?:Category:\s*)?(.+?)(?:\s+Rules?)?:?\s*$", re.IGNORECASE)
_RULE_RE = re.compile(r"^(\d+)\.\s+(.+)$")

_SEVERITY_HINTS: dict[str, str] = {
    "attributable": "major",
    "legible": "minor",
    "contemporaneous": "major",
    "original": "major",
    "accurate": "critical",
    "complete": "major",
    "consistent": "major",
    "enduring": "minor",
    "available": "minor",
    "equipment identification": "major",
    "sop references": "major",
    "environmental conditions": "major",
    "corrections and amendments": "critical",
    "material reconciliation": "critical",
    "yield calculations": "major",
    "in-process controls": "major",
    "checkbox completeness": "major",
    "signature verification": "critical",
    "date completeness": "major",
    "blank field detection": "major",
    "attachment verification": "minor",
    "cross-contamination checklists": "major",
    "equipment operation checklists": "minor",
    "sop reference validation": "major",
    "step alignment": "major",
    "deviation documentation": "critical",
    "parameter compliance": "major",
    "material handling": "major",
}


@dataclass
class AgentMeta:
    id: str
    label: str
    description: str = ""


_SECTIONS_RE = re.compile(r"\[sections:\s*(.+?)\]")


_DOCUMENT_SCOPE_CATEGORIES = frozenset({
    "enduring", "available",
})


@dataclass
class AuditRule:
    id: str
    number: int
    category: str
    category_display: str
    agent: str
    text: str
    severity_hint: str = "observation"
    scope: str = "page"  # "page" | "document" | "section"
    context_sections: list[str] = field(default_factory=list)
    # Applicability metadata (loaded from YAML config)
    applicable_page_types: list[str] = field(default_factory=list)
    applicable_section_types: list[str] = field(default_factory=list)
    applicable_document_types: list[str] = field(default_factory=list)
    excluded_document_types: list[str] = field(default_factory=list)
    # Either a registered requirement-ID string (looked up against
    # ``cross_page.interface._REQUIREMENTS``) or an inline
    # ``{section_type, in_document_type}`` dict resolved against the
    # current segmentation. The cross-page resolver dispatches on
    # shape — see ``cross_page.interface.resolve_requirement``.
    cross_section_requirements: list[Any] = field(default_factory=list)
    skip_conditions: list[str] = field(default_factory=list)
    pass_criteria: str = ""
    evaluation_mode: str = "llm"  # "llm" | "cannot_evaluate"
    # "text" | "vision" | "text_and_vision" | "text_primary" |
    # "llm_arbitrated" | "agentic_audit". The first three drive the
    # per-page router in ``evaluator._run()``; ``text_primary`` and
    # ``llm_arbitrated`` run text + vision in parallel and merge via
    # ``_merge_text_primary`` / ``_merge_llm_arbitrated``;
    # ``agentic_audit`` rules are filtered out of normal batches and
    # handled by ``run_agentic_postpass``. Unknown values fall
    # through to text with a ``compliance.unknown_evaluation_strategy``
    # telemetry warning so dangling strategies don't silently
    # degrade (the GMP rule 11 / 009-branch failure mode).
    evaluation_strategy: str = "text"
    visual_checks: list[str] = field(default_factory=list)
    cannot_evaluate_reason: str = ""
    requires_external_data: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    notes: str = ""
    context_sources: list[dict] = field(default_factory=list)


@dataclass
class RuleBatch:
    batch_id: str
    category: str
    agent: str
    rules: list[AuditRule] = field(default_factory=list)


# ── YAML config loading ─────────────────────────────────────

_YAML_LIST_FIELDS = frozenset({
    "applicable_page_types", "applicable_section_types", "skip_conditions",
    "keywords", "requires_external_data", "applicable_document_types",
    "excluded_document_types", "cross_section_requirements", "visual_checks",
})
_YAML_STR_FIELDS = frozenset({
    "scope", "severity", "evaluation_mode", "evaluation_strategy",
    "cannot_evaluate_reason", "pass_criteria", "notes",
})
_YAML_DICT_LIST_FIELDS = frozenset({"context_sources"})


def _rule_config_path(agent_id: str) -> Path:
    return _RULES_DIR / f"{agent_id}_rules.yaml"


def _load_rule_config(agent: str) -> dict[str, Any]:
    """Load YAML config for an agent. Returns empty dict if file absent."""
    path = _rule_config_path(agent)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Failed to load YAML config from %s", path, exc_info=True)
        return {}


def _resolve_rule_config(
    yaml_config: dict[str, Any],
    category_slug: str,
    rule_number: int,
) -> dict[str, Any]:
    """Cascade: defaults -> category-level -> per-rule overrides."""
    defaults = yaml_config.get("defaults", {})
    categories = yaml_config.get("categories", {})
    cat_config = categories.get(category_slug, {})
    cat_rules = cat_config.get("rules", {})
    rule_config = cat_rules.get(rule_number, {})

    merged: dict[str, Any] = {}
    for key in _YAML_LIST_FIELDS | _YAML_STR_FIELDS:
        val = rule_config.get(key)
        if val is None:
            val = cat_config.get(key)
        if val is None:
            val = defaults.get(key)
        if val is not None:
            merged[key] = val

    for key in _YAML_DICT_LIST_FIELDS:
        val = rule_config.get(key)
        if val is None:
            val = cat_config.get(key)
        if val is None:
            val = defaults.get(key)
        if val is not None:
            merged[key] = val

    return merged


# ── Parsing helpers ──────────────────────────────────────────


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _as_csr_list(value: Any) -> list[Any]:
    """Preserve heterogeneous shape of ``cross_section_requirements``.

    Two valid entry shapes — a registered requirement-ID string or
    an inline ``{section_type, in_document_type}`` dict — must
    survive this normalization untouched. The legacy ``_as_str_list``
    stringifies dicts to ``"{'section_type': ...}"`` which the
    cross-page resolver can never look up, silently neutering every
    inline requirement on every rule.

    Returns:
      * ``[]`` when value is None or not a list
      * dicts pass through (after a coarse type guard)
      * strings stripped, empties dropped
      * anything else logged once and dropped
    """

    if not isinstance(value, list):
        return []

    out: list[Any] = []
    for entry in value:
        if isinstance(entry, dict):
            out.append(entry)
        elif isinstance(entry, str):
            stripped = entry.strip()
            if stripped:
                out.append(stripped)
        else:
            logger.warning(
                "cross_section_requirements entry has unsupported type %s; "
                "expected str or dict — entry dropped",
                type(entry).__name__,
            )
    return out


def _finalise_rule(
    agent: str,
    num: int,
    raw_text: str,
    category: str,
    category_display: str,
    severity: str,
    yaml_overrides: dict[str, Any] | None = None,
) -> AuditRule:
    """Build an AuditRule from accumulated text, extracting [sections: ...] if present.

    yaml_overrides are merged last — they can override severity, scope, and
    all applicability metadata fields.
    """
    context_sections: list[str] = []
    sec_match = _SECTIONS_RE.search(raw_text)
    if sec_match:
        context_sections = [s.strip() for s in sec_match.group(1).split(",")]
        raw_text = _SECTIONS_RE.sub("", raw_text).strip()

    prefix = agent.upper()[:3]
    cat_short = category[:3].upper()
    rule_id = f"{prefix}-{cat_short}{num}"

    scope = "document" if category in _DOCUMENT_SCOPE_CATEGORIES else "page"

    ov = yaml_overrides or {}

    return AuditRule(
        id=rule_id,
        number=num,
        category=category,
        category_display=category_display,
        agent=agent,
        text=raw_text,
        severity_hint=ov.get("severity", severity),
        scope=ov.get("scope", scope),
        context_sections=context_sections,
        applicable_page_types=_as_str_list(ov.get("applicable_page_types", [])),
        applicable_section_types=_as_str_list(ov.get("applicable_section_types", [])),
        applicable_document_types=_as_str_list(ov.get("applicable_document_types", [])),
        excluded_document_types=_as_str_list(ov.get("excluded_document_types", [])),
        cross_section_requirements=_as_csr_list(ov.get("cross_section_requirements", [])),
        skip_conditions=_as_str_list(ov.get("skip_conditions", [])),
        pass_criteria=str(ov.get("pass_criteria", "") or "").strip(),
        evaluation_mode=ov.get("evaluation_mode", "llm"),
        evaluation_strategy=ov.get("evaluation_strategy", "text"),
        visual_checks=_as_str_list(ov.get("visual_checks", [])),
        cannot_evaluate_reason=str(ov.get("cannot_evaluate_reason", "") or "").strip(),
        requires_external_data=_as_str_list(ov.get("requires_external_data", [])),
        keywords=_as_str_list(ov.get("keywords", [])),
        notes=str(ov.get("notes", "") or "").strip(),
        context_sources=list(ov.get("context_sources") or []),
    )


def _parse_rules_file(path: Path, agent: str, yaml_config: dict[str, Any] | None = None) -> list[AuditRule]:
    """Parse a markdown rules file into a list of AuditRule objects.

    If yaml_config is provided, each rule's metadata is enriched via the
    defaults -> category -> per-rule cascade.
    """
    yaml_config = yaml_config or {}
    rules: list[AuditRule] = []
    current_category = "general"
    current_display = "General"

    pending_num: int | None = None
    pending_text: str = ""

    def _flush() -> None:
        nonlocal pending_num, pending_text
        if pending_num is not None and pending_text:
            severity = _SEVERITY_HINTS.get(current_display.lower(), "observation")
            overrides = _resolve_rule_config(yaml_config, current_category, pending_num) if yaml_config else None
            rules.append(_finalise_rule(
                agent, pending_num, pending_text,
                current_category, current_display, severity,
                yaml_overrides=overrides,
            ))
        pending_num = None
        pending_text = ""

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--") or stripped.startswith("You are"):
            _flush()
            continue

        cat_match = _CATEGORY_RE.match(stripped)
        if cat_match and not _RULE_RE.match(stripped):
            _flush()
            current_display = cat_match.group(1).strip()
            current_category = _slugify(current_display)
            continue

        rule_match = _RULE_RE.match(stripped)
        if rule_match:
            _flush()
            pending_num = int(rule_match.group(1))
            pending_text = rule_match.group(2).strip()
            continue

        if pending_num is not None:
            pending_text += " " + stripped

    _flush()
    return rules


def _load_agents_meta() -> list[AgentMeta]:
    if _META_FILE.exists():
        data = json.loads(_META_FILE.read_text(encoding="utf-8"))
        return [AgentMeta(**item) for item in data]
    return []


def _save_agents_meta(meta: list[AgentMeta]) -> None:
    _META_FILE.write_text(
        json.dumps([asdict(m) for m in meta], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _rule_file_path(agent_id: str) -> Path:
    return _RULES_DIR / f"{agent_id}_rules.md"


# ── Registry ─────────────────────────────────────────────────


class RuleRegistry:
    """Loads and manages all audit rules with full CRUD support."""

    def __init__(self) -> None:
        self._rules: dict[str, list[AuditRule]] = {}
        self._meta: list[AgentMeta] = []
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._load_all()

    def _load_all(self) -> None:
        self._meta = _load_agents_meta()
        self._rules.clear()

        for meta in self._meta:
            path = _rule_file_path(meta.id)
            if path.exists():
                yaml_cfg = _load_rule_config(meta.id)
                self._rules[meta.id] = _parse_rules_file(path, meta.id, yaml_cfg)
            else:
                self._rules[meta.id] = []

        for md_file in _RULES_DIR.glob("*_rules.md"):
            agent_id = md_file.stem.replace("_rules", "")
            if agent_id not in self._rules:
                yaml_cfg = _load_rule_config(agent_id)
                self._rules[agent_id] = _parse_rules_file(md_file, agent_id, yaml_cfg)
                if not any(m.id == agent_id for m in self._meta):
                    self._meta.append(AgentMeta(id=agent_id, label=agent_id.upper()))

    def _get_lock(self, agent: str) -> threading.Lock:
        with self._global_lock:
            if agent not in self._locks:
                self._locks[agent] = threading.Lock()
            return self._locks[agent]

    # ── Read methods ─────────────────────────────────────────

    def get_rules(self, agent: str) -> list[AuditRule]:
        return list(self._rules.get(agent, []))

    def get_categories(self, agent: str) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.get_rules(agent):
            seen.setdefault(r.category, None)
        return list(seen)

    def get_category_display(self, agent: str) -> dict[str, str]:
        return {r.category: r.category_display for r in self.get_rules(agent)}

    def get_batches(
        self, agent: str, batch_size: int = 7, by_category: bool = True,
        scope_filter: str | None = None,
    ) -> list[RuleBatch]:
        rules = [r for r in self.get_rules(agent) if r.evaluation_strategy != "agentic_audit"]
        if scope_filter:
            rules = [r for r in rules if r.scope == scope_filter]
        if not rules:
            return []

        batches: list[RuleBatch] = []

        if by_category:
            groups: dict[str, list[AuditRule]] = {}
            for r in rules:
                groups.setdefault(r.category, []).append(r)
            for cat, cat_rules in groups.items():
                for i in range(0, len(cat_rules), batch_size):
                    chunk = cat_rules[i : i + batch_size]
                    batch_id = f"{agent}-{cat}-{i // batch_size}"
                    batches.append(RuleBatch(
                        batch_id=batch_id, category=cat, agent=agent, rules=chunk,
                    ))
        else:
            for i in range(0, len(rules), batch_size):
                chunk = rules[i : i + batch_size]
                batch_id = f"{agent}-batch-{i // batch_size}"
                cat = chunk[0].category if chunk else "general"
                batches.append(RuleBatch(
                    batch_id=batch_id, category=cat, agent=agent, rules=chunk,
                ))

        return batches

    @property
    def agents(self) -> list[str]:
        return list(self._rules.keys())

    def summary(self) -> dict[str, int]:
        return {agent: len(rules) for agent, rules in self._rules.items()}

    def get_agent_meta(self, agent: str) -> AgentMeta | None:
        return next((m for m in self._meta if m.id == agent), None)

    def get_all_agents_meta(self) -> list[AgentMeta]:
        return list(self._meta)

    # ── Write: agents ────────────────────────────────────────

    def add_agent(self, agent_id: str, label: str, description: str = "") -> AgentMeta:
        if any(m.id == agent_id for m in self._meta):
            raise ValueError(f"Agent '{agent_id}' already exists")

        meta = AgentMeta(id=agent_id, label=label, description=description)
        self._meta.append(meta)
        self._rules[agent_id] = []

        path = _rule_file_path(agent_id)
        path.write_text(
            f"{'─' * 50}\n{label.upper()} RULES\n{'─' * 50}\n\n",
            encoding="utf-8",
        )

        _save_agents_meta(self._meta)
        return meta

    def update_agent_meta(
        self, agent_id: str, label: str | None = None, description: str | None = None,
    ) -> AgentMeta:
        meta = self.get_agent_meta(agent_id)
        if meta is None:
            raise ValueError(f"Agent '{agent_id}' not found")

        if label is not None:
            meta.label = label
        if description is not None:
            meta.description = description

        _save_agents_meta(self._meta)
        return meta

    def delete_agent(self, agent_id: str) -> bool:
        meta = self.get_agent_meta(agent_id)
        if meta is None:
            raise ValueError(f"Agent '{agent_id}' not found")

        self._meta = [m for m in self._meta if m.id != agent_id]
        self._rules.pop(agent_id, None)

        path = _rule_file_path(agent_id)
        if path.exists():
            path.unlink()

        _save_agents_meta(self._meta)
        return True

    # ── Write: categories ────────────────────────────────────

    def add_category(self, agent: str, category_display: str) -> str:
        if agent not in self._rules:
            raise ValueError(f"Agent '{agent}' not found")

        category_id = _slugify(category_display)
        existing = self.get_categories(agent)
        if category_id in existing:
            raise ValueError(f"Category '{category_id}' already exists in agent '{agent}'")

        with self._get_lock(agent):
            self._write_file(agent)

            path = _rule_file_path(agent)
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            content = content.rstrip() + f"\n\n---\n\nCategory: {category_display}\n\n"
            path.write_text(content, encoding="utf-8")

            yaml_cfg = _load_rule_config(agent)
            self._rules[agent] = _parse_rules_file(path, agent, yaml_cfg)

        return category_id

    # ── Write: rules ─────────────────────────────────────────

    def add_rule(
        self,
        agent: str,
        category: str,
        category_display: str,
        text: str,
        severity_hint: str = "observation",
    ) -> AuditRule:
        if agent not in self._rules:
            raise ValueError(f"Agent '{agent}' not found")

        with self._get_lock(agent):
            existing_in_cat = [r for r in self._rules[agent] if r.category == category]
            next_num = max((r.number for r in existing_in_cat), default=0) + 1

            prefix = agent.upper()[:3]
            cat_short = category[:3].upper()
            rule_id = f"{prefix}-{cat_short}{next_num}"

            rule = AuditRule(
                id=rule_id,
                number=next_num,
                category=category,
                category_display=category_display,
                agent=agent,
                text=text,
                severity_hint=severity_hint,
            )
            self._rules[agent].append(rule)
            self._write_file(agent)

        return rule

    def bulk_add_rules(
        self,
        agent: str,
        category: str,
        category_display: str,
        texts: list[str],
        severity_hint: str = "observation",
    ) -> list[AuditRule]:
        if agent not in self._rules:
            raise ValueError(f"Agent '{agent}' not found")
        if not texts:
            return []

        added: list[AuditRule] = []
        with self._get_lock(agent):
            existing_in_cat = [r for r in self._rules[agent] if r.category == category]
            next_num = max((r.number for r in existing_in_cat), default=0) + 1
            prefix = agent.upper()[:3]
            cat_short = category[:3].upper()

            for text in texts:
                text = text.strip()
                if not text:
                    continue
                rule_id = f"{prefix}-{cat_short}{next_num}"
                rule = AuditRule(
                    id=rule_id,
                    number=next_num,
                    category=category,
                    category_display=category_display,
                    agent=agent,
                    text=text,
                    severity_hint=severity_hint,
                )
                self._rules[agent].append(rule)
                added.append(rule)
                next_num += 1

            self._write_file(agent)

        return added

    def update_rule(
        self,
        rule_id: str,
        text: str | None = None,
        severity_hint: str | None = None,
    ) -> AuditRule:
        for agent, rules in self._rules.items():
            for rule in rules:
                if rule.id == rule_id:
                    with self._get_lock(agent):
                        if text is not None:
                            rule.text = text
                        if severity_hint is not None:
                            rule.severity_hint = severity_hint
                        self._write_file(agent)
                    return rule

        raise ValueError(f"Rule '{rule_id}' not found")

    def delete_rule(self, rule_id: str) -> bool:
        for agent, rules in self._rules.items():
            for i, rule in enumerate(rules):
                if rule.id == rule_id:
                    category = rule.category
                    with self._get_lock(agent):
                        rules.pop(i)
                        self._renumber_category(agent, category)
                        self._write_file(agent)
                    return True

        raise ValueError(f"Rule '{rule_id}' not found")

    def _renumber_category(self, agent: str, category: str) -> None:
        """Re-number rules within a category and regenerate IDs."""
        prefix = agent.upper()[:3]
        cat_short = category[:3].upper()
        num = 1
        for rule in self._rules[agent]:
            if rule.category == category:
                rule.number = num
                rule.id = f"{prefix}-{cat_short}{num}"
                num += 1

    # ── Serialization ────────────────────────────────────────

    def _write_file(self, agent: str) -> None:
        path = _rule_file_path(agent)
        content = self._serialize_to_markdown(agent)
        path.write_text(content, encoding="utf-8")

    def _serialize_to_markdown(self, agent: str) -> str:
        rules = self._rules.get(agent, [])
        meta = self.get_agent_meta(agent)
        title = meta.label.upper() if meta else agent.upper()

        lines: list[str] = [
            "─" * 50,
            f"{title} RULES",
            "─" * 50,
            "",
        ]

        categories_ordered: list[str] = []
        cat_display: dict[str, str] = {}
        cat_rules: dict[str, list[AuditRule]] = {}
        for r in rules:
            if r.category not in cat_rules:
                categories_ordered.append(r.category)
                cat_display[r.category] = r.category_display
                cat_rules[r.category] = []
            cat_rules[r.category].append(r)

        for idx, cat in enumerate(categories_ordered):
            if idx > 0:
                lines.append("---")
                lines.append("")
            lines.append(f"Category: {cat_display[cat]}")
            lines.append("")
            for rule in cat_rules[cat]:
                rule_line = f"{rule.number}. {rule.text}"
                if rule.context_sections:
                    rule_line += f" [sections: {', '.join(rule.context_sections)}]"
                lines.append(rule_line)
            lines.append("")

        return "\n".join(lines)


# ── Singleton access ─────────────────────────────────────────

_registry: RuleRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> RuleRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = RuleRegistry()
    return _registry


def invalidate_registry() -> None:
    global _registry
    with _registry_lock:
        _registry = None
