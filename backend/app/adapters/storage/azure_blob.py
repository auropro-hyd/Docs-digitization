"""Azure Blob Storage adapter (for staging deployment)."""

from __future__ import annotations

from app.config.settings import StorageConfig
from app.core.models.document import DigitalDocument


class AzureBlobAdapter:
    """Placeholder for Azure Blob Storage -- implemented when staging deployment is configured."""

    def __init__(self, config: StorageConfig):
        self._connection_string = config.azure_connection_string
        self._container = config.azure_container

    async def save_document(self, doc: DigitalDocument) -> str:
        raise NotImplementedError("Azure Blob adapter not yet implemented")

    async def get_document(self, doc_id: str) -> DigitalDocument | None:
        raise NotImplementedError("Azure Blob adapter not yet implemented")

    async def list_documents(self, *, limit: int = 50, offset: int = 0) -> list[DigitalDocument]:
        raise NotImplementedError("Azure Blob adapter not yet implemented")

    async def save_file(self, file_bytes: bytes, filename: str) -> str:
        raise NotImplementedError("Azure Blob adapter not yet implemented")

    async def get_file(self, path: str) -> bytes:
        raise NotImplementedError("Azure Blob adapter not yet implemented")
