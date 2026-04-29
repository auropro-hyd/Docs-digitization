"""Production wiring for Spec 007 BPCR section detection.

Composes the three pure capabilities (spec loader → detector → tagger)
into the single ``SectionEnricher`` callable that
:func:`make_extraction_stage` accepts. Loads the canonical section
spec **once** at construction time so a malformed YAML fails service
startup loudly (Constitution VI), not silently per-run.

Production wiring:

- :func:`build_default_section_enricher` reads
  ``backend/config/bmr/pilot/bpcr-section-spec.yaml`` (or whatever
  ``AT_BMR__BPCR_SECTIONS_SPEC`` points at), constructs an enricher
  that walks every BPCR ``DocumentRef`` in the package, looks up the
  matching OCR sidecar via :func:`load_ocr_sidecar`, runs the
  detector, and stamps the resulting :class:`BPCRSectionMap` onto
  the :class:`ExtractedPackage` via the tagger.

- When the OCR sidecar is missing for a BPCR doc the enricher
  records a structured warning and skips that document. The run
  continues; section-aware rules degrade per their existing
  ``fallback`` policy (Spec 007 FR-016).

The whole pipeline is opt-in via ``AT_BMR__BPCR_SECTIONS_ENABLED``
(default true). When the flag is off the workflow stage short-circuits
the enricher altogether — the factory is never called.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from app.bmr.capabilities.bpcr_section_detect import (
    BPCRSectionMap,
    DetectionMode,
    detect_bpcr_sections,
)
from app.bmr.capabilities.bpcr_section_tagger import tag_bpcr_pages
from app.bmr.capabilities.bpcr_sections_spec import (
    BPCRSectionsSpec,
    BPCRSectionsSpecError,
    load_spec,
)
from app.bmr.capabilities.extracted_data import ExtractedPackage
from app.bmr.ingest.models import DocumentPackage
from app.bmr.workflow.extractor import load_ocr_sidecar
from app.core.ports.ocr import OCRPageResult, OCRResult

logger = logging.getLogger(__name__)

SectionEnricher = Callable[
    [ExtractedPackage, DocumentPackage, Path], ExtractedPackage
]
"""Re-export for callers that want to type their own enricher
identically to the workflow stage's expectation."""


def _synthesize_ocr_from_extracted(
    extracted: ExtractedPackage, doc_id: str
) -> OCRResult | None:
    """Build a markdown-only :class:`OCRResult` from extraction.json.

    Walks ``extracted.pages`` for the given ``doc_id`` and returns one
    :class:`OCRPageResult` per page that has a non-empty ``text``
    field. When no page carries text, returns ``None`` so the caller
    can log the missing-input warning.

    The synthesized result has no bounding regions — the detector's
    markdown-only fallback path consumes it with reduced confidence,
    which is correct behaviour for "we have content but no layout".
    """

    pages: list[OCRPageResult] = []
    for p in sorted(
        (p for p in extracted.pages if p.doc_id == doc_id),
        key=lambda x: x.page_index,
    ):
        text = (p.text or "").strip()
        if not text:
            continue
        pages.append(
            OCRPageResult(
                page_num=p.page_index,
                markdown=p.text or "",
            )
        )
    if not pages:
        return None
    return OCRResult(
        pages=pages,
        full_markdown="\n\n".join(p.markdown for p in pages),
    )


class _ProductionSectionEnricher:
    """Callable that walks BPCR docs and stamps section_id on pages.

    Holds the loaded :class:`BPCRSectionsSpec` so we don't re-parse
    the YAML on every BMR run. The ``mode`` field is exposed so
    Phase 2 can flip to ``hybrid`` without changing the constructor
    contract.
    """

    def __init__(
        self,
        *,
        sections_spec: BPCRSectionsSpec,
        mode: DetectionMode = "heuristic",
    ) -> None:
        self._spec = sections_spec
        self._mode = mode

    def __call__(
        self,
        extracted: ExtractedPackage,
        package: DocumentPackage,
        package_dir: Path,
    ) -> ExtractedPackage:
        bpcr_docs = [d for d in package.documents if d.role == "BPCR"]
        if not bpcr_docs:
            return extracted

        section_maps: dict[str, BPCRSectionMap] = {}
        for doc in bpcr_docs:
            ocr = load_ocr_sidecar(package_dir, doc.doc_id)
            if ocr is None:
                # Pilot fallback (Spec 007 follow-up):
                # Many real packages reach Stage 3 via SidecarExtractor —
                # there's no OCR sidecar, only an extraction.json. If the
                # extraction.json's pages carry per-page ``text``, we
                # synthesize a markdown-only OCRResult from it so the
                # detector still has something to scan. The detector's
                # markdown-only fallback path handles the missing layout
                # coordinates with reduced confidence (heuristic-only
                # mode is fine without bounding boxes).
                ocr = _synthesize_ocr_from_extracted(extracted, doc.doc_id)
                if ocr is None:
                    logger.warning(
                        "bpcr.section_enricher.no_ocr_sidecar package_id=%s "
                        "doc_id=%s — drop %s/ocr/%s.json next to "
                        "extraction.json (or populate ExtractedPage.text "
                        "in extraction.json) to enable section detection",
                        package.package_id,
                        doc.doc_id,
                        package_dir.name,
                        doc.doc_id,
                    )
                    continue
                logger.info(
                    "bpcr.section_enricher.synth_ocr_from_extraction "
                    "package_id=%s doc_id=%s pages=%d — running detector "
                    "in markdown-only fallback (no layout coordinates)",
                    package.package_id,
                    doc.doc_id,
                    len(ocr.pages),
                )
            try:
                section_maps[doc.doc_id] = detect_bpcr_sections(
                    doc_id=doc.doc_id, ocr=ocr, sections_spec=self._spec, mode=self._mode
                )
            except NotImplementedError:
                raise
            except Exception:  # noqa: BLE001 — fail-open per FR-006
                logger.exception(
                    "bpcr.section_enricher.detect_failed package_id=%s "
                    "doc_id=%s",
                    package.package_id,
                    doc.doc_id,
                )

        if not section_maps:
            return extracted

        return tag_bpcr_pages(extracted, section_maps=section_maps)


def build_default_section_enricher(
    *,
    spec_path: Path | None = None,
    mode: DetectionMode = "heuristic",
) -> SectionEnricher | None:
    """Construct the production enricher from the canonical YAML spec.

    Returns ``None`` when the spec file cannot be loaded — the
    extraction stage then runs as if section detection were disabled,
    so a deployment that hasn't shipped a spec yet still works.
    Service startup logs the failure so the operator sees why
    section_id is missing from findings.

    Pass an explicit ``spec_path`` in tests; production should rely
    on the env-aware default in
    :func:`app.bmr.capabilities.bpcr_sections_spec.default_spec_path`.
    """

    try:
        spec = load_spec(spec_path)
    except BPCRSectionsSpecError as exc:
        logger.warning(
            "bpcr.section_enricher.spec_unavailable error=%s — section "
            "detection disabled for this process",
            exc,
        )
        return None

    logger.info(
        "bpcr.section_enricher.ready spec_version=%s sections=%d mode=%s",
        spec.spec_version,
        len(spec.sections),
        mode,
    )
    return _ProductionSectionEnricher(sections_spec=spec, mode=mode)


__all__ = [
    "SectionEnricher",
    "build_default_section_enricher",
]
