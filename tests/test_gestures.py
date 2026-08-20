"""Synthetic landmark sequences in, state transitions out.

The hands here are built in metres and then projected through a pinhole camera,
because that is the order the real pipeline works in and it is the only way the
orientation tests can mean anything: a fixture that made up the 2D and the 3D
landmarks independently could be made to agree with whatever it was asked to
prove.
"""

import math

import pytest

from kinesis.tracking.gestures import (
    Detection,
    GestureEngine,
    hand_scale,
    map_to_canvas,
    pinch_point,
    pinch_ratio,
)
from kinesis.tracking.protocol import Tuning

PALM_M = 0.095        # wrist -> middle MCP, measured off a real detected hand
PINCHED_M = 0.014     # fingertips together: 0.147 of the palm
OPEN_M = 0.050        # fingertips apart: 0.53 of the palm
FRAME_W, FRAME_H = 640, 480                            # 4:3, as the camera runs
FOCAL = (FRAME_W / 2) / math.tan(math.radians(30))     # ~60 deg horizontal FOV
DEPTH = 0.50          # metres from the lens


def world_hand(gap: float, tilt: float = 0.0, roll: float = 0.0):
    """21 metric landmarks: `gap` metres between the fingertips.

    `tilt` turns the hand toward the lens, so the palm axis foreshortens under
    projection while the fingertip gap does not -- the geometry of #32. `roll`
    turns the pinch axis within the palm plane, from across the palm at 0 to
    along it at 90, which is what the per-axis 2D normalization treats unevenly.
    Only the four points the maths reads are placed; the rest sit mid-palm.
    """
    a, r = math.radians(tilt), math.radians(roll)
    up = (0.0, -math.cos(a), -math.sin(a))             # wrist -> fingers
    axis = (math.cos(r),                                # thumb tip -> index tip
            math.sin(r) * up[1],
            math.sin(r) * up[2])
    mmcp = tuple(PALM_M * c for c in up)
    mid = tuple(1.5 * PALM_M * c for c in up)           # fingertips, past the MCPs
    pts = [tuple(c / 2 for c in mmcp)] * 21
    pts[0] = (0.0, 0.0, 0.0)
    pts[9] = mmcp
    pts[4] = tuple(m - c * gap / 2 for m, c in zip(mid, axis))
    pts[8] = tuple(m + c * gap / 2 for m, c in zip(mid, axis))
    return pts


def project(world, at=None, depth: float = DEPTH):
    """Pinhole-project metres to normalized frame coords, per axis over 4:3.

    Dividing x by 640 and y by 480 is what MediaPipe hands back, and is where
    the 1.33x anisotropy in the old projected ratio came from.
    """
    pts = [((FOCAL * x / (z + depth) + FRAME_W / 2) / FRAME_W,
            (FOCAL * y / (z + depth) + FRAME_H / 2) / FRAME_H) for x, y, z in world]
    if at is None:
        return pts
    mid = pinch_point(pts)
    return [(x + at[0] - mid[0], y + at[1] - mid[1]) for x, y in pts]


def det(gap: float = OPEN_M, tilt: float = 0.0, roll: float = 0.0,
        at=(0.5, 0.4), depth: float = DEPTH, label: str = "Right") -> Detection:
    world = world_hand(gap, tilt, roll)
    return Detection(label, project(world, at, depth), world)


def projected_ratio(detection: Detection) -> float:
    """The old formula, on the 2D landmarks -- kept to show the defect is real."""
    lm = detection.landmarks
    return math.dist(lm[4], lm[8]) / math.dist(lm[0], lm[9])


def feed(engine, gaps, dt=1 / 30, start=0.0, **kw):
    """Run a sequence of gaps through the engine, returning each Hand."""
    out, t = [], start
    for gap in gaps:
        hands = engine.update([det(gap, **kw)], t)
        out.append(hands[0] if hands else None)
        t += dt
    return out


# ---------- ratio maths ----------

def test_ratio_is_free_of_hand_size():
    """A bigger hand making the same shape must give the same ratio."""
    small = world_hand(0.014)
    big = [tuple(c * 1.4 for c in p) for p in small]
    assert pinch_ratio(small) == pytest.approx(pinch_ratio(big))


def test_open_hand_ratio_exceeds_pinched():
    assert pinch_ratio(world_hand(OPEN_M)) > pinch_ratio(world_hand(PINCHED_M))


def test_cursor_is_midpoint_of_thumb_and_index():
    assert pinch_point(det(at=(0.4, 0.3)).landmarks) == pytest.approx((0.4, 0.3))


def test_hand_scale_shrinks_with_distance():
    """It is the depth proxy, so it must be the projected length, not the metric one."""
    near = hand_scale(det(depth=0.4).landmarks)
    far = hand_scale(det(depth=0.8).landmarks)
    assert far == pytest.approx(near / 2, rel=0.02)


def test_degenerate_palm_does_not_divide_by_zero():
    pts = world_hand(PINCHED_M)
    pts[0] = pts[9]                    # wrist and MCP coincide
    assert pinch_ratio(pts) == 999.0


# ---------- orientation: the point of the exercise ----------

