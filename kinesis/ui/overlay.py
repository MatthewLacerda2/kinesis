"""Debug overlay: picture-in-picture camera preview, hand cursors, HUD.

Drawn inside BoardView.drawForeground rather than as a stacked child widget --
the viewport is a QOpenGLWidget, and painting into it directly avoids the
compositing problems an overlapping transparent widget runs into.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap

# Cursor colours by handedness, so you can tell which hand is which.
HAND_COLORS = {
    "Left": QColor(255, 176, 66),
    "Right": QColor(96, 214, 255),
}
DEFAULT_COLOR = QColor(200, 200, 200)

CURSOR_RADIUS = 17
PIP_W, PIP_H = 288, 216
PIP_MARGIN = 14

# MediaPipe hand skeleton.
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


def hand_color(label: str) -> QColor:
    return HAND_COLORS.get(label, DEFAULT_COLOR)


def draw_cursors(painter: QPainter, cursors: list) -> None:
    """Draw one ring per hand at its viewport position; filled while pinching."""
    for cursor in cursors:
        color = hand_color(cursor.label)
        center = QPoint(int(cursor.x), int(cursor.y))

        if cursor.pinching:
            painter.setPen(QPen(color, 2))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 150))
            painter.drawEllipse(center, CURSOR_RADIUS - 4, CURSOR_RADIUS - 4)
        else:
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, CURSOR_RADIUS, CURSOR_RADIUS)
            # Small centre dot marks the exact pick point.
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(center, 2, 2)

        if cursor.grabbing:
            painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, CURSOR_RADIUS + 7, CURSOR_RADIUS + 7)


def draw_pip(painter: QPainter, viewport: QRect, jpeg: bytes | None,
             hands: list, tuning) -> None:
    """Camera feed with the skeleton drawn, bottom-left, plus the active rect."""
    if not jpeg:
        return
    image = QImage.fromData(jpeg, "JPG")
    if image.isNull():
        return

    box = QRect(PIP_MARGIN, viewport.height() - PIP_H - PIP_MARGIN, PIP_W, PIP_H)
    painter.setOpacity(0.95)
    painter.drawPixmap(box, QPixmap.fromImage(image))
    painter.setOpacity(1.0)
    painter.setPen(QPen(QColor(90, 95, 105), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(box)

    # The active sub-rectangle: inside this region your hand reaches the whole canvas.
    active = QRectF(
        box.x() + tuning.rect_x0 * box.width(),
        box.y() + tuning.rect_y0 * box.height(),
        (tuning.rect_x1 - tuning.rect_x0) * box.width(),
        (tuning.rect_y1 - tuning.rect_y0) * box.height(),
    )
    painter.setPen(QPen(QColor(150, 160, 175), 1, Qt.PenStyle.DashLine))
    painter.drawRect(active)

    for hand in hands:
        if not hand.landmarks:
            continue
        color = hand_color(hand.handedness)
        pts = [QPoint(int(box.x() + x * box.width()), int(box.y() + y * box.height()))
               for x, y in hand.landmarks]
        painter.setPen(QPen(color if hand.pinching else QColor(220, 220, 220), 1))
        for a, b in CONNECTIONS:
            painter.drawLine(pts[a], pts[b])
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        for p in pts:
            painter.drawEllipse(p, 2, 2)


def draw_hud(painter: QPainter, viewport: QRect, fps: float, latency_ms: float,
             hands: list, message: str = "") -> None:
    """Tracker FPS, end-to-end latency and per-hand pinch ratio, top-right."""
    font = QFont()
    font.setPixelSize(12)
    painter.setFont(font)

    lines = [f"tracker {fps:4.1f} fps    latency {latency_ms:5.1f} ms"]
    for hand in hands:
        lines.append(
            f"{hand.handedness:<5} ratio {hand.pinch_ratio:5.3f}"
            f"  {'PINCH' if hand.pinching else '     '}"
        )
    if not hands:
        lines.append("no hands detected")
    if message:
        lines.append(message)

    width = max(painter.fontMetrics().horizontalAdvance(line) for line in lines) + 16
    height = len(lines) * 16 + 10
    box = QRect(viewport.width() - width - PIP_MARGIN, PIP_MARGIN, width, height)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(18, 19, 22, 190))
    painter.drawRoundedRect(box, 5, 5)

    painter.setPen(QColor(225, 228, 235))
    y = box.y() + 18
    for i, line in enumerate(lines):
        if i > 0 and i <= len(hands):
            painter.setPen(hand_color(hands[i - 1].handedness))
        else:
            painter.setPen(QColor(225, 228, 235))
        painter.drawText(box.x() + 8, y, line)
        y += 16
