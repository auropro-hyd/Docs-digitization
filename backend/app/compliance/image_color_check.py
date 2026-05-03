"""Cheap deterministic check: does an image carry meaningful colour?

The VLM ink-colour rule (``VC-INK-COLOR``) was returning false positives
on black-and-white scans — telling reviewers there was red ink on pages
that contained no chromatic information at all. Root cause: the prompt
asks "what COLOR ink was used? (blue/black/red/green/pencil/other)"
with a closed list, and the VLM tends to pick a colour even when none
is present rather than emit "uncertain".

The right primary fix is to never ask the question on a B&W image.
This module computes whether the page actually has colour by sampling
HSV saturation across a thumbnail. Ink colour rules then short-circuit
to ``not_applicable`` when the image is grayscale, with a reason a
reviewer can act on ("page is a B&W scan; ink colour can't be
determined from this rendering").

The check is conservative: a small fraction of saturated pixels
(e.g. coloured stamps, colour highlights on a stamp) keeps the image
classified as colour so genuine red-ink entries still reach the VLM.
The threshold is tuned for typical scanned BPCRs (24-bit RGB after
ImageMagick / pdf rasteriser); adjust via env if real-world docs
shift the distribution.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Final

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Saturation thresholds in 0–255 range (PIL HSV).
#
# - ``MEAN_THRESHOLD``: mean saturation across the thumbnail. A pure
#   B&W scan averages ~0; a JPEG-compressed B&W scan with subtle
#   chroma noise averages well under 5; any document with even one
#   colour stamp jumps to ≥10. 8 is the ballpark dividing line.
# - ``COLOURED_PIXEL_FRACTION``: fraction of pixels whose saturation
#   exceeds the floor. Useful as a second criterion so a tiny
#   coloured stamp on an otherwise B&W page still passes (we want
#   the VLM to look at it).
_DEFAULT_MEAN_THRESHOLD: Final[float] = 8.0
_DEFAULT_PIXEL_FRACTION: Final[float] = 0.005  # 0.5%
_DEFAULT_PIXEL_SATURATION: Final[float] = 30.0


_MEAN_THRESHOLD = _env_float("AT_VLM__GRAYSCALE_MEAN_THRESHOLD", _DEFAULT_MEAN_THRESHOLD)
_PIXEL_FRACTION = _env_float(
    "AT_VLM__GRAYSCALE_COLOURED_PIXEL_FRACTION", _DEFAULT_PIXEL_FRACTION,
)
_PIXEL_SATURATION = _env_float(
    "AT_VLM__GRAYSCALE_PIXEL_SATURATION_FLOOR", _DEFAULT_PIXEL_SATURATION,
)


def image_has_meaningful_colour(image_bytes: bytes) -> bool:
    """Return True when ``image_bytes`` carries chromatic information.

    A B&W scan, a 1-bit bilevel image, or a 3-channel image with all
    channels equal returns False. Any document with meaningful colour
    content (a stamp, a highlight, real ink colour variation) returns
    True so downstream colour-aware checks still run.

    Fail-open: any decode error returns True so a flaky image doesn't
    silently disable the colour-based checks. Logged for visibility.
    """

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover — Pillow is an installed dep
        logger.warning(
            "Pillow not available; skipping grayscale check — "
            "treating image as colour-bearing",
        )
        return True

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            mode = img.mode
            # Bilevel ("1") and luminance ("L", "LA", "I", "F") modes
            # carry no colour by definition. Short-circuit.
            if mode in {"1", "L", "LA", "I", "F"}:
                return False

            # Any non-RGB(A) mode that we don't explicitly handle is
            # treated as colour-bearing — better to over-include than
            # mis-classify a paletted or CMYK page as B&W.
            if mode not in {"RGB", "RGBA"}:
                return True

            # Down-sample to a thumbnail for cheap statistics. 256 px
            # on the long edge gives ~50k pixels of signal, which is
            # plenty for a saturation estimate without paying for a
            # full-page conversion.
            img.thumbnail((256, 256))
            hsv = img.convert("HSV")
            sat = hsv.split()[1]
            stats = sat.getextrema(), _saturation_stats(sat)
            (sat_min, sat_max), (mean, fraction_above_floor) = stats

            if sat_max == 0:
                return False
            if mean < _MEAN_THRESHOLD and fraction_above_floor < _PIXEL_FRACTION:
                return False
            return True
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "Grayscale check failed for image (%d bytes): %s — treating "
            "as colour-bearing to avoid false negatives",
            len(image_bytes), exc,
        )
        return True


def _saturation_stats(saturation_band: object) -> tuple[float, float]:
    """Return ``(mean, fraction_above_floor)`` for the saturation band.

    Pulled out so the thresholds are testable in isolation. Uses
    ``getdata()`` rather than numpy to keep the grayscale-detection
    path dependency-light — this module is on the hot path for every
    page of every compliance run and adding numpy just for one
    histogram pass would be noise.
    """

    pixels = list(saturation_band.getdata())  # type: ignore[attr-defined]
    if not pixels:
        return 0.0, 0.0
    total = sum(pixels)
    mean = total / len(pixels)
    above_floor = sum(1 for p in pixels if p >= _PIXEL_SATURATION)
    fraction = above_floor / len(pixels)
    return mean, fraction


__all__ = ["image_has_meaningful_colour"]
