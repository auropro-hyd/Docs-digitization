"""Pydantic models for BMR package ingestion.

Mirrors the data-model defined in
``specs/002-document-package-classification/data-model.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PackageStatus(StrEnum):
    """Lifecycle state of a :class:`DocumentPackage`."""

    RECEIVED = "received"
    CLASSIFYING = "classifying"
    CLASSIFIED = "classified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ClassificationDecisionSource(StrEnum):
    """Which classifier tier produced the final role decision for a file."""

    FILENAME = "filename"
    HEADER = "header"
    VLM = "vlm"
    USER_OVERRIDE = "user_override"
    UNKNOWN = "unknown"


class PackageIssueKind(StrEnum):
    """Discrete reasons a package may have landed in NEEDS_REVIEW or REJECTED."""

    MISSING_REQUIRED_ROLE = "missing_required_role"
    DUPLICATE_CANONICAL = "duplicate_canonical"
    UNCLASSIFIED_FILE = "unclassified_file"
    MANIFEST_NOT_FOUND = "manifest_not_found"
    NO_FILES = "no_files"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    EMPTY_FILE = "empty_file"


class UploadedFile(BaseModel):
    """Input descriptor for a single file accepted by the package endpoint."""

    filename: str = Field(min_length=1, max_length=512)
    content_type: str | None = None
    size_bytes: int = Field(ge=0)

    model_config = ConfigDict(frozen=True)


class DocumentRef(BaseModel):
    """A file stored inside a :class:`DocumentPackage`.

    ``role`` is the final role assignment; ``decision_source`` records which
    classifier tier won.
    """

    doc_id: str
    filename: str
    stored_path: str
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    role: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    decision_source: ClassificationDecisionSource = ClassificationDecisionSource.UNKNOWN
    tier_scores: dict[str, float] = Field(default_factory=dict)
    is_canonical: bool = False
    classifier_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(frozen=False)


class PackageIssue(BaseModel):
    """A single discrete issue preventing a package from reaching CLASSIFIED."""

    kind: PackageIssueKind
    message: str
    role_id: str | None = None
    filename: str | None = None
    details: dict[str, str | int | float | bool] = Field(default_factory=dict)


class DocumentPackage(BaseModel):
    """Top-level aggregate: a multi-file BMR upload under a manifest."""

    package_id: str
    manifest_id: str
    manifest_version: str
    status: PackageStatus
    documents: list[DocumentRef] = Field(default_factory=list)
    issues: list[PackageIssue] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(frozen=False)

    def get_by_role(self, role: str) -> list[DocumentRef]:
        return [d for d in self.documents if d.role == role]

    def has_issue(self, kind: PackageIssueKind) -> bool:
        return any(i.kind == kind for i in self.issues)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "ClassificationDecisionSource",
    "DocumentPackage",
    "DocumentRef",
    "PackageIssue",
    "PackageIssueKind",
    "PackageStatus",
    "UploadedFile",
    "now_utc",
]
