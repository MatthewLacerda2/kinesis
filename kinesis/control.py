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

from PySide6.QtCore import QBuffer, QObject, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtNetwork import QHostAddress, QTcpServer

CONTROL_PATH = Path.home() / ".config" / "kinesis" / "control.json"

SHOT_MAX_EDGE = 1400


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

    def cmd_list_images(self, _request: dict) -> dict:
        """The images on the board, with id, path, description, position and size.

        An image whose "description" is an empty string has never been described:
        nothing here ever fills that field in for you, so empty means nobody has
        looked at the picture yet and it is the caller's cue to do so.

        Today every board item is an image, so this is also the whole board. When
        that stops being true this listing goes board-wide (issue #3): a caller
        has to be able to see everything cmd_clear_board would remove.
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
