"""Compliance rule validation CLI.

Evaluates a single rule against one or more document pages and reports whether
each page PASSES, FAILS, or is NOT_APPLICABLE. Useful during rule authoring to
verify that a newly authored rule behaves correctly before committing.

Usage::

    python -m app.compliance.rules.validate_cli \\
        --agent alcoa \\
        --rule 27 \\
        --doc <doc_id> \\
        --pages 3,7 \\
        --expect pass

    # Multiple page ranges
    python -m app.compliance.rules.validate_cli \\
        --agent gmp \\
        --rule 5 \\
        --doc <doc_id> \\
        --pages 1-3,10 \\
        --expect fail
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


# ── colour helpers ────────────────────────────────────────────────────────────

class _Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    GREY = "\033[90m"


def _c(text: str, *codes: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return "".join(codes) + text + _Style.RESET


def _use_color(color_arg: str) -> bool:
    if color_arg == "always":
        return True
    if color_arg == "never":
        return False
    return sys.stdout.isatty()


# ── page range parser ─────────────────────────────────────────────────────────

def _parse_pages(spec: str) -> list[int]:
    """Parse '3,7' or '1-3,10' into [1,2,3,10]."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


# ── status symbol ─────────────────────────────────────────────────────────────

_STATUS_SYMBOL: dict[str, str] = {
    "compliant": "PASS",
    "non_compliant": "FAIL",
    "not_applicable": "N/A ",
    "uncertain": "UNCR",
    "error": "ERR ",
}

_EXPECT_ALIASES: dict[str, str] = {
    "pass": "compliant",
    "compliant": "compliant",
    "fail": "non_compliant",
    "non_compliant": "non_compliant",
    "not_applicable": "not_applicable",
    "na": "not_applicable",
}


# ── core async evaluation ─────────────────────────────────────────────────────

async def _evaluate_page(
    *,
    agent: str,
    rule_number: int,
    doc_id: str,
    page_num: int,
    vlm_enabled: bool,
    storage_base: Path,
) -> dict:
    """Evaluate one rule against one page. Returns a result dict."""
    from app.compliance.context_builder import build_enriched_context
    from app.compliance.evaluator import RuleBatchEvaluator, _merge_text_vision
    from app.compliance.rules.registry import RuleBatch, get_registry
    from app.config.container import create_compliance_llm, create_vlm_provider
    from app.config.settings import get_settings

    settings = get_settings()

    # -- find the rule --
    registry = get_registry()
    all_rules = registry.get_rules(agent)
    matched = [r for r in all_rules if r.number == rule_number]
    if not matched:
        return {
            "page": page_num,
            "status": "error",
            "error": f"Rule {rule_number} not found for agent '{agent}'",
        }
    rule = matched[0]

    # -- load extraction --
    result_path = storage_base / doc_id / "result.json"
    if not result_path.exists():
        return {
            "page": page_num,
            "status": "error",
            "error": f"result.json not found: {result_path}",
        }
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    azure_di = raw.get("azure_di_results", {})
    extraction = azure_di.get(str(page_num))
    if extraction is None:
        return {
            "page": page_num,
            "status": "error",
            "error": f"Page {page_num} not found in azure_di_results",
        }

    # -- build context --
    enriched = build_enriched_context(extraction, page_num)

    # -- text evaluation --
    llm = create_compliance_llm("evaluator", settings)
    batch = RuleBatch(
        batch_id=f"{agent}-rule{rule_number}-p{page_num}",
        category=rule.category,
        agent=agent,
        rules=[rule],
    )
    evaluator = RuleBatchEvaluator()
    _, _, text_result = await evaluator.evaluate_batch(batch, enriched, page_num, llm)
    text_ev = next((e for e in text_result.evaluations if e.rule_id == rule.id), None)

    # -- vision evaluation (if applicable) --
    vision_ev = None
    strategy = rule.evaluation_strategy
    needs_vision = strategy in ("vision", "text_and_vision")

    if needs_vision and vlm_enabled and settings.vlm.enabled:
        try:
            from app.compliance.page_image_loader import load_page_image
            from app.compliance.vision_evaluator import VisionBatchEvaluator

            page_image = await load_page_image(doc_id, page_num)
            if page_image:
                vlm = create_vlm_provider(settings)
                vision_batch = RuleBatch(
                    batch_id=f"{agent}-rule{rule_number}-p{page_num}-vision",
                    category=rule.category,
                    agent=agent,
                    rules=[rule],
                )
                vision_evaluator = VisionBatchEvaluator()
                _, _, vision_result = await vision_evaluator.evaluate_batch(
                    vision_batch, page_image, page_num, vlm,
                )
                vision_ev = next(
                    (e for e in vision_result.evaluations if e.rule_id == rule.id), None
                )
        except Exception as exc:
            pass  # vision failure is non-fatal; fall back to text result

    # -- merge --
    if strategy == "text_and_vision" and vision_ev is not None:
        final_ev = _merge_text_vision(rule, text_ev, vision_ev)
    elif strategy == "vision" and vision_ev is not None:
        final_ev = vision_ev
    else:
        final_ev = text_ev

    if final_ev is None:
        return {"page": page_num, "status": "error", "error": "Evaluator returned no result"}

    return {
        "page": page_num,
        "rule_id": final_ev.rule_id,
        "status": final_ev.status,
        "confidence": final_ev.confidence,
        "reasoning": final_ev.reasoning,
        "evidence": final_ev.evidence,
        "description": final_ev.description,
        "recommendation": final_ev.recommendation,
    }


