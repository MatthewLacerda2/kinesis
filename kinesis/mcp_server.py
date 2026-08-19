"""MCP server exposing the running kinesis board.

Talks to the app's local control channel (kinesis/control.py). The app must be
running -- this server does not launch it, so that closing the board doesn't
leave an orphaned window around.

Run:  python -m kinesis.mcp_server
"""

from __future__ import annotations

import base64
import json
import socket
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from .control import CONTROL_PATH

server = MCPServer(
    name="kinesis",
    instructions=(
        "Controls a running kinesis reference board: add images to it, remove "
        "them, list what is on it, screenshot it, and switch the canvas "
        "background between the webcam feed and the plain dark board. The "
        "kinesis app must already be open."
    ),
)


class BoardUnavailable(RuntimeError):
    pass


def _call(cmd: str, **payload) -> dict:
    """One request/response round trip over the control channel."""
    try:
        config = json.loads(CONTROL_PATH.read_text())
    except (OSError, ValueError) as exc:
        raise BoardUnavailable(
            "kinesis does not appear to be running (no control file at "
            f"{CONTROL_PATH}). Start it with ./run.sh"
        ) from exc

    request = json.dumps({"cmd": cmd, "token": config["token"], **payload}) + "\n"
    try:
        with socket.create_connection(("127.0.0.1", config["port"]), timeout=10) as sock:
            sock.sendall(request.encode())
            chunks = b""
            while b"\n" not in chunks:
                data = sock.recv(65536)
                if not data:
                    break
                chunks += data
    except OSError as exc:
        raise BoardUnavailable(
            f"Could not reach the kinesis board on port {config['port']}: {exc}. "
            "Is the app still open?"
        ) from exc

    reply = json.loads(chunks.decode().split("\n", 1)[0])
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "unknown error"))
    return reply


@server.tool()
def add_image(path: str) -> str:
    """Add one image file to the kinesis board.

    path: absolute path to a png, jpg, webp, gif, bmp or tiff file.
    """
    result = _call("add_image", path=str(Path(path).expanduser()))
    return f"Added {result['path']} (id {result['id']})"


@server.tool()
def add_images(paths: list[str]) -> str:
    """Add several image files to the board at once, then fit the view to them."""
    result = _call("add_images", paths=[str(Path(p).expanduser()) for p in paths])
    lines = [f"Added {len(result['added'])} image(s):"]
    lines += [f"  {item['id']}  {item['path']}" for item in result["added"]]
    if result["failed"]:
        lines.append(f"Failed ({len(result['failed'])}):")
        lines += [f"  {f['path']}: {f['error']}" for f in result["failed"]]
    return "\n".join(lines)


@server.tool()
def list_images() -> str:
    """List the images currently on the board, with their ids and positions."""
    images = _call("list_images")["images"]
    if not images:
        return "The board is empty."
    lines = [f"{len(images)} image(s) on the board:"]
    for item in images:
        name = Path(item["path"]).name if item["path"] else "(pasted)"
        lines.append(
            f"  {item['id']}  {name}  {item['width']}x{item['height']}"
            f"  at ({item['x']}, {item['y']})  scale {item['scale']}"
        )
    return "\n".join(lines)


@server.tool()
def remove_image(image_id: str) -> str:
    """Remove one image from the board by its id (see list_images)."""
    removed = _call("remove_image", id=image_id)["removed"]
    return f"Removed {image_id}" if removed else f"No image with id {image_id}"


@server.tool()
def clear_board() -> str:
    """Remove every image from the board."""
    return f"Removed {_call('clear_board')['removed']} image(s)."


@server.tool()
def fit_view() -> str:
    """Zoom the board so every image is visible."""
    _call("fit")
    return "Fitted the view to all images."


@server.tool()
def set_camera_background(enabled: bool) -> str:
    """Show the webcam feed as the board background (True) or the dark board (False)."""
    active = _call("set_background", enabled=enabled)["enabled"]
    return f"Camera background is now {'on' if active else 'off'}."


@server.tool()
def toggle_camera_background() -> str:
    """Flip the board background between the webcam feed and the dark board."""
    active = _call("set_background")["enabled"]
    return f"Camera background is now {'on' if active else 'off'}."


@server.tool()
def board_status() -> str:
    """Report how many images are on the board and whether the webcam background is on."""
    reply = _call("ping")
    return (f"{reply['images']} image(s) on the board; camera background "
            f"{'on' if reply['background'] else 'off'}.")


@server.tool()
def screenshot_board() -> Image:
    """Render the current board as an image, to see how it is arranged.

    Renders the scene, so the webcam background is not part of the shot.
    """
    result = _call("screenshot")
    return Image(data=base64.b64decode(result["png_base64"]), format="png")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
