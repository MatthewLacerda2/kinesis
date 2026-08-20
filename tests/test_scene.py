"""The one mutation surface, asserted on directly.

Drag-drop, paste, the menu, the hand and MCP all end up in these methods, so a
regression here is a regression in every entry point at once. What is checked is
what a caller relies on: the item exists where it was asked for, it is on top,
the board_changed signal fired, and a file the board refuses leaves no half-added
item behind.
"""

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage

from kinesis.canvas import scene as scene_module
from kinesis.canvas.items import ImageItem
from kinesis.canvas.scene import DEFAULT_LONG_EDGE, BoardScene


@pytest.fixture
def board(qapp, tmp_path, monkeypatch):
    """An empty board whose paste cache is redirected into the tmp dir.

    add_qimage writes to a real cache folder under $HOME; pointing it elsewhere
    keeps the suite from leaving files on the developer's machine.
    """
    monkeypatch.setattr(scene_module, "PASTE_DIR", tmp_path / "pasted")
    return BoardScene()


@pytest.fixture
def changes(board):
    """Counts board_changed emissions -- the signal every view repaints on."""
    seen = []
    board.board_changed.connect(lambda: seen.append(1))
    return seen


# ---------- add ----------

def test_add_image_normalises_the_long_edge(board, make_image):
    item = board.add_image(make_image(w=400, h=300))
    assert board.image_items() == [item]
    assert item.scale() == pytest.approx(DEFAULT_LONG_EDGE / 400)
    assert item.sceneBoundingRect().width() == pytest.approx(DEFAULT_LONG_EDGE)


def test_add_image_honours_an_explicit_position_and_long_edge(board, make_image):
    item = board.add_image(make_image(), pos=QPointF(120, -40), long_edge=None)
    assert item.pos() == QPointF(120, -40)
    assert item.scale() == pytest.approx(1.0)


def test_add_image_announces_the_change(board, make_image, changes):
    board.add_image(make_image())
    assert len(changes) == 1


def test_add_image_puts_each_new_image_on_top(board, make_image):
    first = board.add_image(make_image("a.png"))
    second = board.add_image(make_image("b.png"))
    assert board.image_items() == [first, second]
    assert second.zValue() > first.zValue()


def test_missing_file_is_refused_without_touching_the_board(board, changes):
    with pytest.raises(FileNotFoundError):
        board.add_image("/no/such/image.png")
    assert board.board_items() == []
    assert changes == []


def test_unsupported_suffix_is_refused_without_touching_the_board(board, tmp_path, changes):
    path = tmp_path / "notes.txt"
    path.write_text("not an image")
    with pytest.raises(ValueError):
        board.add_image(path)
    assert board.board_items() == []
    assert changes == []


def test_undecodable_file_leaves_no_half_added_item(board, tmp_path, changes):
    """A .png that isn't one: the item must never reach the scene."""
    path = tmp_path / "broken.png"
    path.write_bytes(b"not really a png")
    with pytest.raises(OSError):
        board.add_image(path)
    assert board.board_items() == []
    assert changes == []


def test_add_qimage_lands_with_a_real_path(board):
    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor("#ff8800"))
    item = board.add_qimage(img, pos=QPointF(0, 0))
    assert board.image_items() == [item]
    # A path is what makes a pasted image save, load and duplicate like any other.
    assert item.source_path and item.natural_size() == (200, 100)


# ---------- remove / clear ----------

def test_remove_image_by_item_and_by_id(board, make_image, changes):
    first = board.add_image(make_image("a.png"))
    second = board.add_image(make_image("b.png"))
    assert board.remove_image(first) is True
    assert board.remove_image(second.item_id) is True
    assert board.image_items() == []
    assert len(changes) == 4


def test_removing_an_unknown_id_reports_false_and_changes_nothing(board, make_image):
    item = board.add_image(make_image())
    seen = []
    board.board_changed.connect(lambda: seen.append(1))
    assert board.remove_image("nope") is False
    assert board.image_items() == [item]
    assert seen == []


