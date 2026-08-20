"""Grab detection and per-hand state. Pure: landmarks in, Hand states out.

No camera, no Qt, no MediaPipe imports -- everything here is driven by plain
sequences of landmarks so it can be tested with synthetic input.

Two coordinate spaces arrive per hand and they are not interchangeable. The
normalized 2D landmarks are a projection: they are what the cursor and the
preview skeleton need, and they are the wrong place to decide a pinch. Turn the
hand toward the lens and the palm foreshortens while the fingertip gap, lying
across it, does not -- so a ratio of the two climbs without bound while the
gesture is unchanged, and the pinch becomes impossible to make (#32). The same
projection is normalized per axis over a 4:3 frame, which quietly makes a
vertical gap count 1.33x a horizontal one. The metric 3D world landmarks have
neither problem, because distance in metres is invariant to rotation and metric
space is isotropic, so every pinch decision here reads those.

Closing the whole hand is the second spelling of the same grab (#34), and it is
decided the same way and in the same space -- a fist held toward the lens is the
worst case of that projection, every fingertip collapsing onto the knuckle it is
measured against. There is one latch, not two: the two measurements are two ways
of reading one gesture, so either can take it and either can hold it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .filters import OneEuroFilter, Vec2Filter
from .protocol import Hand, Tuning

# MediaPipe hand landmark indices.
THUMB_TIP = 4
INDEX_TIP = 8
WRIST = 0
MIDDLE_MCP = 9

# Index, middle, ring and pinky, each tip paired with its own knuckle. The thumb
# is left out on purpose: it folds across the fingers rather than into the palm,
# so it says little about whether the hand is closed, and the pinch already
# reads it.
FINGER_TIPS = (8, 12, 16, 20)
FINGER_MCPS = (5, 9, 13, 17)

Landmarks = list[tuple[float, float]]
Landmarks3D = list[tuple[float, float, float]]


@dataclass(frozen=True)
class Detection:
    """One hand as the camera saw it, in both spaces, named so they can't swap.

    Both come out of the same MediaPipe result and never leave the tracker
    process: the queue still carries only the decided `Hand`.
    """

    handedness: str          # "Left" | "Right"
    landmarks: Landmarks     # normalized 0..1 in mirrored frame space
    world: Landmarks3D       # metres, origin near the centre of the hand


def pinch_ratio(world: Landmarks3D) -> float:
    """Fingertip gap over palm length, both metric.

        ratio = |thumb_tip - index_tip| / |wrist - middle_mcp|

    Dividing by hand size is what makes the pinch work at any distance from the
    camera and at any size of hand: both lengths are properties of the same
    hand. Taken in metres it also holds at any orientation -- the two distances
    are 3D, so neither foreshortens when the hand turns.
    """
    palm = math.dist(world[WRIST], world[MIDDLE_MCP])
    if palm <= 1e-6:
        return 999.0
    return math.dist(world[THUMB_TIP], world[INDEX_TIP]) / palm


def fist_ratio(world: Landmarks3D) -> float:
    """How far the four fingertips sit from their own knuckles, over palm length.

        ratio = mean(|tip_i - mcp_i|) / |wrist - middle_mcp|

    Each finger measured against its own knuckle, not against the wrist: on real
    landmarks the wrist estimate wanders when the forearm is hidden, and the two
    fists it was checked against read 0.90 and 1.30 from the wrist while reading
    0.358 and 0.359 from the knuckles -- the same hand shape, one number.

    Metric for the same reason the pinch is (#32), and more so: a fist pointed
    at the lens foreshortens every one of these four distances at once.
    """
    palm = math.dist(world[WRIST], world[MIDDLE_MCP])
    if palm <= 1e-6:
        return 999.0
    reach = sum(math.dist(world[t], world[m])
                for t, m in zip(FINGER_TIPS, FINGER_MCPS))
    return reach / (len(FINGER_TIPS) * palm)


def hand_scale(landmarks: Landmarks) -> float:
    """Projected palm length: it shrinks with distance, so it proxies depth.

    Deliberately the 2D one. The metric palm length is the same number however
    far away the hand is, which is exactly what makes it useless here.
    """
    return math.dist(landmarks[WRIST], landmarks[MIDDLE_MCP])


def pinch_point(landmarks: Landmarks) -> tuple[float, float]:
    """Midpoint of thumb and index tips -- the point the eye aims with."""
    ax, ay = landmarks[THUMB_TIP]
    bx, by = landmarks[INDEX_TIP]
    return (ax + bx) / 2.0, (ay + by) / 2.0


def map_to_canvas(xy: tuple[float, float], tuning: Tuning) -> tuple[float, float]:
    """Map the active sub-rectangle of the frame onto the full canvas, clamped.

    Without this you would have to stretch to the very edge of the camera's view
    to reach the corners of the screen.
    """
    x, y = xy
    span_x = max(1e-6, tuning.rect_x1 - tuning.rect_x0)
    span_y = max(1e-6, tuning.rect_y1 - tuning.rect_y0)
    u = (x - tuning.rect_x0) / span_x
    v = (y - tuning.rect_y0) / span_y
    return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))


def grab_state(held: bool, ratio: float, curl: float,
               tuning: Tuning) -> tuple[bool, str]:
    """One latch, two Schmitt triggers: is the hand closed, and by which spelling.

    Either measurement can take the grab (below its close threshold) and either
    can keep it (below its open threshold), which is what makes a fist a synonym
    for a pinch rather than a second verb. The latch is shared because the
    handover between them is real: a completed fist measures 0.29-0.42 on the
    pinch ratio -- above pinch_open -- so the pinch lets go part-way into a fist
    that the fist test has not taken yet. Latching each half separately would
    drop the image in that gap and grab it again a few frames later.

    Returns the latch and a label for it. The label is for the tuning panel and
    the overlay to show; nothing downstream may branch on it.
    """
    by_pinch = ratio <= tuning.pinch_open
    by_fist = curl <= tuning.fist_open
    if held:
        held = by_pinch or by_fist
    else:
        held = ratio < tuning.pinch_close or curl < tuning.fist_close
    if not held:
        return False, ""
    return True, "both" if by_pinch and by_fist else "pinch" if by_pinch else "fist"


class HandFilterState:
    """Per-hand smoothing + latched grab state.

    Keyed by handedness label by the engine, never by list index: indices swap
    between frames when hands cross or one drops out, which makes grabbed
    objects teleport between hands.
    """

    def __init__(self, tuning: Tuning) -> None:
        self.point = Vec2Filter(tuning.min_cutoff, tuning.beta, tuning.d_cutoff)
        self.scale = OneEuroFilter(tuning.min_cutoff, tuning.beta, tuning.d_cutoff)
        self.pinching = False

    def set_params(self, tuning: Tuning) -> None:
        self.point.set_params(tuning.min_cutoff, tuning.beta, tuning.d_cutoff)
        self.scale.min_cutoff = tuning.min_cutoff
        self.scale.beta = tuning.beta
        self.scale.d_cutoff = tuning.d_cutoff

    def reset(self) -> None:
        self.point.reset()
        self.scale.reset()
        self.pinching = False


class GestureEngine:
    """Turns raw per-frame detections into smoothed, hysteresis-latched Hands."""

    def __init__(self, tuning: Tuning | None = None) -> None:
        self.tuning = tuning or Tuning()
        self._states: dict[str, HandFilterState] = {}

    def set_tuning(self, tuning: Tuning) -> None:
        self.tuning = tuning
        for state in self._states.values():
            state.set_params(tuning)

    def reset(self) -> None:
        self._states.clear()

    def update(self, detections: list[Detection], t: float,
               include_landmarks: bool = False) -> list[Hand]:
        """Advance one frame.

        detections: one per hand, 2D landmarks already in mirrored frame space
        and normalized 0..1, world landmarks in metres.
        t: capture time in seconds (real elapsed time drives the filters).
        """
        tuning = self.tuning
        seen: set[str] = set()
        hands: list[Hand] = []

        for det in detections:
            if det.handedness in seen:
                continue  # MediaPipe occasionally reports two of the same label
            seen.add(det.handedness)

            state = self._states.get(det.handedness)
            if state is None:
                state = self._states[det.handedness] = HandFilterState(tuning)

            ratio = pinch_ratio(det.world)
            curl = fist_ratio(det.world)
            smooth_xy = state.point(pinch_point(det.landmarks), t)
            smooth_scale = state.scale(hand_scale(det.landmarks), t)

            # Schmitt triggers. A single threshold flickers around the boundary
            # and makes you drop images constantly.
            state.pinching, grip = grab_state(state.pinching, ratio, curl, tuning)

            hands.append(Hand(
                handedness=det.handedness,
                # A fist aims with the same point a pinch does, so the cursor
                # does not jump when one tightens into the other.
                pinch_xy=map_to_canvas(smooth_xy, tuning),
                raw_xy=smooth_xy,
                pinch_ratio=ratio,
                pinching=state.pinching,
                hand_scale=smooth_scale,
                group_delay_ms=state.point.group_delay * 1000.0,
                fist_ratio=curl,
                grip=grip,
                landmarks=list(det.landmarks) if include_landmarks else None,
            ))

        # A hand that left the frame must not keep a stale latched pinch; its
        # filters restart clean so it doesn't glide in from the old position.
        for label, state in self._states.items():
            if label not in seen:
                state.reset()

        return hands
