from pathlib import Path

from tests.benchmark.benchmark_ci_gate import run_gate


def test_benchmark_ci_gate_passes_within_thresholds():
    report = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "extraction_benchmark"
        / "reports"
        / "latest_compare_report.json"
    )
    if not report.exists():
        return
    ok, details = run_gate(
        str(report),
        min_quality_f1_delta=-0.5,
        max_latency_ms_delta=500.0,
        max_cost_usd_delta=1.0,
    )
    assert ok is True
    assert "delta" in details
