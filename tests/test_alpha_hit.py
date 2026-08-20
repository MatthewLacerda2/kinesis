"""Transparent PNGs are cut-outs: a grab falls through their see-through parts.

Runs Qt offscreen -- the shape comes off real QImage alpha and is consumed by
the scene's own hit-testing, so faking either would test nothing. No camera, no
window, no file: the images are built in memory.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.items import ImageItem  # noqa: E402
from kinesis.canvas.scene import BoardScene  # noqa: E402
from kinesis.ui.hand_control import HandControl  # noqa: E402

W, H = 400, 200


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def cutout(alpha: int = 255) -> QImage:
    """Transparent border, opaque block in the middle -- a logo in miniature."""
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.fillRect(W // 4, H // 4, W // 2, H // 2, QColor(255, 0, 0, alpha))
    p.end()
    return img


def solid() -> QImage:
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#446688"))
    return img


CENTRE = QPointF(0, 0)                      # inside the opaque block
CORNER = QPointF(-W / 2 + 5, -H / 2 + 5)    # inside the box, but see-through


def test_transparent_pixels_are_not_the_item(qapp):
    item = ImageItem(None, image=cutout())
    assert item.contains(CENTRE)
    assert not item.contains(CORNER)


def test_faint_pixels_still_grabbable(qapp):
    """40% opacity is faint, not absent -- the user can see it, so can aim at it."""
    item = ImageItem(None, image=cutout(alpha=102))
    assert item.contains(CENTRE)
    assert not item.contains(CORNER)


def test_opaque_image_keeps_its_whole_box(qapp):
    """The common case (jpg, opaque png) pays for no mask: shape == boundingRect."""
    item = ImageItem(None, image=solid())
    assert item.contains(CENTRE) and item.contains(CORNER)
    assert item.shape().boundingRect() == item.boundingRect()
    assert item.shape().elementCount() == 5  # one rectangle, not a mask


def test_degenerate_images_fall_back_to_the_box(qapp):
    """Nothing visible, or nothing to scan: never a ghost that can't be picked up."""
    blank = QImage(W, H, QImage.Format.Format_ARGB32)
    blank.fill(QColor(0, 0, 0, 0))
    assert ImageItem(None, image=blank).contains(CORNER)

    tiny = QImage(1, 1, QImage.Format.Format_ARGB32)
    tiny.fill(QColor(0, 0, 0, 0))
    one_px = ImageItem(None, image=tiny)
    assert one_px.contains(QPointF(0, 0))
    assert one_px.shape().boundingRect() == one_px.boundingRect()


def test_pinch_falls_through_to_the_image_behind(qapp):
    """The whole point: a logo over a photo, pinched through the hole."""
    board = BoardScene()
    photo = board.add_qimage(solid(), pos=QPointF(0, 0))
    logo = board.add_qimage(cutout(), pos=QPointF(0, 0))
    board.bring_to_front(logo)

    over_hole = logo.mapToScene(CORNER)
    assert not HandControl._hits(logo, over_hole)
    assert HandControl._hits(photo, over_hole)

    over_logo = logo.mapToScene(CENTRE)
    assert HandControl._hits(logo, over_logo)


def test_mouse_falls_through_to_the_image_behind(qapp):
    """view.itemAt goes through the scene, which asks the item's shape."""
    board = BoardScene()
    photo = board.add_qimage(solid(), pos=QPointF(0, 0))
    logo = board.add_qimage(cutout(), pos=QPointF(0, 0))
    board.bring_to_front(logo)

    assert board.itemAt(logo.mapToScene(CORNER), QTransform()) is photo
    assert board.itemAt(logo.mapToScene(CENTRE), QTransform()) is logo


def test_selection_and_trash_still_follow_the_box(qapp):
    """Decided: only *grabbing* uses the mask. The marquee and the bin use the
    bounding rect, which must therefore still cover the transparent border."""
    board = BoardScene()
    logo = board.add_qimage(cutout(), pos=QPointF(0, 0))
    assert logo.sceneBoundingRect().contains(logo.mapToScene(CORNER))
    assert logo.boundingRect().width() == W and logo.boundingRect().height() == H
