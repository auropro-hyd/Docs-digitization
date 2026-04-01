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
