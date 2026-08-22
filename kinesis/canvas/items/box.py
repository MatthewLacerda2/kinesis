"""A box: a rectangle or an ellipse drawn round things on the board.

It is geometry, not a picture. The alternative -- authoring a rectangle with
alpha in another program and dropping the PNG on the board -- is soft the first
time you zoom in, and zooming is the primary thing this canvas does. A box that
is geometry stays crisp at 64x and costs a few dozen bytes in the save file.

**Two geometries, and that is meant to stay two.** A rounded rectangle is a
rectangle with a radius, not a third kind. No polygons, no stars, no dashes, no
shadows, no gradients: each of those is a drawing program growing inside a
reference board.

`radius` is a fraction of the box's own shorter side, capped at 0.5, so 0 is a
square corner and 0.5 is a pill whatever size the box is. That keeps the corners
circular rather than elliptical, and it is checkable from the item alone -- a
radius in scene units larger than the box it rounds is nonsense nothing could
catch until something drew it.

**The stroke is in scene units, not cosmetic.** The border is content: zoom in
and it gets thicker, the way a drawn line does. The 0-width cosmetic pen used
for chrome is the opposite case and stays that way.

**What can be grabbed is the part of this that is about feel.** A filled box is
grabbed anywhere on it. An unfilled one is grabbed by its border, and the empty
middle falls through to whatever is behind -- the same rule a transparent PNG
already follows (#2), because an outline box is a cut-out with a very large
hole. A box that swallowed every grab through its interior would make drawing
one round three images the thing that stops you picking any of them up. The
grabbable band is wider than the drawn stroke, because a border is a thin thing
to aim a hand at, and how much wider is a grab number and so lives in `Tuning`.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem

from .base import BoardItem

GEOMETRIES = ("rect", "ellipse")

MAX_RADIUS = 0.5


class BoxItem(BoardItem):
    """A rectangle or ellipse with an interior colour, a border colour, or both.

    Either colour may be absent but not both -- a box that would draw nothing
    looks exactly like a box that failed to draw, and a board quietly missing
    one is a mistake you find later. That refusal lives in BoardScene, where
    every mutation path already goes.
    """

    kind = "box"

    def __init__(self, width: float, height: float, geometry: str = "rect",
                 fill: QColor | None = None, stroke: QColor | None = None,
                 stroke_width: float = 4.0, radius: float = 0.0,
                 item_id: str | None = None):
        super().__init__(item_id)
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))
        self.geometry = geometry if geometry in GEOMETRIES else "rect"
        self.fill = fill
        self.stroke = stroke
        self.stroke_width = max(0.0, float(stroke_width))
        self.radius = min(MAX_RADIUS, max(0.0, float(radius)))

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    # ---------- geometry ----------

    def rect(self) -> QRectF:
        """The box itself, centred on the item origin like every other kind."""
        return QRectF(-self.width / 2, -self.height / 2, self.width, self.height)

    def _band(self) -> float:
        """How wide a border is to aim a hand at. Never narrower than it looks."""
        scene = self.scene()
        return max(self.stroke_width, scene.grab_band if scene is not None else 0.0)

    def boundingRect(self) -> QRectF:
        pad = self._band() / 2
        return self.rect().adjusted(-pad, -pad, pad, pad)

    def outline(self) -> QPainterPath:
        path = QPainterPath()
        if self.geometry == "ellipse":
            path.addEllipse(self.rect())
        elif self.radius > 0:
            r = self.radius * min(self.width, self.height)
            path.addRoundedRect(self.rect(), r, r)
        else:
            path.addRect(self.rect())
        return path

    def shape(self) -> QPainterPath:
        """Everything for a filled box, the border band for an unfilled one."""
        outline = self.outline()
        stroker = QPainterPathStroker()
        stroker.setWidth(self._band())
        band = stroker.createStroke(outline)
        return outline.united(band) if self.fill is not None else band

    def resize(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self.width = max(1.0, float(width))
        self.height = max(1.0, float(height))

    def refresh(self) -> None:
        """Re-derive what depends on numbers owned outside the item -- today the
        grab band, which is tuned live and changes the hit shape and the box."""
        self.prepareGeometryChange()
        self.update()

    # ---------- colour ----------

    def border_color(self) -> QColor | None:
        """What the border is actually drawn in.

        An explicit stroke always wins. Failing that, a box that parents a group
        takes the group's colour (#4), which is what lets the border say *which
        set* while the fill stays independent -- a wash, or nothing at all so the
        images inside are not obscured.
        """
        if self.stroke is not None:
            return self.stroke
        scene = self.scene()
        return scene.group_color(self) if scene is not None else None

    def draws_nothing(self) -> bool:
        return self.fill is None and self.stroke is None

    # ---------- painting ----------

    def paint(self, painter, option, widget=None) -> None:
        border = self.border_color()
        painter.setBrush(self.fill if self.fill is not None else Qt.BrushStyle.NoBrush)
        if border is not None and self.stroke_width > 0:
            painter.setPen(QPen(border, self.stroke_width))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(self.outline())

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "width": self.width,
            "height": self.height,
            "geometry": self.geometry,
            "radius": self.radius,
            "fill": self.fill.name(QColor.NameFormat.HexArgb) if self.fill else None,
            "stroke": self.stroke.name(QColor.NameFormat.HexArgb) if self.stroke else None,
            "stroke_width": self.stroke_width,
        }

    def apply_dict(self, record: dict) -> None:
        super().apply_dict(record)
        self.resize(record.get("width", self.width), record.get("height", self.height))
        self.geometry = record.get("geometry", "rect")
        self.radius = min(MAX_RADIUS, max(0.0, float(record.get("radius", 0.0))))
        self.fill = parse_color(record.get("fill"))
        self.stroke = parse_color(record.get("stroke"))
        self.stroke_width = max(0.0, float(record.get("stroke_width", 4.0)))


def parse_color(value: str | None) -> QColor | None:
    """A colour off the wire or out of a file, or None for "no colour at all".

    None and an unreadable string are the same answer on purpose: a caller who
    sent nonsense gets a box that refuses rather than one silently drawn black,
    since a box drawing the wrong thing is harder to notice than one that
    complains.
    """
    if not value:
        return None
    colour = QColor(value)
    return colour if colour.isValid() else None
