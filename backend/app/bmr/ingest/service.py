"""Orchestration for BMR package ingestion.

Wires together :class:`PackageStore`, :class:`HybridClassifier`, and manifest
validation into a single ``ingest(...)`` call that the API layer depends on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.bmr.ingest.classifier import (
    HybridClassifier,
    HybridClassifierConfig,
)
from app.bmr.ingest.manifest import Manifest, ManifestValidationError, load_manifest
from app.bmr.ingest.models import (
    DocumentPackage,
    DocumentRef,
    PackageIssue,
    PackageIssueKind,
    PackageStatus,
    now_utc,
)
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.pdf_header import extract_first_page_header


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    content: bytes
    content_type: str | None = None


class PackageIngestService:
    """Materialise an uploaded bundle into a stored :class:`DocumentPackage`."""

    def __init__(
        self,
        *,
        store: PackageStore,
        manifests_dir: Path,
        classifier_config: HybridClassifierConfig | None = None,
    ) -> None:
        self._store = store
        self._manifests_dir = Path(manifests_dir).resolve()
        self._classifier_config = classifier_config or HybridClassifierConfig()

    # ── public ───────────────────────────────────────────────────────────

    def ingest(
        self,
        *,
        manifest_id: str,
        files: list[IncomingFile],
    ) -> DocumentPackage:
        """Ingest ``files`` against the given manifest and return the package.

        Always returns a package; validation failures are attached as
        :class:`PackageIssue` records and surfaced via ``status``.
        """

        try:
            manifest = self._load_manifest(manifest_id)
        except ManifestValidationError as exc:
            return self._make_package_with_manifest_error(manifest_id, str(exc))

        package_id, _ = self._store.new_package_dir()
        created = now_utc()

        issues: list[PackageIssue] = []
        if not files:
            issues.append(
                PackageIssue(
                    kind=PackageIssueKind.NO_FILES,
                    message="package contains no files",
                )
            )

        classifier = HybridClassifier(
            manifest,
            config=self._classifier_config,
            header_text_extractor=extract_first_page_header,
        )

        documents: list[DocumentRef] = []
        for incoming in files:
            ref, file_issues = self._ingest_single(
                package_id=package_id,
                manifest=manifest,
                classifier=classifier,
                incoming=incoming,
            )
            if ref is not None:
                documents.append(ref)
            issues.extend(file_issues)

        self._apply_canonical_role(manifest, documents, issues)
        self._check_cardinality(manifest, documents, issues)

        status = _infer_status(issues)

        package = DocumentPackage(
            package_id=package_id,
            manifest_id=manifest.id,
            manifest_version=manifest.manifest_version,
            status=status,
            documents=documents,
            issues=issues,
            created_at=created,
            updated_at=created,
        )
        self._store.save(package)
        return package

    # ── internals ────────────────────────────────────────────────────────

    def _load_manifest(self, manifest_id: str) -> Manifest:
        path = self._manifests_dir / f"{manifest_id}.yaml"
        return load_manifest(path)

    def _make_package_with_manifest_error(
        self, manifest_id: str, detail: str
    ) -> DocumentPackage:
        package_id, _ = self._store.new_package_dir()
        now = now_utc()
        package = DocumentPackage(
            package_id=package_id,
            manifest_id=manifest_id,
            manifest_version="0.0",
            status=PackageStatus.REJECTED,
            documents=[],
            issues=[
                PackageIssue(
                    kind=PackageIssueKind.MANIFEST_NOT_FOUND,
                    message=detail,
                )
            ],
            created_at=now,
            updated_at=now,
        )
        self._store.save(package)
        return package

    def _ingest_single(
        self,
        *,
        package_id: str,
        manifest: Manifest,
        classifier: HybridClassifier,
        incoming: IncomingFile,
    ) -> tuple[DocumentRef | None, list[PackageIssue]]:
        issues: list[PackageIssue] = []

        if not incoming.content:
            issues.append(
                PackageIssue(
                    kind=PackageIssueKind.EMPTY_FILE,
                    message=f"file {incoming.filename!r} is empty",
                    filename=incoming.filename,
                )
            )
            return None, issues

        if not _looks_like_pdf(incoming.filename, incoming.content_type):
            issues.append(
                PackageIssue(
                    kind=PackageIssueKind.UNSUPPORTED_FILE_TYPE,
                    message=(
                        f"file {incoming.filename!r} is not a PDF; v0 ingestion "
                        "only accepts application/pdf"
                    ),
                    filename=incoming.filename,
                    details={"content_type": incoming.content_type or ""},
                )
            )
            return None, issues

        doc_id, stored_path = self._store.store_file(
            package_id, incoming.filename, incoming.content
        )
        sha = hashlib.sha256(incoming.content).hexdigest()

        outcome = classifier.classify_file(
            filename=incoming.filename, content=incoming.content
        )

        ref = DocumentRef(
            doc_id=doc_id,
            filename=incoming.filename,
            stored_path=str(stored_path),
            size_bytes=len(incoming.content),
            sha256=sha,
            role=outcome.role,
            confidence=outcome.confidence,
            decision_source=outcome.decision_source,
            tier_scores=dict(outcome.tier_scores),
            is_canonical=False,
            classifier_notes=list(outcome.notes),
        )

        if outcome.role is None:
            issues.append(
                PackageIssue(
                    kind=PackageIssueKind.UNCLASSIFIED_FILE,
                    message=(
                        f"file {incoming.filename!r} could not be auto-classified; "
                        "reviewer override required"
                    ),
                    filename=incoming.filename,
                    details={
                        "confidence": round(outcome.confidence, 3),
                        "notes": " | ".join(outcome.notes) if outcome.notes else "",
                    },
                )
            )

        return ref, issues

    def _apply_canonical_role(
        self,
        manifest: Manifest,
        documents: list[DocumentRef],
        issues: list[PackageIssue],
    ) -> None:
        canonical_role = manifest.canonical_role_id
        if canonical_role is None:
            return

        candidates = [d for d in documents if d.role == canonical_role]
        if len(candidates) == 1:
            candidates[0].is_canonical = True
            return
        if len(candidates) > 1:
            issues.append(
                PackageIssue(
                    kind=PackageIssueKind.DUPLICATE_CANONICAL,
                    message=(
                        f"manifest declares role {canonical_role!r} as canonical, "
                        f"but {len(candidates)} files were classified as it; "
                        "reviewer must pick one."
                    ),
                    role_id=canonical_role,
                    details={"candidate_count": len(candidates)},
                )
            )

    def _check_cardinality(
        self,
        manifest: Manifest,
        documents: list[DocumentRef],
        issues: list[PackageIssue],
    ) -> None:
        by_role: dict[str, int] = {}
        for d in documents:
            if d.role:
                by_role[d.role] = by_role.get(d.role, 0) + 1

        for role in manifest.required_roles:
            count = by_role.get(role.id, 0)
            if role.cardinality == "exactly_one" and count != 1:
                issues.append(
                    PackageIssue(
                        kind=PackageIssueKind.MISSING_REQUIRED_ROLE
                        if count == 0
                        else PackageIssueKind.DUPLICATE_CANONICAL,
                        message=(
                            f"role {role.id!r} requires exactly one document; "
                            f"got {count}."
                        ),
                        role_id=role.id,
                        details={"expected": 1, "actual": count},
                    )
                )
            elif role.cardinality == "at_least_one" and count < 1:
                issues.append(
                    PackageIssue(
                        kind=PackageIssueKind.MISSING_REQUIRED_ROLE,
                        message=f"role {role.id!r} requires at least one document; got {count}.",
                        role_id=role.id,
                        details={"expected": 1, "actual": count},
                    )
                )


# ── helpers ──────────────────────────────────────────────────────────────────


def _looks_like_pdf(filename: str, content_type: str | None) -> bool:
    if content_type and content_type.lower().startswith("application/pdf"):
        return True
    return filename.lower().endswith(".pdf")


_REJECT_KINDS = frozenset({
    PackageIssueKind.MANIFEST_NOT_FOUND,
    PackageIssueKind.NO_FILES,
})


def _infer_status(issues: list[PackageIssue]) -> PackageStatus:
    for issue in issues:
        if issue.kind in _REJECT_KINDS:
            return PackageStatus.REJECTED
    if issues:
        return PackageStatus.NEEDS_REVIEW
    return PackageStatus.CLASSIFIED


__all__ = ["IncomingFile", "PackageIngestService"]
