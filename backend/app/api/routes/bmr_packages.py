"""BMR package ingestion routes.

Narrow v0 surface:

- ``POST /api/bmr/packages``  — multi-file upload + classification.
- ``GET  /api/bmr/packages/{package_id}`` — retrieve stored package.
- ``GET  /api/bmr/packages`` — list package ids (dev/debug only).
- ``GET  /api/bmr/manifests`` — list available manifest ids.

Lives alongside the existing ``documents`` router — no regression on
the legacy upload path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import require_actor
from app.bmr.ingest import (
    DocumentPackage,
    PackageIngestService,
    PackageStore,
)
from app.bmr.ingest.service import (
    MAX_FILES_PER_PACKAGE,
    MAX_UPLOAD_BYTES,
    IncomingFile,
    PackageTooLargeError,
)
from app.config.settings import get_settings

router = APIRouter()


class PackageListItem(BaseModel):
    package_id: str


class PackageListResponse(BaseModel):
    packages: list[PackageListItem]


class ManifestListItem(BaseModel):
    id: str
    path: str


class ManifestListResponse(BaseModel):
    manifests: list[ManifestListItem]


# ── wiring ───────────────────────────────────────────────────────────────────


def _bmr_base_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.base_path).resolve().parent / "bmr-packages"


def _manifests_dir() -> Path:
    # Manifests are config, not user data — keep them under `backend/config/bmr/`.
    return (Path(__file__).resolve().parents[3] / "config" / "bmr" / "pilot" / "manifests").resolve()


@lru_cache(maxsize=1)
def _service() -> PackageIngestService:
    store = PackageStore(_bmr_base_dir())
    return PackageIngestService(store=store, manifests_dir=_manifests_dir())


def _store() -> PackageStore:
    return PackageStore(_bmr_base_dir())


# ── endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/packages",
    response_model=DocumentPackage,
    status_code=status.HTTP_201_CREATED,
)
async def upload_package(
    files: list[UploadFile] = File(..., description="One or more PDFs."),  # noqa: B008 — FastAPI pattern
    manifest_id: str = Form(..., description="Manifest id, e.g. 'default'."),  # noqa: B008
    _actor: str = Depends(require_actor),
) -> DocumentPackage:
    """Upload a BMR document package and classify its files.

    Files that cannot be classified end up with ``role=None`` and the
    package enters ``NEEDS_REVIEW`` status. A reviewer-override endpoint
    will be added in a later slice.
    """

    if not files:
        raise HTTPException(status_code=400, detail="at least one file is required")
    if len(files) > MAX_FILES_PER_PACKAGE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"package contains {len(files)} files; "
                f"max allowed is {MAX_FILES_PER_PACKAGE}"
            ),
        )

    incoming: list[IncomingFile] = []
    running_total = 0
    for f in files:
        content = await f.read()
        running_total += len(content)
        if running_total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"package exceeds {MAX_UPLOAD_BYTES} bytes",
            )
        incoming.append(
            IncomingFile(
                filename=f.filename or "unnamed.pdf",
                content=content,
                content_type=f.content_type,
            )
        )

    service = _service()
    try:
        package = service.ingest(manifest_id=manifest_id, files=incoming)
    except PackageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return package


@router.get("/packages/{package_id}", response_model=DocumentPackage)
async def get_package(package_id: str, _actor: str = Depends(require_actor)) -> DocumentPackage:
    package = _store().load(package_id)
    if package is None:
        raise HTTPException(status_code=404, detail=f"package {package_id} not found")
    return package


@router.get("/packages", response_model=PackageListResponse)
async def list_packages(_actor: str = Depends(require_actor)) -> PackageListResponse:
    ids = _store().list_ids()
    return PackageListResponse(packages=[PackageListItem(package_id=i) for i in ids])


@router.get("/manifests", response_model=ManifestListResponse)
async def list_manifests(_actor: str = Depends(require_actor)) -> ManifestListResponse:
    manifests_dir = _manifests_dir()
    if not manifests_dir.is_dir():
        return ManifestListResponse(manifests=[])
    items: list[ManifestListItem] = []
    for path in sorted(manifests_dir.glob("*.yaml")):
        items.append(ManifestListItem(id=path.stem, path=str(path)))
    return ManifestListResponse(manifests=items)


__all__ = ["router"]