# ── output formatting ─────────────────────────────────────────────────────────

def _print_result(result: dict, expect: str | None, *, color: bool) -> bool:
    """Print one page result. Returns True if expectation is met (or no expectation)."""
    page = result["page"]
    status = result.get("status", "error")
    symbol = _STATUS_SYMBOL.get(status, status.upper()[:4])

    if status == "error":
        label = _c(f"[{symbol}]", _Style.BOLD, _Style.RED, enabled=color)
        print(f"  Page {page:>4}  {label}  {result.get('error', 'unknown error')}")
        return expect is None

    # colour the symbol
    if status == "compliant":
        sym_str = _c(f"[{symbol}]", _Style.BOLD, _Style.GREEN, enabled=color)
    elif status == "non_compliant":
        sym_str = _c(f"[{symbol}]", _Style.BOLD, _Style.RED, enabled=color)
    elif status == "not_applicable":
        sym_str = _c(f"[{symbol}]", _Style.GREY, enabled=color)
    else:
        sym_str = _c(f"[{symbol}]", _Style.YELLOW, enabled=color)

    conf = result.get("confidence", 0.0)
    conf_str = _c(f"conf={conf:.2f}", _Style.GREY, enabled=color)
    print(f"  Page {page:>4}  {sym_str}  {conf_str}")

    reasoning = result.get("reasoning", "")
    if reasoning:
        print(f"           {_c('Reasoning:', _Style.CYAN, enabled=color)} {reasoning}")

    evidence = result.get("evidence", "")
    if evidence:
        print(f"           {_c('Evidence: ', _Style.CYAN, enabled=color)} {evidence}")

    description = result.get("description", "")
    if description:
        print(f"           {_c('Finding:  ', _Style.CYAN, enabled=color)} {description}")

    rec = result.get("recommendation", "")
    if rec:
        print(f"           {_c('Suggest:  ', _Style.CYAN, enabled=color)} {rec}")

    print()

    if expect is None:
        return True
    return status == expect


# ── main ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.compliance.rules.validate_cli",
        description="Evaluate a compliance rule against document pages.",
    )
    parser.add_argument("--agent", required=True, help="Agent ID (e.g. alcoa, gmp)")
    parser.add_argument("--rule", required=True, type=int, help="Rule number")
    parser.add_argument("--doc", required=True, help="Document ID (folder under storage base path)")
    parser.add_argument(
        "--pages",
        required=True,
        help="Comma-separated page numbers or ranges, e.g. '3,7' or '1-3,10'",
    )
    parser.add_argument(
        "--expect",
        choices=list(_EXPECT_ALIASES.keys()),
        default=None,
        help="Expected outcome for all pages (exits non-zero if any page contradicts this)",
    )
    parser.add_argument(
        "--no-vlm",
        action="store_true",
        help="Disable VLM evaluation even if configured (text-only)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Colour output (default: auto)",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    from app.config.settings import get_settings

    settings = get_settings()
    storage_base = Path(settings.storage.base_path)
    color = _use_color(args.color)
    pages = _parse_pages(args.pages)
    expect_raw = _EXPECT_ALIASES.get(args.expect) if args.expect else None
    vlm_enabled = not args.no_vlm

    print(
        _c(f"\nRule validation — agent={args.agent} rule={args.rule} doc={args.doc}", _Style.BOLD, enabled=color)
    )
    print(_c(f"Pages: {pages}  expect={args.expect or 'any'}  vlm={'off' if args.no_vlm else 'on'}\n", _Style.GREY, enabled=color))

    tasks = [
        _evaluate_page(
            agent=args.agent,
            rule_number=args.rule,
            doc_id=args.doc,
            page_num=p,
            vlm_enabled=vlm_enabled,
            storage_base=storage_base,
        )
        for p in pages
    ]
    results = await asyncio.gather(*tasks)

    failures: list[int] = []
    for result in results:
        ok = _print_result(result, expect_raw, color=color)
        if not ok:
            failures.append(result["page"])

    if expect_raw is not None:
        if failures:
            msg = f"Expectation '{args.expect}' NOT met on page(s): {failures}"
            print(_c(f"✗  {msg}", _Style.BOLD, _Style.RED, enabled=color))
            return 1
        else:
            msg = f"All {len(pages)} page(s) met expectation '{args.expect}'"
            print(_c(f"✓  {msg}", _Style.BOLD, _Style.GREEN, enabled=color))

    return 0


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
