"""The offline control harness: the real dispatcher, minus the listening socket.

Shared the way tests/handmodel.py is, because three test modules now drive the
same command surface -- the envelope and the image commands, the box commands,
and the group commands -- and each of those belongs with its own subject rather
than in one file that grows until it is split by an arbitrary line.

The scene and the view are real: they are what the commands are actually about.
The camera and the status bar are not -- one needs hardware and the other needs
a window, and neither decides whether a command is correct.
"""

import json

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


def send(control, cmd=None, token=TOKEN, **fields):
    """One request through the real dispatcher, as a caller would send it."""
    request = dict(fields)
    if cmd is not None:
        request["cmd"] = cmd
    if token is not None:
        request["token"] = token
    return control._dispatch(json.dumps(request))
