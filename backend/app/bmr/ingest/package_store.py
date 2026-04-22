"""Filesystem-backed storage for :class:`DocumentPackage` aggregates.

Layout::

    <base>/<package_id>/
        package.json            # serialised DocumentPackage
        files/
            <doc_id>_<safe-filename>.pdf

Writes are best-effort atomic (write to ``.tmp`` then rename). Reads fall
back to ``None`` when a package is missing. Concurrency beyond a single
process is out of scope for v0; a future swap to Postgres is trivial.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from app.bmr.ingest.models import DocumentPackage

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    cleaned = _SAFE_NAME_RE.sub("_", filename).strip("._-")
    return cleaned or "file"


class PackageStore:
    """Simple FS-based store for BMR packages."""

    def __init__(self, base_path: Path) -> None:
        self._base = Path(base_path).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def new_package_dir(self) -> tuple[str, Path]:
        package_id = uuid.uuid4().hex
        pkg_dir = self._base / package_id
        (pkg_dir / "files").mkdir(parents=True, exist_ok=True)
        return package_id, pkg_dir

    def store_file(
        self,
        package_id: str,
        filename: str,
        content: bytes,
    ) -> tuple[str, Path]:
        """Persist one uploaded file under the package's ``files/`` dir.

        Returns ``(doc_id, stored_path)``.
        """

        doc_id = uuid.uuid4().hex
        safe = _safe_filename(filename)
        pkg_dir = self._base / package_id
        files_dir = pkg_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        stored = files_dir / f"{doc_id}_{safe}"
        tmp = stored.with_suffix(stored.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.rename(stored)
        return doc_id, stored

    def save(self, package: DocumentPackage) -> None:
        pkg_dir = self._base / package.package_id
        pkg_dir.mkdir(parents=True, exist_ok=True)
        target = pkg_dir / "package.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(
            package.model_dump_json(indent=2),
            encoding="utf-8",
        )
        tmp.rename(target)

    def load(self, package_id: str) -> DocumentPackage | None:
        target = self._base / package_id / "package.json"
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return DocumentPackage.model_validate(payload)

    def list_ids(self) -> list[str]:
        if not self._base.is_dir():
            return []
        return sorted(p.name for p in self._base.iterdir() if p.is_dir())


__all__ = ["PackageStore"]
