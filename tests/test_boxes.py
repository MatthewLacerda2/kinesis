"""Boxes: what they draw, what can be grabbed, and what a caller is refused.

The hit shape gets the most attention here because it is the part that is about
feel rather than about boxes: an unfilled box is a cut-out with a very large
hole, and a box that swallowed every grab through its middle would make drawing
one round three images the thing that stops you picking any of them up.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.items import BoxItem, parse_color  # noqa: E402
from kinesis.canvas.scene import BoardScene  # noqa: E402

from .boardcontrol import send  # noqa: E402

RED = QColor("#c8552a")
BLUE = QColor("#2a7fc8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def board(qapp):
    scene = BoardScene()
    scene.set_grab_band(40.0)
    return scene


def hits(item, x, y) -> bool:
    """Would a grab aimed here land on this item? The same question the mouse
    and the hand both ask, through contains() -> shape()."""
    return item.contains(item.mapFromScene(QPointF(x, y)))


# ---------- what draws, and what is refused ----------

def test_a_box_with_neither_a_fill_nor_a_border_is_refused(board):
    """It would look exactly like a box that failed to draw."""
    with pytest.raises(ValueError, match="fill"):
        board.add_box(200, 100, fill=None, stroke=None)
    assert board.board_items() == []


def test_a_fill_alone_and_a_border_alone_are_both_fine(board):
    assert board.add_box(200, 100, fill=RED, stroke=None) is not None
    assert board.add_box(200, 100, fill=None, stroke=BLUE) is not None


def test_a_style_change_that_would_empty_the_box_changes_nothing(board):
    box = board.add_box(200, 100, fill=RED, stroke=None)
    with pytest.raises(ValueError):
        board.style_box(box, fill=None)
    assert box.fill == RED, "half a style was applied and the box went invisible"


def test_styling_something_that_is_not_a_box_answers_no(board, tmp_path):
    from PySide6.QtGui import QImage
    path = tmp_path / "i.png"
    img = QImage(40, 40, QImage.Format.Format_RGB32)
    img.fill(RED)
    img.save(str(path))
    image = board.add_image(path)
    assert board.style_box(image.item_id, fill=BLUE) is None


# ---------- the hit shape ----------

def test_a_filled_box_is_grabbed_anywhere_on_it(board):
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=RED)
    assert hits(box, 0, 0)
    assert hits(box, 190, 90)
    assert not hits(box, 400, 0)


def test_an_unfilled_box_falls_through_in_the_middle(board):
    """#2's rule, applied to a cut-out with a very large hole."""
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=None, stroke=BLUE)
    assert not hits(box, 0, 0), "the empty middle swallowed a grab"
    assert hits(box, 200, 0), "the border could not be grabbed"


def test_the_grabbable_band_is_wider_than_the_drawn_border(board):
    """A border is a thin thing to aim a hand at."""
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=None, stroke=BLUE,
                        stroke_width=2.0)
    assert hits(box, 200 - 15, 0), "the band was only as wide as the 2-unit line"
    assert not hits(box, 200 - 60, 0)


def test_the_band_follows_the_tuned_number(board):
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=None, stroke=BLUE,
                        stroke_width=2.0)
    assert not hits(box, 120, 0)
    board.set_grab_band(200.0)
    assert hits(box, 120, 0), "retuning the band left the box hit-testing on the old one"


def test_an_ellipse_is_not_grabbed_in_its_corners(board):
    box = board.add_box(400, 400, pos=QPointF(0, 0), fill=RED, geometry="ellipse")
    assert hits(box, 0, 0)
    assert not hits(box, 195, 195), "the ellipse was hit-tested as its bounding box"


# ---------- z-order, decided by the fill ----------

def test_a_filled_box_goes_behind_and_an_outline_goes_in_front(board):
    filled = board.add_box(400, 200, fill=RED)
    outline = board.add_box(400, 200, fill=None, stroke=BLUE)
    assert filled.zValue() < outline.zValue()
    assert board.board_items()[0] is filled


# ---------- colour ----------

def test_a_box_that_parents_a_group_borders_in_the_group_colour(board):
    box = board.add_box(400, 200, fill=RED, stroke=None)
    child = board.add_box(50, 50, fill=BLUE)
    board.set_parent(child, box)
    assert box.border_color() == board.group_color(box)


def test_an_explicit_border_colour_beats_the_group_default(board):
    box = board.add_box(400, 200, fill=None, stroke=BLUE)
    child = board.add_box(50, 50, fill=RED)
    board.set_parent(child, box)
    assert box.border_color() == BLUE


