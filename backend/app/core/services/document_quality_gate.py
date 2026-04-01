"""Pre-OCR document quality gate for scanned PDF inputs."""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

import pypdfium2 as pdfium


def _render_metrics(page, scale: float = 2.0) -> dict[str, float]:
    bmp = page.render(scale=scale)
    arr = bmp.to_numpy()

    # RGB -> grayscale using weighted average without extra dependencies.
    gray = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
    h, w = gray.shape
    contrast_std = float(gray.std())
    dark_ratio = float((gray < 40).mean())
    bright_ratio = float((gray > 240).mean())

    # Rotation metadata is a practical skew proxy for scanned packets.
    rotation = int(getattr(page, "get_rotation", lambda: 0)() or 0)
    skew_flag = rotation % 360 != 0

    return {
        "render_width": float(w),
        "render_height": float(h),
        "contrast_std": round(contrast_std, 3),
        "dark_ratio": round(dark_ratio, 4),
        "bright_ratio": round(bright_ratio, 4),
        "rotation_deg": float(rotation),
        "skew_or_rotation_flag": 1.0 if skew_flag else 0.0,
    }


def check_document_quality(
    pdf_path: str,
    *,
    sample_pages: int = 3,
    min_width: int = 1200,
    min_height: int = 1600,
    min_contrast_std: float = 26.0,
    max_bright_ratio: float = 0.985,
    block_on_critical: bool = False,
) -> dict[str, Any]:
    doc = pdfium.PdfDocument(str(Path(pdf_path)))
    total_pages = len(doc)
    sampled = list(range(min(total_pages, max(1, sample_pages))))

    page_results: list[dict[str, Any]] = []
    low_res_count = low_contrast_count = artifact_count = skew_count = 0

    for idx in sampled:
        page = doc[idx]
        metrics = _render_metrics(page)
        low_res = metrics["render_width"] < min_width or metrics["render_height"] < min_height
        low_contrast = metrics["contrast_std"] < min_contrast_std
        heavy_artifact = metrics["bright_ratio"] > max_bright_ratio
        skew_flag = metrics["skew_or_rotation_flag"] > 0

        low_res_count += 1 if low_res else 0
        low_contrast_count += 1 if low_contrast else 0
        artifact_count += 1 if heavy_artifact else 0
        skew_count += 1 if skew_flag else 0

        page_results.append({
            "page_num": idx + 1,
            "metrics": metrics,
            "flags": {
                "low_resolution": low_res,
                "low_contrast": low_contrast,
                "artifact_heavy": heavy_artifact,
                "skew_or_rotation": skew_flag,
            },
        })

    doc.close()
    sampled_count = max(1, len(sampled))
    severity_score = (
        0.35 * (low_res_count / sampled_count)
        + 0.25 * (low_contrast_count / sampled_count)
        + 0.25 * (artifact_count / sampled_count)
        + 0.15 * (skew_count / sampled_count)
    )
    severity = "low"
    if severity_score >= 0.6:
        severity = "high"
    elif severity_score >= 0.3:
        severity = "medium"

    decision = "ok"
    if severity == "high":
        decision = "block" if block_on_critical else "warn"
    elif severity == "medium":
        decision = "warn"

    return {
        "summary": {
            "sampled_pages": sampled_count,
            "mean_contrast_std": round(mean([p["metrics"]["contrast_std"] for p in page_results]) if page_results else 0.0, 3),
            "low_resolution_pages": low_res_count,
            "low_contrast_pages": low_contrast_count,
            "artifact_heavy_pages": artifact_count,
            "skew_or_rotation_pages": skew_count,
        },
        "pages": page_results,
        "policy": {
            "severity_score": round(severity_score, 3),
            "severity": severity,
            "decision": decision,  # ok | warn | block
            "block_on_critical": block_on_critical,
        },
    }
