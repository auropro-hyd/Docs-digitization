"""Document upload and processing API routes."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile

from app.config.settings import get_settings

router = APIRouter()


@router.post("/upload")
async def upload_document(file: UploadFile):
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    upload_dir = Path(settings.storage.base_path) / doc_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_path = upload_dir / (file.filename or "document.pdf")
    content = await file.read()
    file_path.write_bytes(content)

    return {
        "doc_id": doc_id,
        "filename": file.filename,
        "size_bytes": len(content),
        "status": "uploaded",
    }


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    return {"doc_id": doc_id, "status": "not_implemented"}


@router.get("/")
async def list_documents():
    return {"documents": [], "total": 0}
