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
from PySide6.QtWidgets import QGraphicsScene

from . import groups
from .items import (
    BoardItem,
    BoxItem,
    ImageItem,
    NoteItem,
    is_supported_image,
    known_family,
)

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
    image_items() is the images on it. Identity is board-wide -- find() answers
    about any kind, because one id space is what makes an arrow able to name the
    thing it is plugged into -- while the methods that only make sense on a
    picture stay image-shaped and say so by refusing anything else.
    """

    board_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(QRectF(-SCENE_EXTENT, -SCENE_EXTENT,
                                 SCENE_EXTENT * 2, SCENE_EXTENT * 2))
        self.setBackgroundBrush(BACKGROUND)
        self._next_z = 1.0
        # How far through the colour roster this board has got. Groups are
        # coloured in the order they are made, so this is the board's count of
        # them and is restored on load rather than recounted.
        self._next_group = 0
        # How wide a box's border is to aim a hand at, in scene units. Tuned
        # live in the T panel, so it is pushed in rather than read from Tuning
        # here -- the canvas does not import the tracker's protocol.
        self.grab_band = 40.0

    # ---------- queries ----------

    def board_items(self) -> list[BoardItem]:
        """Everything on the board, in z-order (bottom first).

        Every scene item is board content: chrome -- trash target, camera button,
        cursors, HUD -- is painted in BoardView, never added to the scene, so the
        scene's contents and the board's are the same set. This is the accessor
        for anything that means "the whole board": fitting, clearing, z-order,
        placement, select-all.
        """
        return sorted(self.items(), key=lambda i: i.zValue())

    def box_items(self) -> list[BoxItem]:
        """The boxes on the board, in z-order (bottom first)."""
        return [i for i in self.board_items() if isinstance(i, BoxItem)]

    def note_items(self) -> list[NoteItem]:
        """The notes on the board, in z-order (bottom first)."""
        return [i for i in self.board_items() if isinstance(i, NoteItem)]

    def image_items(self) -> list[ImageItem]:
        """The images on the board, in z-order (bottom first).

        A strict subset of board_items(). Use it only where the answer genuinely
        has to be an image -- an image-shaped payload, or an operation that only
        makes sense on a picture.
        """
        return [i for i in self.board_items() if isinstance(i, ImageItem)]

    def find(self, item_id: str) -> BoardItem | None:
        """Look up any item by its id -- one id space across every kind.

        Board-wide rather than image-wide because an arrow names its endpoints
        by id (#53): a lookup that could only answer about pictures would make
        an arrow attached to a box resolve to nothing, or worse, to a picture.
        """
        for item in self.board_items():
            if isinstance(item, BoardItem) and item.item_id == item_id:
                return item
        return None

    def content_rect(self) -> QRectF:
        """Bounding box of everything on the board -- what Ctrl+0 and fit frame."""
        rect = QRectF()
        for item in self.board_items():
            box = item.sceneBoundingRect()
            rect = box if rect.isNull() else rect.united(box)
        return rect

    # ---------- groups ----------

    def set_parent(self, child: BoardItem | str, parent: BoardItem | str | None) -> bool:
        """Anchor `child` to `parent`, or pass None to set it loose.

        Returns False when either id names nothing, or when the link would close
        a cycle (A parents B parents A) -- which is a hang rather than a strange
        board, since the move propagation would recurse forever.

        A parent that heads no group yet takes the next colour off the roster
        here. Colours are handed out in a fixed order and never handed back: a
        parent that loses its last child keeps its colour, because a roster that
        renumbered itself as sets came and went would give a board a different
        set of colours every session, which is the one thing the fixed order
        exists to prevent.
        """
        target = self.find(child) if isinstance(child, str) else child
        if target is None:
            return False
        if parent is None:
            target.parent_id = None
            self.board_changed.emit()
            return True

        anchor = self.find(parent) if isinstance(parent, str) else parent
        if anchor is None or groups.would_cycle(self.board_items(), target, anchor):
            return False
        if anchor.group_index is None:
            anchor.group_index = self._next_group
            self._next_group += 1
        target.parent_id = anchor.item_id
        self.board_changed.emit()
        return True

    def children_of(self, item: BoardItem) -> list[BoardItem]:
        return groups.children_of(self.board_items(), item)

    def descendants_of(self, item: BoardItem) -> list[BoardItem]:
        """Everything anchored under `item`, at any depth -- the set it moves,
        and the set deleting it takes with it."""
        return groups.descendants_of(self.board_items(), item)

    def group_color(self, item: BoardItem):
        """The colour `item` is outlined in, or None when it is in no group."""
        return groups.color_of(self.board_items(), item)

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

    def set_grab_band(self, width: float) -> None:
        """Change how wide a box border is to aim at, and tell the boxes.

        The band is part of every box's hit shape and bounding rect, so a change
        that did not invalidate them would leave the boxes hit-testing against a
        number nobody can see any more.
        """
        self.grab_band = max(0.0, float(width))
        for item in self.board_items():
            if isinstance(item, BoxItem):
                item.refresh()

    def add_item(self, item: BoardItem) -> BoardItem:
        """Put an already-built item on the board, placing and styling nothing.

        The load path: a file decides everything the placement and z-order rules
        would otherwise decide, so applying them on top would move a board while
        opening it.
        """
        self.addItem(item)
        return item

    def add_box(self, width: float, height: float, pos: QPointF | None = None,
                geometry: str = "rect", fill=None, stroke=None,
                stroke_width: float = 4.0, radius: float = 0.0) -> BoxItem:
        """Put a box on the board. Refuses one that would draw nothing.

        Neither a fill nor a border is required, but one of them is: a box drawn
        with neither looks exactly like a box that failed to draw, and a board
        quietly missing one is a mistake found much later.

        Z-order is decided by the fill, because the fill is what can hide
        things. A filled box goes behind everything -- it is the wash you put
        under a set, and in front it would cover what it is drawn around. An
        unfilled one goes in front, where its border reads over whatever it
        encloses and its transparent middle obscures nothing.
        """
        box = BoxItem(width, height, geometry, fill, stroke, stroke_width, radius)
        if box.draws_nothing():
            raise ValueError("a box needs a fill, a border, or both -- it was given neither")
        self.addItem(box)
        box.setPos(pos if pos is not None else QPointF(0, 0))
        if fill is None:
            self.bring_to_front(box)
        else:
            self.send_to_back(box)
        self.board_changed.emit()
        return box

    def add_note(self, text: str, pos: QPointF | None = None, family: str | None = None,
                 size: float = 48.0, weight: int = 400, color=None,
                 wrap_width: float = 600.0) -> NoteItem:
        """Write a note on the board. Refuses one with nothing in it.

        A font family is refused when the machine does not have it, rather than
        substituted. That refusal belongs on this path and not on the human one:
        kinesis draws immediately, in front of somebody looking at it, so a
        wrong face is visible the instant it happens -- but a note written over
        MCP has nobody watching, and a silent substitution there is a board that
        is wrong with nothing anywhere saying so.
        """
        if not text.strip():
            raise ValueError("a note needs some text -- it was given none")
        if family and not known_family(family):
            raise ValueError(f"this machine has no font called {family!r}")
        note = NoteItem(text, family, size, weight, color, wrap_width)
        self.addItem(note)
        note.setPos(pos if pos is not None else QPointF(0, 0))
        self.bring_to_front(note)
        self.board_changed.emit()
        return note

    def style_note(self, item: NoteItem | str, **changes) -> NoteItem | None:
        """Change a note's text or how it is set. None when no note has that id.

        Emptying the text is refused for the same reason add_note refuses it: a
        note with nothing in it is indistinguishable from one that failed to
        draw, and it leaves an invisible thing on the board that still catches
        grabs.
        """
        target = self.find(item) if isinstance(item, str) else item
        if not isinstance(target, NoteItem):
            return None
        if "text" in changes and not str(changes["text"]).strip():
            raise ValueError("a note cannot be left with no text")
        family = changes.get("family")
        if family and not known_family(family):
            raise ValueError(f"this machine has no font called {family!r}")
        target.restyle(**changes)
        self.board_changed.emit()
        return target

    def style_box(self, item: BoxItem | str, **changes) -> BoxItem | None:
        """Restyle a box. Returns None when no box on the board has that id.

        The same refusal as add_box: a change that would leave the box with
        neither a fill nor a border is rejected and nothing is applied, rather
        than half-applied and invisible.
        """
        target = self.find(item) if isinstance(item, str) else item
        if not isinstance(target, BoxItem):
            return None
        before = {name: getattr(target, name) for name in changes}
        for name, value in changes.items():
            setattr(target, name, value)
        if target.draws_nothing():
            for name, value in before.items():
                setattr(target, name, value)
            raise ValueError("that would leave the box with neither a fill nor a border")
        target.refresh()
        self.board_changed.emit()
        return target

    def remove_item(self, item: BoardItem | str) -> bool:
        """Take any item off the board, along with everything anchored under it.

        The subtree goes because a group is a thing you moved as one and would
        delete as one; leaving orphans behind pointing at an id that is gone is
        a board that looks fine and is quietly broken. Decided 2026-08-18.
        """
        target = self.find(item) if isinstance(item, str) else item
        if target is None:
            return False
        for doomed in [*self.descendants_of(target), target]:
            self.removeItem(doomed)
        self.board_changed.emit()
        return True

    def remove_image(self, item: ImageItem | str) -> bool:
        """Take an image off the board. Refuses an id that names something else.

        The image-shaped door onto remove_item, kept because the callers that
        use it -- the bin, the delete key, the MCP image commands -- all mean a
        picture, and "remove this image" answering true for a box would be a
        caller finding out it deleted the wrong thing afterwards.
        """
        target = self.find(item) if isinstance(item, str) else item
        if not isinstance(target, ImageItem):
            return False
        return self.remove_item(target)

    def describe_image(self, item: ImageItem | str, description: str) -> ImageItem | None:
        """Record what an image is. Returns None when no item has that id.

        The board only stores the text -- it has no way to produce one, and is
        not allowed to acquire one, so whoever is driving does the looking. An
        empty (or whitespace-only) description clears the field back to "nobody
        has looked at this yet", which is how a wrong reading gets taken back
        rather than being stuck on the image forever.
        """
        target = self.find(item) if isinstance(item, str) else item
        if not isinstance(target, ImageItem):
            return None
        target.description = description.strip()
        self.board_changed.emit()
        return target

    def search(self, query: str) -> list[tuple[BoardItem, str]]:
        """Board items matching `query`, each with the field it matched on.

        Board-wide, not image-wide: asked for the lighting references, a caller
        should find the note that says "lighting" as well as the images sitting
        under it (#52). A kind with no words in it never matches.

        In z-order like every other listing, except that hits on words somebody
        deliberately wrote -- a description, a note's own text -- come out ahead
        of bare file-name hits. A caller acting on the first result should get
        the thing something actually read, not the one whose file happens to be
        named after it.
        """
        hits = [(item, field) for item in self.board_items()
                if (field := item.matches(query)) is not None]
        return sorted(hits, key=lambda hit: hit[1] == "path")

    def clear_board(self) -> int:
        """Empty the board -- every item, not just the images. Returns how many."""
        items = self.board_items()
        for item in items:
            self.removeItem(item)
        self._next_z = 1.0
        self._next_group = 0
        # How wide a box's border is to aim a hand at, in scene units. Tuned
        # live in the T panel, so it is pushed in rather than read from Tuning
        # here -- the canvas does not import the tracker's protocol.
        self.grab_band = 40.0
        self.board_changed.emit()
        return len(items)

    def duplicate(self, item: ImageItem) -> ImageItem | None:
        if not item.source_path:
            return None  # pasted images have no path to re-read; skipped for now
        clone = ImageItem(item.source_path)
        clone.description = item.description  # same picture, so the same reading of it
        clone.parent_id = item.parent_id      # a copy of a member of a set is in that set
        clone.setScale(item.scale())
        clone.setRotation(item.rotation())
        self.addItem(clone)
        clone.setPos(item.pos() + QPointF(40, 40))
        self.bring_to_front(clone)
        self.board_changed.emit()
        return clone

    # ---------- z-order ----------

    def bring_to_front(self, item: BoardItem) -> None:
        self._next_z += 1.0
        item.setZValue(self._next_z)

    def send_to_back(self, item: BoardItem) -> None:
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
