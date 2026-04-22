"""Pluggable Stage-3 extraction (Spec 001 / Constitution VII).

The compliance stage works against :class:`ExtractedPackage` — a pure
Pydantic aggregate of fields and pages. How that data comes into being
is an adapter concern, so Stage 3 talks to an :class:`ExtractorPort`:

- :class:`SidecarExtractor` — reads a hand-crafted ``extraction.json``
  (v0 default; keeps the test suite deterministic).
- :class:`OCRBackedExtractor` — runs an existing
  :class:`~app.core.ports.ocr.OCREngine` across the package's PDFs and
  projects :class:`~app.core.ports.ocr.OCRResult.key_value_pairs` into
  :class:`~app.bmr.capabilities.extracted_data.FieldValue` records via a
  declarative ``field_map``. The result is cached as a sidecar so a
  reviewer opening the run later sees the exact extraction that
  compliance saw (Constitution III — everything we act on is
  attributable).

New adapters (e.g. layout-aware parsers, VLM-backed extractors) plug in
by implementing :class:`ExtractorPort`. The BMR run service accepts an
extractor via its constructor so production and tests can inject their
own.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


# Cap OCR work per document so a malformed (or maliciously large) PDF
# cannot exhaust worker memory. Configurable via env because real BPCRs
# can legitimately run to the low hundreds of pages.
MAX_OCR_PAGES_PER_DOC = _env_int("AT_BMR__MAX_OCR_PAGES_PER_DOC", 500)

from app.bmr.capabilities.extracted_data import (
    ExtractedPackage,
    ExtractedPage,
    FieldValue,
)
from app.bmr.ingest.models import DocumentPackage, DocumentRef
from app.bmr.workflow.extraction import load_extracted_package
from app.core.ports.ocr import OCREngine, OCRResult

# Role → (field name → OCR key label(s) to pull). Each role can also
# declare ``page_tags`` so the resulting :class:`ExtractedPage` gets the
# same tag that hand-written fixtures use (e.g. ``bpcr_step_page``).
FieldMap = Mapping[str, "RoleExtraction"]


class RoleExtraction(Protocol):
    """What to pull out of OCR for a given document role."""

    @property
    def page_tags(self) -> list[str]: ...

    @property
    def fields(self) -> Mapping[str, list[str]]: ...

    @property
    def document_role(self) -> str: ...


class ExtractorPort(Protocol):
    """Produces an :class:`ExtractedPackage` for a BMR run.

    Implementations are called once per run from Stage 3. They are
    allowed to block on I/O; the workflow service runs the graph on a
    worker thread.
    """

    def extract(
        self,
        package: DocumentPackage,
        *,
        package_dir: Path,
        extraction_path: Path | None = None,
    ) -> ExtractedPackage: ...


class SidecarExtractor:
    """v0 default — reads a pre-built ``extraction.json`` sidecar."""

    def extract(
        self,
        package: DocumentPackage,
        *,
        package_dir: Path,
        extraction_path: Path | None = None,
    ) -> ExtractedPackage:
        return load_extracted_package(
            package.package_id,
            package_dir=package_dir,
            extraction_path=extraction_path,
        )


class OCRBackedExtractor:
    """Wrap an :class:`OCREngine` to produce :class:`ExtractedPackage`.

    For each document in the package whose role appears in ``field_map``,
    runs the OCR engine, then pulls the named keys out of
    :class:`~app.core.ports.ocr.OCRResult.key_value_pairs` and emits one
    :class:`~app.bmr.capabilities.extracted_data.FieldValue` per match
    per page. The resulting package is written back to
    ``<package_dir>/extraction.json`` so subsequent runs (and corrections,
    follow-up #4) reuse the same extraction.

    This intentionally does not cover every OCR capability in the shared
    port — it is the minimum projection needed for Spec 003 rules. Richer
    projections (tables, signatures, formulas) slot in as new methods on
    :class:`RoleExtraction`.
    """

    def __init__(
        self,
        *,
        ocr_engine: OCREngine,
        field_map: Mapping[str, OCRRoleExtraction],
        write_sidecar: bool = True,
    ) -> None:
        self._ocr = ocr_engine
        self._field_map = field_map
        self._write_sidecar = write_sidecar

    def extract(
        self,
        package: DocumentPackage,
        *,
        package_dir: Path,
        extraction_path: Path | None = None,
    ) -> ExtractedPackage:
        pages: list[ExtractedPage] = []
        for doc in package.documents:
            role_cfg = self._field_map.get(doc.role)
            if role_cfg is None:
                continue
            pdf_path = Path(doc.stored_path)
            if not pdf_path.is_absolute():
                pdf_path = package_dir / pdf_path
            if not pdf_path.is_file():
                continue
            ocr_result = _run_async(
                self._ocr.extract(
                    str(pdf_path),
                    pages=list(range(1, MAX_OCR_PAGES_PER_DOC + 1)),
                )
            )
            pages.extend(_project_result_to_pages(doc, role_cfg, ocr_result))

        extracted = ExtractedPackage(package_id=package.package_id, pages=pages)

        if self._write_sidecar:
            target = package_dir / "extraction.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(extracted.model_dump(mode="json"), indent=2),
                encoding="utf-8",
            )
        return extracted


class OCRRoleExtraction:
    """Declarative field-map entry for :class:`OCRBackedExtractor`."""

    def __init__(
        self,
        *,
        document_role: str,
        page_tags: Iterable[str] = (),
        fields: Mapping[str, Iterable[str]],
    ) -> None:
        self.document_role = document_role
        self.page_tags = list(page_tags)
        self.fields = {k: list(v) for k, v in fields.items()}


def _project_result_to_pages(
    doc: DocumentRef,
    role_cfg: OCRRoleExtraction,
    result: OCRResult,
) -> list[ExtractedPage]:
    """Fold OCR key/value pairs into per-page extraction records.

    The BMR capabilities work with 1-indexed page numbers; the OCR port
    already uses ``page_num`` in the same convention. We keep one
    :class:`ExtractedPage` per physical page so rules that target a
    particular ``page_index`` align exactly with the OCR output.
    """

    pages: dict[int, list[FieldValue]] = {}
    for kv in result.key_value_pairs:
        field_name = _match_field(kv.key, role_cfg.fields)
        if field_name is None:
            continue
        pages.setdefault(kv.page_num or 1, []).append(
            FieldValue(
                field=field_name,
                value=kv.value,
                confidence=kv.confidence,
                source_doc_id=doc.doc_id,
                source_page_index=kv.page_num or 1,
            )
        )

    out: list[ExtractedPage] = []
    for page_num, fields in sorted(pages.items()):
        out.append(
            ExtractedPage(
                doc_id=doc.doc_id,
                document_role=role_cfg.document_role,
                page_index=page_num,
                tags=list(role_cfg.page_tags),
                fields=fields,
            )
        )
    return out


def _match_field(
    ocr_key: str, field_map: Mapping[str, list[str]]
) -> str | None:
    """Return the canonical field for an OCR key, or ``None``.

    Matching is case-insensitive and ignores surrounding whitespace so
    reviewers can declare natural labels like ``"Dispensed weight (kg)"``
    and have it bind to ``dispensed_weight_kg``.
    """

    needle = ocr_key.strip().lower()
    for field, labels in field_map.items():
        for label in labels:
            if label.strip().lower() == needle:
                return field
    return None


def _run_async(coro: Any) -> Any:
    """Run an awaitable from a sync context.

    The BMR workflow stages are synchronous by design (Constitution II),
    but the OCR port is async. We bridge via ``asyncio.run`` when no loop
    is running and ``asyncio.new_event_loop`` otherwise. Adapters that
    don't want this bridging can wrap themselves in a sync facade before
    passing to :class:`OCRBackedExtractor`.
    """

    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


__all__ = [
    "ExtractorPort",
    "FieldMap",
    "OCRBackedExtractor",
    "OCRRoleExtraction",
    "RoleExtraction",
    "SidecarExtractor",
]
