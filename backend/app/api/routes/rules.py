"""Rules management API routes.

CRUD operations for compliance agents, categories, and rules.

GET  /agents                        — list agents with metadata
POST /agents                        — create new agent
PUT  /agents/{agent}                — update agent label/description
DELETE /agents/{agent}              — delete agent
GET  /{agent}                       — all rules grouped by category
POST /{agent}/rules                 — add single rule
POST /{agent}/rules/bulk            — add multiple rules
PUT  /{agent}/rules/{rule_id}       — update rule
DELETE /{agent}/rules/{rule_id}     — delete rule
POST /{agent}/categories            — add category
"""

from __future__ import annotations

import glob
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.compliance.rules.registry import get_registry
from app.config.settings import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request / response models ────────────────────────────────


class CreateAgentRequest(BaseModel):
    id: str = Field(..., pattern=r"^[a-z][a-z0-9_-]*$", min_length=2, max_length=40)
    label: str = Field(..., min_length=1, max_length=80)
    description: str = ""


class UpdateAgentRequest(BaseModel):
    label: str | None = None
    description: str | None = None


class AddRuleRequest(BaseModel):
    category: str = Field(..., min_length=1)
    category_display: str = Field(..., min_length=1)
    text: str = Field(..., min_length=3)
    severity_hint: str = Field("observation", pattern=r"^(critical|major|minor|observation)$")


class BulkAddRulesRequest(BaseModel):
    category: str = Field(..., min_length=1)
    category_display: str = Field(..., min_length=1)
    texts: list[str] = Field(..., min_length=1)
    severity_hint: str = Field("observation", pattern=r"^(critical|major|minor|observation)$")


class UpdateRuleRequest(BaseModel):
    text: str | None = None
    severity_hint: str | None = Field(None, pattern=r"^(critical|major|minor|observation)$")


class AddCategoryRequest(BaseModel):
    display: str = Field(..., min_length=1, max_length=80)


# ── Helpers ──────────────────────────────────────────────────


def _check_agent_in_reports(agent_id: str) -> list[str]:
    """Return doc_ids whose compliance reports reference this agent."""
    settings = get_settings()
    base = settings.storage.base_path
    doc_ids: list[str] = []
    for path_str in glob.glob(f"{base}/*/compliance_result.json"):
        try:
            data = json.loads(open(path_str, encoding="utf-8").read())
            agents_executed = data.get("audit_trail", {}).get("agents_executed", [])
            if agent_id in agents_executed:
                doc_ids.append(data.get("doc_id", "unknown"))
        except Exception:
            continue
    return doc_ids


# ── Agent endpoints ──────────────────────────────────────────


@router.get("/agents")
async def list_agents():
    """List all compliance agents with metadata."""
    reg = get_registry()
    result = []
    for meta in reg.get_all_agents_meta():
        rules = reg.get_rules(meta.id)
        cats = reg.get_categories(meta.id)
        result.append({
            "id": meta.id,
            "label": meta.label,
            "description": meta.description,
            "rule_count": len(rules),
            "category_count": len(cats),
            "categories": cats,
        })
    return result


@router.post("/agents", status_code=201)
async def create_agent(body: CreateAgentRequest):
    """Create a new compliance agent."""
    reg = get_registry()
    try:
        meta = reg.add_agent(body.id, body.label, body.description)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"id": meta.id, "label": meta.label, "description": meta.description}


@router.put("/agents/{agent}")
async def update_agent(agent: str, body: UpdateAgentRequest):
    """Update agent label and/or description."""
    reg = get_registry()
    try:
        meta = reg.update_agent_meta(agent, label=body.label, description=body.description)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": meta.id, "label": meta.label, "description": meta.description}


@router.delete("/agents/{agent}")
async def delete_agent(agent: str):
    """Delete a compliance agent. Blocked if existing reports reference it."""
    doc_ids = _check_agent_in_reports(agent)
    if doc_ids:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: {len(doc_ids)} compliance report(s) reference this agent.",
        )
    reg = get_registry()
    try:
        reg.delete_agent(agent)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": agent}


# ── Agent rules endpoints ────────────────────────────────────


@router.get("/{agent}")
async def get_agent_rules(agent: str):
    """Get all rules for an agent, grouped by category."""
    reg = get_registry()
    meta = reg.get_agent_meta(agent)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent}' not found")

    rules = reg.get_rules(agent)
    cat_display = reg.get_category_display(agent)

    categories_ordered: list[str] = []
    cat_rules: dict[str, list[dict]] = {}
    for r in rules:
        if r.category not in cat_rules:
            categories_ordered.append(r.category)
            cat_rules[r.category] = []
        cat_rules[r.category].append({
            "id": r.id,
            "number": r.number,
            "text": r.text,
            "severity_hint": r.severity_hint,
        })

    return {
        "agent": agent,
        "label": meta.label,
        "description": meta.description,
        "categories": [
            {
                "id": cat,
                "display": cat_display.get(cat, cat),
                "rules": cat_rules.get(cat, []),
            }
            for cat in categories_ordered
        ],
        "total_rules": len(rules),
    }


@router.post("/{agent}/rules", status_code=201)
async def add_rule(agent: str, body: AddRuleRequest):
    """Add a single rule to an agent."""
    reg = get_registry()
    try:
        rule = reg.add_rule(
            agent, body.category, body.category_display, body.text, body.severity_hint,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return asdict(rule)


@router.post("/{agent}/rules/bulk", status_code=201)
async def bulk_add_rules(agent: str, body: BulkAddRulesRequest):
    """Add multiple rules at once to an agent."""
    reg = get_registry()
    try:
        rules = reg.bulk_add_rules(
            agent, body.category, body.category_display, body.texts, body.severity_hint,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"added": len(rules), "rules": [asdict(r) for r in rules]}


@router.put("/{agent}/rules/{rule_id}")
async def update_rule(agent: str, rule_id: str, body: UpdateRuleRequest):
    """Update a rule's text and/or severity hint."""
    reg = get_registry()
    try:
        rule = reg.update_rule(rule_id, text=body.text, severity_hint=body.severity_hint)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return asdict(rule)


@router.delete("/{agent}/rules/{rule_id}")
async def delete_rule(agent: str, rule_id: str):
    """Delete a rule."""
    reg = get_registry()
    try:
        reg.delete_rule(rule_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": rule_id}


# ── Category endpoints ───────────────────────────────────────


@router.post("/{agent}/categories", status_code=201)
async def add_category(agent: str, body: AddCategoryRequest):
    """Add a new category to an agent."""
    reg = get_registry()
    try:
        cat_id = reg.add_category(agent, body.display)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": cat_id, "display": body.display}
