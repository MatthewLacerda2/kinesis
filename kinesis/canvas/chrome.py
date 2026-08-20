"""Everything BoardView paints on top of (and behind) the board.

All of it is painted rather than built from child widgets: the viewport is a
QOpenGLWidget and a stacked transparent widget composites badly over it (see
CLAUDE.md), so the trash target, the corner toolbar, the hand cursors, the HUD
and the webcam background are all drawn inside the view's paint callbacks.

It is its own module because that makes painted chrome a growth area -- every
future overlay lands here by the same constraint -- and view.py already owns
every interaction path. Keeping the two fused meant the file that grows without
bound was also the file that handles input.

Chrome reads board state, it never mutates it. The state it owns is its own
(the webcam frame, the camera button's lit/unlit state, the hand overlay pushed
in by the 60Hz tick); anything the input handlers decide -- selection, marquee,
whether the bin is armed -- is read back off the view.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ..ui import buttons, overlay

SELECT_COLOR = QColor(120, 190, 255)
MARQUEE_FILL = QColor(120, 190, 255, 40)


class BoardChrome:
    def __init__(self, view):
        self.view = view

        # Webcam background: newest frame, or None for the plain dark board.
        self._bg_image = None
        self.camera_on = False

        # Hand-tracking overlay state, pushed in by HandControl each UI tick.
        self._hand_frame = None
        self._hand_cursors: list = []
        self._hand_fps = 0.0
        self._hand_latency = 0.0
        self._hand_tuning = None
        self._hand_message = ""

    # ---------- camera background ----------

    def set_background_image(self, image) -> None:
        """Newest webcam frame, or None to fall back to the dark background."""
        self._bg_image = image
        self.view.viewport().update()

    def set_camera_on(self, on: bool) -> None:
        """Button state; tracks the request, not the first frame's arrival."""
        self.camera_on = on
        if not on:
            self._bg_image = None
        self.view.viewport().update()

    # ---------- hand overlay ----------

    def set_hand_overlay(self, frame, cursors, fps: float, latency_ms: float,
                         tuning=None, message: str = "") -> None:
        """Called from the 60Hz hand-tracking tick."""
        self._hand_frame = frame
        self._hand_cursors = cursors
        self._hand_fps = fps
        self._hand_latency = latency_ms
        self._hand_message = message
        if tuning is not None:
            self._hand_tuning = tuning
        self.view.viewport().update()

    def clear_hand_overlay(self) -> None:
        self._hand_frame = None
        self._hand_cursors = []
        self._hand_tuning = None
        # Tracking stopped, so nothing is being held over the bin any more.
        self.view.trash_armed = False
        self.view.viewport().update()

    # ---------- painting ----------

    def draw_background(self, painter: QPainter) -> bool:
        """Paint the webcam feed behind the board. False means there is no
        frame and the caller should fall back to QGraphicsView's own."""
        image = self._bg_image
        if image is None or image.isNull():
            return False

        painter.save()
        painter.resetTransform()  # the feed is screen-fixed; it must not pan or zoom
        vp = self.view.viewport().rect()
        painter.fillRect(vp, self.view.board.backgroundBrush())

        # Cover the viewport, centre-cropped, so the feed never letterboxes.
        scale = max(vp.width() / image.width(), vp.height() / image.height())
        src_w = min(image.width(), vp.width() / scale)
        src_h = min(image.height(), vp.height() / scale)
        source = QRectF((image.width() - src_w) / 2, (image.height() - src_h) / 2,
                        src_w, src_h)
        painter.drawImage(QRectF(vp), image, source)
        painter.restore()
        return True

    def draw_foreground(self, painter: QPainter) -> None:
        view = self.view
        painter.save()
        painter.resetTransform()  # draw in viewport px so overlays keep a fixed size

        sel = view.selection_rect()
        if not sel.isNull():
            tl, br = view.mapFromScene(sel.topLeft()), view.mapFromScene(sel.bottomRight())
            painter.setPen(QPen(SELECT_COLOR, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRect(tl, br))

        if view.marquee is not None:
            painter.setPen(QPen(SELECT_COLOR, 1, Qt.PenStyle.DashLine))
            painter.setBrush(MARQUEE_FILL)
            painter.drawRect(view.marquee.normalized())

        buttons.draw_trash(painter, view.viewport().rect(), view.trash_armed)
        buttons.draw_top_left(painter, ("camera",) if self.camera_on else ())

        if self._hand_tuning is not None:
            vp = view.viewport().rect()
            if self._hand_frame is not None:
                overlay.draw_pip(painter, vp, self._hand_frame.jpeg,
                                 self._hand_frame.hands, self._hand_tuning)
            overlay.draw_cursors(painter, self._hand_cursors)
            overlay.draw_hud(painter, vp, self._hand_fps, self._hand_latency,
                             self._hand_frame.hands if self._hand_frame else [],
                             self._hand_message)

        painter.restore()
