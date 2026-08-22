"""A saved board has to come back as the same board.

Nobody watches a load: an agent or the command line opens a file and reports
success, so a scrambled z-order or a dropped item shows up as a board that is
quietly wrong rather than as an error. The round trip is asserted on the things
a person would notice missing -- count, position, scale, stacking -- and the
version refusal is asserted because it is the only thing standing between a
future format and a file that loads wrong.
"""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF

from kinesis.canvas.persistence import FORMAT_VERSION, load_scene, save_scene
from kinesis.canvas.scene import BoardScene
from kinesis.canvas.view import BoardView


@pytest.fixture
def board(qapp):
    return BoardScene()


@pytest.fixture
def populated(board, make_image):
    """Three images with positions, scales and a stacking order worth losing."""
    layout = [(QPointF(-300, -100), 0.4), (QPointF(0, 0), 1.25), (QPointF(420, 90), 0.8)]
    items = []
    for n, (pos, scale) in enumerate(layout):
        item = board.add_image(make_image(f"{n}.png", w=200 + n * 40, h=150),
                               pos=pos, long_edge=None)
        item.setScale(scale)
        items.append(item)
    board.send_to_back(items[-1])  # so z-order is not just insertion order
    return board, items


def snapshot(scene):
    """What a caller would notice: order, identity, place and size."""
    return [(i.item_id, i.source_path, i.pos().x(), i.pos().y(), i.scale())
            for i in scene.image_items()]


@pytest.mark.parametrize("pack", [False, True])
def test_round_trip_preserves_every_item(populated, qapp, tmp_path, pack):
    board, items = populated
    before = snapshot(board)
    path = save_scene(board, tmp_path / "board.kinesis", pack=pack)

    fresh = BoardScene()
    loaded, missing = load_scene(fresh, path)
    assert (loaded, missing) == (len(items), [])
    after = snapshot(fresh)
    assert [row[0] for row in after] == [row[0] for row in before], "z-order changed"
    for got, want in zip(after, before):
        assert got[2:] == pytest.approx(want[2:]), "position or scale changed"


def test_save_forces_the_kinesis_suffix(populated, tmp_path):
    board, _items = populated
    path = save_scene(board, tmp_path / "no-suffix")
    assert path.name == "no-suffix.kinesis" and path.exists()


def test_a_packed_scene_survives_being_moved_away_from_its_images(populated, tmp_path):
    """The point of pack=True: the folder is self-contained."""
    board, items = populated
    path = save_scene(board, tmp_path / "trip.kinesis", pack=True)
    for item in items:
        Path(item.source_path).unlink()  # only the copies remain
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    (tmp_path / "trip_files").rename(moved / "trip_files")
    path.rename(moved / "trip.kinesis")

    fresh = BoardScene()
    loaded, missing = load_scene(fresh, moved / "trip.kinesis")
    assert (loaded, missing) == (len(items), [])


def test_an_unpacked_scene_records_absolute_paths(populated, tmp_path):
    board, items = populated
    path = save_scene(board, tmp_path / "linked.kinesis")
    stored = json.loads(path.read_text())["images"]
    assert [record["path"] for record in stored] == [i.source_path for i in
                                                     sorted(items, key=lambda i: i.zValue())]

    # Absolute means the file itself can move; the images stay where they are.
    (tmp_path / "sub").mkdir()
    path.rename(tmp_path / "sub" / "linked.kinesis")
    fresh = BoardScene()
    assert load_scene(fresh, tmp_path / "sub" / "linked.kinesis") == (len(items), [])


def test_loading_replaces_the_board_rather_than_adding_to_it(populated, tmp_path, make_image):
    board, items = populated
    path = save_scene(board, tmp_path / "board.kinesis")
    board.add_image(make_image("extra.png"))
    loaded, _missing = load_scene(board, path)
    assert loaded == len(items)
    assert len(board.image_items()) == len(items)


