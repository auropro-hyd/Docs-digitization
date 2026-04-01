import app.core.services.document_quality_gate as dqg


class _FakePage:
    pass


class _FakeDoc:
    def __init__(self):
        self._pages = [_FakePage(), _FakePage(), _FakePage()]

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, idx):
        return self._pages[idx]

    def close(self):
        return None


def test_quality_gate_warns_when_quality_is_poor(monkeypatch):
    monkeypatch.setattr(dqg.pdfium, "PdfDocument", lambda _p: _FakeDoc())
    values = [
        {"render_width": 800, "render_height": 900, "contrast_std": 10.0, "dark_ratio": 0.01, "bright_ratio": 0.995, "rotation_deg": 0.0, "skew_or_rotation_flag": 1.0},
        {"render_width": 1000, "render_height": 1000, "contrast_std": 12.0, "dark_ratio": 0.02, "bright_ratio": 0.991, "rotation_deg": 0.0, "skew_or_rotation_flag": 0.0},
        {"render_width": 900, "render_height": 1200, "contrast_std": 8.0, "dark_ratio": 0.01, "bright_ratio": 0.993, "rotation_deg": 90.0, "skew_or_rotation_flag": 1.0},
    ]
    monkeypatch.setattr(dqg, "_render_metrics", lambda _p, scale=2.0: values.pop(0))

    report = dqg.check_document_quality("dummy.pdf", block_on_critical=False)
    assert report["policy"]["decision"] == "warn"
    assert report["summary"]["low_resolution_pages"] >= 1


def test_quality_gate_blocks_when_configured(monkeypatch):
    monkeypatch.setattr(dqg.pdfium, "PdfDocument", lambda _p: _FakeDoc())
    monkeypatch.setattr(
        dqg,
        "_render_metrics",
        lambda _p, scale=2.0: {
            "render_width": 800,
            "render_height": 900,
            "contrast_std": 5.0,
            "dark_ratio": 0.0,
            "bright_ratio": 0.998,
            "rotation_deg": 0.0,
            "skew_or_rotation_flag": 1.0,
        },
    )

    report = dqg.check_document_quality("dummy.pdf", block_on_critical=True)
    assert report["policy"]["decision"] == "block"
