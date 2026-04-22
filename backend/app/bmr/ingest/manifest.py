"""Manifest loader.

Manifests live under ``backend/config/bmr/<profile>/manifests/<id>.yaml``.
They declare the logical document roles expected inside a package, which
role is canonical (Spec 002 FR-010), and the hybrid classifier policy.

v0 intentionally keeps the manifest schema simple — a pydantic model is
sufficient and avoids a second JSON Schema vocabulary alongside the rule
schema in Spec 005.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ManifestValidationError(ValueError):
    """Raised when a manifest file fails to load or validate."""


Cardinality = Literal["exactly_one", "at_least_one", "zero_or_more", "zero_or_one"]


class ManifestRoleSpec(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    cardinality: Cardinality
    canonical: bool = False

    model_config = ConfigDict(frozen=True)


class ClassifierPolicy(BaseModel):
    strategy: Literal["hybrid", "filename_only", "header_only"] = "hybrid"
    tiers: list[Literal["filename", "header", "vlm"]] = Field(
        default_factory=lambda: ["filename", "header"]
    )
    filename_patterns: dict[str, list[str]] = Field(default_factory=dict)
    header_keywords: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(frozen=True)


class Manifest(BaseModel):
    manifest_version: str = Field(pattern=r"^\d+\.\d+$")
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    required_roles: list[ManifestRoleSpec] = Field(default_factory=list)
    optional_roles: list[ManifestRoleSpec] = Field(default_factory=list)
    classifier: ClassifierPolicy = Field(default_factory=ClassifierPolicy)

    model_config = ConfigDict(frozen=True)

    @property
    def canonical_role_id(self) -> str | None:
        for role in self.required_roles + self.optional_roles:
            if role.canonical:
                return role.id
        return None

    @property
    def all_roles(self) -> list[ManifestRoleSpec]:
        return list(self.required_roles) + list(self.optional_roles)

    def role_ids(self) -> set[str]:
        return {r.id for r in self.all_roles}

    @model_validator(mode="after")
    def _validate(self) -> Manifest:
        seen: set[str] = set()
        canonical_count = 0
        for role in self.all_roles:
            if role.id in seen:
                raise ValueError(f"duplicate role id {role.id!r} in manifest {self.id!r}")
            seen.add(role.id)
            if role.canonical:
                canonical_count += 1
        if canonical_count > 1:
            raise ValueError(
                f"manifest {self.id!r} declares {canonical_count} canonical roles; "
                "exactly zero or one is allowed"
            )
        role_ids = seen
        for key, patterns in self.classifier.filename_patterns.items():
            if key not in role_ids:
                raise ValueError(
                    f"classifier.filename_patterns references unknown role {key!r} "
                    f"in manifest {self.id!r}"
                )
            if not patterns:
                raise ValueError(
                    f"classifier.filename_patterns[{key!r}] must not be empty"
                )
        for key, keywords in self.classifier.header_keywords.items():
            if key not in role_ids:
                raise ValueError(
                    f"classifier.header_keywords references unknown role {key!r} "
                    f"in manifest {self.id!r}"
                )
            if not keywords:
                raise ValueError(
                    f"classifier.header_keywords[{key!r}] must not be empty"
                )
        return self


# Defence against YAML anchor-expansion bombs. ``safe_load`` already
# rejects !!python tags, but billion-laughs-style alias graphs are still
# accepted and blow up during expansion. We reject oversized manifests
# before parsing so that graph cannot run.
_MAX_MANIFEST_BYTES = 1 * 1024 * 1024  # 1 MiB


def load_manifest(path: Path) -> Manifest:
    """Load and validate a manifest YAML file."""

    if not path.exists():
        raise ManifestValidationError(f"manifest file not found: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ManifestValidationError(f"cannot stat manifest {path}: {exc}") from exc
    if size > _MAX_MANIFEST_BYTES:
        raise ManifestValidationError(
            f"manifest {path} is {size} bytes; max allowed is {_MAX_MANIFEST_BYTES}"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestValidationError(f"cannot read manifest {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestValidationError(f"invalid YAML in manifest {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestValidationError(
            f"manifest {path} must be a mapping at the top level"
        )

    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestValidationError(
            f"manifest {path} failed schema validation: {exc}"
        ) from exc


__all__ = [
    "ClassifierPolicy",
    "Manifest",
    "ManifestRoleSpec",
    "ManifestValidationError",
    "load_manifest",
]