def test_the_next_image_added_after_a_load_lands_on_top(populated, tmp_path, make_image):
    """The z counter has to follow the file, not restart from how many items came in."""
    board, items = populated
    items[0].setZValue(90)  # a saved board can hold z values far above its item count
    path = save_scene(board, tmp_path / "board.kinesis")
    fresh = BoardScene()
    load_scene(fresh, path)
    added = fresh.add_image(make_image("new.png"))
    assert fresh.image_items()[-1] is added


def test_a_missing_image_is_reported_and_the_rest_still_load(populated, tmp_path):
    board, items = populated
    path = save_scene(board, tmp_path / "board.kinesis")
    gone = items[0].source_path
    Path(gone).unlink()

    fresh = BoardScene()
    loaded, missing = load_scene(fresh, path)
    assert loaded == len(items) - 1
    assert [Path(m).name for m in missing] == [Path(gone).name]


def test_descriptions_survive_the_round_trip_and_absence_survives_it_too(populated, tmp_path):
    """The point of persisting them: the reading is done once, not once a session.

    The image left undescribed matters as much as the described ones -- if a
    load invented a description for it, a later caller would think that picture
    had already been looked at.
    """
    board, items = populated
    items[0].description = "a copper kettle, steaming"
    items[1].description = "hand study, three fingers splayed"
    path = save_scene(board, tmp_path / "board.kinesis")

    fresh = BoardScene()
    load_scene(fresh, path)
    by_id = {i.item_id: i.description for i in fresh.image_items()}
    assert by_id[items[0].item_id] == "a copper kettle, steaming"
    assert by_id[items[1].item_id] == "hand study, three fingers splayed"
    assert by_id[items[2].item_id] == "", "an undescribed image came back described"


def test_a_file_from_the_previous_format_version_is_refused_intact(populated, tmp_path):
    """The bump is only worth anything if the old file loses to it, loudly.

    Version 4 has no "boxes" list. Reading one anyway would produce a board
    silently missing every box it was drawn with -- which looks exactly like a
    board nobody ever drew on, and so is the quiet misread the version check
    exists to turn into a refusal. And the board that was already open has to
    still be there afterwards, because a refusal that cleared the canvas first
    would be worse than the misread it prevented.
    """
    board, items = populated
    path = save_scene(board, tmp_path / "old.kinesis")
    stale = json.loads(path.read_text())
    stale["version"] = FORMAT_VERSION - 1
    stale.pop("boxes", None)
    path.write_text(json.dumps(stale))

    before = snapshot(board)
    with pytest.raises(ValueError, match=str(FORMAT_VERSION - 1)):
        load_scene(board, path)
    assert snapshot(board) == before, "a refused load disturbed the board that was open"


def test_a_file_from_another_format_version_is_refused(board, tmp_path, make_image):
    """No migrations, by policy -- so an unknown version must not be read at all."""
    board.add_image(make_image())
    path = tmp_path / "future.kinesis"
    path.write_text(json.dumps({"format": "kinesis", "version": FORMAT_VERSION + 1,
                                "packed": False, "images": []}))
    with pytest.raises(ValueError, match=str(FORMAT_VERSION + 1)):
        load_scene(board, path)
    assert len(board.image_items()) == 1, "a refused load must not empty the board"


def test_a_file_with_no_version_at_all_is_refused(board, tmp_path):
    path = tmp_path / "old.kinesis"
    path.write_text(json.dumps({"format": "kinesis", "images": []}))
    with pytest.raises(ValueError):
        load_scene(board, path)


def test_a_file_that_is_not_a_kinesis_scene_is_refused(board, tmp_path):
    path = tmp_path / "other.kinesis"
    path.write_text(json.dumps({"format": "something-else", "version": FORMAT_VERSION}))
    with pytest.raises(ValueError, match="not a .kinesis scene"):
        load_scene(board, path)


def test_the_viewport_comes_back_with_the_scene(populated, qapp, tmp_path):
    board, _items = populated
    view = BoardView(board)
    view.resize(800, 600)
    view.scale(2.0, 2.0)
    view.centerOn(120, -60)
    path = save_scene(board, tmp_path / "board.kinesis", view=view)

    fresh = BoardScene()
    restored = BoardView(fresh)
    restored.resize(800, 600)
    load_scene(fresh, path, restored)
    assert restored.transform().m11() == pytest.approx(2.0)
    center = restored.mapToScene(restored.viewport().rect().center())
    assert (center.x(), center.y()) == pytest.approx((120, -60), abs=2.0)


