"""Two fists on empty canvas: pan and zoom the view rather than an image.

Split out of `hand_control` because it is the one hand gesture that never
touches an item -- it reads two cursors and drives the view transform, and
nothing about it needs the grab table or the scene. Keeping it here leaves the
grab state machine reading as one thing instead of two interleaved ones, and is
what kept that module under the size cap when the grab rules grew.

It owns no cursors of its own: `update` is handed the live cursor table each
tick and ends the gesture itself the moment either hand stops pinching or
disappears, so a caller cannot leave a stale pan running.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF

# The view's zoom is clamped here rather than at the call site: a gesture that
# can run for as long as two hands stay closed is exactly where an unbounded
# multiply ends up as a blank screen nobody can navigate back from.
MIN_ZOOM, MAX_ZOOM = 0.02, 64.0


class CanvasGesture:
    """A pan/zoom in progress, or nothing at all."""

    def __init__(self, view):
        self.view = view
        self._state: dict | None = None

    @property
    def active(self) -> bool:
        return self._state is not None

    def holds(self, label: str) -> bool:
        return self._state is not None and label in self._state["labels"]

    def clear(self) -> None:
        self._state = None

    def begin(self, ca, cb) -> None:
        mid_vp = QPointF((ca.x + cb.x) / 2, (ca.y + cb.y) / 2)
        self._state = {
            "labels": (ca.label, cb.label),
            "ref_dist": max(1e-6, math.hypot(cb.x - ca.x, cb.y - ca.y)),
            "ref_scene": self.view.mapToScene(int(mid_vp.x()), int(mid_vp.y())),
            "ref_zoom": self.view.transform().m11(),
        }

    def update(self, cursors: dict) -> None:
        state = self._state
        if state is None:
            return
        label_a, label_b = state["labels"]
        ca, cb = cursors.get(label_a), cursors.get(label_b)
        if ca is None or cb is None or not (ca.pinching and cb.pinching):
            self._state = None
            return

        dist = math.hypot(cb.x - ca.x, cb.y - ca.y)
        zoom = state["ref_zoom"] * (dist / state["ref_dist"])
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))

        self.view.resetTransform()
        self.view.scale(zoom, zoom)

        # Keep the scene point that was under the midpoint pinned to the midpoint.
        mid_vp = QPointF((ca.x + cb.x) / 2, (ca.y + cb.y) / 2)
        vp_center = self.view.viewport().rect().center()
        ref = state["ref_scene"]
        self.view.centerOn(ref.x() - (mid_vp.x() - vp_center.x()) / zoom,
                           ref.y() - (mid_vp.y() - vp_center.y()) / zoom)
