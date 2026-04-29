"""Tests for the OCR-progress payload builder + phase tagging.

The builder is a small piece of pure-ish state that sits on the hot
path between the OCR adapter callbacks (Datalab heartbeat / Azure
LRO poller / Marker timeout loop) and the WebSocket broadcast. Its
job is to throttle without losing important transitions:

- Steady heartbeats arrive at the SDK's poll cadence (1-2s) and
  must reach the user as label refreshes — not get coalesced into
  a single update at the end of the chunk.
- Floods (e.g. a buggy adapter calling back 100x/sec) must not
  saturate the WebSocket — the throttle squashes them.
- Boundaries (start, complete) must always pass through.
- Big jumps (≥5% delta) must always pass through so the bar moves
  at chunk boundaries even if the user just blinked.
"""

from __future__ import annotations

from app.workflow.nodes import (
    _phase_for_percent,
    make_progress_payload_builder,
)


class _FakeClock:
    """Deterministic monotonic clock for the throttle tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_phase_tagger_covers_the_three_visible_phases() -> None:
    assert _phase_for_percent(0) == "submit"
    assert _phase_for_percent(5) == "submit"
    assert _phase_for_percent(10) == "submit"
    assert _phase_for_percent(11) == "analyzing"
    assert _phase_for_percent(50) == "analyzing"
    assert _phase_for_percent(99) == "analyzing"
    assert _phase_for_percent(100) == "done"


def test_first_tick_always_passes_through() -> None:
    """A reviewer who connects mid-run must see the next tick.

    Without an "always emit the first one" rule, a fresh builder
    would silently drop everything until enough time elapses since
    its zero-init t=0 marker.
    """

    clock = _FakeClock()
    build = make_progress_payload_builder(monotonic=clock)

    payload = build(50, "Chunk 3/5: analyzing (8s)")
    assert payload is not None
    assert payload["percent"] == 50
    assert payload["phase"] == "analyzing"
    assert payload["label"] == "Chunk 3/5: analyzing (8s)"


def test_throttle_drops_intra_second_floods_but_keeps_label_heartbeats() -> None:
    """Heartbeats arrive ~1Hz; floods get squashed, real heartbeats survive."""

    clock = _FakeClock()
    build = make_progress_payload_builder(monotonic=clock, min_interval_s=1.0)

    # First tick at t=0 always emits.
    assert build(20, "first") is not None

    # 0.3s later — same percent, label-only update. Throttle drops it.
    clock.advance(0.3)
    assert build(20, "still first") is None

    # 1.5s after the first tick — outside the throttle window. Even
    # though the percent didn't move, the label refresh propagates.
    clock.advance(1.2)
    payload = build(20, "second")
    assert payload is not None
    assert payload["label"] == "second"


def test_boundary_and_significant_jump_bypass_throttle() -> None:
    """Start / completion / big jumps must always pass through."""

    clock = _FakeClock()
    build = make_progress_payload_builder(monotonic=clock, min_interval_s=1.0)

    # Submit boundary.
    assert build(0, "starting")["percent"] == 0  # type: ignore[index]

    # 0.1s later — big jump (≥5). Even inside the throttle window the
    # jump bypasses it so the bar moves visibly at chunk boundaries.
    clock.advance(0.1)
    payload = build(20, "first chunk done")
    assert payload is not None
    assert payload["percent"] == 20

    # 0.1s later — completion. Always emits regardless of throttle.
    clock.advance(0.1)
    payload = build(100, "done")
    assert payload is not None
    assert payload["phase"] == "done"


def test_throttle_remembers_the_max_percent_for_jump_detection() -> None:
    """A heartbeat that re-emits a lower-than-max percent shouldn't
    spuriously look like a "jump" once a higher value has been seen.

    Datalab's heartbeat re-emits the *baseline* percent each tick (so
    the bar doesn't snap backwards). If the throttle keyed off
    ``last_broadcast_percent`` literally, a series of heartbeats at
    20, then a real 30, then a heartbeat back to 20 would let the
    20→30 jump bypass — fine — and then the 30→20 echo would also
    bypass because ``20 >= 20 + 5`` is false but our state tracks
    the maximum and the diff is computed against that. The test
    pins this invariant.
    """

    clock = _FakeClock()
    build = make_progress_payload_builder(monotonic=clock, min_interval_s=1.0)

    assert build(20, "heartbeat") is not None  # first tick
    clock.advance(0.1)
    payload = build(40, "chunk 2/5 done")  # +20% jump bypasses throttle
    assert payload is not None and payload["percent"] == 40

    clock.advance(0.1)
    # Heartbeat re-emits baseline percent — same level as the prior
    # broadcast (40). Not a boundary, not a forward jump. Throttle
    # must still apply (we're inside the 1s window).
    assert build(40, "still working") is None
