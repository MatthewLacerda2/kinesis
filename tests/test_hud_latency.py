"""The two delay numbers the HUD reports, end to end.

Qt offscreen because the point of the first one is *where* it is measured: the
paint is a separate event-loop turn from the tick that requested it, and only a
real repaint can show that the easing and the board's own painting fall inside
the number. No camera and no window -- the hand data is synthetic.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

import time  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas.scene import BoardScene  # noqa: E402
from kinesis.canvas.view import BoardView  # noqa: E402
from kinesis.tracking.gestures import Detection, GestureEngine  # noqa: E402
from kinesis.tracking.protocol import Hand, HandFrame, Tuning  # noqa: E402
from kinesis.ui.hand_control import HandControl  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rig(qapp):
    scene = BoardScene()
    view = BoardView(scene)
    view.resize(800, 600)
    view.show()
    qapp.processEvents()
    return view, HandControl(view, Tuning())


def a_hand(**kw):
    fields = dict(handedness="Right", pinch_xy=(0.5, 0.5), raw_xy=(0.5, 0.5),
                  pinch_ratio=0.4, pinching=False, hand_scale=0.1)
    fields.update(kw)
    return Hand(**fields)


def test_nothing_is_stamped_until_something_paints(rig):
    view, _ = rig
    assert view.chrome.latency_ms == 0.0


def test_latency_is_stamped_at_paint_not_at_the_tick(rig, qapp):
    """The old number ended at the tick; this one has to outlast it.

    The tick eases the cursors and then only *requests* a repaint, so the paint
    happens strictly later. Measuring at the tick therefore cannot see the
    easing, the wait for Qt to dispatch, or the board's own paint -- which is
    the gap that made a 20 ms readout coexist with a 120 ms felt lag (#38).
    """
    view, control = rig
    captured = time.perf_counter()
    control.latest = HandFrame(t=captured, hands=[a_hand()], fps=30.0)

    control._tick()
    tick_span_ms = (time.perf_counter() - captured) * 1000.0
    assert view.chrome.latency_ms == 0.0, "the tick must not stamp it"

    qapp.processEvents()
    assert view.chrome.latency_ms > tick_span_ms


def test_latency_grows_while_the_frame_ages(rig, qapp):
    """A frame that stopped being refreshed is genuinely older every repaint."""
    view, control = rig
    control.latest = HandFrame(t=time.perf_counter(), hands=[a_hand()], fps=30.0)
    control._tick()
    qapp.processEvents()
    first = view.chrome.latency_ms

    view.viewport().update()
    qapp.processEvents()
    assert view.chrome.latency_ms > first


def test_stopping_tracking_clears_the_readout(rig, qapp):
    view, control = rig
    control.latest = HandFrame(t=time.perf_counter(), hands=[a_hand()], fps=30.0)
    control._tick()
    qapp.processEvents()
    assert view.chrome.latency_ms > 0.0

    view.chrome.clear_hand_overlay()
    qapp.processEvents()
    assert view.chrome.latency_ms == 0.0


def flat_hand(x: float):
    """A crude detection at horizontal offset `x`; only the four read points matter."""
    world = [(0.0, 0.0, 0.0)] * 21
    world[9] = (0.0, -0.095, 0.0)          # palm length, metres
    world[4] = (-0.007, -0.14, 0.0)        # fingertips 14 mm apart: a pinch
    world[8] = (0.007, -0.14, 0.0)
    landmarks = [(x, 0.5)] * 21
    landmarks[9] = (x, 0.4)
    landmarks[4] = (x - 0.01, 0.3)
    landmarks[8] = (x + 0.01, 0.3)
    return Detection(handedness="Right", landmarks=landmarks, world=world)


def test_the_tracker_publishes_the_filter_lag_it_used():
    """The group delay has to cross the queue: the UI cannot compute it.

    It is a property of the cutoff the tracker chose on that frame, not a span
    any clock on the UI side can time.
    """
    engine = GestureEngine(Tuning())
    t, hands = 0.0, []
    for i in range(90):
        hands = engine.update([flat_hand(0.3 + 0.004 * i)], t)
        t += 1 / 30

    published = hands[0].group_delay_ms
    assert published > 0.0
    assert published < 1000.0 / (2 * 3.14159 * Tuning().min_cutoff) + 1e-6
