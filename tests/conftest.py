"""Shared offscreen Qt setup for the tests that need a real scene.

The platform plugin and the no-GL flag have to be set before anything imports
PySide6, and a conftest is the only place guaranteed to run first -- a test
module that sets them at the top of itself is already too late if a sibling
imported Qt before it. The QApplication lives for the whole session because Qt
only ever wants one, and tearing it down between modules is how offscreen runs
start segfaulting.

Nothing here touches a camera, a window or a real socket; that rule has no
exceptions in this suite.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KINESIS_NO_GL", "1")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_image(tmp_path):
    """Write a solid-colour PNG and hand back its path.

    Tests need files that decode, not pictures; the size is what matters, since
    placement and scaling are derived from it.
    """
    def _make(name="t.png", w=400, h=300, colour="#446688"):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(colour))
        path = tmp_path / name
        assert img.save(str(path))
        return path
    return _make
