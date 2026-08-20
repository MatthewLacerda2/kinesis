"""Mouse + keyboard interaction for the board.

Scaling is a two-hand pinch gesture (see ui/hand_control.py), so there are no
corner handles on the canvas. Alt+drag is the mouse equivalent, keeping the app
fully usable without the camera. There is no rotation.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QGraphicsView

from ..ui import buttons, overlay
from .items import ImageItem, is_supported_image
from .scene import BoardScene

MIN_ZOOM, MAX_ZOOM = 0.02, 64.0

SELECT_COLOR = QColor(120, 190, 255)
MARQUEE_FILL = QColor(120, 190, 255, 40)


class BoardView(QGraphicsView):
    camera_button_clicked = Signal()
    add_images_clicked = Signal()

    def __init__(self, scene: BoardScene, parent=None):
        super().__init__(scene, parent)
        self.board = scene

        # GL viewport by default; KINESIS_NO_GL=1 falls back to raster, which is
        # also the only way to screenshot the canvas (QWidget.grab can't read a
        # QOpenGLWidget's framebuffer).
        if os.environ.get("KINESIS_NO_GL") != "1":
            self.setViewport(QOpenGLWidget())
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self._space_held = False
        self._panning = False
        self._pan_last = QPoint()
        self._marquee_origin: QPoint | None = None
        self._marquee: QRect | None = None
        self._scale_op: dict | None = None    # Alt+drag scaling
        self._dragging_items = False          # a plain mouse move of selected items

        # Set while something is held over the trash, by mouse or by hand.
        self.trash_armed = False

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

        # The painted top-left strip: button name -> what a click on it emits.
        self._corner_signals = {
            "camera": self.camera_button_clicked,
            "add_images": self.add_images_clicked,
        }

        scene.changed.connect(lambda *_: self.viewport().update())
        scene.selectionChanged.connect(self.viewport().update)

    # ---------- selection geometry ----------

    def selected_items(self) -> list[ImageItem]:
        """Selected images. Selected non-image items are excluded on purpose:
        every caller here (Alt+drag scale, delete, z-order) is an image op."""
        return [i for i in self.board.selectedItems() if isinstance(i, ImageItem)]

    def _selection_rect(self) -> QRectF:
        rect = QRectF()
        for item in self.selected_items():
            r = item.sceneBoundingRect()
            rect = r if rect.isNull() else rect.united(r)
        return rect

    # ---------- trash ----------

    def trash_rect(self) -> QRect:
        return buttons.trash_rect(self.viewport().rect())

    def is_over_trash(self, pos: QPoint | QPointF) -> bool:
        point = pos.toPoint() if isinstance(pos, QPointF) else pos
        return self.trash_rect().contains(point)

    # ---------- camera background ----------

    def set_background_image(self, image) -> None:
        """Newest webcam frame, or None to fall back to the dark background."""
        self._bg_image = image
        self.viewport().update()

    def set_camera_on(self, on: bool) -> None:
        """Button state; tracks the request, not the first frame's arrival."""
        self.camera_on = on
        if not on:
            self._bg_image = None
        self.viewport().update()

    # ---------- painting ----------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        image = self._bg_image
        if image is None or image.isNull():
            super().drawBackground(painter, rect)
            return

        painter.save()
        painter.resetTransform()  # the feed is screen-fixed; it must not pan or zoom
        vp = self.viewport().rect()
        painter.fillRect(vp, self.board.backgroundBrush())

        # Cover the viewport, centre-cropped, so the feed never letterboxes.
        scale = max(vp.width() / image.width(), vp.height() / image.height())
        src_w = min(image.width(), vp.width() / scale)
        src_h = min(image.height(), vp.height() / scale)
        source = QRectF((image.width() - src_w) / 2, (image.height() - src_h) / 2,
                        src_w, src_h)
        painter.drawImage(QRectF(vp), image, source)
        painter.restore()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        painter.save()
        painter.resetTransform()  # draw in viewport px so overlays keep a fixed size

        sel = self._selection_rect()
        if not sel.isNull():
            tl, br = self.mapFromScene(sel.topLeft()), self.mapFromScene(sel.bottomRight())
            painter.setPen(QPen(SELECT_COLOR, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRect(tl, br))

        if self._marquee is not None:
            painter.setPen(QPen(SELECT_COLOR, 1, Qt.PenStyle.DashLine))
            painter.setBrush(MARQUEE_FILL)
            painter.drawRect(self._marquee.normalized())

        buttons.draw_trash(painter, self.viewport().rect(), self.trash_armed)
        buttons.draw_top_left(painter, ("camera",) if self.camera_on else ())

        if self._hand_tuning is not None:
            vp = self.viewport().rect()
            if self._hand_frame is not None:
                overlay.draw_pip(painter, vp, self._hand_frame.jpeg,
                                 self._hand_frame.hands, self._hand_tuning)
            overlay.draw_cursors(painter, self._hand_cursors)
            overlay.draw_hud(painter, vp, self._hand_fps, self._hand_latency,
                             self._hand_frame.hands if self._hand_frame else [],
                             self._hand_message)

        painter.restore()

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
        self.viewport().update()

    def clear_hand_overlay(self) -> None:
        self._hand_frame = None
        self._hand_cursors = []
        self._hand_tuning = None
        self.trash_armed = False
        self.viewport().update()

    # ---------- zoom ----------

    def zoom_by(self, factor: float, anchor_pos: QPoint | None = None) -> None:
        current = self.transform().m11()
        target = current * factor
        if target < MIN_ZOOM:
            factor = MIN_ZOOM / current
        elif target > MAX_ZOOM:
            factor = MAX_ZOOM / current
        if abs(factor - 1.0) < 1e-9:
            return
        if anchor_pos is None:
            self.scale(factor, factor)
            return
        before = self.mapToScene(anchor_pos)
        self.scale(factor, factor)
        after = self.mapToScene(anchor_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        self.zoom_by(math.pow(1.0015, delta), event.position().toPoint())
        event.accept()

    def viewportEvent(self, event) -> bool:
        # Trackpad pinch arrives as a native gesture, not a wheel event.
        if event.type() == QEvent.Type.NativeGesture:
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_by(1.0 + event.value(), event.position().toPoint())
                return True
        return super().viewportEvent(event)

    def zoom_to_fit(self) -> None:
        rect = self.board.content_rect()
        if rect.isNull():
            self.resetTransform()
            self.centerOn(0, 0)
            return
        self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom_100(self) -> None:
        anchor = self.mapToScene(self.viewport().rect().center())
        self.resetTransform()
        self.centerOn(anchor)

    # ---------- mouse ----------

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()

        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self._space_held
        ):
            self._panning = True
            self._pan_last = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # Top-left strip: camera background, add images.
            hit = buttons.hit_top_left(pos)
            if hit is not None:
                self._corner_signals[hit].emit()
                event.accept()
                return

            # Clicking the bin deletes the current selection.
            if self.is_over_trash(pos):
                self.delete_selection()
                event.accept()
                return

            item = self.itemAt(pos)
            if item is not None and event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self._begin_scale(pos)
                event.accept()
                return

            if item is None:
                # Empty canvas: start a marquee instead of Qt's own rubber band,
                # which doesn't play well with the OpenGL viewport.
                if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    self.board.clearSelection()
                self._marquee_origin = pos
                self._marquee = QRect(pos, pos)
                event.accept()
                return

            self._dragging_items = True

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()

        if self._panning:
            delta = pos - self._pan_last
            self._pan_last = pos
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return

        if self._scale_op is not None:
            self._update_scale(pos)
            event.accept()
            return

        if self._marquee_origin is not None:
            self._marquee = QRect(self._marquee_origin, pos)
            self.viewport().update()
            event.accept()
            return

        if self._dragging_items:
            armed = self.is_over_trash(pos)
            if armed != self.trash_armed:
                self.trash_armed = armed
                self.viewport().update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space_held
                           else Qt.CursorShape.ArrowCursor)
            event.accept()
            return

        if self._scale_op is not None:
            self._scale_op = None
            self.board.board_changed.emit()
            event.accept()
            return

        if self._marquee_origin is not None:
            rect = self._marquee.normalized() if self._marquee else QRect()
            if rect.width() > 3 and rect.height() > 3:
                scene_rect = self.mapToScene(rect).boundingRect()
                for item in self.board.board_items():
                    if item.sceneBoundingRect().intersects(scene_rect):
                        item.setSelected(True)
            self._marquee_origin = None
            self._marquee = None
            self.viewport().update()
            event.accept()
            return

        if self._dragging_items:
            self._dragging_items = False
            if self.trash_armed:
                self.trash_armed = False
                self.delete_selection()
                event.accept()
                return

        super().mouseReleaseEvent(event)

    def delete_selection(self) -> int:
        items = self.selected_items()
        for item in items:
            self.board.remove_image(item)
        return len(items)

    # ---------- Alt+drag scale (mouse equivalent of the two-hand pinch) ----------

    def _begin_scale(self, pos: QPoint) -> None:
        items = self.selected_items()
        if not items:
            item = self.itemAt(pos)
            if not isinstance(item, ImageItem):
                return
            self.board.clearSelection()
            item.setSelected(True)
            items = [item]
        center = self._selection_rect().center()
        start = self.mapToScene(pos)
        self._scale_op = {
            "center": center,
            "start_dist": max(1e-6, math.hypot(start.x() - center.x(),
                                               start.y() - center.y())),
            # Snapshot up front: the whole drag is computed from the original
            # state, never accumulated frame to frame, so it cannot drift.
            "items": [(i, i.pos(), i.scale()) for i in items],
        }

    def _update_scale(self, pos: QPoint) -> None:
        op = self._scale_op
        cur = self.mapToScene(pos)
        center = op["center"]
        dist = math.hypot(cur.x() - center.x(), cur.y() - center.y())
        f = max(0.02, dist / op["start_dist"])
        for item, p0, s0 in op["items"]:
            item.setScale(max(1e-4, s0 * f))
            item.setPos(center.x() + (p0.x() - center.x()) * f,
                        center.y() + (p0.y() - center.y()) * f)
        self.viewport().update()

    # ---------- keyboard ----------

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if not self._panning:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ---------- drag & drop ----------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasImage():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        mime = event.mimeData()
        where = self.mapToScene(event.position().toPoint())
        added = 0
        if mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.is_dir():
                    for child in sorted(path.iterdir()):
                        if is_supported_image(child):
                            self.board.add_image(child, None)
                            added += 1
                    continue
                if is_supported_image(path):
                    self.board.add_image(path, where if added == 0 else None)
                    added += 1
        if added == 0 and mime.hasImage():
            image = mime.imageData()
            if image is not None:
                self.board.add_qimage(image, where)
                added += 1
        if added:
            event.acceptProposedAction()
