"""Integration tests for PackageIngestService (no HTTP layer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.bmr.ingest.models import PackageIssueKind, PackageStatus
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import IncomingFile, PackageIngestService

PILOT_MANIFESTS = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "bmr"
    / "pilot"
    / "manifests"
)


def _svc(tmp_path: Path) -> PackageIngestService:
    store = PackageStore(tmp_path / "packages")
    return PackageIngestService(store=store, manifests_dir=PILOT_MANIFESTS)


def test_manifest_not_found_rejects_package(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(manifest_id="does-not-exist", files=[])
    assert pkg.status == PackageStatus.REJECTED
    assert pkg.has_issue(PackageIssueKind.MANIFEST_NOT_FOUND)


def test_no_files_rejects_package(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(manifest_id="default", files=[])
    assert pkg.status == PackageStatus.REJECTED
    assert pkg.has_issue(PackageIssueKind.NO_FILES)


def test_non_pdf_file_flagged(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[IncomingFile(filename="note.txt", content=b"hello", content_type="text/plain")],
    )
    assert pkg.has_issue(PackageIssueKind.UNSUPPORTED_FILE_TYPE)
    assert pkg.status == PackageStatus.NEEDS_REVIEW


def test_classified_package_reaches_classified_status(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[
            IncomingFile(filename="batch42_bmr.pdf", content=b"%PDF-1.4 stub"),
            IncomingFile(filename="batch42_bpcr.pdf", content=b"%PDF-1.4 stub"),
            IncomingFile(filename="raw_material_rm_a.pdf", content=b"%PDF-1.4 stub"),
        ],
    )
    # All 3 must be classified via filename tier (pilot manifest has globs).
    roles = {d.role for d in pkg.documents}
    assert {"BMR", "BPCR", "RawMaterialPage"} <= roles
    assert pkg.status == PackageStatus.CLASSIFIED, [
        i.model_dump() for i in pkg.issues
    ]
    # Canonical BPCR flag is set when exactly one file is classified as BPCR.
    bpcr_docs = pkg.get_by_role("BPCR")
    assert len(bpcr_docs) == 1
    assert bpcr_docs[0].is_canonical is True


def test_missing_required_role_surfaced(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[
            IncomingFile(filename="bmr_batch.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="raw_material.pdf", content=b"%PDF-1.4"),
        ],  # no BPCR
    )
    assert pkg.has_issue(PackageIssueKind.MISSING_REQUIRED_ROLE)
    assert pkg.status == PackageStatus.NEEDS_REVIEW
    missing = [i for i in pkg.issues if i.kind == PackageIssueKind.MISSING_REQUIRED_ROLE]
    assert any(m.role_id == "BPCR" for m in missing)


def test_duplicate_canonical_flagged(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[
            IncomingFile(filename="batch42_bpcr_a.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="batch42_bpcr_b.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="batch42_bmr.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="raw_material_rm.pdf", content=b"%PDF-1.4"),
        ],
    )
    assert pkg.has_issue(PackageIssueKind.DUPLICATE_CANONICAL)
    assert pkg.status == PackageStatus.NEEDS_REVIEW


def test_package_roundtrips_through_store(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[
            IncomingFile(filename="batch42_bmr.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="batch42_bpcr.pdf", content=b"%PDF-1.4"),
            IncomingFile(filename="raw_material.pdf", content=b"%PDF-1.4"),
        ],
    )
    store = PackageStore(tmp_path / "packages")
    reloaded = store.load(pkg.package_id)
    assert reloaded is not None
    assert reloaded.package_id == pkg.package_id
    assert {d.role for d in reloaded.documents} == {d.role for d in pkg.documents}


def test_empty_file_flagged(tmp_path: Path):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[IncomingFile(filename="empty.pdf", content=b"", content_type="application/pdf")],
    )
    assert pkg.has_issue(PackageIssueKind.EMPTY_FILE)


@pytest.mark.parametrize(
    "filename,expected_role",
    [
        ("ProjectX_BPCR_batch_9912.pdf", "BPCR"),
        ("PX_BMR_batch_9912.pdf", "BMR"),
        ("PX_RM_batch_9912.pdf", "RawMaterialPage"),
    ],
)
def test_filename_classification_matrix(tmp_path: Path, filename: str, expected_role: str):
    service = _svc(tmp_path)
    pkg = service.ingest(
        manifest_id="default",
        files=[IncomingFile(filename=filename, content=b"%PDF-1.4 stub")],
    )
    roles = [d.role for d in pkg.documents]
    assert expected_role in roles, f"expected {expected_role}, got {roles}"
