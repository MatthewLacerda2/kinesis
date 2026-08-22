"""A note: text on the board, put there by a sentence.

A reference board with no words on it is a board where you remember what each
group was for, until you don't. A note labels a set -- "lighting", "the version
he approved", "do not use" -- and it is the complement of the per-image
descriptions nobody on this side of the screen ever sees (#9): this is text the
*person* sees.

**The wrapped block is the item's rectangle**, and everything that asks where a
note is gets that one rectangle: the hit shape, the selection outline,
content_rect, the listing, and what an arrow attaches to (#53). It is computed
once, from Qt's own layout, because the longest line and the wrapped block are
two different rectangles and two implementations will otherwise drift apart on
which they meant.

**Wrapping is to a width the note carries.** A note is a label, not a document:
it wraps at the width it was given and grows downward. No auto-shrink to fit, no
scrolling, no rich text, no per-character anything.

**Font, size, weight and colour are properties, and there is no bold flag** -- a
flag would be a second, coarser way to say a number that already exists. The
size is in scene units and the text is drawn at the board's own scale, so it is
crisp at 64x rather than a rasterised label that goes soft.

**There is no caret, and that is the point.** A note is written over MCP and only
over MCP: no double-click to create, no text cursor, no on-canvas editing. A
caret is the one genuinely unavoidable mode in this whole set -- you cannot type
without one -- and a mode here means deciding what a closed hand does while it
is up, drawing an indicator so nobody is surprised by it, and suppressing the
grab underneath. None of that has to exist if the text arrives as a sentence to
an assistant, which is how it was going to arrive anyway.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPainterPath
from PySide6.QtWidgets import QGraphicsItem

from .base import BoardItem

# Light enough to read on the dark board, and not white: a note is a label, not
# a headline, and pure white on this background reads as louder than it is.
DEFAULT_COLOR = QColor(232, 234, 237)

DEFAULT_SIZE = 48.0
DEFAULT_WEIGHT = 400
DEFAULT_WRAP = 600.0

# Tall enough that the wrap never runs out of room; the block's real height is
# whatever Qt lays the text out to, and this is only the space it may use.
_UNBOUNDED = 1_000_000.0

TEXT_FLAGS = int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft
                                                 | Qt.AlignmentFlag.AlignTop)


def system_family() -> str:
    """The UI font of the machine the board is running on.

    The whole shipped font set for now, deliberately: shipping font files is a
    packaging question this project has not answered (pyproject has no
    package-data), and the family is a property, so a shipped set later is
    additive rather than a format change.
    """
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def known_family(family: str) -> bool:
    """Is this a font the machine actually has?

    Checked so the agent path can refuse rather than substitute. A person at the
    board sees a wrong face the instant it is drawn; nobody is looking when a
    call comes in over MCP, which is the whole difference.
    """
    return family in QFontDatabase.families()


class NoteItem(BoardItem):
    """A block of wrapped text, centred on the item origin like every kind."""

    kind = "note"

    def __init__(self, text: str, family: str | None = None,
                 size: float = DEFAULT_SIZE, weight: int = DEFAULT_WEIGHT,
                 color: QColor | None = None, wrap_width: float = DEFAULT_WRAP,
                 item_id: str | None = None):
        super().__init__(item_id)
        self.text = text
        self.family = family or system_family()
        self.size = max(1.0, float(size))
        self.weight = int(weight)
        self.color = color or QColor(DEFAULT_COLOR)
        self.wrap_width = max(1.0, float(wrap_width))
        self._block = QRectF()
        self._relayout()

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    # ---------- layout ----------

    def font(self) -> QFont:
        font = QFont(self.family)
        font.setPointSizeF(self.size)
        font.setWeight(QFont.Weight(max(1, min(1000, self.weight))))
        return font

    def _relayout(self) -> None:
        """Measure the wrapped block and centre it on the origin."""
        self.prepareGeometryChange()
        metrics = QFontMetricsF(self.font())
        laid = metrics.boundingRect(QRectF(0, 0, self.wrap_width, _UNBOUNDED),
                                    TEXT_FLAGS, self.text)
        width = max(1.0, min(self.wrap_width, laid.width()))
        height = max(1.0, laid.height())
        self._block = QRectF(-width / 2, -height / 2, width, height)
        self.update()

    def restyle(self, **changes) -> None:
        for name, value in changes.items():
            setattr(self, name, value)
        self._relayout()

    def boundingRect(self) -> QRectF:
        return self._block

    def shape(self) -> QPainterPath:
        """The whole block. Aiming a hand between two letters is not a thing to
        ask of anybody, so a note is grabbed anywhere on the text it lays out."""
        path = QPainterPath()
        path.addRect(self._block)
        return path

    def draws_nothing(self) -> bool:
        return not self.text.strip()

    # ---------- search ----------

    def matches(self, query: str) -> str | None:
        """"text" when the query is in this note, None otherwise.

        A note is worth as much to a search as a description is: both are words
        somebody deliberately wrote about what is on the board. Asked for the
        lighting references, an agent should find the note that says "lighting"
        as well as the images sitting under it.
        """
        needle = query.strip().lower()
        return "text" if needle and needle in self.text.lower() else None

    # ---------- painting ----------

    def paint(self, painter, option, widget=None) -> None:
        painter.setFont(self.font())
        painter.setPen(self.color)
        painter.drawText(self._block, TEXT_FLAGS, self.text)

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "text": self.text,
            "family": self.family,
            "size": self.size,
            "weight": self.weight,
            "color": self.color.name(QColor.NameFormat.HexArgb),
            "wrap_width": self.wrap_width,
        }

    def apply_dict(self, record: dict) -> None:
        super().apply_dict(record)
        self.text = record.get("text", "")
        self.family = record.get("family") or system_family()
        self.size = max(1.0, float(record.get("size", DEFAULT_SIZE)))
        self.weight = int(record.get("weight", DEFAULT_WEIGHT))
        colour = QColor(record.get("color") or "")
        self.color = colour if colour.isValid() else QColor(DEFAULT_COLOR)
        self.wrap_width = max(1.0, float(record.get("wrap_width", DEFAULT_WRAP)))
        self._relayout()
