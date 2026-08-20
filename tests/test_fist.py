"""Closing the whole hand, the second spelling of the same grab (#34).

Same synthetic hand as the pinch tests, closed by the `curl` parameter instead
of the fingertip gap. What these check is that the two spellings are one verb:
either latches, neither flickers, and tightening one into the other never lets
go of the image in between.
"""

import math

import pytest

from kinesis.tracking.gestures import GestureEngine, fist_ratio, pinch_ratio
from kinesis.tracking.protocol import Tuning
from tests.handmodel import FIST_GAP_M, OPEN_M, PINCHED_M, det, feed, world_hand

TILTS = (0, 10, 20, 30, 40, 50, 60, 70, 80)

# What MediaPipe world landmarks actually read, off photographs of real hands at
# 5 to 87 degrees off-axis. The thresholds were derived from these, so they are
# written down here: a change to either default has to be argued against them.
MEASURED_FISTS = (0.302, 0.305, 0.327, 0.358, 0.359, 0.431)
MEASURED_OPEN = (0.741, 0.759, 0.769, 0.775, 0.781, 0.782, 0.784, 0.948, 0.979)
MEASURED_NEITHER = (0.533, 0.564)          # thumbs-up, pointing: must not grab


def projected_curl(detection) -> float:
    """The same measurement taken on the 2D landmarks -- the mistake #32 fixed."""
    lm = detection.landmarks
    palm = math.dist(lm[0], lm[9])
    return sum(math.dist(lm[t], lm[m]) for t, m in
               zip((8, 12, 16, 20), (5, 9, 13, 17))) / (4 * palm)


# ---------- the measurement ----------

def test_fist_ratio_is_free_of_hand_size():
    """A bigger hand making the same shape must give the same ratio."""
    small = world_hand(FIST_GAP_M, 1.0)
    big = [tuple(c * 1.4 for c in p) for p in small]
    assert fist_ratio(small) == pytest.approx(fist_ratio(big))


def test_a_closed_hand_reads_far_below_an_open_one():
    assert fist_ratio(world_hand(FIST_GAP_M, 1.0)) < 0.5 * fist_ratio(world_hand(OPEN_M))


def test_the_synthetic_hand_reads_where_real_ones_do():
    """Guard on the fixture: if it does not land in the measured range, the
    thresholds below are being tested against a hand that does not exist."""
    assert min(MEASURED_FISTS) <= fist_ratio(world_hand(FIST_GAP_M, 1.0)) <= 0.38
    assert min(MEASURED_OPEN) <= fist_ratio(world_hand(OPEN_M)) <= max(MEASURED_OPEN)


def test_degenerate_palm_does_not_divide_by_zero():
    pts = world_hand(FIST_GAP_M, 1.0)
    pts[0] = pts[9]                    # wrist and MCP coincide
    assert fist_ratio(pts) == 999.0


def test_defaults_sit_between_the_measured_clusters():
    """The derivation, pinned. Close is above every fist ever measured and well
    below the poses that must not grab; open is below the loosest open hand."""
    t = Tuning()
    assert max(MEASURED_FISTS) < t.fist_close < min(MEASURED_NEITHER)
    assert t.fist_close < t.fist_open < min(MEASURED_OPEN)


# ---------- orientation: the #32 lesson, applied to the fist ----------

def test_a_closed_hand_latches_at_every_palm_angle():
    for tilt in TILTS:
        engine = GestureEngine(Tuning())
        hand = feed(engine, [FIST_GAP_M] * 3, curl=1.0, tilt=tilt)[-1]
        assert hand.pinching, (tilt, hand.fist_ratio)
        assert hand.grip == "fist", (tilt, hand.pinch_ratio)


def test_an_open_hand_latches_nothing_at_any_palm_angle():
    for tilt in TILTS:
        engine = GestureEngine(Tuning())
        hand = feed(engine, [OPEN_M] * 3, tilt=tilt)[-1]
        assert not hand.pinching, (tilt, hand.fist_ratio)
        assert hand.grip == ""


def test_the_projection_really_does_foreshorten_the_curl():
    """Guard on the fixture, not on the code.

    A fist turned toward the lens is the worst case of the #32 projection
    defect: every fingertip collapses onto the knuckle it is measured against.
    If the synthetic hand did not reproduce that, the two tests above would pass
    for the wrong reason and prove nothing about reading the metric landmarks.
    """
    curls = [projected_curl(det(FIST_GAP_M, 1.0, tilt=t)) for t in TILTS]
    assert max(curls) > 5.0 * min(curls), curls


# ---------- one verb, two spellings ----------

