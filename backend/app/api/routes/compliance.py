"""Compliance review API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/{doc_id}/report")
async def get_compliance_report(doc_id: str):
    return {"doc_id": doc_id, "report": None, "status": "not_implemented"}


@router.post("/{doc_id}/run")
async def run_compliance_review(doc_id: str):
    return {"doc_id": doc_id, "status": "queued"}
