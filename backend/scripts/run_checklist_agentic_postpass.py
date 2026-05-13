"""Run agentic post-pass for checklist rule 2 (CHE-BPC2) on local doc data.

Loads page extractions from ``results.json`` or ``result.json`` under a doc folder,
plus ``segmentation.json`` for per-page document/section types, then invokes
``run_agentic_postpass`` with the checklist agent registry filtered to rule 2 only.

Usage (from ``backend/``):

    uv run python scripts/run_checklist_agentic_postpass.py <doc_id>

    # or:
    PYTHONPATH=. python3 scripts/run_checklist_agentic_postpass.py <doc_id>

Set ``AT_STORAGE__BASE_PATH`` (and other AT_* vars) in ``backend/.env`` so the doc folder matches the API.

Environment / settings:
    Uses ``create_compliance_llm("evaluator")`` and ``get_settings().compliance``
    (same as the API compliance pipeline). Configure LLM via ``.env`` / YAML as usual.

    For a console-visible NDJSON line per agentic rule on stderr (without touching
    stdout JSON), use ``--verbose-debug`` or set ``COMPLIANCE_AGENTIC_SNAPSHOT_STDERR=1``.

Document folder is ``Path(AT_STORAGE__BASE_PATH) / <doc_id>`` (via ``get_settings().storage``),
same as the API compliance pipeline.

Inputs in that folder:

    - ``results.json`` or ``result.json`` — pipeline-style payload with ``extractions``,
      or ``raw_markdown`` page map when ``extractions`` is empty.
    - ``segmentation.json`` — cached ``DocumentSegmentation`` (optional but recommended;
      if missing, section_map is empty and applicability may degrade).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Repo layout: scripts/ lives under backend/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.compliance.agentic.postpass import run_agentic_postpass
from app.compliance.checklist import AGENT_NAME
from app.compliance.segmentation import build_page_to_section, load_segmentation
from app.compliance.rules.registry import RuleRegistry
from app.config.container import create_compliance_llm
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

CHECKLIST_RULE_2 = 2


class _RuleSubsetRegistry:
    """Delegate to RuleRegistry but restrict ``get_rules(agent)`` to selected numbers."""

    def __init__(self, inner: RuleRegistry, agent: str, rule_numbers: frozenset[int]) -> None:
        self._inner = inner
        self._agent = agent
        self._numbers = rule_numbers

    def get_rules(self, agent: str):
        rules = self._inner.get_rules(agent)
        if agent != self._agent:
            return rules
        return [r for r in rules if r.number in self._numbers]


def _pick_result_json(doc_dir: Path) -> Path:
    for name in ("results.json", "result.json"):
        p = doc_dir / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f"No results.json or result.json under {doc_dir}",
    )


def _load_extractions_from_result_payload(data: dict) -> list[dict]:
    """Match ``_run_compliance_pipeline`` extraction loading."""
    extractions: list[dict] = list(data.get("extractions") or [])
    if extractions:
        return extractions

    raw_md: dict = data.get("raw_markdown") or {}
    out: list[dict] = []
    for page_key, md in sorted(raw_md.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        page_num = int(page_key) if str(page_key).isdigit() else 0
        out.append({"page_num": page_num, "markdown": md})
    return out


def _serialize_postpass_results(results: list) -> list[dict]:
    """Convert postpass tuples to JSON-serializable dicts."""
    out: list[dict] = []
    for batch_id, page_num, batch_result in results:
        out.append({
            "batch_id": batch_id,
            "page_num": page_num,
            "evaluations": [e.model_dump(mode="json") for e in batch_result.evaluations],
        })
    return out


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def _async_main(doc_id: str, rule_number: int) -> list[dict]:
    _eprint(f"[run_checklist_agentic_postpass] doc_id={doc_id!r} rule_number={rule_number}")
    settings = get_settings()
    doc_dir = Path(settings.storage.base_path) / doc_id
    if not doc_dir.is_dir():
        raise FileNotFoundError(
            f"Document directory does not exist: {doc_dir} "
            f"(set AT_STORAGE__BASE_PATH / doc layout to match the API)",
        )
    result_path = _pick_result_json(doc_dir)
    data = json.loads(result_path.read_text(encoding="utf-8"))
    extractions = _load_extractions_from_result_payload(data)
    if not extractions:
        raise ValueError(
            f"No extractions in {result_path.name}: add extractions[] or raw_markdown {{}}.",
        )

    seg = load_segmentation(doc_dir)
    section_map = build_page_to_section(seg) if seg else {}
    if seg is None:
        logger.warning("No segmentation.json in %s — using empty section_map", doc_dir)

    _eprint(
        f"[run_checklist_agentic_postpass] loaded extractions={len(extractions)} "
        f"segmentation={'yes' if seg else 'no'} section_map_pages={len(section_map)}",
    )

    llm = create_compliance_llm("evaluator")
    full_registry = RuleRegistry()
    registry = _RuleSubsetRegistry(
        full_registry,
        AGENT_NAME,
        frozenset({rule_number}),
    )
    n_agentic = len([
        r for r in registry.get_rules(AGENT_NAME)
        if r.evaluation_strategy == "agentic_audit" and r.context_sources
    ])
    _eprint(
        f"[run_checklist_agentic_postpass] agentic_audit rules to run: {n_agentic} "
        f"(this can take several minutes: context summaries + graph LLM calls).",
    )
    if n_agentic == 0:
        _eprint(
            "[run_checklist_agentic_postpass] WARNING: no agentic_audit rules with "
            "context_sources for this agent/rule filter — empty result.",
        )

    results = await run_agentic_postpass(
        AGENT_NAME,
        registry,
        extractions,
        section_map,
        llm,
        settings.compliance,
        doc_id=doc_id,
        progress_callback=None,
    )
    _eprint(f"[run_checklist_agentic_postpass] finished ({len(results)} batch result(s)); printing JSON to stdout")
    return _serialize_postpass_results(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "doc_id",
        help="Document id/folder name under AT_STORAGE__BASE_PATH",
    )
    parser.add_argument(
        "--rule-number",
        type=int,
        default=CHECKLIST_RULE_2,
        help="Checklist rule number in checklist_rules.md / YAML (default: 2)",
    )
    parser.add_argument(
        "--verbose-debug",
        action="store_true",
        help=(
            "Enable DEBUG on agentic graph (prints full JSON snapshots to stderr) "
            "in addition to the default INFO one-line summary + NDJSON file."
        ),
    )
    args = parser.parse_args()
    if args.verbose_debug:
        os.environ.setdefault("COMPLIANCE_AGENTIC_SNAPSHOT_STDERR", "1")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose_debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        serialized = asyncio.run(_async_main(args.doc_id, args.rule_number))
    except FileNotFoundError as exc:
        parser.error(str(exc))
    print(json.dumps(serialized, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
