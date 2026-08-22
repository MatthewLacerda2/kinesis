"""The group graph: who is anchored to whom, and what colour that set is.

Parenting is stored **flat** -- an item holds its parent's id and the scene stays
a flat list -- and never with Qt's own `setParentItem`/`QGraphicsItemGroup`.
Verified 2026-08-18: Qt parenting puts children in parent coordinates, so a
parent `setScale(2)` both grew a child (50 -> 100px) *and* shoved its position
outward (100 -> 200). Children moving with a parent but *not* scaling with it is
the whole model, because a two-hand pinch already means "resize this image", and
Qt's version fights that as well as the z-order logic.

So the graph is ids, and this module is the arithmetic over it: children,
descendants, ancestors, the cycle check that keeps all three terminating, and
which colour a set is drawn in. Everything here takes the board's items as a
plain list and returns plain answers -- it holds no state, mutates nothing, and
so is the part of the group model that can be tested without a scene at all.

**The roster is fixed and cycled in order.** The first group made on a board is
always the first colour, the second always the second. That is the point of it:
a group's colour is not something you look up, it is something you come to know,
and that only happens if it is the same colour every session. Which is also why
what an item stores is its *index* into the roster and not a colour -- the order
is the durable fact, and the colour is derived from it.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

from .items import BoardItem

# Cycled in this order, and this order does not change. Chosen to sit clearly
# against the dark board and against each other, and to stay off the selection
# blue, so "in no group" reads as its own answer rather than as group one.
GROUP_COLORS: tuple[QColor, ...] = (
    QColor(255, 138, 101),   # coral
    QColor(255, 213, 79),    # amber
    QColor(129, 199, 132),   # green
    QColor(77, 208, 225),    # cyan
    QColor(186, 156, 255),   # violet
    QColor(240, 98, 146),    # pink
)


def color_for_index(index: int) -> QColor:
    """The roster colour a group index draws in. Wraps, so a board with more
    groups than colours repeats rather than running out."""
    return GROUP_COLORS[index % len(GROUP_COLORS)]


def children_of(items: list[BoardItem], item: BoardItem) -> list[BoardItem]:
    """The items anchored directly to `item`, in the order given."""
    return [i for i in items if i.parent_id == item.item_id]


def descendants_of(items: list[BoardItem], item: BoardItem) -> list[BoardItem]:
    """Every item under `item`, at any depth.

    Breadth-first with a seen-set: the set-parent path refuses cycles, and this
    still refuses to loop on one, because a graph edited from four places
    (mouse, hand, MCP, a loaded file) is not a graph to take on trust.
    """
    found: list[BoardItem] = []
    seen = {item.item_id}
    frontier = [item]
    while frontier:
        for child in children_of(items, frontier.pop()):
            if child.item_id in seen:
                continue
            seen.add(child.item_id)
            found.append(child)
            frontier.append(child)
    return found


def ancestors_of(items: list[BoardItem], item: BoardItem) -> list[BoardItem]:
    """The chain of parents above `item`, nearest first."""
    by_id = {i.item_id: i for i in items}
    chain: list[BoardItem] = []
    seen = {item.item_id}
    current = by_id.get(item.parent_id or "")
    while current is not None and current.item_id not in seen:
        chain.append(current)
        seen.add(current.item_id)
        current = by_id.get(current.parent_id or "")
    return chain


def would_cycle(items: list[BoardItem], child: BoardItem, parent: BoardItem) -> bool:
    """Would anchoring `child` to `parent` close a loop?

    A loop is not a strange board, it is a hang: every walk over the graph, and
    the move propagation itself, would recurse forever. Checked before the edge
    is made rather than defended against afterwards.
    """
    return parent is child or any(d is parent for d in descendants_of(items, child))


def group_of(items: list[BoardItem], item: BoardItem) -> BoardItem | None:
    """The item whose colour `item` is drawn in: itself or its nearest ancestor
    that is a group parent. None when it is in no group at all.

    Nearest rather than root, so a nested subgroup shows as its own set. The
    outermost parent still shows as its own, which is what makes nesting
    visible instead of collapsing a board into one colour.
    """
    for candidate in (item, *ancestors_of(items, item)):
        if candidate.group_index is not None:
            return candidate
    return None


def color_of(items: list[BoardItem], item: BoardItem) -> QColor | None:
    """The colour of the group `item` is in, or None when it is in none."""
    group = group_of(items, item)
    return None if group is None else color_for_index(group.group_index)
