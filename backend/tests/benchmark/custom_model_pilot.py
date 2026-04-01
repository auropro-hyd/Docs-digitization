"""Custom model pilot evaluator against fixture dataset."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "custom_model_dataset"


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _score_variant(sample_id: str, variant_dir: Path, labels_dir: Path) -> tuple[int, int]:
    label_file = labels_dir / f"{sample_id}.json"
    pred_file = variant_dir / f"{sample_id}.json"
    if not (label_file.exists() and pred_file.exists()):
        return 0, 0
    labels = json.loads(label_file.read_text()).get("fields", {})
    preds = json.loads(pred_file.read_text()).get("fields", {})
    tp = 0
    total = 0
    for fid, expected in labels.items():
        total += 1
        if _norm(preds.get(fid, "")) == _norm(expected):
            tp += 1
    return tp, total


def run() -> dict:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    labels_dir = ROOT / "labels"
    base_dir = ROOT / "predictions_baseline"
    custom_dir = ROOT / "predictions_custom"

    base_tp = base_total = custom_tp = custom_total = 0
    for s in manifest.get("samples", []):
        sample_id = s.get("sample_id", "")
        bt, btot = _score_variant(sample_id, base_dir, labels_dir)
        ct, ctot = _score_variant(sample_id, custom_dir, labels_dir)
        base_tp += bt
        base_total += btot
        custom_tp += ct
        custom_total += ctot

    base_acc = (base_tp / base_total) if base_total else 0.0
    custom_acc = (custom_tp / custom_total) if custom_total else 0.0
    out = {
        "baseline_accuracy": round(base_acc, 4),
        "custom_accuracy": round(custom_acc, 4),
        "accuracy_gain": round(custom_acc - base_acc, 4),
        "samples": len(manifest.get("samples", [])),
    }
    report_dir = ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "pilot_report.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
