"""BMR package ingestion: manifest loading, multi-file upload, classification.

Public surface exposed through this module is what other BMR subpackages
(rule engine, workflow) depend on. External code should import from here,
not from the private modules.
"""

from app.bmr.ingest.classifier import (
    ClassificationDecision,
    ClassifierOutcome,
    HybridClassifier,
    HybridClassifierConfig,
)
from app.bmr.ingest.manifest import (
    Manifest,
    ManifestRoleSpec,
    ManifestValidationError,
    load_manifest,
)
from app.bmr.ingest.models import (
    DocumentPackage,
    DocumentRef,
    PackageIssue,
    PackageStatus,
    UploadedFile,
)
from app.bmr.ingest.package_store import PackageStore
from app.bmr.ingest.service import PackageIngestService

__all__ = [
    "ClassificationDecision",
    "ClassifierOutcome",
    "DocumentPackage",
    "DocumentRef",
    "HybridClassifier",
    "HybridClassifierConfig",
    "Manifest",
    "ManifestRoleSpec",
    "ManifestValidationError",
    "PackageIngestService",
    "PackageIssue",
    "PackageStatus",
    "PackageStore",
    "UploadedFile",
    "load_manifest",
]
