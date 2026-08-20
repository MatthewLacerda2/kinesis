"""Single <-> two-hand handoffs, trash drop, and the tightened thresholds.

Runs Qt offscreen: the two-hand transform needs real scene/view geometry, so it
cannot live in the pure gesture layer.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.scene import BoardScene  # noqa: E402
from kinesis.canvas.view import BoardView  # noqa: E402
from kinesis.tracking.protocol import Hand, HandFrame, Tuning  # noqa: E402
from kinesis.ui.hand_control import HandControl  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def image_path(tmp_path_factory):
    img = QImage(600, 400, QImage.Format.Format_RGB32)
    img.fill(QColor("#446688"))
    path = Path(tempfile.mkdtemp()) / "t.png"
    img.save(str(path))
    return path


@pytest.fixture
def rig(qapp, image_path):
    scene = BoardScene()
    view = BoardView(scene)
    view.resize(1000, 700)
    view.show()
    qapp.processEvents()
    view.resetTransform()
    view.centerOn(0, 0)
    item = scene.add_image(image_path, pos=QPointF(0, 0))
    control = HandControl(view, Tuning())
    return scene, view, item, control


def canvas_xy(view, scene_point):
    """Scene point -> normalised canvas coords the tracker would emit."""
    vp = view.viewport().rect()
    p = view.mapFromScene(scene_point)
    return p.x() / vp.width(), p.y() / vp.height()


def settle(hc, f, n=15):
    """Feed a frame repeatedly so the UI lerp reaches the target position.

    One tick only moves a cursor `lerp_alpha` of the way there, so a single
    tick lands short of the intended point.
    """
    hc.latest = f
    for _ in range(n):
        hc._tick()


def frame(*hands):
    return HandFrame(t=time.perf_counter(), fps=30.0, hands=[
        Hand(handedness=label, pinch_xy=(x, y), raw_xy=(x, y),
             pinch_ratio=0.10 if pinching else 0.80,
             pinching=pinching, hand_scale=0.2)
        for label, x, y, pinching in hands
    ])


def test_pinch_defaults_are_in_metric_units():
    """The thresholds gate a metric ratio now, so they carry no old value.

    18 mm between the fingertip landmarks is finger pads touching, on a palm
    that measures about 95 mm: the trigger has to sit just above that or the
    pinch cannot be made at all.
    """
    t = Tuning()
    assert t.pinch_close == pytest.approx(0.20)
    assert 0.018 / 0.095 < t.pinch_close < 0.25
    # The band keeps its 1.5x shape, so hysteresis is as flicker-resistant as before.
    assert t.pinch_open / t.pinch_close == pytest.approx(1.5)


def test_second_pinch_on_same_image_scales_it(rig):
    scene, view, item, hc = rig
    left = canvas_xy(view, QPointF(-100, 0))
    right = canvas_xy(view, QPointF(100, 0))

    hc.latest = frame(("Right", *left, True))
    hc._tick()
    assert hc._grabs and hc._two_hand is None
    scale_before = item.scale()

    # Second hand pinches the same image -> two-hand scale, no independent grab.
    hc.latest = frame(("Right", *left, True), ("Left", *right, True))
    hc._tick()
    assert hc._two_hand is not None
    assert item.scale() == pytest.approx(scale_before), "must not jump when the 2nd hand joins"

    # Hands move apart -> image grows.
    far_left = canvas_xy(view, QPointF(-200, 0))
    far_right = canvas_xy(view, QPointF(200, 0))
    settle(hc, frame(("Right", *far_left, True), ("Left", *far_right, True)))
    assert item.scale() > scale_before * 1.5

    # And back together -> shrinks back.
    settle(hc, frame(("Right", *left, True), ("Left", *right, True)))
    assert item.scale() == pytest.approx(scale_before, rel=0.05)


def test_releasing_one_hand_hands_back_without_jump(rig):
    scene, view, item, hc = rig
    left = canvas_xy(view, QPointF(-100, 0))
    right = canvas_xy(view, QPointF(100, 0))

    hc.latest = frame(("Right", *left, True))
    hc._tick()
    hc.latest = frame(("Right", *left, True), ("Left", *right, True))
    hc._tick()
    far = canvas_xy(view, QPointF(-220, 0))
    settle(hc, frame(("Right", *far, True),
                     ("Left", *canvas_xy(view, QPointF(220, 0)), True)))

    pos_before = QPointF(item.pos())
    scale_before = item.scale()

    # Left lets go; Right keeps holding.
    hc.latest = frame(("Right", *far, True), ("Left", *canvas_xy(view, QPointF(220, 0)), False))
    hc._tick()
    assert hc._two_hand is None
    assert "Right" in hc._grabs
    assert item.pos().x() == pytest.approx(pos_before.x(), abs=2.0), "image jumped on handoff"
    assert item.pos().y() == pytest.approx(pos_before.y(), abs=2.0)
    assert item.scale() == pytest.approx(scale_before)

    # Still draggable with the one remaining hand.
    moved = canvas_xy(view, QPointF(-120, 90))
    settle(hc, frame(("Right", *moved, True)))
    assert item.pos() != pos_before


def test_two_hands_on_empty_canvas_zoom_the_view(rig):
    scene, view, item, hc = rig
    scene.remove_image(item)
    zoom_before = view.transform().m11()

    a = canvas_xy(view, QPointF(-100, 0))
    b = canvas_xy(view, QPointF(100, 0))
    hc.latest = frame(("Right", *a, True), ("Left", *b, True))
    hc._tick()
    assert hc._canvas_gesture is not None
    assert not hc._grabs

    settle(hc, frame(("Right", 0.1, 0.5, True), ("Left", 0.9, 0.5, True)))
    assert view.transform().m11() > zoom_before


def test_release_over_trash_deletes_the_image(rig):
    scene, view, item, hc = rig
    start = canvas_xy(view, QPointF(0, 0))
    hc.latest = frame(("Right", *start, True))
    hc._tick()
    assert hc._grabs

    # Drag onto the bin and let go.
    vp = view.viewport().rect()
    bin_center = view.trash_rect().center()
    over = (bin_center.x() / vp.width(), bin_center.y() / vp.height())
    settle(hc, frame(("Right", *over, True)))
    assert view.trash_armed is True

    hc.latest = frame(("Right", *over, False))
    hc._tick()
    assert scene.image_items() == []
    assert not hc._grabs
    assert view.trash_armed is False


def test_dropping_elsewhere_does_not_delete(rig):
    scene, view, item, hc = rig
    start = canvas_xy(view, QPointF(0, 0))
    hc.latest = frame(("Right", *start, True))
    hc._tick()
    hc.latest = frame(("Right", *start, False))
    hc._tick()
    assert len(scene.image_items()) == 1


def test_hand_that_leaves_releases_while_the_other_stays(rig):
    """A departed hand must time out on its own clock, not the other hand's."""
    scene, view, item, hc = rig
    hc.tuning = hc.tuning.replace(lost_hold_ms=20.0)
    left = canvas_xy(view, QPointF(-100, 0))
    right = canvas_xy(view, QPointF(100, 0))

    hc.latest = frame(("Right", *left, True))
    hc._tick()
    hc.latest = frame(("Right", *left, True), ("Left", *right, True))
    hc._tick()
    assert hc._two_hand is not None
    scale_before = item.scale()

    # Left walks out of frame; Right stays visible and keeps pinching.
    hc.latest = frame(("Right", *left, True))
    hc._tick()
    time.sleep(0.05)
    hc._tick()

    assert "Left" not in hc._grabs
    assert hc.cursors["Left"].pinching is False
    assert hc._two_hand is None

    # The one remaining hand drags; it must not scale against a ghost.
    settle(hc, frame(("Right", *canvas_xy(view, QPointF(-300, 0)), True)))
    assert item.scale() == pytest.approx(scale_before)


