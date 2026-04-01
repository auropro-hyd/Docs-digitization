from tests.benchmark.custom_model_pilot import run


def test_custom_model_pilot_reports_gain():
    out = run()
    assert out["samples"] >= 1
    assert out["custom_accuracy"] >= out["baseline_accuracy"]
