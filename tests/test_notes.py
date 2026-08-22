"""Notes: the wrapped block, the refusals, and the search that finds them.

The rectangle gets the most attention because everything asks for it -- the hit
shape, the selection outline, content_rect, the listing, and what an arrow will
attach to. The longest line and the wrapped block are two different rectangles,
and two implementations drift on which they meant, so it is asserted rather than
assumed.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.items import NoteItem  # noqa: E402
from kinesis.canvas.scene import BoardScene  # noqa: E402

from .boardcontrol import send  # noqa: E402

LONG = "lighting references for the second act, the ones he actually approved"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def board(qapp):
    return BoardScene()


# ---------- the block ----------

def test_the_rectangle_is_the_wrapped_block_not_the_longest_line(board):
    narrow = board.add_note(LONG, wrap_width=200)
    wide = board.add_note(LONG, wrap_width=2000)
    assert narrow.boundingRect().width() <= 200
    assert narrow.boundingRect().height() > wide.boundingRect().height(), \
        "wrapping did not make the block taller"


def test_the_block_never_exceeds_the_wrap_width(board):
    note = board.add_note("supercalifragilistic", wrap_width=80)
    assert note.boundingRect().width() <= 80


def test_the_block_is_centred_on_the_origin_like_every_other_kind(board):
    note = board.add_note("hello", wrap_width=600)
    assert note.boundingRect().center() == QPointF(0, 0)


def test_the_hit_shape_is_the_whole_block(board):
    note = board.add_note(LONG, wrap_width=400)
    rect = note.boundingRect()
    assert note.contains(rect.center())
    assert not note.contains(QPointF(rect.right() + 50, 0))


def test_changing_the_text_relays_out_the_block(board):
    note = board.add_note("short", wrap_width=400)
    before = note.boundingRect().height()
    board.style_note(note, text=LONG + " " + LONG)
    assert note.boundingRect().height() > before


def test_a_bigger_size_makes_a_bigger_block(board):
    small = board.add_note(LONG, wrap_width=600, size=20)
    large = board.add_note(LONG, wrap_width=600, size=80)
    assert large.boundingRect().height() > small.boundingRect().height()


# ---------- refusals ----------

def test_a_note_with_no_text_is_refused(board):
    """It is invisible and still catches grabs, which is worse than absent."""
    with pytest.raises(ValueError, match="text"):
        board.add_note("   ")
    assert board.board_items() == []


def test_a_note_cannot_be_emptied_afterwards_either(board):
    note = board.add_note("lighting")
    with pytest.raises(ValueError):
        board.style_note(note, text="")
    assert note.text == "lighting"


def test_a_font_the_machine_does_not_have_is_refused_not_substituted(board):
    """Nobody is watching this call, so a wrong face would be a board that is
    wrong with nothing anywhere saying so."""
    with pytest.raises(ValueError, match="font"):
        board.add_note("lighting", family="Definitely Not A Font 9000")


def test_the_default_family_is_never_refused(board):
    assert board.add_note("lighting").family


def test_styling_something_that_is_not_a_note_answers_no(board):
    box = board.add_box(100, 100, fill=QColor("#c8552a"))
    assert board.style_note(box.item_id, text="hello") is None


# ---------- weight is a number ----------

def test_weight_is_a_number_and_is_kept_as_given(board):
    note = board.add_note("lighting", weight=700)
    assert note.weight == 700
    assert note.font().weight().value == 700


# ---------- search ----------

def test_a_note_is_found_by_its_own_words(board):
    note = board.add_note("lighting")
    assert board.search("light") == [(note, "text")]


def test_a_note_and_the_images_under_it_are_one_answer(board, tmp_path):
    from PySide6.QtGui import QImage
    path = tmp_path / "ref.png"
    img = QImage(40, 40, QImage.Format.Format_RGB32)
    img.fill(QColor("#446688"))
    img.save(str(path))

    image = board.add_image(path)
    board.describe_image(image, "a lighting reference, three-quarter key")
    note = board.add_note("lighting")
    found = dict(board.search("lighting"))
    assert set(found) == {note, image}
    assert found[note] == "text" and found[image] == "description"


def test_a_bare_file_name_hit_still_comes_last(board, tmp_path):
    from PySide6.QtGui import QImage
    path = tmp_path / "lighting.png"
    img = QImage(40, 40, QImage.Format.Format_RGB32)
    img.fill(QColor("#446688"))
    img.save(str(path))
    board.add_image(path)
    note = board.add_note("lighting")
    assert board.search("lighting")[0][0] is note


def test_a_box_has_no_words_and_never_matches(board):
    board.add_box(100, 100, fill=QColor("#c8552a"))
    assert board.search("box") == []


# ---------- an ordinary board item ----------

def test_a_note_moves_with_the_box_it_is_parented_to(board):
    box = board.add_box(400, 200, pos=QPointF(0, 0), fill=QColor("#c8552a"))
    note = board.add_note("lighting", pos=QPointF(0, 120))
    board.set_parent(note, box)
    box.setPos(300, 300)
    assert note.pos() == QPointF(300, 420)


def test_a_note_does_not_scale_with_its_parent(board):
    """A two-hand pinch on a box means 'resize this box'; the label should not
    run away with it."""
    box = board.add_box(400, 200, fill=QColor("#c8552a"))
    note = board.add_note("lighting")
    board.set_parent(note, box)
    box.setScale(4.0)
    assert note.scale() == 1.0


def test_a_note_is_grabbable(board):
    assert NoteItem("x").grabbable is True


# ---------- over the control channel ----------

def test_add_note_puts_one_on_the_board_and_lists_it(control):
    reply = send(control, "add_note", text="lighting", x=40, y=-10)
    listed = send(control, "list_items")["items"]
    assert [i["kind"] for i in listed] == ["note"]
    assert listed[0]["id"] == reply["id"]
    assert (listed[0]["x"], listed[0]["y"]) == pytest.approx((40, -10))


def test_an_empty_note_comes_back_as_an_error(control):
    reply = send(control, "add_note", text="")
    assert reply["ok"] is False and "text" in reply["error"]
    assert send(control, "list_items")["items"] == []


def test_an_unknown_font_comes_back_as_an_error(control):
    reply = send(control, "add_note", text="lighting", family="Nope Sans 9000")
    assert reply["ok"] is False and "font" in reply["error"]


def test_set_note_text_changes_only_what_it_is_given(control):
    note = send(control, "add_note", text="lighting", size=30)["id"]
    assert send(control, "set_note_text", id=note, text="approved")["styled"] is True
    item = control.board.find(note)
    assert item.text == "approved"
    assert item.size == 30, "an untouched property was reset"


def test_set_note_text_on_an_id_that_is_not_a_note_says_so(control, make_image):
    image = send(control, "add_image", path=str(make_image()))["id"]
    assert send(control, "set_note_text", id=image, text="hi")["styled"] is False


def test_find_images_returns_a_matching_note_with_its_text(control, make_image):
    send(control, "add_note", text="lighting references")
    matches = send(control, "find_images", query="lighting")["matches"]
    assert len(matches) == 1
    assert matches[0]["kind"] == "note"
    assert matches[0]["matched"] == "text"
    assert matches[0]["text"] == "lighting references"


def test_find_images_still_describes_an_image_fully(control, make_image):
    image = send(control, "add_image", path=str(make_image()))["id"]
    send(control, "describe_image", id=image, description="a lighting reference")
    match = send(control, "find_images", query="lighting")["matches"][0]
    assert match["matched"] == "description"
    assert match["path"] and match["description"]