def test_brief_dropout_of_one_hand_keeps_its_grab(rig):
    """The hold window's original job: ride out a lost detection, not a departure."""
    scene, view, item, hc = rig
    left = canvas_xy(view, QPointF(-100, 0))
    right = canvas_xy(view, QPointF(100, 0))

    hc.latest = frame(("Right", *left, True))
    hc._tick()
    hc.latest = frame(("Right", *left, True), ("Left", *right, True))
    hc._tick()
    assert hc._two_hand is not None

    # One frame without Left, well inside the default 300ms window.
    hc.latest = frame(("Right", *left, True))
    hc._tick()
    assert "Left" in hc._grabs
    assert hc._two_hand is not None

    # Left comes back and the scale carries on from where it was.
    settle(hc, frame(("Right", *canvas_xy(view, QPointF(-200, 0)), True),
                     ("Left", *canvas_xy(view, QPointF(200, 0)), True)))
    assert hc._two_hand is not None
    assert item.scale() > 1.5


def test_all_hands_vanishing_still_releases_everything(rig):
    scene, view, item, hc = rig
    hc.tuning = hc.tuning.replace(lost_hold_ms=20.0)
    start = canvas_xy(view, QPointF(0, 0))
    hc.latest = frame(("Right", *start, True))
    hc._tick()
    assert hc._grabs

    hc.latest = frame()
    hc._tick()
    assert hc._grabs, "still inside the hold window"
    time.sleep(0.05)
    hc._tick()
    assert not hc._grabs
    assert hc.cursors["Right"].pinching is False
    assert len(scene.image_items()) == 1


def test_hand_vanishing_over_the_bin_does_not_delete(rig):
    """A lost detection is not a drop -- the hold window exists to prevent this."""
    scene, view, item, hc = rig
    hc.tuning = hc.tuning.replace(lost_hold_ms=20.0)
    vp = view.viewport().rect()
    bin_center = view.trash_rect().center()
    over = (bin_center.x() / vp.width(), bin_center.y() / vp.height())
    other = canvas_xy(view, QPointF(0, 0))

    # Right grabs the image and carries it onto the bin; Left is merely present.
    hc.latest = frame(("Right", *other, True), ("Left", *other, False))
    hc._tick()
    assert "Right" in hc._grabs
    settle(hc, frame(("Right", *over, True), ("Left", *other, False)))
    assert view.trash_armed is True

    # Right leaves the frame while parked on the bin; Left stays visible.
    hc.latest = frame(("Left", *other, False))
    hc._tick()
    time.sleep(0.05)
    hc._tick()
    assert "Right" not in hc._grabs
    assert len(scene.image_items()) == 1, "a hand that vanished must not delete"
    assert view.trash_armed is False
