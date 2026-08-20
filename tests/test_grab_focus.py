"""A grab claims the board, and a fist on nothing means the selected image.

Two rules meet here and every interesting case is in the overlap, so they are
tested together: the second hand joins whatever the first hand already holds
without having to find it, and a fist closing on empty canvas picks up the
selection instead of meaning nothing. Where they collide -- both hands on empty
canvas, which is the pan/zoom gesture -- is the handoff test at the bottom.

The board here is deliberately small (`long_edge=200` against a 998x698
viewport) where test_two_hand's fills most of the view. These rules are all
about what happens *off* the picture, and a rig with no room off the picture
would have every coordinate sitting a pixel from an edge.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF  # noqa: E402

from kinesis.canvas.scene import BoardScene  # noqa: E402
from kinesis.canvas.view import BoardView  # noqa: E402
from kinesis.tracking.protocol import Tuning  # noqa: E402
from kinesis.ui.hand_control import HandControl  # noqa: E402
from tests.test_two_hand import frame, settle  # noqa: E402

# Normalised viewport coords. The image spans scene +/-100 in x, so anything
# past ~0.6 across is off it with room to spare.
ON = (0.5, 0.5)
OFF = (0.9, 0.5)
FAR = (0.99, 0.5)


@pytest.fixture
def rig(qapp, make_image):
    scene = BoardScene()
    view = BoardView(scene)
    view.resize(1000, 700)
    view.show()
    qapp.processEvents()
    view.resetTransform()
    view.centerOn(0, 0)
    item = scene.add_image(make_image(), pos=QPointF(0, 0), long_edge=200)
    return scene, view, item, HandControl(view, Tuning())


def scene_at(view, norm):
    vp = view.viewport().rect()
    return view.mapToScene(int(norm[0] * vp.width()), int(norm[1] * vp.height()))


def test_second_hand_joins_the_scale_from_off_the_image(rig):
    scene, view, item, hc = rig
    hc.latest = frame(("Right", *ON, True))
    hc._tick()
    assert hc._grabs and hc._two_hand is None
    scale_before = item.scale()

    hc.latest = frame(("Right", *ON, True), ("Left", 0.7, 0.5, True))
    hc._tick()
    assert hc._two_hand is not None, "second hand must join from off the image"
    assert item.scale() == pytest.approx(scale_before), "must not jump on join"

    settle(hc, frame(("Right", *ON, True), ("Left", *FAR, True)))
    assert item.scale() > scale_before * 1.5


def test_scaling_pivots_on_the_holding_hand(rig):
    """The point of the image under the holding hand stays under it."""
    scene, view, item, hc = rig
    hold = (0.55, 0.5)
    hc.latest = frame(("Right", *hold, True))
    hc._tick()
    hc.latest = frame(("Right", *hold, True), ("Left", 0.7, 0.5, True))
    hc._tick()

    anchor = scene_at(view, hold)
    local_before = item.mapFromScene(anchor)
    scale_before = item.scale()
    settle(hc, frame(("Right", *hold, True), ("Left", *FAR, True)))

    assert item.scale() > scale_before * 1.5, "the gesture has to have actually scaled"
    local_after = item.mapFromScene(anchor)
    assert local_after.x() == pytest.approx(local_before.x(), abs=2.0)
    assert local_after.y() == pytest.approx(local_before.y(), abs=2.0)


def test_second_hand_cannot_take_a_different_image(rig, make_image):
    """One image at a time: the second fist joins the grab, it never forks."""
    scene, view, item, hc = rig
    other = scene.add_image(make_image("other.png"), pos=QPointF(300, 0), long_edge=200)
    other_pos = QPointF(other.pos())

    hc.latest = frame(("Right", *ON, True))
    hc._tick()
    hc.latest = frame(("Right", *ON, True), ("Left", 0.8, 0.5, True))  # on `other`
    hc._tick()

    assert hc._two_hand is not None and hc._two_hand["item"] is item
    assert {it for it, _ in hc._grabs.values()} == {item}, "the other image is untouchable"
    assert other.pos() == other_pos


def test_fist_on_empty_canvas_moves_the_selected_image(rig):
    scene, view, item, hc = rig
    item.setSelected(True)
    start = QPointF(item.pos())

    hc.latest = frame(("Right", *OFF, True))
    hc._tick()
    assert hc._grabs and hc._fallback == {"Right"}
    assert item.pos() == start, "picking it up must not move it"

    settle(hc, frame(("Right", 0.7, 0.5, True)))
    delta = scene_at(view, (0.7, 0.5)).x() - scene_at(view, OFF).x()
    assert item.pos().x() == pytest.approx(start.x() + delta, abs=25), "moves by the hand's delta"


def test_fist_on_empty_canvas_with_nothing_selected_does_nothing(rig):
    scene, view, item, hc = rig
    scene.clearSelection()
    start = QPointF(item.pos())

    settle(hc, frame(("Right", *OFF, True)))
    assert not hc._grabs
    assert item.pos() == start


def test_second_fist_on_empty_canvas_hands_off_to_the_canvas(rig):
    """Both hands on nothing means the board: drop the selection, pan and zoom."""
    scene, view, item, hc = rig
    item.setSelected(True)
    zoom_before = view.transform().m11()

    hc.latest = frame(("Right", *OFF, True))
    hc._tick()
    assert hc._fallback == {"Right"}
    handed_off_at = QPointF(item.pos())

    hc.latest = frame(("Right", *OFF, True), ("Left", 0.1, 0.5, True))
    hc._tick()
    assert not hc._grabs and not hc._fallback
    assert not scene.selectedItems(), "the selection must clear, or pan/zoom stays shut"
    assert hc.canvas_gesture.active
    assert item.pos() == handed_off_at, "released where it got to, no snap back"

    settle(hc, frame(("Right", *FAR, True), ("Left", 0.01, 0.5, True)))
    assert view.transform().m11() > zoom_before


def test_scaling_an_off_target_grab_pivots_on_the_image(rig):
    """Neither hand is on the picture, so it must grow in place, not fly off."""
    scene, view, item, hc = rig
    item.setSelected(True)

    hc.latest = frame(("Right", *OFF, True))          # fallback grab, off the image
    hc._tick()
    hc.latest = frame(("Right", *OFF, True), ("Left", *ON, True))  # 2nd hand finds it
    hc._tick()
    assert hc._two_hand is not None
    pos_before, scale_before = QPointF(item.pos()), item.scale()

    settle(hc, frame(("Right", *OFF, True), ("Left", 0.05, 0.5, True)))
    assert item.scale() > scale_before * 1.5
    assert item.pos().x() == pytest.approx(pos_before.x(), abs=2.0), "image flew off"
    assert item.pos().y() == pytest.approx(pos_before.y(), abs=2.0)
