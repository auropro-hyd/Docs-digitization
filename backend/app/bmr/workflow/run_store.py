"""Filesystem-backed store for :class:`RunReport` records.

Layout::

    <base>/<run_id>/run.json

Mirrors :class:`app.bmr.ingest.package_store.PackageStore` in style;
trivial to swap for Postgres later. Writes are best-effort atomic via
``.tmp`` → rename.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from app.bmr.workflow.models import RunReport

logger = logging.getLogger(__name__)


class RunStore:
    """FS-based store for BMR run reports."""

    def __init__(self, base_path: Path) -> None:
        self._base = Path(base_path).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    @property
    def base_path(self) -> Path:
        return self._base

    def new_run_id(self) -> str:
        run_id = uuid.uuid4().hex
        (self._base / run_id).mkdir(parents=True, exist_ok=True)
        return run_id

    def save(self, report: RunReport) -> None:
        run_dir = self._base / report.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / "run.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(target)

    def load(self, run_id: str) -> RunReport | None:
        target = self._base / run_id / "run.json"
        if not target.exists():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("could not load run report %s: %s", target, exc)
            return None
        try:
            return RunReport.model_validate(payload)
        except Exception as exc:
            logger.error("run report %s failed model validation: %s", target, exc)
            return None

    def list_ids(self) -> list[str]:
        if not self._base.is_dir():
            return []
        return sorted(p.name for p in self._base.iterdir() if p.is_dir())


__all__ = ["RunStore"]
