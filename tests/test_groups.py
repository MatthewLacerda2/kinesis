"""Items that move together: the graph, the propagation, and the colour.

Two halves, deliberately: the graph arithmetic in canvas/groups.py is checked
against plain lists of items, and the propagation is checked against a real
scene because the seam is Qt's own itemChange. `Marker` stands in for a kind
that is not an image -- the model has to be about board items, not pictures.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from kinesis.canvas import groups  # noqa: E402
from kinesis.canvas.items import BoardItem  # noqa: E402
from kinesis.canvas.scene import BoardScene  # noqa: E402


class Marker(BoardItem):
    kind = "marker"

    def boundingRect(self):
        return QRectF(-10, -10, 20, 20)

    def paint(self, painter, option, widget=None):
        pass


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def board(qapp):
    """A chain: root -> mid -> leaf, plus one item in no group at all."""
    scene = BoardScene()
    made = {}
    for name in ("root", "mid", "leaf", "loner"):
        item = Marker()
        scene.addItem(item)
        item.setPos(0, 0)
        made[name] = item
    scene.set_parent(made["mid"], made["root"])
    scene.set_parent(made["leaf"], made["mid"])
    return scene, made


# ---------- the graph ----------

def test_descendants_reach_every_depth(board):
    scene, m = board
    assert set(scene.descendants_of(m["root"])) == {m["mid"], m["leaf"]}
    assert scene.descendants_of(m["leaf"]) == []


def test_children_are_only_the_direct_ones(board):
    scene, m = board
    assert scene.children_of(m["root"]) == [m["mid"]]


def test_a_link_that_would_close_a_loop_is_refused(board):
    """Not a strange board -- a hang, since the move propagation would recurse."""
    scene, m = board
    assert scene.set_parent(m["root"], m["leaf"]) is False
    assert m["root"].parent_id is None
    assert scene.set_parent(m["root"], m["root"]) is False


def test_a_walk_terminates_even_if_a_loop_is_forced_into_the_data(board):
    """The graph is edited from four places and loaded from a file; a walk that
    trusted it would hang the app rather than draw a wrong board."""
    scene, m = board
    m["root"].parent_id = m["leaf"].item_id  # forced past set_parent's refusal
    items = scene.board_items()
    assert len(groups.descendants_of(items, m["root"])) == 2
    assert len(groups.ancestors_of(items, m["leaf"])) == 2


def test_an_unknown_id_is_refused_rather_than_guessed(board):
    scene, m = board
    assert scene.set_parent("no-such-id", m["root"]) is False
    assert scene.set_parent(m["loner"], "no-such-id") is False


# ---------- moving ----------

def test_a_parent_carries_its_whole_subtree(board):
    scene, m = board
    m["root"].setPos(100, 50)
    assert m["mid"].pos() == QPointF(100, 50)
    assert m["leaf"].pos() == QPointF(100, 50), "the move stopped one level down"
    assert m["loner"].pos() == QPointF(0, 0)


def test_a_child_moves_without_dragging_its_parent(board):
    scene, m = board
    m["mid"].setPos(30, 30)
    assert m["root"].pos() == QPointF(0, 0)
    assert m["leaf"].pos() == QPointF(30, 30)


def test_scale_does_not_propagate(board):
    """The two-hand pinch means 'resize *this*', so a propagating scale would
    fight the primary gesture."""
    scene, m = board
    m["root"].setScale(3.0)
    assert m["mid"].scale() == 1.0 and m["leaf"].scale() == 1.0


def test_moves_accumulate_rather_than_snapping_to_the_parent(board):
    scene, m = board
    m["leaf"].setPos(200, 0)          # the child sits offset from its parent
    m["root"].setPos(10, 10)
    assert m["leaf"].pos() == QPointF(210, 10), "the offset within the set was lost"


def test_detaching_stops_the_carry_and_leaves_it_where_it_is(board):
    scene, m = board
    m["root"].setPos(100, 0)
    assert scene.set_parent(m["mid"], None) is True
    m["root"].setPos(200, 0)
    assert m["mid"].pos() == QPointF(100, 0)
    assert m["leaf"].pos() == QPointF(100, 0), "the rest of the subtree came apart"


# ---------- deleting ----------

def test_deleting_a_parent_takes_the_subtree_with_it(board):
    scene, m = board
    assert scene.remove_item(m["root"]) is True
    assert scene.board_items() == [m["loner"]]


def test_deleting_a_child_leaves_the_rest_of_the_set(board):
    scene, m = board
    scene.remove_item(m["mid"])
    assert set(scene.board_items()) == {m["root"], m["loner"]}


# ---------- colour ----------

def test_groups_take_the_roster_in_the_order_they_are_made(qapp):
    scene = BoardScene()
    parents = []
    for _ in range(3):
        parent, child = Marker(), Marker()
        scene.addItem(parent)
        scene.addItem(child)
        scene.set_parent(child, parent)
        parents.append(parent)
    assert [p.group_index for p in parents] == [0, 1, 2]
    assert [scene.group_color(p) for p in parents] == list(groups.GROUP_COLORS[:3])


def test_the_roster_wraps_rather_than_running_out():
    n = len(groups.GROUP_COLORS)
    assert groups.color_for_index(n) == groups.GROUP_COLORS[0]


def test_a_child_is_drawn_in_its_nearest_group_colour(board):
    """Nearest and not root, so a nested set reads as its own rather than the
    whole board collapsing into one colour."""
    scene, m = board
    assert scene.group_color(m["leaf"]) == scene.group_color(m["mid"])
    assert scene.group_color(m["mid"]) != scene.group_color(m["root"])


def test_an_item_in_no_group_has_no_colour_of_its_own(board):
    scene, m = board
    assert scene.group_color(m["loner"]) is None


def test_a_group_keeps_its_colour_after_its_last_child_leaves(board):
    """A roster that renumbered itself as sets came and went would give the
    board different colours every session, which is what the fixed order is for."""
    scene, m = board
    before = scene.group_color(m["root"])
    scene.set_parent(m["mid"], None)
    assert m["root"].group_index is not None
    assert groups.color_for_index(m["root"].group_index) == before


def test_a_kind_that_sets_its_own_flags_still_carries_its_children(board, qapp):
    """Qt's setFlags replaces the set rather than adding to it, so a kind that
    lists its own flags would otherwise switch the whole model off silently."""
    from PySide6.QtWidgets import QGraphicsItem

    scene, m = board
    m["root"].setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
    m["root"].setPos(60, 0)
    assert m["leaf"].pos() == QPointF(60, 0)


# ---------- what it looks like ----------

def _painted_colours(view) -> set[tuple[int, int, int]]:
    """Every colour in a grab of the view. The outline is chrome, painted in
    drawForeground, so nothing short of rendering the widget can see it."""
    image = view.grab().toImage()
    return {QColor(image.pixel(x, y)).getRgb()[:3]
            for x in range(image.width()) for y in range(image.height())}


def test_a_grouped_item_is_outlined_in_its_group_colour(qapp, make_image):
    """The point of the whole colour half of #4: without it, parenting changes
    what dragging does and leaves no way to see that it did."""
    from kinesis.canvas.chrome import SELECT_COLOR
    from kinesis.canvas.view import BoardView

    scene = BoardScene()
    parent = scene.add_image(make_image("p.png"), pos=QPointF(0, 0))
    child = scene.add_image(make_image("c.png"), pos=QPointF(0, 0))
    scene.set_parent(child, parent)
    view = BoardView(scene)
    view.resize(400, 300)
    view.zoom_to_fit()
    view.zoom_by(0.5)   # leave room round the edges for the outline to land in

    parent.setSelected(True)
    painted = _painted_colours(view)
    assert scene.group_color(parent).getRgb()[:3] in painted
    assert SELECT_COLOR.getRgb()[:3] not in painted, "a grouped item drew the plain blue"


def test_an_ungrouped_item_keeps_the_plain_selection_colour(qapp, make_image):
    from kinesis.canvas.chrome import SELECT_COLOR
    from kinesis.canvas.view import BoardView

    scene = BoardScene()
    item = scene.add_image(make_image("only.png"), pos=QPointF(0, 0))
    view = BoardView(scene)
    view.resize(400, 300)
    view.zoom_to_fit()
    view.zoom_by(0.5)   # leave room round the edges for the outline to land in

    item.setSelected(True)
    assert SELECT_COLOR.getRgb()[:3] in _painted_colours(view)
