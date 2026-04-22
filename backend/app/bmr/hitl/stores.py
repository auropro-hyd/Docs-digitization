"""Filesystem-backed stores for HITL-related aggregates.

Three stores mirror the :class:`RunStore` pattern:

- :class:`ResolutionStore` — one JSON per resolution under
  ``<base>/resolutions/<run_id>/<resolution_id>.json``.
- :class:`FeedbackStore` — one JSON per sample under
  ``<base>/feedback/<run_id>/<sample_id>.json``.
- :class:`RevisionStore` — one subdirectory per revision with
  ``revision.json``, ``report.pdf``, and ``bundle.json``.

All writes are best-effort atomic. Swapping to Postgres later touches
only these files.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.bmr.hitl.models import (
    AuditReportRevision,
    CorrectionWorkflow,
    FeedbackSample,
    StructuredResolution,
)

logger = logging.getLogger(__name__)

# Run / resolution / revision / sample identifiers must be filesystem-safe
# tokens. Anything not matching this charset is rejected before it is
# interpolated into a Path, preventing traversal (../) and absolute-path
# injection.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


class _UnsafeId(ValueError):
    """Raised when an identifier fails the safe-token check."""


def _assert_safe(*tokens: str) -> None:
    for tok in tokens:
        if not isinstance(tok, str) or not _SAFE_ID_RE.fullmatch(tok):
            raise _UnsafeId(f"invalid identifier token: {tok!r}")


def _assert_contained(base: Path, target: Path) -> None:
    resolved = target.resolve()
    if not resolved.is_relative_to(base):
        raise _UnsafeId(f"resolved path escapes store base: {resolved!s}")


def _atomic_write(target: Path, data: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.rename(target)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(target)


class ResolutionStore:
    def __init__(self, base_path: Path) -> None:
        self._base = (Path(base_path) / "resolutions").resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def _path(self, run_id: str, resolution_id: str) -> Path:
        _assert_safe(run_id, resolution_id)
        target = self._base / run_id / f"{resolution_id}.json"
        _assert_contained(self._base, target)
        return target

    def save(self, resolution: StructuredResolution) -> None:
        _atomic_write(
            self._path(resolution.run_id, resolution.resolution_id),
            resolution.model_dump_json(indent=2),
        )

    def load(
        self, run_id: str, resolution_id: str
    ) -> StructuredResolution | None:
        try:
            target = self._path(run_id, resolution_id)
        except _UnsafeId:
            return None
        if not target.exists():
            return None
        try:
            return StructuredResolution.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error("could not load store record %s: %s", target, exc)
            return None

    def list_for_run(self, run_id: str) -> list[StructuredResolution]:
        try:
            _assert_safe(run_id)
        except _UnsafeId:
            return []
        run_dir = self._base / run_id
        if not run_dir.is_dir():
            return []
        out: list[StructuredResolution] = []
        for path in sorted(run_dir.glob("*.json")):
            try:
                out.append(
                    StructuredResolution.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.error("skipping corrupt store record %s: %s", path, exc)
                continue
        return out

    def list_active_by_finding(self, run_id: str) -> dict[str, StructuredResolution]:
        """Return the most recent non-superseded resolution per finding.

        A resolution is *active* when nothing supersedes it.
        """

        rows = self.list_for_run(run_id)
        superseded_ids = {r.supersedes_id for r in rows if r.supersedes_id}
        active: dict[str, StructuredResolution] = {}
        for row in rows:
            if row.resolution_id in superseded_ids:
                continue
            existing = active.get(row.finding_id)
            if existing is None or row.created_at > existing.created_at:
                active[row.finding_id] = row
        return active


class FeedbackStore:
    def __init__(self, base_path: Path) -> None:
        self._base = (Path(base_path) / "feedback").resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def save(self, sample: FeedbackSample) -> None:
        _assert_safe(sample.run_id, sample.sample_id)
        target = self._base / sample.run_id / f"{sample.sample_id}.json"
        _assert_contained(self._base, target)
        _atomic_write(target, sample.model_dump_json(indent=2))

    def list_for_run(self, run_id: str) -> list[FeedbackSample]:
        try:
            _assert_safe(run_id)
        except _UnsafeId:
            return []
        run_dir = self._base / run_id
        if not run_dir.is_dir():
            return []
        out: list[FeedbackSample] = []
        for path in sorted(run_dir.glob("*.json")):
            try:
                out.append(
                    FeedbackSample.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.error("skipping corrupt store record %s: %s", path, exc)
                continue
        return out

    def list_all(self) -> list[FeedbackSample]:
        if not self._base.is_dir():
            return []
        out: list[FeedbackSample] = []
        for run_dir in sorted(self._base.iterdir()):
            if not run_dir.is_dir():
                continue
            for path in sorted(run_dir.glob("*.json")):
                try:
                    out.append(
                        FeedbackSample.model_validate_json(
                            path.read_text(encoding="utf-8")
                        )
                    )
                except (OSError, json.JSONDecodeError, ValueError):
                    continue
        return out


class RevisionStore:
    def __init__(self, base_path: Path) -> None:
        self._base = (Path(base_path) / "revisions").resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def next_revision_number(self, run_id: str) -> int:
        try:
            _assert_safe(run_id)
        except _UnsafeId:
            return 1
        run_dir = self._base / run_id
        if not run_dir.is_dir():
            return 1
        existing = self.list_for_run(run_id)
        if not existing:
            return 1
        return max(r.revision_number for r in existing) + 1

    def save(
        self,
        revision: AuditReportRevision,
        *,
        pdf_bytes: bytes,
        bundle_bytes: bytes,
    ) -> None:
        _assert_safe(revision.run_id, revision.revision_id)
        rev_dir = self._base / revision.run_id / revision.revision_id
        _assert_contained(self._base, rev_dir)
        rev_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(rev_dir / "report.pdf", pdf_bytes)
        _atomic_write_bytes(rev_dir / "bundle.json", bundle_bytes)
        _atomic_write(
            rev_dir / "revision.json", revision.model_dump_json(indent=2)
        )

    def load(self, revision_id: str) -> AuditReportRevision | None:
        try:
            _assert_safe(revision_id)
        except _UnsafeId:
            return None
        for run_dir in self._base.iterdir():
            if not run_dir.is_dir():
                continue
            target = run_dir / revision_id / "revision.json"
            if target.exists():
                try:
                    return AuditReportRevision.model_validate_json(
                        target.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError, ValueError):
                    return None
        return None

    def list_for_run(self, run_id: str) -> list[AuditReportRevision]:
        try:
            _assert_safe(run_id)
        except _UnsafeId:
            return []
        run_dir = self._base / run_id
        if not run_dir.is_dir():
            return []
        out: list[AuditReportRevision] = []
        for rev_dir in sorted(run_dir.iterdir()):
            if not rev_dir.is_dir():
                continue
            target = rev_dir / "revision.json"
            if not target.exists():
                continue
            try:
                out.append(
                    AuditReportRevision.model_validate_json(
                        target.read_text(encoding="utf-8")
                    )
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.error("skipping corrupt store record %s: %s", path, exc)
                continue
        return out

    def read_pdf(self, revision_id: str) -> bytes | None:
        try:
            _assert_safe(revision_id)
        except _UnsafeId:
            return None
        for run_dir in self._base.iterdir():
            if not run_dir.is_dir():
                continue
            target = run_dir / revision_id / "report.pdf"
            try:
                _assert_contained(self._base, target)
            except _UnsafeId:
                continue
            if target.exists():
                return target.read_bytes()
        return None

    def read_bundle(self, revision_id: str) -> bytes | None:
        try:
            _assert_safe(revision_id)
        except _UnsafeId:
            return None
        for run_dir in self._base.iterdir():
            if not run_dir.is_dir():
                continue
            target = run_dir / revision_id / "bundle.json"
            try:
                _assert_contained(self._base, target)
            except _UnsafeId:
                continue
            if target.exists():
                return target.read_bytes()
        return None


class CorrectionStore:
    """Filesystem-backed store for :class:`CorrectionWorkflow` records."""

    def __init__(self, base_path: Path) -> None:
        self._base = (Path(base_path) / "corrections").resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def _path(self, run_id: str, workflow_id: str) -> Path:
        _assert_safe(run_id, workflow_id)
        target = self._base / run_id / f"{workflow_id}.json"
        _assert_contained(self._base, target)
        return target

    def save(self, workflow: CorrectionWorkflow) -> None:
        _atomic_write(
            self._path(workflow.run_id, workflow.workflow_id),
            workflow.model_dump_json(indent=2),
        )

    def load(self, run_id: str, workflow_id: str) -> CorrectionWorkflow | None:
        try:
            target = self._path(run_id, workflow_id)
        except _UnsafeId:
            return None
        if not target.exists():
            return None
        try:
            return CorrectionWorkflow.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.error("could not load store record %s: %s", target, exc)
            return None

    def list_for_run(self, run_id: str) -> list[CorrectionWorkflow]:
        try:
            _assert_safe(run_id)
        except _UnsafeId:
            return []
        run_dir = self._base / run_id
        if not run_dir.is_dir():
            return []
        out: list[CorrectionWorkflow] = []
        for path in sorted(run_dir.glob("*.json")):
            try:
                out.append(
                    CorrectionWorkflow.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                logger.error("skipping corrupt store record %s: %s", path, exc)
                continue
        return out


__all__ = [
    "CorrectionStore",
    "FeedbackStore",
    "ResolutionStore",
    "RevisionStore",
]
