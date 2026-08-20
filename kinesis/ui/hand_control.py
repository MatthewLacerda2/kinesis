"""Drives the canvas from hand data: cursor interpolation and close-to-grab.

Owns the tracker process lifetime, the 60Hz UI timer, and the grab state
machine. Gestures affect only this canvas -- the real macOS cursor is never
touched and no Accessibility permission is involved.
"""

from __future__ import annotations

import math
import queue
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QPointF, QTimer, Signal

from ..canvas.items import ImageItem
from ..tracking.protocol import HandFrame, SetTuning, Stop, TrackerStatus, Tuning
from ..tracking.worker import start_tracker

# 120Hz: the camera is capped at 30fps, but ticking faster cuts the wait
# between a frame arriving and it reaching the screen (and matches ProMotion).
UI_HZ = 120


@dataclass
class Cursor:
    """A hand's on-screen cursor, in viewport pixels."""
    label: str
    x: float = 0.0
    y: float = 0.0
    pinching: bool = False
    grabbing: bool = False
    initialized: bool = False


class HandControl(QObject):
    status_changed = Signal(str, str)   # state, message

    def __init__(self, view, tuning: Tuning, parent=None):
        super().__init__(parent)
        self.view = view
        self.board = view.board
        self.tuning = tuning

        self.active = False
        self.proc = None
        self.frames_q = None
        self.control_q = None

        self.cursors: dict[str, Cursor] = {}
        self.latest: HandFrame | None = None
        # Per hand label, not one clock for all hands: a single clock refreshed
        # by any visible hand never expires while one hand stays, so a hand that
        # left the frame kept its grab and went on scaling as a ghost (#33).
        self.last_seen: dict[str, float] = {}
        self.fps = 0.0

        # label -> (item, offset from cursor to item origin, in scene coords)
        self._grabs: dict[str, tuple[ImageItem, QPointF]] = {}

        # Set while both hands pinch the same image; captured at the transition
        # frame so joining or leaving the second hand never jumps the image.
        self._two_hand: dict | None = None
        self._canvas_gesture: dict | None = None

        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / UI_HZ))
        self.timer.timeout.connect(self._tick)

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self.active:
            return
        self.proc, self.frames_q, self.control_q = start_tracker(self.tuning)
        self.active = True
        self.timer.start()
        self.status_changed.emit("starting", "starting camera…")

    def stop(self) -> None:
        if not self.active:
            return
        self.timer.stop()
        self.active = False
        self._release_all()
        if self.control_q is not None:
            try:
                self.control_q.put_nowait(Stop())
            except (queue.Full, OSError):
                pass
        if self.proc is not None:
            self.proc.join(timeout=1.5)
            if self.proc.is_alive():
                self.proc.terminate()
        self.proc = self.frames_q = self.control_q = None
        self.cursors.clear()
        self.last_seen.clear()
        self.latest = None
        self.view.chrome.clear_hand_overlay()
        self.status_changed.emit("stopped", "hand tracking off")

    def toggle(self) -> None:
        self.stop() if self.active else self.start()

    def push_tuning(self, tuning: Tuning) -> None:
        self.tuning = tuning
        if self.control_q is not None:
            try:
                self.control_q.put_nowait(SetTuning(tuning))
            except (queue.Full, OSError):
                pass

    # ---------- per-frame ----------

    def _drain(self) -> None:
        """Take only the newest frame; never work through a backlog."""
        if self.frames_q is None:
            return
        while True:
            try:
                msg = self.frames_q.get_nowait()
            except (queue.Empty, OSError):
                break
            if isinstance(msg, TrackerStatus):
                self.status_changed.emit(msg.state, msg.message)
                if msg.state in ("error", "stopped"):
                    self.timer.stop()
                    self.active = False
                    self._release_all()
                continue
            if isinstance(msg, HandFrame):
                self.latest = msg

    def _lost(self, label: str, now: float) -> bool:
        """Has this hand been gone longer than the hold window?"""
        last = self.last_seen.get(label, 0.0)
        return (now - last) * 1000.0 > self.tuning.lost_hold_ms

    def _all_lost(self, now: float) -> bool:
        """Same question for every hand at once: has the most recent one expired?"""
        last = max(self.last_seen.values(), default=0.0)
        return (now - last) * 1000.0 > self.tuning.lost_hold_ms

    def _tick(self) -> None:
        self._drain()
        frame = self.latest
        now = time.perf_counter()

        if frame is None:
            self.view.viewport().update()
            return

        self.fps = frame.fps
        # Latency is not measured here any more. The span this tick could time
        # ends before the easing below and long before the paint, so it read a
        # fifth of the real budget; chrome stamps it where it actually ends.

        seen = {h.handedness for h in frame.hands}
        for label in seen:
            self.last_seen[label] = now
        viewport = self.view.viewport().rect()
        alpha = max(0.05, min(1.0, self.tuning.lerp_alpha))

        for hand in frame.hands:
            cursor = self.cursors.get(hand.handedness)
            if cursor is None:
                cursor = self.cursors[hand.handedness] = Cursor(hand.handedness)
            target_x = hand.pinch_xy[0] * viewport.width()
            target_y = hand.pinch_xy[1] * viewport.height()
            if not cursor.initialized:
                cursor.x, cursor.y, cursor.initialized = target_x, target_y, True
            else:
                # Camera is ~30fps, UI is 60: ease toward the newest sample so
                # motion reads smoothly. Kept light -- One Euro does the real work.
                cursor.x += alpha * (target_x - cursor.x)
                cursor.y += alpha * (target_y - cursor.y)

            was_pinching = cursor.pinching
            cursor.pinching = hand.pinching
            if hand.pinching and not was_pinching:
                self._begin_grab(cursor)
            elif was_pinching and not hand.pinching:
                self._end_grab(cursor)

        # Hands that vanished: hold briefly before releasing, so a dropped
        # detection doesn't fling images around. Each hand runs down its own
        # hold window, so one hand staying in frame can no longer keep the
        # other's grab -- and its half of a two-hand scale -- alive forever.
        if not frame.hands and self._grabs and self._all_lost(now):
            self._release_all()
        for label in list(self.cursors):
            if label not in seen and label in self._grabs and self._lost(label, now):
                self._end_grab(self.cursors[label], opened=False)

        self._sync_two_hand()
        self._apply_grabs()

        # Arm the bin when a held image is dragged over it.
        armed = False
        for label in self._grabs:
            cursor = self.cursors.get(label)
            if cursor and self.view.is_over_trash(QPointF(cursor.x, cursor.y)):
                armed = True
                break
        if armed != self.view.trash_armed:
            self.view.trash_armed = armed

        cursors = [c for label, c in self.cursors.items() if label in seen]
        self.view.chrome.set_hand_overlay(frame, cursors, self.fps, tuning=self.tuning)

    # ---------- grabbing ----------

    def _scene_pos(self, cursor: Cursor) -> QPointF:
        return self.view.mapToScene(int(cursor.x), int(cursor.y))

    @staticmethod
    def _hits(item: ImageItem, scene_pos: QPointF) -> bool:
        return (item.sceneBoundingRect().contains(scene_pos)
                and item.contains(item.mapFromScene(scene_pos)))

    def _begin_grab(self, cursor: Cursor) -> None:
        scene_pos = self._scene_pos(cursor)

        # Second hand pinching an image the other hand already holds: promote to
        # a two-hand scale rather than starting an independent grab.
        for other_label, (item, _) in list(self._grabs.items()):
            if other_label == cursor.label:
                continue
            if self._hits(item, scene_pos):
                self._begin_two_hand(other_label, cursor.label, item)
                cursor.grabbing = True
                return

        held = {item for item, _ in self._grabs.values()}
        # Images only: a pinch moves and scales a picture. What a hand does to a
        # non-image item is that item's question to answer, not this loop's.
        for item in sorted(self.board.image_items(), key=lambda i: -i.zValue()):
            if item in held:
                continue
            if self._hits(item, scene_pos):
                # Keep the offset so the image doesn't snap its centre to the cursor.
                self._grabs[cursor.label] = (item, item.pos() - scene_pos)
                self.board.bring_to_front(item)
                cursor.grabbing = True
                self.board.clearSelection()
                item.setSelected(True)
                return

        cursor.grabbing = False
        # Both hands pinching empty canvas: pan and zoom the view instead.
        others = [c for c in self.cursors.values()
                  if c is not cursor and c.pinching and not c.grabbing]
        if others and not self._grabs:
            self._begin_canvas_gesture(others[0], cursor)

    def _end_grab(self, cursor: Cursor, opened: bool = True) -> None:
        """End a grab. `opened` is False when the hand vanished rather than let go.

        A hand that left the frame never dropped anything, so a timeout must not
        count as a drop over the bin -- that would make a lost detection delete
        an image, which is exactly what the hold window exists to prevent.
        """
        entry = self._grabs.get(cursor.label)

        # Released over the bin: delete the image it was holding.
        if opened and entry is not None and self.view.is_over_trash(QPointF(cursor.x, cursor.y)):
            item = entry[0]
            for label in [lab for lab, (it, _) in list(self._grabs.items()) if it is item]:
                self._grabs.pop(label, None)
                other = self.cursors.get(label)
                if other is not None:
                    other.grabbing = False
            if self._two_hand and self._two_hand["item"] is item:
                self._two_hand = None
            self.board.remove_image(item)
            self.view.trash_armed = False
            cursor.grabbing = False
            cursor.pinching = False
            return

        if self._two_hand and cursor.label in self._two_hand["labels"]:
            self._end_two_hand(released=cursor.label)
        self._grabs.pop(cursor.label, None)
        cursor.grabbing = False
        cursor.pinching = False
        if self._canvas_gesture and cursor.label in self._canvas_gesture["labels"]:
            self._canvas_gesture = None

    def _release_all(self) -> None:
        self._grabs.clear()
        self._two_hand = None
        self._canvas_gesture = None
        for cursor in self.cursors.values():
            cursor.grabbing = False
            cursor.pinching = False
        if self.view.trash_armed:
            self.view.trash_armed = False

    def _apply_grabs(self) -> None:
        scaling = self._two_hand["item"] if self._two_hand else None
        for label, (item, offset) in self._grabs.items():
            if item is scaling:
                continue  # driven by the two-hand transform instead
            cursor = self.cursors.get(label)
            if cursor is None:
                continue
            item.setPos(self._scene_pos(cursor) + offset)

    # ---------- two-hand scale ----------

    def _begin_two_hand(self, label_a: str, label_b: str, item: ImageItem) -> None:
        ca, cb = self.cursors[label_a], self.cursors[label_b]
        pa, pb = self._scene_pos(ca), self._scene_pos(cb)
        mid = QPointF((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2)
        self._two_hand = {
            "item": item,
            "labels": (label_a, label_b),
            # Reference state captured at the transition frame, so the image
            # does not jump at the moment the second hand joins.
            "ref_dist": max(1e-6, math.hypot(pb.x() - pa.x(), pb.y() - pa.y())),
            "ref_mid": mid,
            "p0": item.pos(),
            "s0": item.scale(),
        }
        self._grabs[label_b] = (item, item.pos() - pb)

    def _sync_two_hand(self) -> None:
        if self._canvas_gesture is not None:
            self._update_canvas_gesture()
        if self._two_hand is None:
            return
        state = self._two_hand
        label_a, label_b = state["labels"]
        ca, cb = self.cursors.get(label_a), self.cursors.get(label_b)
        if ca is None or cb is None or not (ca.pinching and cb.pinching):
            return

        pa, pb = self._scene_pos(ca), self._scene_pos(cb)
        dist = math.hypot(pb.x() - pa.x(), pb.y() - pa.y())
        f = max(0.02, dist / state["ref_dist"])
        mid = QPointF((pa.x() + pb.x()) / 2, (pa.y() + pb.y()) / 2)

        item = state["item"]
        item.setScale(max(1e-4, state["s0"] * f))
        # Scale about the pinch midpoint, and follow it, so the image both
        # resizes and moves with the hands.
        item.setPos(mid.x() + (state["p0"].x() - state["ref_mid"].x()) * f,
                    mid.y() + (state["p0"].y() - state["ref_mid"].y()) * f)

    def _end_two_hand(self, released: str) -> None:
        """One hand let go: hand back to a single-hand drag with no jump."""
        state = self._two_hand
        self._two_hand = None
        if state is None:
            return
        item = state["item"]
        remaining = [lab for lab in state["labels"] if lab != released]
        for label in remaining:
            cursor = self.cursors.get(label)
            if cursor is None or not cursor.pinching:
                self._grabs.pop(label, None)
                continue
            # Recompute the offset from where the image actually is now.
            self._grabs[label] = (item, item.pos() - self._scene_pos(cursor))

    # ---------- two-hand canvas pan/zoom ----------

    def _begin_canvas_gesture(self, ca: Cursor, cb: Cursor) -> None:
        mid_vp = QPointF((ca.x + cb.x) / 2, (ca.y + cb.y) / 2)
        self._canvas_gesture = {
            "labels": (ca.label, cb.label),
            "ref_dist": max(1e-6, math.hypot(cb.x - ca.x, cb.y - ca.y)),
            "ref_scene": self.view.mapToScene(int(mid_vp.x()), int(mid_vp.y())),
            "ref_zoom": self.view.transform().m11(),
        }

    def _update_canvas_gesture(self) -> None:
        state = self._canvas_gesture
        label_a, label_b = state["labels"]
        ca, cb = self.cursors.get(label_a), self.cursors.get(label_b)
        if ca is None or cb is None or not (ca.pinching and cb.pinching):
            self._canvas_gesture = None
            return

        dist = math.hypot(cb.x - ca.x, cb.y - ca.y)
        zoom = state["ref_zoom"] * (dist / state["ref_dist"])
        zoom = max(0.02, min(64.0, zoom))

        self.view.resetTransform()
        self.view.scale(zoom, zoom)

        # Keep the scene point that was under the midpoint pinned to the midpoint.
        mid_vp = QPointF((ca.x + cb.x) / 2, (ca.y + cb.y) / 2)
        vp_center = self.view.viewport().rect().center()
        ref = state["ref_scene"]
        self.view.centerOn(ref.x() - (mid_vp.x() - vp_center.x()) / zoom,
                           ref.y() - (mid_vp.y() - vp_center.y()) / zoom)
