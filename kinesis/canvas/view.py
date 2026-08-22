"""Mouse + keyboard interaction for the board.

Scaling is a two-hand pinch gesture (see ui/hand_control.py), so there are no
corner handles on the canvas. Alt+drag is the mouse equivalent, keeping the app
fully usable without the camera. There is no rotation.

Interaction only: everything the view paints lives in canvas/chrome.py, and the
paint callbacks here do nothing but hand off to it. Painted chrome grows with
every overlay the app gains, and this file already owns every input path -- so
they are kept apart rather than growing together.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QGraphicsView

from ..ui import buttons
from .chrome import BoardChrome
from .items import BoardItem, is_supported_image
from .scene import BoardScene

MIN_ZOOM, MAX_ZOOM = 0.02, 64.0


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
        self.marquee: QRect | None = None     # read back by the chrome painter
        self._scale_op: dict | None = None    # Alt+drag scaling
        self._dragging_items = False          # a plain mouse move of selected items

        # Set while something is held over the trash, by mouse or by hand.
        self.trash_armed = False

        # Everything painted over and behind the board, and the state it owns.
        self.chrome = BoardChrome(self)

        # The painted top-left strip: button name -> what a click on it emits.
        self._corner_signals = {
            "camera": self.camera_button_clicked,
            "add_images": self.add_images_clicked,
        }

        scene.changed.connect(lambda *_: self.viewport().update())
        scene.selectionChanged.connect(self.viewport().update)

    # ---------- selection geometry ----------

    def selected_items(self) -> list[BoardItem]:
        """Selected board items, of any kind.

        Every caller here -- Alt+drag scale, delete, z-order -- is something a
        box wants as much as a picture does, and a kind that should not be
        aimed at is one that never gets selected in the first place.
        """
        return [i for i in self.board.selectedItems() if isinstance(i, BoardItem)]

    def selection_rect(self) -> QRectF:
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

    # ---------- painting ----------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        if not self.chrome.draw_background(painter):
            super().drawBackground(painter, rect)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        self.chrome.draw_foreground(painter)

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
                self.marquee = QRect(pos, pos)
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
            self.marquee = QRect(self._marquee_origin, pos)
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
            rect = self.marquee.normalized() if self.marquee else QRect()
            if rect.width() > 3 and rect.height() > 3:
                scene_rect = self.mapToScene(rect).boundingRect()
                for item in self.board.board_items():
                    if item.sceneBoundingRect().intersects(scene_rect):
                        item.setSelected(True)
            self._marquee_origin = None
            self.marquee = None
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
            self.board.remove_item(item)
        return len(items)

    # ---------- Alt+drag scale (mouse equivalent of the two-hand pinch) ----------

    def _begin_scale(self, pos: QPoint) -> None:
        items = self.selected_items()
        if not items:
            item = self.itemAt(pos)
            if not isinstance(item, BoardItem):
                return
            self.board.clearSelection()
            item.setSelected(True)
            items = [item]
        center = self.selection_rect().center()
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
