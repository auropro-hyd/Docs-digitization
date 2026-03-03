"""Local filesystem storage adapter."""

from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import StorageConfig
from app.core.models.document import DigitalDocument


class FileSystemAdapter:
    def __init__(self, config: StorageConfig):
        self._base = Path(config.base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    async def save_document(self, doc: DigitalDocument) -> str:
        doc_dir = self._base / doc.doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "document.json").write_text(doc.model_dump_json(indent=2))
        return doc.doc_id

    async def get_document(self, doc_id: str) -> DigitalDocument | None:
        path = self._base / doc_id / "document.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return DigitalDocument.model_validate(data)

    async def list_documents(self, *, limit: int = 50, offset: int = 0) -> list[DigitalDocument]:
        docs: list[DigitalDocument] = []
        if not self._base.exists():
            return docs
        entries = sorted(self._base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in entries[offset : offset + limit]:
            doc_file = entry / "document.json"
            if doc_file.exists():
                data = json.loads(doc_file.read_text())
                docs.append(DigitalDocument.model_validate(data))
        return docs

    async def save_file(self, file_bytes: bytes, filename: str) -> str:
        path = self._base / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file_bytes)
        return str(path)

    async def get_file(self, path: str) -> bytes:
        return Path(path).read_bytes()