def test_clear_board_resets_z_so_the_next_image_starts_low(board, make_image):
    for name in ("a.png", "b.png", "c.png"):
        board.add_image(make_image(name))
    top_z = board.image_items()[-1].zValue()
    assert board.clear_board() == 3
    fresh = board.add_image(make_image("d.png"))
    assert board.image_items() == [fresh]
    assert fresh.zValue() < top_z


# ---------- describe / search ----------

def test_describe_image_takes_an_item_or_an_id_and_reports_the_change(board, make_image,
                                                                     changes):
    item = board.add_image(make_image())
    assert board.describe_image(item, "a copper kettle") is item
    assert item.description == "a copper kettle"
    assert board.describe_image(item.item_id, "  a kettle, steaming  ") is item
    assert item.description == "a kettle, steaming", "stored text is stripped"
    assert len(changes) == 3, "a description is board state, so it is a board change"


def test_describing_an_unknown_id_reports_none_and_changes_nothing(board, make_image):
    board.add_image(make_image())
    seen = []
    board.board_changed.connect(lambda: seen.append(1))
    assert board.describe_image("nope", "anything") is None
    assert seen == []


def test_a_duplicate_carries_the_description_of_what_it_copies(board, make_image):
    """The clone is the same picture, so the reading of it is still true."""
    original = board.describe_image(board.add_image(make_image()), "a copper kettle")
    assert board.duplicate(original).description == "a copper kettle"


def test_search_puts_described_images_ahead_of_file_name_hits(board, make_image):
    guessed = board.add_image(make_image("kettle-ref.png"))
    read = board.add_image(make_image("img_204.png"))
    board.describe_image(read, "a kettle on a stove")
    assert board.search("kettle") == [(read, "description"), (guessed, "path")]


def test_an_undescribed_image_matches_nothing_a_description_would(board, make_image):
    board.add_image(make_image("img_204.png"))
    assert board.search("kettle") == []
    assert board.search("") == [], "no description is not a match for everything"


# ---------- duplicate ----------

def test_duplicate_copies_the_transform_and_offsets_the_copy(board, make_image):
    original = board.add_image(make_image(), pos=QPointF(50, 50))
    original.setScale(0.5)
    clone = board.duplicate(original)
    assert clone is not original
    assert clone.source_path == original.source_path
    assert clone.scale() == pytest.approx(0.5)
    assert clone.pos() != original.pos()
    assert board.image_items() == [original, clone]  # the copy is on top


def test_duplicate_declines_an_image_with_no_file_behind_it(board):
    img = QImage(50, 50, QImage.Format.Format_RGB32)
    img.fill(QColor("#123456"))
    item = ImageItem(None, image=img)
    board.addItem(item)
    assert board.duplicate(item) is None
    assert board.image_items() == [item]


# ---------- z-order ----------

def test_bring_to_front_and_send_to_back_reorder_the_board(board, make_image):
    a = board.add_image(make_image("a.png"))
    b = board.add_image(make_image("b.png"))
    c = board.add_image(make_image("c.png"))
    board.bring_to_front(a)
    assert board.image_items() == [b, c, a]
    board.send_to_back(a)
    assert board.image_items() == [a, b, c]


# ---------- placement / extent ----------

def test_first_image_lands_at_the_origin(board, make_image):
    assert board.add_image(make_image()).pos() == QPointF(0, 0)


def test_added_images_do_not_land_on_top_of_each_other(board, make_image):
    """The MCP path adds a batch with no pointer to anchor to; they must spread."""
    items = [board.add_image(make_image(f"{n}.png")) for n in range(5)]
    rects = [i.sceneBoundingRect() for i in items]
    for n, rect in enumerate(rects):
        for other in rects[n + 1:]:
            assert not rect.intersects(other)


def test_content_rect_is_null_on_an_empty_board_and_covers_every_image(board, make_image):
    assert board.content_rect().isNull()
    a = board.add_image(make_image("a.png"))
    b = board.add_image(make_image("b.png"))
    rect = board.content_rect()
    assert rect.contains(a.sceneBoundingRect())
    assert rect.contains(b.sceneBoundingRect())
