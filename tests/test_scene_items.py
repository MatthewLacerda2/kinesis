"""board_items() vs image_items() -- the split has to survive a non-image item.

Runs Qt offscreen: the distinction is about real QGraphicsItems in a real scene,
so it cannot live in the pure layer. A plain QGraphicsRectItem stands in for the
pen strokes and handles that are coming; nothing here needs it to be either.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsRectItem  # noqa: E402

from kinesis.canvas.scene import BoardScene  # noqa: E402


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
    stroke = QGraphicsRectItem(QRectF(0, 0, 100, 100))
    scene.addItem(stroke)
    stroke.setPos(5000, 0)
    stroke.setZValue(0.5)
    return scene, image, stroke


def test_board_items_holds_everything_image_items_only_images(board):
    scene, image, stroke = board
    assert set(scene.board_items()) == {image, stroke}
    assert scene.image_items() == [image]


def test_board_items_is_in_z_order(board):
    scene, image, stroke = board
    assert scene.board_items() == [stroke, image]  # stroke z 0.5, image on top


def test_content_rect_covers_the_non_image_item(board):
    scene, _image, stroke = board
    assert scene.content_rect().contains(stroke.sceneBoundingRect())


def test_clear_board_empties_the_whole_board(board):
    scene, _image, _stroke = board
    assert scene.clear_board() == 2
    assert scene.board_items() == []


def test_send_to_back_goes_under_the_non_image_item(board):
    scene, image, stroke = board
    scene.send_to_back(image)
    assert image.zValue() < stroke.zValue()


def test_find_only_answers_for_images(board):
    scene, image, _stroke = board
    assert scene.find(image.item_id) is image
    assert scene.find("no-such-id") is None
