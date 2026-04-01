"""Run extraction benchmark on fixture dataset."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tests.benchmark.label_schema import GoldLabelDocument

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "extraction_benchmark"
LABELS_DIR = FIXTURE_ROOT / "labels"
DOCS_DIR = FIXTURE_ROOT / "docs"
REPORT_DIR = FIXTURE_ROOT / "reports"

PLACEHOLDERS = {"-", "n/a", "na", "nil", "none"}


def _norm(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _norm_compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _norm(value))


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    a_area = max(0.0, (ax2 - ax1) * (ay2 - ay1))
    b_area = max(0.0, (bx2 - bx1) * (by2 - by1))
    denom = a_area + b_area - inter
    return inter / denom if denom > 0 else 0.0


def _load_prediction_fields(sample_id: str, variant: str = "default") -> dict[str, dict[str, Any]]:
    sample_dir = DOCS_DIR / sample_id
    pred_file = sample_dir / (
        "predictions.json" if variant == "default" else f"predictions_{variant}.json"
    )
    result_file = sample_dir / "result.json"
    fields: dict[str, dict[str, Any]] = {}

    if pred_file.exists():
        payload = json.loads(pred_file.read_text())
        for item in payload.get("fields", []):
            field_id = str(item.get("field_id") or "").strip()
            if field_id:
                fields[field_id] = item
        return fields

    if result_file.exists():
        payload = json.loads(result_file.read_text())
        for ext in payload.get("extractions", []):
            page_num = ext.get("page_num")
            for kv in ext.get("key_value_pairs", []):
                key = str(kv.get("key") or kv.get("key_text") or "").strip()
                value = str(kv.get("value") or kv.get("value_text") or "").strip()
                if not key:
                    continue
                field_id = _norm(key).replace(" ", "_")
                fields[field_id] = {
                    "field_id": field_id,
                    "raw_value": value,
                    "normalized_value": _norm(value),
                    "page_num": page_num,
                    "region": kv.get("bounding_region"),
                    "is_handwritten": False,
                    "is_placeholder": _norm(value) in PLACEHOLDERS,
                }
        return fields

    return fields


def _evaluate_variant(variant: str) -> dict[str, Any]:
    labels = sorted(LABELS_DIR.glob("*.labels.json"))
    if not labels:
        raise SystemExit(f"No label fixtures found in {LABELS_DIR}")

    field_stats: dict[str, Counter] = defaultdict(Counter)
    taxonomy: Counter = Counter()
    sample_count = 0

    for label_file in labels:
        label_doc = GoldLabelDocument.model_validate(json.loads(label_file.read_text()))
        sample_count += 1
        predictions = _load_prediction_fields(label_doc.sample_id, variant=variant)
        expected_ids = {f.field_id for f in label_doc.fields}

        for fld in label_doc.fields:
            pred = predictions.get(fld.field_id)
            exp = _norm(fld.normalized_value or fld.raw_value)
            pred_raw = str((pred or {}).get("normalized_value") or (pred or {}).get("raw_value") or "")
            got = _norm(pred_raw)

            field_stats[fld.field_id]["support"] += 1
            if got and got == exp:
                field_stats[fld.field_id]["tp"] += 1
                continue

            field_stats[fld.field_id]["fn"] += 1
            if got:
                field_stats[fld.field_id]["fp"] += 1
                pred_page = (pred or {}).get("page_num")
                pred_region = (pred or {}).get("region")
                pred_is_handwritten = bool((pred or {}).get("is_handwritten", False))

                if got in PLACEHOLDERS and not fld.placeholder_allowed:
                    taxonomy["placeholder"] += 1
                elif fld.expected_page is not None and pred_page is not None and int(pred_page) != int(fld.expected_page):
                    taxonomy["wrong-page"] += 1
                elif fld.expected_region and isinstance(pred_region, list) and len(pred_region) == 4 and _iou(
                    fld.expected_region, pred_region
                ) < 0.5:
                    taxonomy["wrong-region"] += 1
                elif fld.handwriting_expected and not pred_is_handwritten:
                    taxonomy["handwriting"] += 1
                elif _norm_compact(got) == _norm_compact(exp):
                    taxonomy["format"] += 1
                else:
                    taxonomy["substitution"] += 1
            else:
                taxonomy["missing"] += 1

        for pred_id, pred in predictions.items():
            if pred_id in expected_ids:
                continue
            extra_val = _norm(str(pred.get("normalized_value") or pred.get("raw_value") or ""))
            if extra_val:
                taxonomy["substitution"] += 1

    per_field: dict[str, Any] = {}
    total_tp = total_fp = total_fn = 0
    for field_id, s in sorted(field_stats.items()):
        tp, fp, fn = s["tp"], s["fp"], s["fn"]
        total_tp += tp
        total_fp += fp
        total_fn += fn
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_field[field_id] = {
            "support": s["support"],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0

    return {
        "samples": sample_count,
        "aggregate": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
        },
        "per_field": per_field,
        "error_taxonomy": {
            "missing": taxonomy["missing"],
            "substitution": taxonomy["substitution"],
            "format": taxonomy["format"],
            "wrong-page": taxonomy["wrong-page"],
            "wrong-region": taxonomy["wrong-region"],
            "handwriting": taxonomy["handwriting"],
            "placeholder": taxonomy["placeholder"],
        },
    }


def run(mode: str = "single") -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if mode != "compare":
        report = _evaluate_variant("default")
        (REPORT_DIR / "latest_report.json").write_text(json.dumps(report, indent=2))
        return report

    baseline = _evaluate_variant("baseline")
    routed = _evaluate_variant("routed")
    delta = {
        "quality_f1_delta": round(routed["aggregate"]["f1"] - baseline["aggregate"]["f1"], 4),
        "quality_precision_delta": round(routed["aggregate"]["precision"] - baseline["aggregate"]["precision"], 4),
        "quality_recall_delta": round(routed["aggregate"]["recall"] - baseline["aggregate"]["recall"], 4),
        # Static placeholders until runtime latency/cost capture is wired to benchmark fixtures.
        "latency_ms_delta": 0.0,
        "cost_usd_delta": 0.0,
    }
    report = {
        "mode": "compare",
        "baseline": baseline,
        "routed": routed,
        "delta": delta,
    }
    (REPORT_DIR / "latest_compare_report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["single", "compare"], default="single")
    args = parser.parse_args()

    out = run(mode=args.mode)
    print("Extraction benchmark complete")
    if args.mode == "compare":
        print("Delta:", json.dumps(out["delta"], indent=2))
    else:
        print(json.dumps(out["aggregate"], indent=2))
        print("Error taxonomy:", json.dumps(out["error_taxonomy"], indent=2))
