from tests.benchmark.run_extraction_benchmark import run


def test_extraction_benchmark_runner_outputs_metrics_and_taxonomy():
    report = run()
    assert report["samples"] >= 1
    assert "aggregate" in report and "f1" in report["aggregate"]
    assert "error_taxonomy" in report
    for key in ("missing", "substitution", "format", "wrong-page", "wrong-region", "handwriting", "placeholder"):
        assert key in report["error_taxonomy"]


def test_extraction_benchmark_compare_mode_outputs_deltas():
    report = run(mode="compare")
    assert report["mode"] == "compare"
    assert "baseline" in report and "routed" in report
    assert "delta" in report
    assert "quality_f1_delta" in report["delta"]
