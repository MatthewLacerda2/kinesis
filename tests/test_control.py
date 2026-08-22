"""The command surface an agent drives with nobody watching.

Every reply here is something an MCP client reports back as fact, so a handler
that returns the wrong shape, or dies on a request missing a key, becomes an
agent confidently describing a board that isn't there. The dispatch layer is
exercised directly against a real BoardScene: same handlers, same replies, no
port bound and nothing for a network stack to make flaky.
"""

import base64
import json

import pytest
from PySide6.QtCore import QObject

from kinesis.canvas.scene import BoardScene
from kinesis.canvas.view import BoardView
from kinesis.control import ControlServer

TOKEN = "test-token"


class FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, _timeout=0):
        self.messages.append(text)


class FakeCameraBackground:
    active = False


class FakeWindow:
    """Everything the handlers reach for on the window, and nothing else.

    The scene and the view are real -- they are what the commands are actually
    about. The camera and the status bar are not: one needs hardware and the
    other needs a window, and neither decides whether a command is correct.
    """

    def __init__(self):
        self.board = BoardScene()
        self.view = BoardView(self.board)
        self.view.resize(800, 600)
        self.camera_bg = FakeCameraBackground()
        self._status = FakeStatusBar()

    def statusBar(self):
        return self._status

    def set_background(self, enabled: bool) -> bool:
        self.camera_bg.active = enabled
        return self.camera_bg.active

    def toggle_background(self) -> bool:
        return self.set_background(not self.camera_bg.active)


class OfflineControl(ControlServer):
    """The real dispatcher and the real handlers, minus the listening socket."""

    def __init__(self, window):
        QObject.__init__(self)
        self.window = window
        self.board = window.board
        self.token = TOKEN


@pytest.fixture
def control(qapp):
    return OfflineControl(FakeWindow())


def send(control, cmd=None, token=TOKEN, **fields):
    request = dict(fields)
    if cmd is not None:
        request["cmd"] = cmd
    if token is not None:
        request["token"] = token
    return control._dispatch(json.dumps(request))


# ---------- the envelope ----------

def test_a_caller_without_the_token_gets_nowhere(control, make_image):
    reply = send(control, "add_image", token=None, path=str(make_image()))
    assert reply["ok"] is False and "token" in reply["error"]
    assert control.board.image_items() == [], "an unauthenticated command still ran"


def test_a_wrong_token_is_rejected(control):
    assert send(control, "ping", token="not-the-token")["ok"] is False


def test_an_unknown_command_is_an_error_not_a_crash(control):
    reply = send(control, "definitely_not_a_command")
    assert reply["ok"] is False and "unknown command" in reply["error"]


def test_malformed_json_is_an_error_not_a_crash(control):
    reply = control._dispatch("{not json at all")
    assert reply["ok"] is False and "bad JSON" in reply["error"]


def test_a_missing_required_key_comes_back_as_an_error(control):
    reply = send(control, "add_image")  # no "path"
    assert reply["ok"] is False and "KeyError" in reply["error"]


def test_a_handler_that_raises_reports_the_reason(control):
    reply = send(control, "add_image", path="/no/such/file.png")
    assert reply["ok"] is False and "FileNotFoundError" in reply["error"]
    assert control.board.image_items() == []


# ---------- the commands ----------

def test_ping_answers_with_the_board_size_and_background_state(control, make_image):
    assert send(control, "ping") == {"images": 0, "background": False, "ok": True}
    control.board.add_image(make_image())
    assert send(control, "ping")["images"] == 1


def test_set_background_switches_and_toggles(control):
    assert send(control, "set_background", enabled=True) == {"enabled": True, "ok": True}
    # Asking for a state you are already in is a no-op, not a flip: an agent that
    # cannot see the board has no way to know which one it would get.
    assert send(control, "set_background", enabled=True)["enabled"] is True
    assert send(control, "set_background", enabled=False)["enabled"] is False
    assert send(control, "set_background")["enabled"] is True, "omitting enabled toggles"
    assert send(control, "set_background")["enabled"] is False


def test_add_image_returns_the_id_the_caller_needs_to_remove_it(control, make_image):
    path = str(make_image())
    reply = send(control, "add_image", path=path)
    assert reply["ok"] is True and reply["path"] == path
    assert control.board.find(reply["id"]) is not None


