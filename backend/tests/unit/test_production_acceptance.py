import json
from pathlib import Path

from tests.benchmark.production_acceptance import run_acceptance


def test_production_acceptance_returns_gate_summary():
    reports = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "extraction_benchmark"
        / "reports"
    )
    compare = reports / "latest_compare_report.json"
    single = reports / "latest_report.json"
    if not (compare.exists() and single.exists()):
        return

    out = run_acceptance(
        compare_report_path=str(compare),
        single_report_path=str(single),
        min_quality_f1_delta=-1.0,
        max_latency_ms_delta=1000.0,
        max_cost_usd_delta=10.0,
        max_signature_fnr=1.0,
        max_placeholder_errors=100,
    )
    assert "status" in out
    assert len(out["gates"]) == 6


def test_production_acceptance_rejects_quality_regression_by_default(tmp_path):
    compare = tmp_path / "compare.json"
    single = tmp_path / "single.json"
    compare.write_text(json.dumps({"delta": {"quality_f1_delta": -0.001, "latency_ms_delta": 0.0, "cost_usd_delta": 0.0}}))
    single.write_text(json.dumps({"error_taxonomy": {"placeholder": 0}}))

    out = run_acceptance(
        compare_report_path=str(compare),
        single_report_path=str(single),
        max_signature_fnr=1.0,
        max_placeholder_errors=0,
    )
    gate_a = next(g for g in out["gates"] if g["gate"] == "Gate A")
    assert gate_a["passed"] is False
    assert out["status"] == "rejected"
