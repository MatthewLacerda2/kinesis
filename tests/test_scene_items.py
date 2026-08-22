"""One id space, and the board-wide half of BoardScene.

Runs Qt offscreen: this is about real QGraphicsItems in a real scene, so it
cannot live in the pure layer. `Marker` is a minimal second kind -- it exists to
be *not an image* while still being a BoardItem, which is exactly the case the
id space, the listing and the removal paths have to survive. Boxes, notes and
arrows are that same case with something to draw.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.items import BoardItem  # noqa: E402
from kinesis.canvas.scene import BoardScene  # noqa: E402


class Marker(BoardItem):
    """A board item that is not an image and draws nothing."""

    kind = "marker"

    def boundingRect(self):
        return QRectF(-50, -50, 100, 100)

    def paint(self, painter, option, widget=None):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def image_path(tmp_path):
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("#446688"))
    path = tmp_path / "t.png"
    img.save(str(path))
    return path


@pytest.fixture
def board(qapp, image_path):
    """A board holding one image at the origin and one non-image item far right."""
    scene = BoardScene()
    image = scene.add_image(image_path, pos=QPointF(0, 0))
    marker = Marker()
    scene.addItem(marker)
    marker.setPos(5000, 0)
    marker.setZValue(0.5)
    return scene, image, marker


def test_board_items_holds_everything_image_items_only_images(board):
    scene, image, marker = board
    assert set(scene.board_items()) == {image, marker}
    assert scene.image_items() == [image]


def test_board_items_is_in_z_order(board):
    scene, image, marker = board
    assert scene.board_items() == [marker, image]  # marker z 0.5, image on top


def test_content_rect_covers_the_non_image_item(board):
    scene, _image, marker = board
    assert scene.content_rect().contains(marker.sceneBoundingRect())


def test_clear_board_empties_the_whole_board(board):
    scene, _image, _marker = board
    assert scene.clear_board() == 2
    assert scene.board_items() == []


def test_send_to_back_goes_under_the_non_image_item(board):
    scene, image, marker = board
    scene.send_to_back(image)
    assert image.zValue() < marker.zValue()


# ---------- one id space ----------

def test_find_answers_for_every_kind_not_only_images(board):
    """The whole of what makes an arrow able to name what it is plugged into."""
    scene, image, marker = board
    assert scene.find(image.item_id) is image
    assert scene.find(marker.item_id) is marker
    assert scene.find("no-such-id") is None


def test_ids_are_unique_across_kinds():
    ids = {Marker().item_id for _ in range(200)}
    assert len(ids) == 200


def test_every_kind_says_which_kind_it_is(board):
    _scene, image, marker = board
    assert (image.kind, marker.kind) == ("image", "marker")


def test_the_serialised_form_carries_the_id_and_the_kind(board):
    _scene, image, marker = board
    for item in (image, marker):
        record = item.to_dict()
        assert record["id"] == item.item_id
        assert record["kind"] == item.kind


def test_apply_dict_restores_the_id_so_attachments_survive_a_load():
    """A saved arrow names its ends by id, so a load that minted fresh ones
    would break every attachment in the file."""
    marker = Marker()
    marker.setPos(10, 20)
    marker.setScale(2.0)
    record = marker.to_dict()

    restored = Marker()
    restored.apply_dict(record)
    assert restored.item_id == marker.item_id
    assert (restored.pos().x(), restored.pos().y()) == (10, 20)
    assert restored.scale() == 2.0


# ---------- removal ----------

def test_remove_item_takes_any_kind_off_the_board(board):
    scene, _image, marker = board
    assert scene.remove_item(marker.item_id) is True
    assert scene.board_items() == scene.image_items()
    assert scene.remove_item(marker.item_id) is False


def test_remove_image_refuses_an_id_that_names_something_else(board):
    """Deleting the wrong thing and reporting success is the failure here."""
    scene, _image, marker = board
    assert scene.remove_image(marker.item_id) is False
    assert marker in scene.board_items()


def test_describe_image_refuses_an_id_that_names_something_else(board):
    scene, _image, marker = board
    assert scene.describe_image(marker.item_id, "not a picture") is None
