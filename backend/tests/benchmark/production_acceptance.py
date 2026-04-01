"""Final production acceptance gate for extraction quality rollout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.services.field_normalization import normalize_field_value


def _gate_result(name: str, passed: bool, details: dict[str, Any]) -> dict[str, Any]:
    return {"gate": name, "passed": passed, "details": details}


def run_acceptance(
    *,
    compare_report_path: str,
    single_report_path: str,
    min_quality_f1_delta: float = 0.0,
    max_latency_ms_delta: float = 150.0,
    max_cost_usd_delta: float = 0.02,
    max_signature_fnr: float = 0.1,
    max_placeholder_errors: int = 0,
) -> dict[str, Any]:
    compare = json.loads(Path(compare_report_path).read_text())
    single = json.loads(Path(single_report_path).read_text())

    delta = compare.get("delta", {})
    taxonomy = single.get("error_taxonomy", {})

    q_delta = float(delta.get("quality_f1_delta", 0.0) or 0.0)
    l_delta = float(delta.get("latency_ms_delta", 0.0) or 0.0)
    c_delta = float(delta.get("cost_usd_delta", 0.0) or 0.0)
    signature_fnr = float(single.get("signature_false_negative_rate", 0.0) or 0.0)
    placeholder_errors = int(taxonomy.get("placeholder", 0) or 0)

    # Determinism gate: repeated normalization of known artifacts must be stable.
    deterministic_samples = [
        ("effective_date", "09-04-2025"),
        ("batch_no", " ab - 123 "),
        ("sample_sent_to_qcd", "-"),
    ]
    det_ok = True
    det_rows = []
    for fid, raw in deterministic_samples:
        a, _ = normalize_field_value(fid, raw)
        b, _ = normalize_field_value(fid, raw)
        stable = a == b
        det_ok = det_ok and stable
        det_rows.append({"field_id": fid, "raw": raw, "normalized": a, "stable": stable})

    # Parser + selection surfacing gate.
    parser_surfaced = bool(single.get("parser_signal_surfaced", True))
    selection_surfaced = bool(single.get("selection_ambiguity_surfaced", True))

    gates = [
        _gate_result("Gate A", q_delta >= min_quality_f1_delta, {"quality_f1_delta": q_delta, "min": min_quality_f1_delta}),
        _gate_result("Gate B", signature_fnr <= max_signature_fnr, {"signature_false_negative_rate": signature_fnr, "max": max_signature_fnr}),
        _gate_result("Gate C", placeholder_errors <= max_placeholder_errors, {"placeholder_errors": placeholder_errors, "max": max_placeholder_errors}),
        _gate_result(
            "Gate D",
            (l_delta <= max_latency_ms_delta and c_delta <= max_cost_usd_delta),
            {"latency_ms_delta": l_delta, "cost_usd_delta": c_delta, "max_latency_ms_delta": max_latency_ms_delta, "max_cost_usd_delta": max_cost_usd_delta},
        ),
        _gate_result("Gate E", det_ok, {"determinism_checks": det_rows}),
        _gate_result("Gate F", parser_surfaced and selection_surfaced, {"parser_signal_surfaced": parser_surfaced, "selection_ambiguity_surfaced": selection_surfaced}),
    ]

    passed = all(g["passed"] for g in gates)
    return {
        "status": "accepted" if passed else "rejected",
        "gates": gates,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compare-report",
        default=str(
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "extraction_benchmark"
            / "reports"
            / "latest_compare_report.json"
        ),
    )
    parser.add_argument(
        "--single-report",
        default=str(
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "extraction_benchmark"
            / "reports"
            / "latest_report.json"
        ),
    )
    parser.add_argument("--min-quality-f1-delta", type=float, default=0.0)
    parser.add_argument("--max-latency-ms-delta", type=float, default=150.0)
    parser.add_argument("--max-cost-usd-delta", type=float, default=0.02)
    parser.add_argument("--max-signature-fnr", type=float, default=0.1)
    parser.add_argument("--max-placeholder-errors", type=int, default=0)
    args = parser.parse_args()
    out = run_acceptance(
        compare_report_path=args.compare_report,
        single_report_path=args.single_report,
        min_quality_f1_delta=args.min_quality_f1_delta,
        max_latency_ms_delta=args.max_latency_ms_delta,
        max_cost_usd_delta=args.max_cost_usd_delta,
        max_signature_fnr=args.max_signature_fnr,
        max_placeholder_errors=args.max_placeholder_errors,
    )
    print(json.dumps(out, indent=2))
    raise SystemExit(0 if out["status"] == "accepted" else 1)
