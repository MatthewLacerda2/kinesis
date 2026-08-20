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
    stored = json.loads(path.read_text())["items"]
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
    board, _items = populated
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


def test_a_file_from_another_format_version_is_refused(board, tmp_path, make_image):
    """No migrations, by policy -- so an unknown version must not be read at all."""
    board.add_image(make_image())
    path = tmp_path / "future.kinesis"
    path.write_text(json.dumps({"format": "kinesis", "version": FORMAT_VERSION + 1,
                                "packed": False, "items": []}))
    with pytest.raises(ValueError, match=str(FORMAT_VERSION + 1)):
        load_scene(board, path)
    assert len(board.image_items()) == 1, "a refused load must not empty the board"


def test_a_file_with_no_version_at_all_is_refused(board, tmp_path):
    path = tmp_path / "old.kinesis"
    path.write_text(json.dumps({"format": "kinesis", "items": []}))
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