def test_each_kind_gets_its_own_list_in_the_file(populated, tmp_path):
    """The format says what it holds, rather than one list that has to be sorted
    out by reading a field on every record (#50)."""
    board, items = populated
    path = save_scene(board, tmp_path / "board.kinesis")
    data = json.loads(path.read_text())
    assert "items" not in data, "a kind was smuggled into a shared list"
    assert len(data["images"]) == len(items)
    assert {record["kind"] for record in data["images"]} == {"image"}


def test_a_group_survives_the_round_trip(populated, tmp_path):
    """The link and the colour both, and the colour by its roster index -- the
    order groups were made in is the durable fact, not the RGB."""
    board, items = populated
    parent, child = items[0], items[1]
    board.set_parent(child, parent)
    colour = board.group_color(parent)

    path = save_scene(board, tmp_path / "grouped.kinesis")
    fresh = BoardScene()
    load_scene(fresh, path)

    back_parent = fresh.find(parent.item_id)
    back_child = fresh.find(child.item_id)
    assert back_child.parent_id == back_parent.item_id
    assert fresh.group_color(back_parent) == colour
    assert fresh.group_color(back_child) == colour


def test_the_next_group_made_after_a_load_gets_a_new_colour(populated, tmp_path):
    board, items = populated
    board.set_parent(items[1], items[0])
    path = save_scene(board, tmp_path / "grouped.kinesis")

    fresh = BoardScene()
    load_scene(fresh, path)
    first_group = fresh.find(items[0].item_id)
    second_group = fresh.find(items[2].item_id)
    fresh.set_parent(first_group, second_group)
    assert fresh.group_color(second_group) != fresh.group_color(first_group)


def test_a_loaded_parent_does_not_drag_its_children_off_their_places(populated, tmp_path):
    """Every item's absolute position is in the file, so restoring a parent
    after one of its children must not carry that child by the parent's move."""
    board, items = populated
    board.set_parent(items[0], items[-1])   # child has the *lowest* z, so it loads first
    before = {i.item_id: (i.pos().x(), i.pos().y()) for i in items}

    path = save_scene(board, tmp_path / "order.kinesis")
    fresh = BoardScene()
    load_scene(fresh, path)
    after = {i.item_id: (i.pos().x(), i.pos().y()) for i in fresh.image_items()}
    assert after == before


def test_boxes_come_back_as_they_were_drawn(populated, tmp_path):
    from PySide6.QtGui import QColor

    board, _items = populated
    box = board.add_box(400, 250, pos=QPointF(70, -30), geometry="ellipse",
                        fill=QColor("#40ff8a65"), stroke=QColor("#2a7fc8"),
                        stroke_width=6.0, radius=0.25)
    path = save_scene(board, tmp_path / "drawn.kinesis")

    fresh = BoardScene()
    load_scene(fresh, path)
    back = fresh.find(box.item_id)
    assert (back.width, back.height) == (400, 250)
    assert back.geometry == "ellipse"
    assert (back.fill, back.stroke) == (box.fill, box.stroke)
    assert (back.stroke_width, back.radius) == (6.0, 0.25)
    assert (back.pos().x(), back.pos().y()) == (70, -30)


def test_a_boxs_place_in_the_stack_survives(populated, tmp_path):
    """A filled box is drawn under the images it groups, and has to load that
    way too -- coming back on top would hide the board it was drawn around."""
    from PySide6.QtGui import QColor

    board, items = populated
    box = board.add_box(900, 700, fill=QColor("#40ff8a65"))
    assert box.zValue() < min(i.zValue() for i in items)

    path = save_scene(board, tmp_path / "stack.kinesis")
    fresh = BoardScene()
    load_scene(fresh, path)
    back = fresh.find(box.item_id)
    assert back.zValue() < min(i.zValue() for i in fresh.image_items())