def closing_fist(steps: int = 20):
    """A pinch tightening into a fist, as one continuous motion.

    The assumption is stated rather than hidden: the thumb leaves the index tip
    in step with the index curling away from it, so the gap grows from a pinch
    (14 mm) to what a clenched fist measures (36 mm) as the fingers close. That
    is the ordering real fists support at both ends -- they read 0.29-0.42 on
    the pinch ratio and 0.30-0.36 on the fist ratio -- and it is the only part
    of the motion no photograph can settle. A hand that flings the thumb open
    first and closes afterwards is two gestures, and is not this test.
    """
    return [(PINCHED_M + (FIST_GAP_M - PINCHED_M) * i / steps, i / steps)
            for i in range(steps + 1)]


def test_a_pinch_tightening_into_a_fist_is_one_continuous_grab():
    """The image must not be dropped and re-grabbed part-way through.

    A completed fist reads above pinch_open, so the pinch does let go on the way
    in -- the fist has to have taken over before it does.
    """
    states = [h.pinching for h in feed(GestureEngine(Tuning()), closing_fist())]
    assert all(states), states


def test_the_two_spellings_overlap_where_they_hand_over():
    """Not a coincidence to be discovered later: there is a stretch of the
    motion where both measurements hold the grab, and that is what carries it."""
    grips = [h.grip for h in feed(GestureEngine(Tuning()), closing_fist())]
    assert grips[0] == "pinch" and grips[-1] == "fist"
    assert "both" in grips, grips
    # pinch, then both, then fist -- in that order and once each.
    assert [g for i, g in enumerate(grips) if i == 0 or g != grips[i - 1]] == [
        "pinch", "both", "fist"]


def test_a_fist_made_from_an_open_hand_grabs_without_a_pinch():
    """The gesture the user reached for: no pinch anywhere in the motion."""
    poses = [(OPEN_M, c / 10) for c in range(11)]
    hands = feed(GestureEngine(Tuning()), poses)
    assert not hands[0].pinching
    assert hands[-1].pinching and hands[-1].grip == "fist"
    assert all(h.pinch_ratio > Tuning().pinch_close for h in hands)


# ---------- hysteresis ----------

def test_holds_a_fist_through_the_hysteresis_band():
    """Between close and open, a held fist must not release."""
    engine = GestureEngine(Tuning())
    feed(engine, [(FIST_GAP_M, 1.0)] * 3)                  # latch
    for curl in (0.75, 0.70, 0.72, 0.78):                  # ratio 0.46..0.53
        hand = feed(engine, [(FIST_GAP_M, curl)], start=1.0)[0]
        assert Tuning().fist_close < hand.fist_ratio < Tuning().fist_open
        assert hand.pinching, hand.fist_ratio


def test_releases_only_above_the_open_threshold():
    engine = GestureEngine(Tuning())
    feed(engine, [(FIST_GAP_M, 1.0)] * 3)
    assert feed(engine, [(FIST_GAP_M, 0.55)], start=1.0)[0].pinching       # 0.62
    assert not feed(engine, [(FIST_GAP_M, 0.45)], start=2.0)[0].pinching   # 0.67


def test_no_flicker_closing_and_opening():
    """Curling in and straightening out must produce exactly one on and one off."""
    curls = [0.0, 0.4, 0.6, 0.8, 1.0, 0.8, 0.6, 0.4, 0.0]
    states = [h.pinching for h in
              feed(GestureEngine(Tuning()), [(OPEN_M, c) for c in curls])]
    transitions = sum(1 for a, b in zip(states, states[1:]) if a != b)
    assert transitions == 2, states


# ---------- per-hand state ----------

def test_a_lost_hand_clears_its_latched_fist():
    engine = GestureEngine(Tuning())
    feed(engine, [(FIST_GAP_M, 1.0)] * 3)
    engine.update([], 1.0)                                 # hand gone
    hand = engine.update([det(OPEN_M)], 1.1)[0]
    assert not hand.pinching and hand.grip == ""


def test_each_hand_keeps_its_own_spelling():
    """Two hands grabbing, one with each gesture, is a scale like any other."""
    engine = GestureEngine(Tuning())
    hands = engine.update([det(PINCHED_M, 0.0, label="Left"),
                           det(FIST_GAP_M, 1.0, label="Right")], 0.0)
    by_label = {h.handedness: h for h in hands}
    assert by_label["Left"].pinching and by_label["Left"].grip == "pinch"
    assert by_label["Right"].pinching and by_label["Right"].grip == "fist"


def test_ratios_are_reported_for_both_spellings():
    """The tuning panel and the overlay can only show what crosses the queue."""
    hand = feed(GestureEngine(Tuning()), [(FIST_GAP_M, 1.0)])[0]
    world = world_hand(FIST_GAP_M, 1.0)
    assert hand.pinch_ratio == pytest.approx(pinch_ratio(world))
    assert hand.fist_ratio == pytest.approx(fist_ratio(world))
