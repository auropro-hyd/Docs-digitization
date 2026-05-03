"""VC-INK-COLOR no longer hallucinates red ink on B&W scans.

Akhilesh's report on 2026-04-30: VLM-based ink-detection rules were
returning false positives — claiming red ink was present on pages
that were entirely black-and-white. Root cause: the prompt asked
"what COLOR ink was used? (blue/black/red/green/pencil/other)" with
a closed list, and the VLM tends to pick a colour even when none is
visible rather than emit "uncertain".

The fix has two layers:

1. **Pre-flight grayscale gate** (deterministic; primary fix). Before
   sending the page image to the VLM, we sample its HSV saturation.
   B&W scans short-circuit colour-dependent visual checks to
   ``not_applicable`` with an actionable reason — the VLM never
   even sees the image for those checks. Saves cost and eliminates
   the failure mode by construction.

2. **Strengthened prompt** (defense in depth). On colour-bearing
   images, the prompt now opens with an explicit GRAYSCALE escape
   hatch so even the VLM has a way out when the chroma is too faint
   to read.

These tests pin layer 1: deterministic, no VLM dependency, no
network, no flakiness.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.compliance.image_color_check import image_has_meaningful_colour
from app.compliance.models import RuleBatchResult
from app.compliance.rules.registry import AuditRule, RuleBatch
from app.compliance.vision_evaluator import VisionBatchEvaluator


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _grayscale_rgb_image(size: tuple[int, int] = (200, 280)) -> bytes:
    """An RGB-mode image with all channels equal — a real-world B&W
    scan rendered through pdf2image looks exactly like this."""

    img = Image.new("RGB", size, color=(245, 245, 245))
    # Paint a few darker rectangles to simulate text
    for x in range(20, 180, 30):
        for y in range(40, 240, 20):
            for dx in range(15):
                for dy in range(8):
                    img.putpixel((x + dx, y + dy), (40, 40, 40))
    return _png_bytes(img)


def _truly_bw_image(size: tuple[int, int] = (200, 280)) -> bytes:
    """A proper L-mode (luminance only) image — no colour channel."""

    img = Image.new("L", size, color=240)
    return _png_bytes(img)


def _colour_image_with_red_stamp(size: tuple[int, int] = (200, 280)) -> bytes:
    """Black-and-white-ish background with one saturated red stamp.
    The minority of coloured pixels still pushes the page over the
    fraction threshold so the gate lets the VLM see it."""

    img = Image.new("RGB", size, color=(245, 245, 245))
    # 35x35 saturated red square — a stamp on an otherwise-grey page
    for x in range(140, 175):
        for y in range(20, 55):
            img.putpixel((x, y), (220, 30, 30))
    return _png_bytes(img)


# ── unit tests for the colour-detection helper ──────────────────────────────


def test_l_mode_image_is_classified_as_grayscale() -> None:
    """1-channel luminance images carry no colour by construction."""

    assert image_has_meaningful_colour(_truly_bw_image()) is False


def test_rgb_image_with_equal_channels_is_classified_as_grayscale() -> None:
    """The most common real-world case: pdf2image rendering a
    grayscale-only PDF as an RGB PNG with R=G=B everywhere. We must
    still detect it as B&W."""

    assert image_has_meaningful_colour(_grayscale_rgb_image()) is False


def test_rgb_image_with_a_red_stamp_keeps_colour_classification() -> None:
    """A document with even one saturated coloured stamp must pass
    through to the VLM — we don't want to suppress real ink-colour
    findings just because most of the page is grayscale."""

    assert image_has_meaningful_colour(_colour_image_with_red_stamp()) is True


def test_corrupt_bytes_fail_open_to_colour_classification() -> None:
    """A malformed image returns ``True`` so a flaky decode doesn't
    silently disable colour-aware checks. Logged separately."""

    assert image_has_meaningful_colour(b"not an image") is True
    assert image_has_meaningful_colour(b"") is True


# ── integration tests for the gate inside VisionBatchEvaluator ──────────────


def _ink_color_rule(rule_id: str = "alcoa.attributable.ink-blue-or-black") -> AuditRule:
    return AuditRule(
        id=rule_id,
        number=1,
        category="attributable",
        category_display="Attributable",
        agent="alcoa",
        text="All entries must be made in blue or black ink.",
        visual_checks=["VC-INK-COLOR"],
    )


def _non_colour_rule(rule_id: str = "alcoa.legible.text-readable") -> AuditRule:
    return AuditRule(
        id=rule_id,
        number=2,
        category="legible",
        category_display="Legible",
        agent="alcoa",
        text="All text on the page must be readable.",
        visual_checks=["VC-LEGIBILITY"],
    )


class _FakeVLM:
    """VLM stub that records whether it was called.

    The point of the gate is that a B&W image never reaches the VLM
    for ink-colour rules. This stub lets us assert that without
    standing up Gemini.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def supports_structured_output(self) -> bool:
        return False

    async def analyze_image(self, image, prompt, system=None) -> str:
        self.calls.append(prompt)
        return (
            '{"checks": [], "rule_evaluations": [{'
            '"rule_id": "alcoa.legible.text-readable", '
            '"status": "compliant", "confidence": 0.9, '
            '"reasoning": "stub", "evidence": "stub"'
            '}]}'
        )

    async def analyze_image_structured(self, image, prompt, schema, system=None):
        return await self.analyze_image(image, prompt, system=system)


