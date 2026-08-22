"""Local control channel so an external process can drive the board.

A loopback-only TCP server speaking newline-delimited JSON. It runs on the Qt
event loop, so command handlers touch the scene on the GUI thread and need no
locking. The port and a random token are written to ~/.config/kinesis/control.json;
callers must present the token, so another local process can't quietly reach in.

This is what kinesis/mcp_server.py talks to.
"""

from __future__ import annotations

import base64
import json
import secrets
from pathlib import Path

from PySide6.QtCore import QBuffer, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtNetwork import QHostAddress, QTcpServer

from .canvas.items import parse_color

CONTROL_PATH = Path.home() / ".config" / "kinesis" / "control.json"

SHOT_MAX_EDGE = 1400


def _color(value, field: str):
    """A colour off the wire, or None for "no colour at all".

    An unreadable string is an error naming the field rather than a None, which
    matters because None is meaningful here: a caller who mistyped a colour and
    got the "a box needs a fill or a border" refusal would go looking in exactly
    the wrong place.
    """
    if value is None or value == "":
        return None
    colour = parse_color(value)
    if colour is None:
        raise ValueError(f"{field}: {value!r} is not a colour -- "
                         "use '#rrggbb', or '#aarrggbb' for one with alpha")
    return colour


class ControlServer(QObject):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.board = window.board
        self.token = secrets.token_hex(16)

        self.server = QTcpServer(self)
        if not self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            self.port = None
            return
        self.port = self.server.serverPort()
        self.server.newConnection.connect(self._on_connection)
        self._write_config()

    # ---------- plumbing ----------

    def _write_config(self) -> None:
        CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_PATH.write_text(json.dumps({"port": self.port, "token": self.token}))
        CONTROL_PATH.chmod(0o600)

    def shutdown(self) -> None:
        self.server.close()
        CONTROL_PATH.unlink(missing_ok=True)

    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        sock.setProperty("buffer", "")
        sock.readyRead.connect(lambda s=sock: self._on_ready(s))
        sock.disconnected.connect(sock.deleteLater)

    def _on_ready(self, sock) -> None:
        buffer = sock.property("buffer") or ""
        buffer += bytes(sock.readAll()).decode("utf-8", "replace")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if line.strip():
                reply = self._dispatch(line)
                sock.write((json.dumps(reply) + "\n").encode())
                sock.flush()
                # A screenshot payload is far larger than one socket buffer.
                while sock.bytesToWrite() > 0:
                    if not sock.waitForBytesWritten(5000):
                        break
        sock.setProperty("buffer", buffer)

    def _dispatch(self, line: str) -> dict:
        try:
            request = json.loads(line)
        except ValueError as exc:
            return {"ok": False, "error": f"bad JSON: {exc}"}
        if request.get("token") != self.token:
            return {"ok": False, "error": "bad token"}

        command = request.get("cmd")
        handler = getattr(self, f"cmd_{command}", None)
        if handler is None:
            return {"ok": False, "error": f"unknown command: {command}"}
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001 - never kill the app over a command
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result.setdefault("ok", True)
        return result

    # ---------- commands ----------

    def cmd_ping(self, _request: dict) -> dict:
        """Liveness check: is the board there, and what is on it?

        Takes no keys. Answers with the number of images and whether the webcam
        background is on.
        """
        return {"images": len(self.board.image_items()),
                "background": self.window.camera_bg.active}

    def cmd_set_background(self, request: dict) -> dict:
        """Turn the webcam background on or off.

        enabled: true for the camera feed, false for the plain dark board.
        Leave the key out entirely to flip whichever way it currently is.
        """
        enabled = request.get("enabled")
        if enabled is None:
            active = self.window.toggle_background()
        else:
            active = self.window.set_background(bool(enabled))
        self.window.statusBar().showMessage(
            f"MCP turned the camera background {'on' if active else 'off'}", 3000)
        return {"enabled": active}

    def cmd_add_image(self, request: dict) -> dict:
        """Add one image file to the board.

        path: absolute path to a png, jpg, webp, gif, bmp or tiff file. A path
        that cannot be read comes back as an error rather than an empty item.

        Answers with the new item's id, which is what cmd_remove_image takes.
        """
        item = self.board.add_image(request["path"])
        self.window.statusBar().showMessage(
            f"MCP added {Path(item.source_path).name}", 3000)
        return {"id": item.item_id, "path": item.source_path}

    def cmd_add_images(self, request: dict) -> dict:
        """Add several image files at once, then fit the view around them.

        paths: list of absolute image paths. One bad path does not sink the
        batch -- it comes back under "failed" while the rest are added.
        """
        added, failed = [], []
        for path in request.get("paths", []):
            try:
                item = self.board.add_image(path)
                added.append({"id": item.item_id, "path": item.source_path})
            except (OSError, ValueError) as exc:
                failed.append({"path": str(path), "error": str(exc)})
        if added:
            self.window.view.zoom_to_fit()
        self.window.statusBar().showMessage(f"MCP added {len(added)} image(s)", 3000)
        return {"added": added, "failed": failed}

    def cmd_remove_image(self, request: dict) -> dict:
        """Take one image off the board.

        id: the item id from cmd_list_images. Answers with "removed": false if
        nothing on the board has that id.
        """
        removed = self.board.remove_image(request["id"])
        return {"removed": removed}

    def cmd_add_box(self, request: dict) -> dict:
        """Draw a rectangle or an ellipse on the board.

        width, height: the box's size in scene units -- the same units
        cmd_list_items reports, so a box sized and placed from that listing lands
        exactly where it was meant to.
        x, y: where its centre goes, in scene units. Defaults to the origin.
        geometry: "rect" (the default) or "ellipse".
        radius: rounds a rectangle's corners. It is a fraction of the box's own
        shorter side, from 0 (square) to 0.5 (a pill), so it means the same
        thing whatever size the box is. Ignored by an ellipse.
        fill: the interior colour, as "#rrggbb" or "#aarrggbb" -- use the alpha
        form for a wash that does not hide what is underneath. null for no
        interior at all, which is what a box drawn *round* things wants.
        stroke: the border colour, same format. null for no border. Leaving it
        out on a box that parents a group gives it the group's colour.
        stroke_width: how thick the border is, in scene units. It is content,
        not chrome: zoom in and it gets thicker, like a drawn line.

        A box with neither a fill nor a border is refused: it would look exactly
        like a box that failed to draw.

        A filled box goes behind everything, since its interior would otherwise
        hide what it is drawn around; an unfilled one goes in front, where its
        border reads over whatever it encloses. An unfilled box is grabbed by
        its border only -- the empty middle falls through to the images inside.
        """
        box = self.board.add_box(
            width=float(request["width"]),
            height=float(request["height"]),
            pos=QPointF(float(request.get("x", 0.0)), float(request.get("y", 0.0))),
            geometry=request.get("geometry", "rect"),
            fill=_color(request.get("fill"), "fill"),
            stroke=_color(request.get("stroke"), "stroke"),
            stroke_width=float(request.get("stroke_width", 4.0)),
            radius=float(request.get("radius", 0.0)),
        )
        self.window.statusBar().showMessage("MCP drew a box", 3000)
        return {"id": box.item_id}

    def cmd_set_box_style(self, request: dict) -> dict:
        """Restyle a box that is already on the board.

        id: the box's item id, from cmd_list_items.
        fill, stroke: colours as "#rrggbb" or "#aarrggbb". Send the key with
        null to take that colour away entirely; leave the key out to keep it.
        stroke_width: border thickness in scene units.
        radius: corner rounding, a fraction of the shorter side (0 to 0.5).
        geometry: "rect" or "ellipse".

        A change that would leave the box with neither a fill nor a border is
        refused and nothing is applied -- half a style is how a box goes
        invisible without anything saying so.

        Answers with "styled": false if no box on the board has that id.
        """
        changes = {}
        if "fill" in request:
            changes["fill"] = _color(request["fill"], "fill")
        if "stroke" in request:
            changes["stroke"] = _color(request["stroke"], "stroke")
        if "stroke_width" in request:
            changes["stroke_width"] = max(0.0, float(request["stroke_width"]))
        if "radius" in request:
            changes["radius"] = min(0.5, max(0.0, float(request["radius"])))
        if "geometry" in request:
            changes["geometry"] = request["geometry"]
        box = self.board.style_box(request["id"], **changes)
        return {"styled": box is not None}

    def cmd_remove_item(self, request: dict) -> dict:
        """Take any item off the board, whatever kind it is.

        id: the item id from cmd_list_items. Anything anchored under that item
        goes with it, so removing a parent removes its whole set.

        Answers with "removed": false if nothing on the board has that id.
        """
        return {"removed": self.board.remove_item(request["id"])}

    def cmd_clear_board(self, _request: dict) -> dict:
        """Empty the board: every item on it, not only the images."""
        return {"removed": self.board.clear_board()}

    def _record(self, item) -> dict:
        """One image as the wire sees it. Shared so every listing agrees."""
        w, h = item.natural_size()
        return {
            "id": item.item_id,
            "path": item.source_path,
            "description": item.description,
            "x": round(item.pos().x(), 1),
            "y": round(item.pos().y(), 1),
            "scale": round(item.scale(), 4),
            "width": w,
            "height": h,
            "z": item.zValue(),
        }

    def _item_record(self, item) -> dict:
        """One item of any kind as the wire sees it.

        The rectangle is Qt's own -- sceneBoundingRect() is what actually placed
        the pixels -- rather than a second calculation of it from scale times
        natural size. Those agree today and would drift the first time a kind
        has a transform this code did not think about, and "close enough" is not
        a thing to aim an arrow with.
        """
        rect = item.sceneBoundingRect()
        colour = self.board.group_color(item)
        return {
            "id": item.item_id,
            "kind": item.kind,
            "x": round(rect.center().x(), 1),
            "y": round(rect.center().y(), 1),
            "width": round(rect.width(), 1),
            "height": round(rect.height(), 1),
            "z": item.zValue(),
            "parent": item.parent_id,
            "group_color": colour.name() if colour is not None else None,
        }

    def cmd_set_parent(self, request: dict) -> dict:
        """Anchor items to a parent item, so they move when it moves.

        parent: the id of the item to anchor them to.
        ids: the ids of the items to anchor. Each one keeps its own size -- a
        child moves with its parent and deliberately does not scale with it.

        Nesting is allowed to any depth. A link that would close a loop (A under
        B under A) is refused and comes back under "refused" rather than being
        silently dropped, as does an id that names nothing.

        The parent takes the next colour off the board's fixed roster, and every
        item in the set is outlined in it once any of them is selected -- which
        is the only way a person at the board can see that they move together.
        """
        parent = request.get("parent")
        anchored, refused = [], []
        for item_id in request.get("ids", []):
            if self.board.set_parent(item_id, parent):
                anchored.append(item_id)
            else:
                refused.append(item_id)
        return {"anchored": anchored, "refused": refused, "parent": parent}

    def cmd_unparent(self, request: dict) -> dict:
        """Set items loose from whatever they are anchored to.

        ids: the ids of the items to detach. They stay exactly where they are
        and stop moving with their former parent. Anything anchored to *them*
        stays anchored to them.
        """
        freed = [i for i in request.get("ids", []) if self.board.set_parent(i, None)]
        return {"freed": freed}

    def cmd_list_items(self, _request: dict) -> dict:
        """Everything on the board -- every kind, not only the images.

        Takes no keys. Each item comes back with its id, its "kind", the scene
        coordinates of its centre, its width and height on the board, the id of
        the item it is anchored to ("parent", null when it is anchored to
        nothing) and the colour of the group it is in ("group_color", null when
        it is in none).

        Those are scene units: the same numbers cmd_add_image takes and a
        .kinesis file stores, so a figure read out of here can be written
        straight back in. This is how a caller aims -- putting something around
        or beside what is already there -- without taking a screenshot first.

        It is also the complete inventory of what cmd_clear_board would remove.
        For the pictures specifically, with their descriptions, use
        cmd_list_images.
        """
        return {"items": [self._item_record(item) for item in self.board.board_items()]}

    def cmd_list_images(self, _request: dict) -> dict:
        """The images on the board, with id, path, description, position and size.

        An image whose "description" is an empty string has never been described:
        nothing here ever fills that field in for you, so empty means nobody has
        looked at the picture yet and it is the caller's cue to do so.

        The images and not the board: this is the listing to use when the next
        thing you do is about pictures -- describing them, finding them, taking
        one off. cmd_list_items is the board-wide one, and it is the one that
        answers "what is on this board" and "what would cmd_clear_board remove".
        """
        return {"images": [self._record(item) for item in self.board.image_items()]}

    def cmd_describe_image(self, request: dict) -> dict:
        """Record what an image shows, in text the person at the board never sees.

        id: the item id from cmd_list_images.
        description: what the image is, written by the caller after looking at
        it -- this board stores descriptions and never generates them. Saved
        with the scene, so the reading survives to the next session. An empty
        string clears it back to "nobody has looked at this yet", which is how a
        wrong description gets withdrawn.

        Answers with "described": false if nothing on the board has that id.
        """
        item = self.board.describe_image(request["id"], request["description"])
        if item is None:
            return {"described": False}
        return {"described": True, "id": item.item_id, "description": item.description}

    def cmd_find_images(self, request: dict) -> dict:
        """Find images by what they are rather than by id.

        query: text matched case-insensitively against each image's description
        and, for images nothing has described, against its file name.

        Answers with the cmd_list_images fields plus "matched", which is
        "description" for an image somebody read and recorded, or "path" for a
        bare file-name hit on an image nobody has looked at -- worth far less,
        and never worth confusing for the first. Description hits come first.
        """
        return {"matches": [dict(self._record(item), matched=field)
                            for item, field in self.board.search(request["query"])]}

    def cmd_fit(self, _request: dict) -> dict:
        """Zoom the board out until everything on it is visible at once.

        Takes no keys.
        """
        self.window.view.zoom_to_fit()
        return {}

    def cmd_screenshot(self, _request: dict) -> dict:
        """Render the scene straight to an image.

        Rendering the scene rather than grabbing the widget means this works
        with the OpenGL viewport, and leaves out the cursors and HUD.
        """
        rect = self.board.content_rect()
        if rect.isNull():
            rect = QRectF(-400, -300, 800, 600)
        rect = rect.adjusted(-40, -40, 40, 40)

        scale = min(1.0, SHOT_MAX_EDGE / max(rect.width(), rect.height()))
        width = max(1, int(rect.width() * scale))
        height = max(1, int(rect.height() * scale))

        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(self.board.backgroundBrush().color())
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.board.render(painter, QRectF(0, 0, width, height), rect,
                          Qt.AspectRatioMode.KeepAspectRatio)
        painter.end()

        # QBuffer() with its own internal QByteArray: passing a temporary
        # QByteArray here leaves the buffer pointing at freed memory.
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return {"png_base64": base64.b64encode(bytes(buffer.data())).decode(),
                "width": width, "height": height}
