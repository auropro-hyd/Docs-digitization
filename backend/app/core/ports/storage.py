"""Document Store port definition.

Storage adapters (PostgreSQL, filesystem, Azure Blob) must implement this protocol.
"""

from __future__ import annotations

from typing import Protocol

from app.core.models.document import DigitalDocument


class DocumentStore(Protocol):
    """Port for document persistence."""

    async def save_document(self, doc: DigitalDocument) -> str:
        """Save a digitalized document and return its ID."""
        ...

    async def get_document(self, doc_id: str) -> DigitalDocument | None:
        """Retrieve a digitalized document by ID."""
        ...

    async def list_documents(self, *, limit: int = 50, offset: int = 0) -> list[DigitalDocument]:
        """List documents with pagination."""
        ...

    async def save_file(self, file_bytes: bytes, filename: str) -> str:
        """Save a raw file (PDF, image) and return the storage path/URL."""
        ...

    async def get_file(self, path: str) -> bytes:
        """Retrieve a raw file by its storage path."""
        ...
