"""The envelope, and the commands about images.

Every reply here is something an MCP client reports back as fact, so a handler
that returns the wrong shape, or dies on a request missing a key, becomes an
agent confidently describing a board that isn't there. The dispatch layer is
exercised directly against a real BoardScene: same handlers, same replies, no
port bound and nothing for a network stack to make flaky. The harness is in
tests/boardcontrol.py.

The commands about a *kind* are tested with that kind -- the box commands in
test_boxes.py, the group commands in test_groups.py -- because what they have to
get right is the kind's own rules, not the envelope's.
"""

import base64

import pytest

from .boardcontrol import send

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