TILTS = (0, 10, 20, 30, 40, 50, 60, 70, 80)
ROLLS = (0, 30, 45, 60, 90)


def test_pinch_verdict_holds_at_every_palm_angle():
    """The same physical gap must decide the same way however the hand is held.

    This is exactly what the projected ratio could not do: past ~20 degrees of
    turn toward the lens the trigger could not fire at all, no matter how hard
    you squeezed.
    """
    for tilt in TILTS:
        engine = GestureEngine(Tuning())
        assert feed(engine, [PINCHED_M] * 3, tilt=tilt)[-1].pinching, tilt
        engine = GestureEngine(Tuning())
        assert not feed(engine, [OPEN_M] * 3, tilt=tilt)[-1].pinching, tilt


def test_pinch_verdict_holds_at_every_pinch_axis_angle():
    """Closing the fingers vertically must read the same as closing them across."""
    for roll in ROLLS:
        engine = GestureEngine(Tuning())
        assert feed(engine, [PINCHED_M] * 3, roll=roll)[-1].pinching, roll


def test_the_projection_really_does_foreshorten():
    """Guard on the fixture, not on the code.

    If the synthetic hand did not reproduce the defect, the two tests above
    would pass for the wrong reason and prove nothing.
    """
    ratios = [projected_ratio(det(PINCHED_M, tilt=t)) for t in TILTS]
    assert max(ratios) > 2.5 * min(ratios), ratios
    rolled = [projected_ratio(det(PINCHED_M, roll=r)) for r in ROLLS]
    assert max(rolled) > 1.3 * min(rolled), rolled


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

BAND = Tuning(pinch_close=0.30, pinch_open=0.45)


def test_pinch_latches_below_close_threshold():
    engine = GestureEngine(Tuning())
    out = feed(engine, [OPEN_M, OPEN_M, PINCHED_M])
    assert [h.pinching for h in out] == [False, False, True]


def test_holds_pinch_through_the_hysteresis_band():
    """Between close and open, an active pinch must NOT release.

    This is the whole point of the Schmitt trigger: a single threshold flickers
    here and drops the image.
    """
    engine = GestureEngine(BAND)
    states = [h.pinching for h in feed(engine, [PINCHED_M] * 3)]
    for gap in [0.030, 0.036, 0.042, 0.030]:       # ratio 0.32..0.44
        h = feed(engine, [gap], start=1.0)[0]
        assert 0.30 < h.pinch_ratio < 0.45, h.pinch_ratio
        states.append(h.pinching)
    assert all(states), states


def test_releases_only_above_open_threshold():
    engine = GestureEngine(BAND)
    feed(engine, [PINCHED_M] * 3)                  # latch
    assert feed(engine, [0.042], start=1.0)[0].pinching        # ratio 0.44
    assert not feed(engine, [0.045], start=2.0)[0].pinching    # ratio 0.47


def test_no_flicker_across_the_band():
    """Sweeping down and back up must produce exactly one on and one off."""
    gaps = [0.060, 0.040, 0.036, 0.032, 0.019, 0.032, 0.036, 0.040, 0.060]
    states = [h.pinching for h in feed(GestureEngine(BAND), gaps)]
    transitions = sum(1 for a, b in zip(states, states[1:]) if a != b)
    assert transitions == 2, states


# ---------- hand identity ----------

def test_hands_tracked_by_label_not_index():
    """Swapping list order must not swap the hands' latched states."""
    engine = GestureEngine(Tuning())
    engine.update([det(PINCHED_M, label="Left"), det(OPEN_M, label="Right")], 0.0)
    # Same poses, reversed order in the list.
    hands = engine.update(
        [det(OPEN_M, label="Right"), det(PINCHED_M, label="Left")], 1 / 30)
    by_label = {h.handedness: h for h in hands}
    assert by_label["Left"].pinching is True
    assert by_label["Right"].pinching is False


def test_duplicate_labels_are_ignored():
    engine = GestureEngine(Tuning())
    hands = engine.update([det(PINCHED_M), det(OPEN_M)], 0.0)
    assert len(hands) == 1


def test_lost_hand_clears_its_latched_pinch():
    """A hand that leaves frame must not come back still pinching."""
    engine = GestureEngine(Tuning())
    feed(engine, [PINCHED_M] * 3)
    engine.update([], 1.0)                         # hand gone
    assert not engine.update([det(OPEN_M)], 1.1)[0].pinching


# ---------- smoothing is applied ----------

def test_output_position_is_smoothed():
    """A jumpy input position should produce a steadier output."""
    engine = GestureEngine(Tuning())
    xs_in, xs_out, t = [], [], 0.0
    for i in range(40):
        x = 0.5 + (0.02 if i % 2 == 0 else -0.02)
        xs_in.append(x)
        xs_out.append(engine.update([det(at=(x, 0.4))], t)[0].raw_xy[0])
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
        out.append(engine.update([det(at=(x, 0.4))], t)[0].raw_xy[0])
        t += 1 / 30
    tail = out[-20:]
    amplitude = max(tail) - min(tail)
    assert amplitude < 0.002, f"resting jitter not attenuated enough: {amplitude:.4f}"
