"""HITL review API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/{doc_id}/pages")
async def get_review_pages(doc_id: str):
    return {"doc_id": doc_id, "pages": [], "status": "not_implemented"}


@router.post("/{doc_id}/pages/{page_num}/approve")
async def approve_page(doc_id: str, page_num: int):
    return {"doc_id": doc_id, "page_num": page_num, "action": "approved"}


@router.post("/{doc_id}/pages/{page_num}/edit")
async def edit_page(doc_id: str, page_num: int):
    return {"doc_id": doc_id, "page_num": page_num, "action": "edited"}


@router.post("/{doc_id}/pages/{page_num}/flag")
async def flag_page(doc_id: str, page_num: int):
    return {"doc_id": doc_id, "page_num": page_num, "action": "flagged"}
