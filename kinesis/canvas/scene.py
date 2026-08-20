"""The board scene.

Every mutation of the board goes through this class -- drag-drop, paste, the
menu, and (later) the MCP server all call the same methods. Keeping that surface
here rather than in event handlers is what makes the board drivable from outside.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

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
    """Holds the board's items and owns z-ordering and placement.

    Two accessors, deliberately distinct: board_items() is the whole board and
    image_items() is the images on it. They return the same thing today, when an
    image is the only kind of item there is, and the pen strokes and handles that
    are coming are exactly why the call sites had to pick one on purpose.
    """

    board_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-SCENE_EXTENT, -SCENE_EXTENT,
                                 SCENE_EXTENT * 2, SCENE_EXTENT * 2))
        self.setBackgroundBrush(BACKGROUND)
        self._next_z = 1.0

    # ---------- queries ----------

    def board_items(self) -> list[QGraphicsItem]:
        """Everything on the board, in z-order (bottom first).

        Every scene item is board content: chrome -- trash target, camera button,
        cursors, HUD -- is painted in BoardView, never added to the scene, so the
        scene's contents and the board's are the same set. This is the accessor
        for anything that means "the whole board": fitting, clearing, z-order,
        placement, select-all.
        """
        return sorted(self.items(), key=lambda i: i.zValue())

    def image_items(self) -> list[ImageItem]:
        """The images on the board, in z-order (bottom first).

        A strict subset of board_items(). Use it only where the answer genuinely
        has to be an image -- an image-shaped payload, or an operation that only
        makes sense on a picture.
        """
        return [i for i in self.board_items() if isinstance(i, ImageItem)]

    def find(self, item_id: str) -> ImageItem | None:
        """Look up an image by its id. Only images carry an id today."""
        for item in self.image_items():
            if item.item_id == item_id:
                return item
        return None

    def content_rect(self) -> QRectF:
        """Bounding box of everything on the board -- what Ctrl+0 and fit frame."""
        rect = QRectF()
        for item in self.board_items():
            box = item.sceneBoundingRect()
            rect = box if rect.isNull() else rect.united(box)
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

    def describe_image(self, item: ImageItem | str, description: str) -> ImageItem | None:
        """Record what an image is. Returns None when no item has that id.

        The board only stores the text -- it has no way to produce one, and is
        not allowed to acquire one, so whoever is driving does the looking. An
        empty (or whitespace-only) description clears the field back to "nobody
        has looked at this yet", which is how a wrong reading gets taken back
        rather than being stuck on the image forever.
        """
        target = self.find(item) if isinstance(item, str) else item
        if target is None:
            return None
        target.description = description.strip()
        self.board_changed.emit()
        return target

    def search(self, query: str) -> list[tuple[ImageItem, str]]:
        """Images matching `query`, each with the field it matched on.

        In z-order like every other listing, and description hits come out ahead
        of file-name hits: a caller acting on the first result should get the
        image something actually read, not the one whose file happens to be
        named after it.
        """
        hits = [(item, field) for item in self.image_items()
                if (field := item.matches(query)) is not None]
        return sorted(hits, key=lambda hit: hit[1] != "description")

    def clear_board(self) -> int:
        """Empty the board -- every item, not just the images. Returns how many."""
        items = self.board_items()
        for item in items:
            self.removeItem(item)
        self._next_z = 1.0
        self.board_changed.emit()
        return len(items)

    def duplicate(self, item: ImageItem) -> ImageItem | None:
        if not item.source_path:
            return None  # pasted images have no path to re-read; skipped for now
        clone = ImageItem(item.source_path)
        clone.description = item.description  # same picture, so the same reading of it
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
        # Behind everything on the board, not merely behind the other images.
        lowest = min((i.zValue() for i in self.board_items()), default=0.0)
        item.setZValue(lowest - 1.0)

    # ---------- placement ----------

    def _free_position(self, item: ImageItem) -> QPointF:
        """Find a spot that doesn't cover anything already on the board.

        Walks an outward spiral from the origin. Matters most for the MCP path,
        where a batch of images gets added with no pointer position to anchor to.
        """
        existing = [i.sceneBoundingRect() for i in self.board_items() if i is not item]
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
