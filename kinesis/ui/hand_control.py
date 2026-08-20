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
from .canvas_gesture import CanvasGesture

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
        # Labels holding an image they never closed on -- see _begin_grab.
        self._fallback: set[str] = set()
        self.canvas_gesture = CanvasGesture(view)

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

        # A grab claims the board: while one hand holds an image, that image is
        # the only thing either hand can act on. So the second hand joins the
        # scale from wherever it happens to be, instead of having to find the
        # picture first -- two hands on one image was the common case, and
        # making the second hand hunt for a target it already knows was work
        # the gesture was asking for no reason.
        for other_label, (item, _) in list(self._grabs.items()):
            if other_label == cursor.label:
                continue
            if other_label in self._fallback and not self._hits(item, scene_pos):
                # The other hand only picked this up because it was selected,
                # and this one closed on nothing too. Two fists on empty canvas
                # means the board, so hand off -- leaving the image where it
                # got to rather than snapping it back, which would be a jump
                # nobody asked for.
                self._end_fallback_grab(other_label)
                break
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

        # Nothing under the hand: a fist means the selected image, wherever it
        # is. The offset is captured the same way as a real grab, so the image
        # moves by however far the hand moves instead of teleporting to it --
        # this is a fist closing on empty canvas, and a picture that jumped
        # across the board every time one did would be unusable.
        selected = self._selected_image()
        if selected is not None and not self._grabs:
            self._grabs[cursor.label] = (selected, selected.pos() - scene_pos)
            self._fallback.add(cursor.label)
            cursor.grabbing = True
            return

        # Both hands pinching empty canvas: pan and zoom the view instead.
        others = [c for c in self.cursors.values()
                  if c is not cursor and c.pinching and not c.grabbing]
        if others and not self._grabs:
            self.canvas_gesture.begin(others[0], cursor)

    def _selected_image(self) -> ImageItem | None:
        """The image a fist means when it closes on nothing.

        Frontmost wins when several are selected: the board has no notion of
        an ordering over a multi-selection, and the one on top is the one the
        user last put there.
        """
        chosen = [i for i in self.board.selectedItems() if isinstance(i, ImageItem)]
        return max(chosen, key=lambda i: i.zValue()) if chosen else None

    def _end_fallback_grab(self, label: str) -> None:
        """Give an image picked up off-target back, and clear the selection.

        Dropping the selection is the point: while something is selected a fist
        on empty canvas means *move it*, so this is the only way back to a
        canvas gesture without reaching for the mouse.
        """
        self._grabs.pop(label, None)
        self._fallback.discard(label)
        cursor = self.cursors.get(label)
        if cursor is not None:
            cursor.grabbing = False
        self.board.clearSelection()

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
                self._fallback.discard(label)
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
        self._fallback.discard(cursor.label)
        cursor.grabbing = False
        cursor.pinching = False
        if self.canvas_gesture.holds(cursor.label):
            self.canvas_gesture.clear()

    def _release_all(self) -> None:
        self._grabs.clear()
        self._fallback.clear()
        self._two_hand = None
        self.canvas_gesture.clear()
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
        self._two_hand = {
            "item": item,
            "labels": (label_a, label_b),
            # Reference state captured at the transition frame, so the image
            # does not jump at the moment the second hand joins.
            "ref_dist": max(1e-6, math.hypot(pb.x() - pa.x(), pb.y() - pa.y())),
            "ref_a": pa,
            # Scaling pivots on the holding hand when that hand is on the
            # picture, and on the image's own centre when it isn't -- which is
            # the case for a hand that picked the image up off-target. Either
            # way the pivot is a point of the image, never one out in empty
            # canvas, because a pivot out there throws the image across the
            # board as it grows.
            "piv": pa if self._hits(item, pa) else item.pos(),
            "p0": item.pos(),
            "s0": item.scale(),
        }
        self._grabs[label_b] = (item, item.pos() - pb)

    def _sync_two_hand(self) -> None:
        self.canvas_gesture.update(self.cursors)
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

        item = state["item"]
        item.setScale(max(1e-4, state["s0"] * f))
        # The holding hand carries the image and the second hand only sizes it:
        # the image translates by that hand's movement, and scales about a
        # pivot fixed in the picture (see _begin_two_hand). The midpoint
        # between the hands used to be the pivot, which was the same thing
        # while both hands were on the picture -- and the second hand can now
        # join from anywhere.
        ref_a, piv, p0 = state["ref_a"], state["piv"], state["p0"]
        item.setPos(piv.x() + (pa.x() - ref_a.x()) - (piv.x() - p0.x()) * f,
                    piv.y() + (pa.y() - ref_a.y()) - (piv.y() - p0.y()) * f)

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