@pytest.mark.asyncio
async def test_grayscale_image_short_circuits_ink_color_rule() -> None:
    """End-to-end: a B&W image + an INK-COLOR rule produces
    ``not_applicable`` without invoking the VLM. This is the exact
    scenario Akhilesh hit; the test pins the fix against regression.
    """

    evaluator = VisionBatchEvaluator()
    rule = _ink_color_rule()
    batch = RuleBatch(
        batch_id="alcoa-attributable-0",
        category="attributable",
        agent="alcoa",
        rules=[rule],
    )
    vlm = _FakeVLM()

    bid, pn, result = await evaluator.evaluate_batch(
        batch, _grayscale_rgb_image(), page_num=3, vlm=vlm,
    )

    assert bid == "alcoa-attributable-0"
    assert pn == 3
    assert vlm.calls == [], (
        "VLM was called for a colour-dependent rule on a B&W page — "
        "the pre-flight gate must skip the call entirely"
    )
    assert isinstance(result, RuleBatchResult)
    assert len(result.evaluations) == 1
    ev = result.evaluations[0]
    assert ev.rule_id == rule.id
    assert ev.status == "not_applicable"
    assert "grayscale" in ev.reasoning.lower()


@pytest.mark.asyncio
async def test_grayscale_image_lets_non_colour_rules_through_to_vlm() -> None:
    """A B&W image should still be evaluated for non-colour-dependent
    rules — the gate is targeted, not a blanket short-circuit. The
    legibility rule below has no VC-INK-COLOR tag and must reach
    the VLM with a normal evaluation."""

    evaluator = VisionBatchEvaluator()
    batch = RuleBatch(
        batch_id="alcoa-mixed-0",
        category="legible",
        agent="alcoa",
        rules=[_ink_color_rule(), _non_colour_rule()],
    )
    vlm = _FakeVLM()

    bid, pn, result = await evaluator.evaluate_batch(
        batch, _grayscale_rgb_image(), page_num=3, vlm=vlm,
    )

    assert len(vlm.calls) == 1, (
        "non-colour-dependent rule must reach the VLM"
    )
    # Prompt must NOT carry the gated rule's instructions.
    assert "VC-INK-COLOR" not in vlm.calls[0], (
        "the gated check must be stripped from the prompt — leaving it "
        "in primes the VLM to volunteer ink-colour answers anyway"
    )
    # Both rules show up in the merged result.
    rule_ids = {ev.rule_id for ev in result.evaluations}
    assert rule_ids == {_ink_color_rule().id, _non_colour_rule().id}
    by_id = {ev.rule_id: ev for ev in result.evaluations}
    assert by_id[_ink_color_rule().id].status == "not_applicable"
    assert by_id[_non_colour_rule().id].status == "compliant"


@pytest.mark.asyncio
async def test_colour_bearing_image_lets_ink_color_through_to_vlm() -> None:
    """A page with a real coloured stamp must NOT be gated — we don't
    want to suppress genuine ink-colour findings. The VLM must
    receive the prompt with VC-INK-COLOR included."""

    evaluator = VisionBatchEvaluator()
    batch = RuleBatch(
        batch_id="alcoa-attributable-0",
        category="attributable",
        agent="alcoa",
        rules=[_ink_color_rule()],
    )
    vlm = _FakeVLM()

    await evaluator.evaluate_batch(
        batch, _colour_image_with_red_stamp(), page_num=3, vlm=vlm,
    )

    assert len(vlm.calls) == 1
    assert "VC-INK-COLOR" in vlm.calls[0], (
        "colour-bearing images must reach the VLM with the ink-colour "
        "check intact — only B&W scans should be gated"
    )
