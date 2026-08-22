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
        "background between the webcam feed and the plain dark board. "
        "list_items reports every item on the board with its position and size "
        "in the board's own units, which is how to place something relative to "
        "what is already there without screenshotting to aim by. Images "
        "can also be described -- look at one, record what it shows with "
        "describe_image, and it stays findable by meaning from then on, in this "
        "session and every later one. The kinesis app must already be open."
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
    """Add several image files to the board at once, then fit the view to them.

    paths: absolute paths to image files. A path that fails to load is reported
    back and the rest are still added.
    """
    result = _call("add_images", paths=[str(Path(p).expanduser()) for p in paths])
    lines = [f"Added {len(result['added'])} image(s):"]
    lines += [f"  {item['id']}  {item['path']}" for item in result["added"]]
    if result["failed"]:
        lines.append(f"Failed ({len(result['failed'])}):")
        lines += [f"  {f['path']}: {f['error']}" for f in result["failed"]]
    return "\n".join(lines)


def _describe_line(item: dict) -> str:
    """One image as a line, ending in what it is or in the fact nobody knows."""
    name = Path(item["path"]).name if item["path"] else "(pasted)"
    said = item.get("description") or "NOT DESCRIBED YET"
    return (f"  {item['id']}  {name}  {item['width']}x{item['height']}"
            f"  at ({item['x']}, {item['y']})  scale {item['scale']}"
            f"\n      {said}")


@server.tool()
def list_items() -> str:
    """List everything on the board -- every kind of item, not only the images.

    Each line is an item's id, its kind, where its centre is, and how big it is
    on the board. Those numbers are scene units: exactly what add_image and a
    saved board use, so a position read out of here can be handed straight back
    to a call that places something. That is how to put a thing beside, around
    or between what is already there without taking a screenshot to aim by.

    This is the complete inventory of what clear_board would remove. Use
    list_images instead when what you are about to do is about pictures -- it
    carries the file names and the descriptions, which this listing does not.
    """
    items = _call("list_items")["items"]
    if not items:
        return "The board is empty."
    lines = [f"{len(items)} item(s) on the board:"]
    lines += [f"  {item['id']}  {item['kind']:<6} {item['width']}x{item['height']}"
              f"  centred at ({item['x']}, {item['y']})" for item in items]
    return "\n".join(lines)


@server.tool()
def list_images() -> str:
    """List the images on the board: ids, positions, and what each one is.

    The last line of each entry is the image's description. "NOT DESCRIBED YET"
    means no client has ever looked at that image and written down what it
    shows -- nothing fills that in automatically, so it is a real gap and not a
    formatting quirk. Those are the images to look at and describe_image.

    This is the pictures, which is not necessarily the whole board: list_items
    is the board-wide one, and the inventory of what clear_board would remove.
    """
    images = _call("list_images")["images"]
    if not images:
        return "The board is empty."
    lines = [f"{len(images)} image(s) on the board:"]
    lines += [_describe_line(item) for item in images]
    return "\n".join(lines)


@server.tool()
def describe_image(image_id: str, description: str) -> str:
    """Record what an image shows, so it never has to be looked at twice.

    The board stores the text and never writes it for you: look at the image
    yourself, then say what it is here. It is saved with the board, so a later
    session -- or another client entirely -- can act on the image by meaning
    ("the two vessel photos") instead of by id, and nobody re-reads a picture
    that has already been read. It is never shown to the person at the board.

    image_id: the id shown by list_images.
    description: what the image is, in your own words. Pass an empty string to
    take a wrong description back, leaving the image marked as not yet described.
    """
    reply = _call("describe_image", id=image_id, description=description)
    if not reply["described"]:
        return f"No image with id {image_id}"
    if not reply["description"]:
        return f"Cleared the description of {image_id}; it is undescribed again."
    return f"{image_id} is now described as: {reply['description']}"


@server.tool()
def find_images(query: str) -> str:
    """Find images on the board by what they show, rather than by id.

    query: what to look for. Matched against the descriptions written by
    describe_image and, for images nobody has described, against file names.

    Each result says which of the two it matched on. A "description" hit is
    something a client looked at and recorded; a "path" hit is only a file name
    and may be nothing of the sort, so check it before acting on it.
    """
    matches = _call("find_images", query=query)["matches"]
    if not matches:
        return f"Nothing on the board matches {query!r}."
    lines = [f"{len(matches)} match(es) for {query!r}:"]
    for item in matches:
        lines.append(f"{_describe_line(item)}   [matched on {item['matched']}]")
    return "\n".join(lines)


@server.tool()
def remove_image(image_id: str) -> str:
    """Remove one image from the board.

    image_id: the id shown by list_images.
    """
    removed = _call("remove_image", id=image_id)["removed"]
    return f"Removed {image_id}" if removed else f"No image with id {image_id}"


@server.tool()
def clear_board() -> str:
    """Empty the board completely: every item on it, images included."""
    return f"Removed {_call('clear_board')['removed']} item(s)."


@server.tool()
def fit_view() -> str:
    """Zoom the board out until everything on it is visible at once."""
    _call("fit")
    return "Fitted the view to the whole board."


@server.tool()
def set_camera_background(enabled: bool) -> str:
    """Show the webcam feed as the board background, or the plain dark board.

    enabled: True for the webcam feed, False for the dark board.
    """
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
