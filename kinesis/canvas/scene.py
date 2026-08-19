"""The board scene.

Every mutation of the board goes through this class -- drag-drop, paste, the
menu, and (later) the MCP server all call the same methods. Keeping that surface
here rather than in event handlers is what makes the board drivable from outside.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QGraphicsScene

from .items import ImageItem, is_supported_image

BACKGROUND = QColor(32, 33, 36)

# New images are normalised to this long edge in scene units, so a 6000px photo
# and a 400px sketch land on the board at comparable sizes.
DEFAULT_LONG_EDGE = 800.0

# Half-width of the (effectively infinite) scene.
SCENE_EXTENT = 1_000_000.0

# Clipboard images are written here so they have a path like any other item.
PASTE_DIR = Path.home() / ".cache" / "kinesis" / "pasted"


class BoardScene(QGraphicsScene):
    """Holds ImageItems and owns z-ordering and placement."""

    board_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-SCENE_EXTENT, -SCENE_EXTENT,
                                 SCENE_EXTENT * 2, SCENE_EXTENT * 2))
        self.setBackgroundBrush(BACKGROUND)
        self._next_z = 1.0

    # ---------- queries ----------

    def image_items(self) -> list[ImageItem]:
        """Board images in z-order (bottom first)."""
        return sorted(
            (i for i in self.items() if isinstance(i, ImageItem)),
            key=lambda i: i.zValue(),
        )

    def find(self, item_id: str) -> ImageItem | None:
        for item in self.image_items():
            if item.item_id == item_id:
                return item
        return None

    def content_rect(self) -> QRectF:
        rect = QRectF()
        for item in self.image_items():
            rect = item.sceneBoundingRect() if rect.isNull() else rect.united(item.sceneBoundingRect())
        return rect

    # ---------- mutation ----------

    def add_image(self, path: str | Path, pos: QPointF | None = None,
                  long_edge: float | None = DEFAULT_LONG_EDGE) -> ImageItem:
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"No such image: {path}")
        if not is_supported_image(path):
            raise ValueError(f"Unsupported image type: {path.suffix}")
        return self._place(ImageItem(str(path)), pos, long_edge)

    def add_qimage(self, image: QImage, pos: QPointF | None = None,
                   long_edge: float | None = DEFAULT_LONG_EDGE) -> ImageItem:
        """Add an in-memory image (clipboard paste).

        Written to the cache dir first so it gets a real path -- that keeps
        save/load and duplicate working the same way as a dropped file, instead
        of pasted images being second-class items that vanish on save.
        """
        PASTE_DIR.mkdir(parents=True, exist_ok=True)
        dest = PASTE_DIR / f"paste-{uuid.uuid4().hex[:12]}.png"
        if image.save(str(dest), "PNG"):
            return self.add_image(dest, pos, long_edge)
        return self._place(ImageItem(None, image=image), pos, long_edge)

    def _place(self, item: ImageItem, pos: QPointF | None, long_edge: float | None) -> ImageItem:
        w, h = item.natural_size()
        if long_edge:
            item.setScale(long_edge / max(w, h))
        self.addItem(item)
        item.setPos(pos if pos is not None else self._free_position(item))
        self.bring_to_front(item)
        self.board_changed.emit()
        return item

    def remove_image(self, item: ImageItem | str) -> bool:
        target = self.find(item) if isinstance(item, str) else item
        if target is None:
            return False
        self.removeItem(target)
        self.board_changed.emit()
        return True

    def clear_board(self) -> int:
        items = self.image_items()
        for item in items:
            self.removeItem(item)
        self._next_z = 1.0
        self.board_changed.emit()
        return len(items)

    def duplicate(self, item: ImageItem) -> ImageItem | None:
        if not item.source_path:
            return None  # pasted images have no path to re-read; skipped for now
        clone = ImageItem(item.source_path)
        clone.setScale(item.scale())
        clone.setRotation(item.rotation())
        self.addItem(clone)
        clone.setPos(item.pos() + QPointF(40, 40))
        self.bring_to_front(clone)
        self.board_changed.emit()
        return clone

    # ---------- z-order ----------

    def bring_to_front(self, item: ImageItem) -> None:
        self._next_z += 1.0
        item.setZValue(self._next_z)

    def send_to_back(self, item: ImageItem) -> None:
        lowest = min((i.zValue() for i in self.image_items()), default=0.0)
        item.setZValue(lowest - 1.0)

    # ---------- placement ----------

    def _free_position(self, item: ImageItem) -> QPointF:
        """Find a spot that doesn't cover an existing image.

        Walks an outward spiral from the origin. Matters most for the MCP path,
        where a batch of images gets added with no pointer position to anchor to.
        """
        existing = [i.sceneBoundingRect() for i in self.image_items() if i is not item]
        w, h = item.natural_size()
        s = item.scale()
        size_w, size_h = w * s, h * s
        step = max(size_w, size_h) * 1.1

        if not existing:
            return QPointF(0, 0)

        for ring in range(0, 40):
            for k in range(max(1, ring * 8)):
                if ring == 0:
                    cx = cy = 0.0
                else:
                    angle = 2 * math.pi * k / (ring * 8)
                    cx = math.cos(angle) * ring * step
                    cy = math.sin(angle) * ring * step
                candidate = QRectF(cx - size_w / 2, cy - size_h / 2, size_w, size_h)
                probe = candidate.adjusted(-20, -20, 20, 20)
                if not any(probe.intersects(r) for r in existing):
                    return QPointF(cx, cy)
        return QPointF(0, 0)
