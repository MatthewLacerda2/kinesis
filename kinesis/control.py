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
        return {"images": len(self.board.image_items()),
                "background": self.window.camera_bg.active}

    def cmd_set_background(self, request: dict) -> dict:
        """Webcam background on/off; omitting "enabled" toggles it."""
        enabled = request.get("enabled")
        if enabled is None:
            active = self.window.toggle_background()
        else:
            active = self.window.set_background(bool(enabled))
        self.window.statusBar().showMessage(
            f"MCP turned the camera background {'on' if active else 'off'}", 3000)
        return {"enabled": active}

    def cmd_add_image(self, request: dict) -> dict:
        item = self.board.add_image(request["path"])
        self.window.statusBar().showMessage(
            f"MCP added {Path(item.source_path).name}", 3000)
        return {"id": item.item_id, "path": item.source_path}

    def cmd_add_images(self, request: dict) -> dict:
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
        removed = self.board.remove_image(request["id"])
        return {"removed": removed}

    def cmd_clear_board(self, _request: dict) -> dict:
        return {"removed": self.board.clear_board()}

    def cmd_list_images(self, _request: dict) -> dict:
        images = []
        for item in self.board.image_items():
            w, h = item.natural_size()
            images.append({
                "id": item.item_id,
                "path": item.source_path,
                "x": round(item.pos().x(), 1),
                "y": round(item.pos().y(), 1),
                "scale": round(item.scale(), 4),
                "width": w,
                "height": h,
                "z": item.zValue(),
            })
        return {"images": images}

    def cmd_fit(self, _request: dict) -> dict:
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