def test_add_images_reports_each_success_and_each_failure(control, make_image, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not an image")
    reply = send(control, "add_images",
                 paths=[str(make_image("a.png")), str(bad), "/no/such.png"])
    assert reply["ok"] is True, "a partial batch is still a completed command"
    assert len(reply["added"]) == 1
    assert [f["path"] for f in reply["failed"]] == [str(bad), "/no/such.png"]
    assert all(f["error"] for f in reply["failed"])
    assert len(control.board.image_items()) == 1


def test_add_images_with_nothing_to_add_is_still_a_clean_reply(control):
    assert send(control, "add_images") == {"added": [], "failed": [], "ok": True}


def test_remove_image_says_whether_it_removed_anything(control, make_image):
    item_id = send(control, "add_image", path=str(make_image()))["id"]
    assert send(control, "remove_image", id=item_id) == {"removed": True, "ok": True}
    assert control.board.image_items() == []
    assert send(control, "remove_image", id=item_id) == {"removed": False, "ok": True}


def test_clear_board_reports_how_many_it_took(control, make_image):
    for name in ("a.png", "b.png"):
        send(control, "add_image", path=str(make_image(name)))
    assert send(control, "clear_board") == {"removed": 2, "ok": True}
    assert control.board.board_items() == []


def test_list_images_describes_every_image_in_z_order(control, make_image):
    ids = [send(control, "add_image", path=str(make_image(f"{n}.png")))["id"]
           for n in range(3)]
    images = send(control, "list_images")["images"]
    assert [i["id"] for i in images] == ids
    first = images[0]
    assert set(first) == {"id", "path", "description", "x", "y", "scale",
                          "width", "height", "z"}
    assert (first["width"], first["height"]) == (400, 300)
    assert first["scale"] > 0


def test_list_images_on_an_empty_board(control):
    assert send(control, "list_images") == {"images": [], "ok": True}


# ---------- the board-wide listing ----------

def test_list_items_reports_every_item_with_its_kind(control, make_image):
    ids = [send(control, "add_image", path=str(make_image(f"{n}.png")))["id"]
           for n in range(2)]
    items = send(control, "list_items")["items"]
    assert [i["id"] for i in items] == ids
    assert {i["kind"] for i in items} == {"image"}
    assert set(items[0]) == {"id", "kind", "x", "y", "width", "height", "z",
                             "parent", "group_color"}


def test_list_items_reports_the_size_qt_actually_drew(control, make_image):
    """Scene units, off Qt's own rectangle -- so a caller can aim with them.

    The test image is 400x300 and the board normalises the long edge to 800, so
    the answer has to be the size on the board and not the size in the file.
    """
    item_id = send(control, "add_image", path=str(make_image(w=400, h=300)))["id"]
    item = next(i for i in send(control, "list_items")["items"] if i["id"] == item_id)
    assert (item["width"], item["height"]) == pytest.approx((800.0, 600.0))
    real = control.board.find(item_id).sceneBoundingRect()
    assert (item["x"], item["y"]) == pytest.approx((real.center().x(), real.center().y()))


def test_list_items_on_an_empty_board(control):
    assert send(control, "list_items") == {"items": [], "ok": True}


# ---------- groups ----------

def _two(control, make_image):
    return [send(control, "add_image", path=str(make_image(f"{n}.png")))["id"]
            for n in range(2)]


def test_set_parent_anchors_and_the_listing_shows_it(control, make_image):
    parent, child = _two(control, make_image)
    reply = send(control, "set_parent", parent=parent, ids=[child])
    assert reply["anchored"] == [child] and reply["refused"] == []

    listed = {i["id"]: i for i in send(control, "list_items")["items"]}
    assert listed[child]["parent"] == parent
    assert listed[parent]["parent"] is None
    assert listed[child]["group_color"] == listed[parent]["group_color"] is not None


def test_an_anchored_item_moves_when_its_parent_does(control, make_image):
    parent, child = _two(control, make_image)
    send(control, "set_parent", parent=parent, ids=[child])
    before = {i["id"]: (i["x"], i["y"]) for i in send(control, "list_items")["items"]}
    control.board.find(parent).moveBy(100, 40)
    after = {i["id"]: (i["x"], i["y"]) for i in send(control, "list_items")["items"]}
    assert after[child] == pytest.approx((before[child][0] + 100, before[child][1] + 40))


def test_set_parent_refuses_an_id_it_cannot_use_rather_than_dropping_it(control, make_image):
    parent, child = _two(control, make_image)
    reply = send(control, "set_parent", parent=parent, ids=[child, "no-such-id", parent])
    assert reply["anchored"] == [child]
    assert reply["refused"] == ["no-such-id", parent], "a loop or a bad id passed silently"


def test_unparent_sets_an_item_loose(control, make_image):
    parent, child = _two(control, make_image)
    send(control, "set_parent", parent=parent, ids=[child])
    assert send(control, "unparent", ids=[child])["freed"] == [child]
    listed = {i["id"]: i for i in send(control, "list_items")["items"]}
    assert listed[child]["parent"] is None
    assert listed[child]["group_color"] is None


def test_removing_a_parent_takes_its_children_with_it(control, make_image):
    parent, child = _two(control, make_image)
    send(control, "set_parent", parent=parent, ids=[child])
    assert send(control, "remove_image", id=parent)["removed"] is True
    assert send(control, "list_items")["items"] == []


# ---------- descriptions ----------

def test_a_new_image_is_listed_as_having_no_description(control, make_image):
    """The signal the whole feature rests on: empty means nobody has looked.

    A caller decides what to spend a vision pass on by reading this field, so an
    image that arrived by drag-drop must come back empty rather than helpfully
    pre-filled with its file name.
    """
    send(control, "add_image", path=str(make_image("kettle.png")))
    assert send(control, "list_images")["images"][0]["description"] == ""


def test_a_description_is_written_and_read_back(control, make_image):
    item_id = send(control, "add_image", path=str(make_image()))["id"]
    reply = send(control, "describe_image", id=item_id, description="a copper kettle")
    assert reply == {"described": True, "id": item_id,
                     "description": "a copper kettle", "ok": True}
    assert send(control, "list_images")["images"][0]["description"] == "a copper kettle"


def test_a_description_can_be_overwritten_and_cleared(control, make_image):
    """A wrong reading must never be stuck on an image nobody can reach."""
    item_id = send(control, "add_image", path=str(make_image()))["id"]
    send(control, "describe_image", id=item_id, description="a teapot")
    send(control, "describe_image", id=item_id, description="a kettle")
    assert send(control, "list_images")["images"][0]["description"] == "a kettle"
    assert send(control, "describe_image", id=item_id, description="")["description"] == ""
    assert send(control, "list_images")["images"][0]["description"] == ""


def test_describing_an_id_that_is_not_there_says_so(control):
    assert send(control, "describe_image", id="nope", description="x") == {
        "described": False, "ok": True}


def test_find_images_matches_a_description_and_names_the_field(control, make_image):
    described = send(control, "add_image", path=str(make_image("a.png")))["id"]
    send(control, "add_image", path=str(make_image("b.png")))
    send(control, "describe_image", id=described, description="A copper kettle, steaming")

    matches = send(control, "find_images", query="KETTLE")["matches"]
    assert [m["id"] for m in matches] == [described], "case-insensitive, and only the hit"
    assert matches[0]["matched"] == "description"


def test_find_images_tells_a_read_image_apart_from_a_lucky_file_name(control, make_image):
    """Both can match; a caller has to know which kind of answer it got.

    A file-name hit is a guess about an image nobody has looked at. Reporting it
    as if it were a description would put the filename back in the one field
    that exists to not contain one -- and description hits sort first, so acting
    on the first result acts on the image something actually read.
    """
    guessed = send(control, "add_image", path=str(make_image("kettle-photo.png")))["id"]
    read = send(control, "add_image", path=str(make_image("img_204.png")))["id"]
    send(control, "describe_image", id=read, description="a kettle on a stove")

    matches = send(control, "find_images", query="kettle")["matches"]
    assert [(m["id"], m["matched"]) for m in matches] == [
        (read, "description"), (guessed, "path")]
    assert matches[1]["description"] == "", "a path hit is still an undescribed image"


def test_find_images_never_treats_an_empty_description_as_a_wildcard(control, make_image):
    """Undescribed is a state, not a match-everything.

    Clearing a description has to put the image back exactly where an untouched
    one is, or "described as nothing" quietly becomes a third state that matches
    every query there is.
    """
    item_id = send(control, "add_image", path=str(make_image("img_9.png")))["id"]
    send(control, "describe_image", id=item_id, description="a kettle")
    send(control, "describe_image", id=item_id, description="   ")

    assert send(control, "find_images", query="kettle")["matches"] == []
    assert send(control, "find_images", query="")["matches"] == []
    assert send(control, "find_images", query="   ")["matches"] == []
    assert send(control, "list_images")["images"][0]["description"] == ""


def test_fit_frames_the_content(control, make_image):
    control.board.add_image(make_image(), long_edge=None)
    before = control.window.view.transform().m11()
    assert send(control, "fit") == {"ok": True}
    assert control.window.view.transform().m11() != before


def test_screenshot_returns_a_decodable_png_of_the_size_it_claims(control, make_image):
    control.board.add_image(make_image())
    reply = send(control, "screenshot")
    assert reply["ok"] is True
    png = base64.b64decode(reply["png_base64"])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert reply["width"] > 0 and reply["height"] > 0


def test_screenshot_of_an_empty_board_still_works(control):
    reply = send(control, "screenshot")
    assert reply["ok"] is True and reply["width"] > 0
