"""Shared fixtures for BMR workflow tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import IncomingFile, PackageIngestService
from app.bmr.workflow.run_store import RunStore
from app.bmr.workflow.service import BMRRunService

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_ROOT = Path(__file__).resolve().parents[3]
PILOT_MANIFESTS = BACKEND_ROOT / "config" / "bmr" / "pilot" / "manifests"
PILOT_RULES_DIR = BACKEND_ROOT / "config" / "rules" / "pilot" / "bank"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def package_store(tmp_path: Path) -> PackageStore:
    return PackageStore(tmp_path / "packages")


@pytest.fixture
def run_store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "runs")


@pytest.fixture
def run_service(
    package_store: PackageStore, run_store: RunStore, repo_root: Path
) -> BMRRunService:
    return BMRRunService(
        package_store=package_store, run_store=run_store, repo_root=repo_root
    )


@pytest.fixture
def ingest_service(package_store: PackageStore) -> PackageIngestService:
    return PackageIngestService(store=package_store, manifests_dir=PILOT_MANIFESTS)


@pytest.fixture
def pilot_rules_dir() -> Path:
    return PILOT_RULES_DIR


def write_extraction_fixture(
    package_store: PackageStore,
    package_id: str,
    *,
    bpcr_doc_id: str,
    rm_doc_id: str,
    bpcr_weight_kg: float,
    rm_weight_kg: float,
    operator_signature: str | None,
) -> Path:
    """Write an ``extraction.json`` alongside the package.

    The extraction mirrors what a real OCR + field-extraction step will
    produce. For v0 we hand-roll it so the rule evaluator has something
    concrete to score.
    """

    extraction = {
        "package_id": package_id,
        "pages": [
            {
                "doc_id": bpcr_doc_id,
                "document_role": "BPCR",
                "page_index": 2,
                "tags": ["bpcr_step_page"],
                "fields": [
                    {
                        "field": "dispensed_weight_kg",
                        "value": bpcr_weight_kg,
                        "entity_name": "Lactose Monohydrate",
                        "source_doc_id": bpcr_doc_id,
                        "source_page_index": 2,
                    },
                    {
                        "field": "operator_signature",
                        "value": operator_signature,
                        "source_doc_id": bpcr_doc_id,
                        "source_page_index": 2,
                    },
                ],
            },
            {
                "doc_id": rm_doc_id,
                "document_role": "RawMaterialPage",
                "page_index": 1,
                "tags": ["raw_material_page"],
                "fields": [
                    {
                        "field": "weight_kg",
                        "value": rm_weight_kg,
                        "entity_name": "Lactose Monohydrate",
                        "source_doc_id": rm_doc_id,
                        "source_page_index": 1,
                    },
                ],
            },
        ],
    }
    target = package_store.base_path / package_id / "extraction.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(extraction, indent=2), encoding="utf-8")
    return target


def build_classified_package(
    ingest_service: PackageIngestService,
) -> tuple[str, str, str]:
    """Ingest a canonical 3-document package and return ids.

    Returns ``(package_id, bpcr_doc_id, rm_doc_id)``.
    """

    pkg = ingest_service.ingest(
        manifest_id="default",
        files=[
            IncomingFile(filename="batch42_bmr.pdf", content=b"%PDF-1.4 stub"),
            IncomingFile(filename="batch42_bpcr.pdf", content=b"%PDF-1.4 stub"),
            IncomingFile(filename="raw_material_lactose.pdf", content=b"%PDF-1.4 stub"),
        ],
    )
    bpcr = pkg.get_by_role("BPCR")
    rm = pkg.get_by_role("RawMaterialPage")
    assert bpcr, f"no BPCR in package {pkg.issues}"
    assert rm, f"no RawMaterialPage in package {pkg.issues}"
    return pkg.package_id, bpcr[0].doc_id, rm[0].doc_id
