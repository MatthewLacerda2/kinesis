"""Pinch detection and per-hand state. Pure: landmarks in, Hand states out.

No camera, no Qt, no MediaPipe imports -- everything here is driven by plain
sequences of (label, landmarks) so it can be tested with synthetic input.
"""

from __future__ import annotations

import math

from .filters import OneEuroFilter, Vec2Filter
from .protocol import Hand, Tuning

# MediaPipe hand landmark indices.
THUMB_TIP = 4
INDEX_TIP = 8
WRIST = 0
MIDDLE_MCP = 9

Landmarks = list[tuple[float, float]]


def pinch_ratio(landmarks: Landmarks) -> tuple[float, float]:
    """Return (ratio, hand_scale).

    ratio = |thumb_tip - index_tip| / |wrist - middle_mcp|

    Dividing by hand size is what makes the pinch work at any distance from the
    camera: both distances shrink together as the hand moves away.
    """
    d = math.dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP])
    scale = math.dist(landmarks[WRIST], landmarks[MIDDLE_MCP])
    if scale <= 1e-6:
        return 999.0, 0.0
    return d / scale, scale


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


class HandFilterState:
    """Per-hand smoothing + latched pinch state.

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

    def update(self, detections: list[tuple[str, Landmarks]], t: float,
               include_landmarks: bool = False) -> list[Hand]:
        """Advance one frame.

        detections: (handedness_label, landmarks) with landmarks already in
        mirrored frame space, normalized 0..1.
        t: capture time in seconds (real elapsed time drives the filters).
        """
        tuning = self.tuning
        seen: set[str] = set()
        hands: list[Hand] = []

        for label, landmarks in detections:
            if label in seen:
                continue  # MediaPipe occasionally reports two of the same label
            seen.add(label)

            state = self._states.get(label)
            if state is None:
                state = self._states[label] = HandFilterState(tuning)

            ratio, raw_scale = pinch_ratio(landmarks)
            smooth_xy = state.point(pinch_point(landmarks), t)
            smooth_scale = state.scale(raw_scale, t)

            # Schmitt trigger. A single threshold flickers around the boundary
            # and makes you drop images constantly.
            if state.pinching:
                state.pinching = ratio <= tuning.pinch_open
            else:
                state.pinching = ratio < tuning.pinch_close

            hands.append(Hand(
                handedness=label,
                pinch_xy=map_to_canvas(smooth_xy, tuning),
                raw_xy=smooth_xy,
                pinch_ratio=ratio,
                pinching=state.pinching,
                hand_scale=smooth_scale,
                landmarks=list(landmarks) if include_landmarks else None,
            ))

        # A hand that left the frame must not keep a stale latched pinch; its
        # filters restart clean so it doesn't glide in from the old position.
        for label, state in self._states.items():
            if label not in seen:
                state.reset()

        return hands
