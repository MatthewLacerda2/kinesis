"""Painted on-canvas buttons: the top-left strip and the bottom-right bin.

Drawn inside BoardChrome.draw_foreground rather than as child widgets -- the
viewport is a QOpenGLWidget and a stacked transparent widget composites badly
over it (see CLAUDE.md).

It lives here rather than with the rest of the canvas painting because the
top-left corner is a toolbar now, not a one-off rectangle: layout, hit test and
glyph are one concern, and keeping them together means adding a button is a name
in TOP_LEFT, a glyph, and a signal to fire -- not a fourth hardcoded rect and a
fourth near-identical `is_over_x`.
"""

from __future__ import annotations

from typing import Container

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen

SIZE = 52
MARGIN = 18
GAP = 10

# Left to right along the top edge; this order is the layout.
TOP_LEFT = ("camera", "add_images")

BODY = QColor(52, 54, 60)
BODY_ON = QColor(80, 150, 230)
BODY_ARMED = QColor(200, 90, 80)
GLYPH = QColor(150, 156, 168)
GLYPH_ON = QColor(240, 246, 255)
GLYPH_ARMED = QColor(255, 235, 232)
STRIKE = QColor(200, 90, 80)


def top_left_rect(name: str) -> QRect:
    """Viewport rect of one top-left button, by name."""
    index = TOP_LEFT.index(name)
    return QRect(MARGIN + index * (SIZE + GAP), MARGIN, SIZE, SIZE)


def hit_top_left(pos: QPoint) -> str | None:
    """Name of the top-left button under a viewport point, or None."""
    for name in TOP_LEFT:
        if top_left_rect(name).contains(pos):
            return name
    return None


def trash_rect(viewport: QRect) -> QRect:
    """Viewport rect of the bin, pinned to the bottom-right corner."""
    return QRect(viewport.width() - SIZE - MARGIN,
                 viewport.height() - SIZE - MARGIN, SIZE, SIZE)


def draw_top_left(painter: QPainter, active: Container[str] = ()) -> None:
    """Paint the strip; buttons named in `active` get the lit treatment."""
    for name in TOP_LEFT:
        on = name in active
        box = top_left_rect(name)
        _body(painter, box, BODY_ON if on else BODY)
        painter.setPen(QPen(GLYPH_ON if on else GLYPH, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _GLYPHS[name](painter, box, on)


def draw_trash(painter: QPainter, viewport: QRect, armed: bool) -> None:
    box = trash_rect(viewport)
    _body(painter, box, BODY_ARMED if armed else BODY)
    painter.setPen(QPen(GLYPH_ARMED if armed else GLYPH, 1.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _bin_glyph(painter, box)


def _body(painter: QPainter, box: QRect, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(box, 9, 9)


def _camera_glyph(painter: QPainter, box: QRect, on: bool) -> None:
    """Body, lens, viewfinder bump -- struck through while off."""
    cx, cy = box.center().x(), box.center().y()
    painter.drawRoundedRect(QRect(cx - 11, cy - 7, 22, 16), 3, 3)
    painter.drawLine(cx - 5, cy - 7, cx - 3, cy - 11)
    painter.drawLine(cx + 3, cy - 11, cx + 5, cy - 7)
    painter.drawLine(cx - 3, cy - 11, cx + 3, cy - 11)
    painter.drawEllipse(QPoint(cx, cy + 1), 5, 5)
    if not on:
        # Struck through while off, so the state reads at a glance.
        painter.setPen(QPen(STRIKE, 2))
        painter.drawLine(cx - 13, cy + 11, cx + 13, cy - 13)


def _add_images_glyph(painter: QPainter, box: QRect, on: bool) -> None:
    """A picture frame with a plus in it."""
    cx, cy = box.center().x(), box.center().y()
    painter.drawRoundedRect(QRect(cx - 11, cy - 9, 22, 18), 3, 3)
    painter.drawLine(cx - 5, cy, cx + 5, cy)
    painter.drawLine(cx, cy - 5, cx, cy + 5)


def _bin_glyph(painter: QPainter, box: QRect) -> None:
    """Lid, body, two ribs."""
    cx, cy = box.center().x(), box.center().y()
    w, h = 18, 20
    painter.drawLine(cx - w // 2 - 2, cy - h // 2, cx + w // 2 + 2, cy - h // 2)
    painter.drawLine(cx - 4, cy - h // 2 - 4, cx + 4, cy - h // 2 - 4)
    painter.drawLine(cx - 4, cy - h // 2 - 4, cx - 4, cy - h // 2)
    painter.drawLine(cx + 4, cy - h // 2 - 4, cx + 4, cy - h // 2)
    painter.drawLine(cx - w // 2, cy - h // 2, cx - w // 2 + 2, cy + h // 2)
    painter.drawLine(cx + w // 2, cy - h // 2, cx + w // 2 - 2, cy + h // 2)
    painter.drawLine(cx - w // 2 + 2, cy + h // 2, cx + w // 2 - 2, cy + h // 2)
    painter.drawLine(cx - 3, cy - h // 2 + 5, cx - 3, cy + h // 2 - 3)
    painter.drawLine(cx + 3, cy - h // 2 + 5, cx + 3, cy + h // 2 - 3)


_GLYPHS = {"camera": _camera_glyph, "add_images": _add_images_glyph}
