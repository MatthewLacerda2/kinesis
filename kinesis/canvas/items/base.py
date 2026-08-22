"""What every item on the board has, regardless of what it draws.

There is **one id space** across every kind, and that is the first reason this
class exists. An arrow names the thing it is plugged into by id (#53), so
per-kind ids -- or two kinds able to collide on one -- means an arrow attached
to a box silently pointing at a photograph instead. Ids are generated here,
once, for everything.

`kind` is the short string the wire and the save file both use. It is what lets
one listing describe a mixed board without the caller guessing the kind from
which fields happen to be present, and what lets persistence file an item under
its own list rather than smuggling every kind into one.

`to_dict()` / `apply_dict()` are the item's own serialised form. Persistence
collects those and knows nothing about what is in them, so a new kind is a new
module here and a list in the format -- never an edit to the saving code.

**The parent link and the move propagation are here for a reason (#4).** Items
anchored to a parent move with it and do not scale with it, and the one seam
that gets every path at once is `itemChange(ItemPositionHasChanged)`: the mouse
drag, the hand grab, the two-hand move+scale and the MCP all end at `setPos`.
Putting it on the base rather than on ImageItem is what makes a box or a note
carry its group the same way a picture does, with nothing to remember.

Deliberately *not* here: `description`. A note's text is its own description and
a box's is its label, so a second field to describe them would be a field for
its own sake. It stays on ImageItem.
"""

from __future__ import annotations

import uuid

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QGraphicsItem


class BoardItem(QGraphicsItem):
    """Base for everything the board holds.

    Subclasses set `kind` and are expected to keep geometry centred on the item
    origin, so setScale pivots about the centre and setPos places it -- the
    convention every mutation path (mouse, hand, MCP) is already written to.
    """

    kind = "item"

    # Can a hand or a mouse pick this up? False for a kind whose whole position
    # is decided by something else -- an arrow is where its two ends are (#53),
    # so there is nothing for a drag to do and a thin diagonal line is the worst
    # possible target for a hand cursor. Everything grabbable answers through
    # shape(), so the mouse and the hand agree for free.
    grabbable = True

    def __init__(self, item_id: str | None = None):
        super().__init__()
        self.item_id = item_id or uuid.uuid4().hex[:12]

        # Flat storage: the id of the item this one is anchored to, never Qt's
        # own parenting. BoardScene.set_parent is the only thing that sets it,
        # because it is the only thing that checks for a cycle first.
        self.parent_id: str | None = None
        # Index into the fixed colour roster, set once this item is a group's
        # parent. None means it heads no group -- which is not the same as
        # being in none, since a child is in its parent's.
        self.group_index: int | None = None

        # Where the last propagated move left us. Qt reports the new position,
        # so the delta the children need has to be differenced against this.
        self._last_pos = QPointF(0.0, 0.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def setFlags(self, flags) -> None:
        """Keep ItemSendsGeometryChanges no matter what a subclass sets.

        Qt's setFlags *replaces* the set rather than adding to it, so a kind
        that lists its own two flags in __init__ silently switches off the
        notification this class's whole group model runs on -- and the symptom
        is a board where a parent stops carrying its children, with nothing
        anywhere to point at. Made true by construction instead of remembered.
        """
        super().setFlags(flags | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

    # ---------- the seam every move goes through ----------

    def itemChange(self, change, value):
        """Carry a move to the items anchored to this one.

        Position only. Scale deliberately does not propagate: on a reference
        board you scale one image to compare it against another, and the
        two-hand pinch already means "resize *this*", so a propagating scale
        would fight the primary gesture.

        The recursion is Qt's own -- moving a child fires this again on that
        child -- so nesting costs nothing here and terminates because
        set_parent refuses a cycle.
        """
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            delta = value - self._last_pos
            self._last_pos = QPointF(value)
            if not delta.isNull():
                for child in self._children():
                    child.moveBy(delta.x(), delta.y())
        return super().itemChange(change, value)

    def _children(self) -> list[BoardItem]:
        """The items anchored directly to this one.

        A scan of the scene rather than a list kept on the item, because a list
        is a second copy of the graph and the two disagree the first time
        something is removed by a path that did not know to maintain it. The
        board holds tens of items, not thousands, and this runs on a move.
        """
        scene = self.scene()
        if scene is None:
            return []
        return [i for i in scene.items()
                if isinstance(i, BoardItem) and i.parent_id == self.item_id]

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        """The fields every kind saves. Subclasses add their own on top."""
        return {
            "id": self.item_id,
            "kind": self.kind,
            "parent": self.parent_id,
            "group_index": self.group_index,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "scale": self.scale(),
            "rotation": self.rotation(),
            "z": self.zValue(),
        }

    def apply_dict(self, record: dict) -> None:
        """Restore where and what this item is. The id included: an arrow saved
        pointing at this item names it by id, so a load that minted a fresh one
        would break every attachment in the file.

        Not the parent link -- see apply_links().
        """
        self.setPos(record.get("x", 0.0), record.get("y", 0.0))
        self._last_pos = QPointF(self.pos())
        self.setScale(record.get("scale", 1.0))
        self.setRotation(record.get("rotation", 0.0))
        self.setZValue(record.get("z", 0.0))
        if record.get("id"):
            self.item_id = record["id"]

    def apply_links(self, record: dict) -> None:
        """Restore the parent link, in a pass of its own after every item is placed.

        It cannot ride along in apply_dict: the file already holds each item's
        absolute position, and a parent restored *after* one of its children
        would carry that child by the whole of its own move. A second pass is
        cheaper than a flag that suspends propagation, and it cannot be left
        switched on by an exception halfway through a load.
        """
        self.parent_id = record.get("parent") or None
        self.group_index = record.get("group_index")
