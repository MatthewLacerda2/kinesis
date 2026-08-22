"""Save/load .kinesis scene files (JSON: one list per kind of board item).

**A kind gets its own list, never a shared "items" one.** A single list keyed by
a `kind` field reads as the flexible choice and is the trap: every reader then
branches on the kind before it knows what fields to expect, and the day two
kinds disagree about what "size" means the file is ambiguous rather than wrong.
Separate lists make the format say what it holds, and adding a kind is adding a
key and a loader -- plus a version bump, since there are no migrations.

What is *in* each record is the item's own business: this module calls
to_dict()/apply_dict() on BoardItem and never reaches inside them. The one
exception is the image path, which this module rewrites for packing, because
where a file went is the saver's fact and not the item's.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QPointF

from .items import BoxItem, NoteItem
from .scene import BoardScene

# 6: notes (#52) get their own list, for the same reason boxes did -- a version
# 5 file has none, and a board silently missing the words written on it is the
# quiet misread the version check turns into a refusal. (5 was: boxes, #51. 4
# was: parent links and group colours, #4. 3 was: one list per kind, #50. 2 was:
# descriptions, #9.)
FORMAT_VERSION = 6


def save_scene(scene: BoardScene, path: str | Path, view=None, pack: bool = False) -> Path:
    """Write the board to `path`.

    pack=True copies every image into a sibling `<name>_files/` folder and stores
    relative paths, so the scene can be moved to another machine intact.
    """
    path = Path(path).expanduser()
    if path.suffix != ".kinesis":
        path = path.with_suffix(".kinesis")

    pack_dir = path.with_name(path.stem + "_files")
    if pack:
        pack_dir.mkdir(parents=True, exist_ok=True)

    images = []
    for item in scene.image_items():
        record = item.to_dict()
        src = record.get("path")
        if src:
            if pack:
                dest = pack_dir / Path(src).name
                if not dest.exists() or not _same_file(Path(src), dest):
                    dest = _unique(pack_dir, Path(src).name)
                    shutil.copy2(src, dest)
                record["path"] = str(dest.relative_to(path.parent))
            else:
                record["path"] = str(Path(src).resolve())
        images.append(record)

    data = {"format": "kinesis", "version": FORMAT_VERSION, "packed": pack,
            "images": images,
            "boxes": [item.to_dict() for item in scene.box_items()],
            "notes": [item.to_dict() for item in scene.note_items()]}

    if view is not None:
        center = view.mapToScene(view.viewport().rect().center())
        data["viewport"] = {"x": center.x(), "y": center.y(), "zoom": view.transform().m11()}

    path.write_text(json.dumps(data, indent=2))
    return path


def load_scene(scene: BoardScene, path: str | Path, view=None) -> tuple[int, list[str]]:
    """Replace the board with `path`'s contents. Returns (loaded, missing_paths).

    "loaded" counts every item of every kind; "missing" is images whose file has
    gone, which is the only way an item can fail to come back.
    """
    path = Path(path).expanduser()
    data = json.loads(path.read_text())
    if data.get("format") != "kinesis":
        raise ValueError(f"{path} is not a .kinesis scene")
    # There are no migrations, by policy, so the only safe thing a build can do
    # with a version it does not write is refuse it -- loudly, and before the
    # board is cleared. Reading it anyway is how a renamed or repurposed field
    # becomes a board that loads wrong and quietly stays wrong, which is the
    # whole reason the version bump is mandatory.
    version = data.get("version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"{path} is format version {version!r}; this build only reads "
            f"version {FORMAT_VERSION}, and there is no migration path"
        )

    scene.clear_board()
    loaded, missing = 0, []
    placed: list[tuple[object, dict]] = []

    for record in sorted(data.get("images", []), key=lambda r: r.get("z", 0)):
        src = record.get("path")
        if not src:
            continue
        resolved = Path(src)
        if not resolved.is_absolute():
            resolved = (path.parent / resolved).resolve()
        if not resolved.exists():
            missing.append(str(src))
            continue
        try:
            item = scene.add_image(
                resolved,
                pos=QPointF(record.get("x", 0.0), record.get("y", 0.0)),
                long_edge=None,  # transform comes from the file, don't renormalise
            )
        except (OSError, ValueError):
            missing.append(str(src))
            continue
        item.apply_dict(record)
        placed.append((item, record))
        loaded += 1

    # Built bare and then filled in from the record: every field these kinds
    # have is in apply_dict already, and going through add_box/add_note would
    # re-decide the placement and the z-order the file is telling us.
    for key, blank in (("boxes", lambda: BoxItem(1.0, 1.0)),
                       ("notes", lambda: NoteItem("."))):
        for record in sorted(data.get(key, []), key=lambda r: r.get("z", 0)):
            item = scene.add_item(blank())
            item.apply_dict(record)
            placed.append((item, record))
            loaded += 1

    # Parent links go on only once every item is where the file says it is: a
    # parent restored after one of its children would otherwise carry that child
    # by the whole of its own move, and the board would load subtly scattered.
    for item, record in placed:
        item.apply_links(record)
    # Past every group this board has made, so the next one gets a new colour
    # rather than reusing the first.
    scene._next_group = max((i.group_index + 1 for i in scene.board_items()
                             if i.group_index is not None), default=0)

    # Above everything on the board, so the next added item stacks on top.
    scene._next_z = max((i.zValue() for i in scene.board_items()), default=1.0) + 1.0

    vp = data.get("viewport")
    if view is not None and vp:
        view.resetTransform()
        zoom = max(0.02, min(64.0, vp.get("zoom", 1.0)))
        view.scale(zoom, zoom)
        view.centerOn(vp.get("x", 0.0), vp.get("y", 0.0))

    return loaded, missing


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size
    except OSError:
        return False


def _unique(folder: Path, name: str) -> Path:
    candidate = folder / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while (folder / f"{stem}-{n}{suffix}").exists():
        n += 1
    return folder / f"{stem}-{n}{suffix}"