def test_a_box_in_no_group_with_no_stroke_has_no_border(board):
    box = board.add_box(400, 200, fill=RED, stroke=None)
    assert box.border_color() is None


def test_an_unreadable_colour_reads_as_no_colour_rather_than_black(board):
    assert parse_color("not-a-colour") is None
    assert parse_color("") is None
    assert parse_color("#ff8a65") == QColor("#ff8a65")


# ---------- radius ----------

def test_the_radius_is_a_fraction_of_the_shorter_side_and_is_capped(board):
    box = board.add_box(400, 200, fill=RED, radius=9.0)
    assert box.radius == 0.5
    assert board.add_box(400, 200, fill=RED, radius=-1).radius == 0.0


def test_a_pill_loses_its_corners(board):
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=RED, radius=0.5)
    assert not hits(box, 195, 95), "a fully rounded box kept a square corner"
    assert hits(box, 0, 95)


# ---------- ordinary board item ----------

def test_a_box_can_be_grabbed_by_a_hand_at_all(board):
    assert BoxItem(10, 10).grabbable is True


def test_a_box_moves_with_the_group_it_is_in(board):
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=RED)
    other = board.add_box(50, 50, pos=QPointF(0, 0), fill=BLUE)
    board.set_parent(box, other)
    other.setPos(100, 25)
    assert box.pos() == QPointF(100, 25)

# ---------- over the control channel ----------

def test_add_box_puts_one_on_the_board_and_lists_it(control):
    reply = send(control, "add_box", width=400, height=200, x=50, y=-20,
                 fill="#40ff8a65", stroke="#2a7fc8")
    listed = send(control, "list_items")["items"]
    assert [i["kind"] for i in listed] == ["box"]
    assert listed[0]["id"] == reply["id"]
    assert (listed[0]["x"], listed[0]["y"]) == pytest.approx((50, -20))


def test_a_box_that_would_draw_nothing_comes_back_as_an_error(control):
    reply = send(control, "add_box", width=100, height=100, fill=None, stroke=None)
    assert reply["ok"] is False and "fill" in reply["error"]
    assert send(control, "list_items")["items"] == []


def test_an_unreadable_colour_is_refused_and_says_which_field(control):
    """A caller who mistyped a colour and got told the box has no fill would go
    looking in exactly the wrong place."""
    reply = send(control, "add_box", width=100, height=100, stroke="chartreusey")
    assert reply["ok"] is False
    assert "stroke" in reply["error"] and "chartreusey" in reply["error"]


def test_set_box_style_changes_only_what_it_is_given(control):
    box = send(control, "add_box", width=100, height=100, fill="#ffc8552a",
               stroke="#ff2a7fc8")["id"]
    assert send(control, "set_box_style", id=box, stroke_width=12)["styled"] is True
    item = control.board.find(box)
    assert item.stroke_width == 12
    assert item.fill.name() == "#c8552a", "an untouched colour was cleared"


def test_set_box_style_can_take_a_colour_away_but_not_both(control):
    box = send(control, "add_box", width=100, height=100, fill="#ffc8552a",
               stroke="#ff2a7fc8")["id"]
    assert send(control, "set_box_style", id=box, fill=None)["styled"] is True
    assert control.board.find(box).fill is None
    assert send(control, "set_box_style", id=box, stroke=None)["ok"] is False
    assert control.board.find(box).stroke is not None


def test_set_box_style_on_an_id_that_is_not_a_box_says_so(control, make_image):
    image = send(control, "add_image", path=str(make_image()))["id"]
    assert send(control, "set_box_style", id=image, radius=0.2)["styled"] is False


def test_remove_item_takes_a_box_off_the_board(control):
    box = send(control, "add_box", width=100, height=100, fill="#ffc8552a")["id"]
    assert send(control, "remove_item", id=box) == {"removed": True, "ok": True}
    assert send(control, "remove_item", id=box)["removed"] is False


def test_remove_image_will_not_remove_a_box(control):
    """The image-shaped door stays image-shaped, so a caller that means a
    picture cannot delete a box and be told it worked."""
    box = send(control, "add_box", width=100, height=100, fill="#ffc8552a")["id"]
    assert send(control, "remove_image", id=box)["removed"] is False


def test_a_box_is_in_the_board_wide_listing_but_not_the_image_one(control, make_image):
    send(control, "add_image", path=str(make_image()))
    send(control, "add_box", width=100, height=100, fill="#ffc8552a")
    assert len(send(control, "list_items")["items"]) == 2
    assert len(send(control, "list_images")["images"]) == 1
