"""v0 extraction loader.

Real OCR-driven extraction will be wired in a later slice (Constitution
VII — leverage the existing OCR pipeline rather than rewrite it). For the
vertical slice we accept a pre-built ``ExtractedPackage`` as a sidecar
JSON file so the graph can run end-to-end with deterministic fixtures.

Lookup order:

1. An explicit ``extraction_path`` passed on the run.
2. ``<package_dir>/extraction.json`` alongside ``package.json``.
3. An empty :class:`ExtractedPackage` (compliance will emit
   ``unevaluated`` findings for every rule — useful for smoke tests).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.bmr.capabilities.extracted_data import ExtractedPackage


class ExtractionPackageMismatchError(ValueError):
    """Raised when an extraction.json declares a different package_id."""


def load_extracted_package(
    package_id: str,
    *,
    package_dir: Path | None = None,
    extraction_path: Path | None = None,
) -> ExtractedPackage:
    candidates: list[Path] = []
    if extraction_path is not None:
        candidates.append(extraction_path)
    if package_dir is not None:
        candidates.append(package_dir / "extraction.json")

    for path in candidates:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            existing_pkg = payload.get("package_id")
            if existing_pkg is not None and existing_pkg != package_id:
                raise ExtractionPackageMismatchError(
                    f"extraction file {path!s} declares package_id "
                    f"{existing_pkg!r} but run targets {package_id!r}"
                )
            payload["package_id"] = package_id
            return ExtractedPackage.model_validate(payload)

    return ExtractedPackage(package_id=package_id, pages=[])


__all__ = ["ExtractionPackageMismatchError", "load_extracted_package"]
