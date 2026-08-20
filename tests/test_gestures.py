"""Synthetic landmark sequences in, state transitions out."""

from kinesis.tracking.gestures import (
    GestureEngine,
    map_to_canvas,
    pinch_point,
    pinch_ratio,
)
from kinesis.tracking.protocol import Tuning


def hand(gap: float, wrist=(0.5, 0.8), mmcp=(0.5, 0.6), at=(0.5, 0.4)):
    """21 landmarks with thumb/index `gap` apart, centred on `at`."""
    pts = [(0.5, 0.5)] * 21
    pts[0] = wrist
    pts[9] = mmcp
    pts[4] = (at[0] - gap / 2, at[1])
    pts[8] = (at[0] + gap / 2, at[1])
    return pts


def feed(engine, gaps, dt=1 / 30, label="Right", start=0.0, **kw):
    """Run a sequence of gaps through the engine, returning each Hand."""
    out, t = [], start
    for gap in gaps:
        hands = engine.update([(label, hand(gap, **kw))], t)
        out.append(hands[0] if hands else None)
        t += dt
    return out


# ---------- ratio maths ----------

def test_ratio_is_depth_invariant():
    """Same pose at half the apparent size must give the same ratio."""
    near = pinch_ratio(hand(0.02, wrist=(0.5, 0.8), mmcp=(0.5, 0.6)))[0]
    far = pinch_ratio(hand(0.01, wrist=(0.5, 0.7), mmcp=(0.5, 0.6)))[0]
    assert abs(near - far) < 1e-9


def test_open_hand_ratio_exceeds_pinched():
    assert pinch_ratio(hand(0.25))[0] > pinch_ratio(hand(0.01))[0]


def test_cursor_is_midpoint_of_thumb_and_index():
    assert pinch_point(hand(0.2, at=(0.4, 0.3))) == (0.4, 0.3)


def test_degenerate_hand_scale_does_not_divide_by_zero():
    pts = hand(0.05)
    pts[0] = pts[9] = (0.5, 0.5)   # wrist and MCP coincide
    ratio, scale = pinch_ratio(pts)
    assert ratio == 999.0 and scale == 0.0


# ---------- active rectangle ----------

def test_active_rect_maps_and_clamps():
    t = Tuning(rect_x0=0.2, rect_x1=0.8, rect_y0=0.15, rect_y1=0.85)
    assert map_to_canvas((0.2, 0.15), t) == (0.0, 0.0)
    assert map_to_canvas((0.8, 0.85), t) == (1.0, 1.0)
    mid = map_to_canvas((0.5, 0.5), t)
    assert abs(mid[0] - 0.5) < 1e-9
    # Outside the active rect clamps rather than running off-canvas.
    assert map_to_canvas((0.0, 0.0), t) == (0.0, 0.0)
    assert map_to_canvas((1.0, 1.0), t) == (1.0, 1.0)


# ---------- hysteresis ----------

def test_pinch_latches_below_close_threshold():
    engine = GestureEngine(Tuning())
    out = feed(engine, [0.30, 0.30, 0.02])
    assert [h.pinching for h in out] == [False, False, True]


def test_holds_pinch_through_the_hysteresis_band():
    """Between close and open, an active pinch must NOT release.

    This is the whole point of the Schmitt trigger: a single threshold flickers
    here and drops the image.
    """
    engine = GestureEngine(Tuning(pinch_close=0.30, pinch_open=0.45))
    ratios = []
    # Latch first, then hover inside the band.
    for h in feed(engine, [0.02] * 3):
        ratios.append(h.pinching)
    for gap in [0.070, 0.080, 0.088, 0.070]:   # ratio 0.35..0.44
        h = feed(engine, [gap], start=1.0)[0]
        assert 0.30 < h.pinch_ratio < 0.45, h.pinch_ratio
        ratios.append(h.pinching)
    assert all(ratios), ratios


def test_releases_only_above_open_threshold():
    engine = GestureEngine(Tuning(pinch_close=0.30, pinch_open=0.45))
    feed(engine, [0.02] * 3)                       # latch
    still = feed(engine, [0.088], start=1.0)[0]    # ratio 0.44
    assert still.pinching
    released = feed(engine, [0.10], start=2.0)[0]  # ratio 0.50
    assert not released.pinching


def test_no_flicker_across_the_band():
    """Sweeping down and back up must produce exactly one on and one off."""
    engine = GestureEngine(Tuning(pinch_close=0.30, pinch_open=0.45))
    gaps = [0.20, 0.10, 0.08, 0.07, 0.02, 0.07, 0.08, 0.10, 0.20]
    states = [h.pinching for h in feed(engine, gaps)]
    transitions = sum(1 for a, b in zip(states, states[1:]) if a != b)
    assert transitions == 2, states


# ---------- hand identity ----------

def test_hands_tracked_by_label_not_index():
    """Swapping list order must not swap the hands' latched states."""
    engine = GestureEngine(Tuning())
    t = 0.0
    engine.update([("Left", hand(0.02)), ("Right", hand(0.30))], t)
    t += 1 / 30
    # Same poses, reversed order in the list.
    hands = engine.update([("Right", hand(0.30)), ("Left", hand(0.02))], t)
    by_label = {h.handedness: h for h in hands}
    assert by_label["Left"].pinching is True
    assert by_label["Right"].pinching is False


def test_duplicate_labels_are_ignored():
    engine = GestureEngine(Tuning())
    hands = engine.update([("Right", hand(0.02)), ("Right", hand(0.30))], 0.0)
    assert len(hands) == 1


def test_lost_hand_clears_its_latched_pinch():
    """A hand that leaves frame must not come back still pinching."""
    engine = GestureEngine(Tuning())
    feed(engine, [0.02] * 3)
    engine.update([], 1.0)                       # hand gone
    back = engine.update([("Right", hand(0.30))], 1.1)[0]
    assert not back.pinching


# ---------- smoothing is applied ----------

def test_output_position_is_smoothed():
    """A jumpy input position should produce a steadier output."""
    engine = GestureEngine(Tuning())
    xs_in, xs_out, t = [], [], 0.0
    for i in range(40):
        x = 0.5 + (0.02 if i % 2 == 0 else -0.02)
        xs_in.append(x)
        h = engine.update([("Right", hand(0.30, at=(x, 0.4)))], t)[0]
        xs_out.append(h.raw_xy[0])
        t += 1 / 30
    in_amp = max(xs_in[-10:]) - min(xs_in[-10:])
    out_amp = max(xs_out[-10:]) - min(xs_out[-10:])
    assert out_amp < in_amp / 2, (in_amp, out_amp)


def test_default_tuning_still_kills_resting_jitter():
    """Guard on the lag-reduction defaults.

    min_cutoff/beta were raised to cut perceived lag; if they are ever pushed
    far enough that a still hand stops being smoothed, the cursor buzzes at
    rest and pinches land in the wrong place. This pins that trade-off.
    """
    engine = GestureEngine(Tuning())
    t, out = 0.0, []
    for i in range(60):
        # A still hand, jittering +/- 0.004 (roughly what raw landmarks do at rest).
        x = 0.5 + (0.004 if i % 2 == 0 else -0.004)
        out.append(engine.update([("Right", hand(0.30, at=(x, 0.4)))], t)[0].raw_xy[0])
        t += 1 / 30
    tail = out[-20:]
    amplitude = max(tail) - min(tail)
    assert amplitude < 0.002, f"resting jitter not attenuated enough: {amplitude:.4f}"
