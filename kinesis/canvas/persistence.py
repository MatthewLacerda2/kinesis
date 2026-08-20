"""Save/load .kinesis scene files (JSON: image paths + per-item transform).

The file holds images and nothing else, so this module walks image_items() and
not board_items() -- a non-image board item has no serialised form here, and a
kind of item that gains one gains its own list in the format (and a version
bump) rather than being smuggled into "items".
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QPointF

from .scene import BoardScene

FORMAT_VERSION = 1


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

    items = []
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
        items.append(record)

    data = {"format": "kinesis", "version": FORMAT_VERSION, "packed": pack, "items": items}

    if view is not None:
        center = view.mapToScene(view.viewport().rect().center())
        data["viewport"] = {"x": center.x(), "y": center.y(), "zoom": view.transform().m11()}

    path.write_text(json.dumps(data, indent=2))
    return path


def load_scene(scene: BoardScene, path: str | Path, view=None) -> tuple[int, list[str]]:
    """Replace the board with `path`'s contents. Returns (loaded, missing_paths)."""
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

    for record in sorted(data.get("items", []), key=lambda r: r.get("z", 0)):
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
        item.setScale(record.get("scale", 1.0))
        item.setRotation(record.get("rotation", 0.0))
        item.setZValue(record.get("z", 0.0))
        if record.get("id"):
            item.item_id = record["id"]
        loaded += 1

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
