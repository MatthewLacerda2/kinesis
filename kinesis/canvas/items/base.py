"""What every item on the board has, regardless of what it draws.

There is **one id space** across every kind, and that is the whole point of this
class. An arrow names the thing it is plugged into by id (#53), so per-kind ids
-- or two kinds able to collide on one -- means an arrow attached to a box
silently pointing at a photograph instead. Ids are generated here, once, for
everything.

`kind` is the short string the wire and the save file both use. It is what lets
one listing describe a mixed board without the caller guessing the kind from
which fields happen to be present, and what lets persistence file an item under
its own list rather than smuggling every kind into one.

`to_dict()` is the item's own serialised form. Persistence collects those and
knows nothing about what is in them, so a new kind is a new module here and a
list in the format -- never an edit to the saving code.

Deliberately *not* here: `description`. A note's text is its own description and
a box's is its label, so a second field to describe them would be a field for
its own sake. It stays on ImageItem.
"""

from __future__ import annotations

import uuid

from PySide6.QtWidgets import QGraphicsItem


class BoardItem(QGraphicsItem):
    """Base for everything the board holds.

    Subclasses set `kind` and are expected to keep geometry centred on the item
    origin, so setScale pivots about the centre and setPos places it -- the
    convention every mutation path (mouse, hand, MCP) is already written to.
    """

    kind = "item"

    def __init__(self, item_id: str | None = None):
        super().__init__()
        self.item_id = item_id or uuid.uuid4().hex[:12]

    def to_dict(self) -> dict:
        """The fields every kind saves. Subclasses add their own on top."""
        return {
            "id": self.item_id,
            "kind": self.kind,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "scale": self.scale(),
            "rotation": self.rotation(),
            "z": self.zValue(),
        }

    def apply_dict(self, record: dict) -> None:
        """Restore the fields to_dict() wrote. The id included: an arrow saved
        pointing at this item names it by id, so a load that minted a fresh one
        would break every attachment in the file."""
        self.setPos(record.get("x", 0.0), record.get("y", 0.0))
        self.setScale(record.get("scale", 1.0))
        self.setRotation(record.get("rotation", 0.0))
        self.setZValue(record.get("z", 0.0))
        if record.get("id"):
            self.item_id = record["id"]
