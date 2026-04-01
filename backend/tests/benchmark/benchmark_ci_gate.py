"""CI gate for extraction benchmark comparison report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def run_gate(
    report_path: str,
    *,
    min_quality_f1_delta: float,
    max_latency_ms_delta: float,
    max_cost_usd_delta: float,
) -> tuple[bool, dict]:
    payload = json.loads(Path(report_path).read_text())
    delta = payload.get("delta", {})
    q = float(delta.get("quality_f1_delta", 0.0) or 0.0)
    l = float(delta.get("latency_ms_delta", 0.0) or 0.0)
    c = float(delta.get("cost_usd_delta", 0.0) or 0.0)

    reasons: list[str] = []
    if q < min_quality_f1_delta:
        reasons.append(f"quality_f1_delta {q} < {min_quality_f1_delta}")
    if l > max_latency_ms_delta:
        reasons.append(f"latency_ms_delta {l} > {max_latency_ms_delta}")
    if c > max_cost_usd_delta:
        reasons.append(f"cost_usd_delta {c} > {max_cost_usd_delta}")

    ok = len(reasons) == 0
    return ok, {"delta": {"quality_f1_delta": q, "latency_ms_delta": l, "cost_usd_delta": c}, "reasons": reasons}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        default=str(
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "extraction_benchmark"
            / "reports"
            / "latest_compare_report.json"
        ),
    )
    parser.add_argument("--min-quality-f1-delta", type=float, default=-0.01)
    parser.add_argument("--max-latency-ms-delta", type=float, default=150.0)
    parser.add_argument("--max-cost-usd-delta", type=float, default=0.02)
    args = parser.parse_args()

    ok, details = run_gate(
        args.report,
        min_quality_f1_delta=args.min_quality_f1_delta,
        max_latency_ms_delta=args.max_latency_ms_delta,
        max_cost_usd_delta=args.max_cost_usd_delta,
    )
    print(json.dumps(details, indent=2))
    raise SystemExit(0 if ok else 1)
