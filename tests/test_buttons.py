"""Corner-button layout and hit test.

Pure geometry: QRect/QPoint need no display, so this needs no QApplication and
no window -- which is the point of the layout living outside view.py.
"""

from PySide6.QtCore import QPoint, QRect

from kinesis.ui import buttons


def test_top_left_buttons_sit_in_a_row_without_overlapping():
    rects = [buttons.top_left_rect(name) for name in buttons.TOP_LEFT]
    assert rects[0].topLeft() == QPoint(buttons.MARGIN, buttons.MARGIN)
    for first, second in zip(rects, rects[1:]):
        assert second.y() == first.y()
        assert second.x() == first.right() + 1 + buttons.GAP
        assert not first.intersects(second)


def test_hit_test_finds_each_button_by_its_own_rect():
    for name in buttons.TOP_LEFT:
        assert buttons.hit_top_left(buttons.top_left_rect(name).center()) == name


def test_hit_test_misses_the_gap_and_the_open_canvas():
    first, second = (buttons.top_left_rect(n) for n in buttons.TOP_LEFT[:2])
    gap = QPoint((first.right() + second.left()) // 2, first.center().y())
    assert buttons.hit_top_left(gap) is None
    assert buttons.hit_top_left(QPoint(0, 0)) is None
    assert buttons.hit_top_left(QPoint(600, 400)) is None


def test_trash_hugs_the_bottom_right_corner():
    viewport = QRect(0, 0, 1000, 700)
    box = buttons.trash_rect(viewport)
    assert viewport.right() - box.right() == buttons.MARGIN
    assert viewport.bottom() - box.bottom() == buttons.MARGIN
    assert box.size().width() == box.size().height() == buttons.SIZE


def test_every_top_left_button_has_a_glyph():
    assert set(buttons.TOP_LEFT) == set(buttons._GLYPHS)
