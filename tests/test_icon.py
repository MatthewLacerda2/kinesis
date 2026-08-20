"""The window icon resolves to a real file, from wherever the package is imported.

QIcon fails silently: point it at a missing path and you get an empty icon and
no error, so the app would launch with the default Qt feather and nothing would
say why. That makes the asset's presence the only thing worth asserting here --
and it is worth asserting, because the two ways it goes missing (a rename, or
package-data dropping out of pyproject) both look like a working build.
"""

from __future__ import annotations

from kinesis.app import ICON_PATH


def test_icon_ships_with_the_package():
    assert ICON_PATH.is_file(), f"window icon missing at {ICON_PATH}"


def test_icon_is_a_png():
    # PNG magic number -- a JPEG renamed to .png loads in Qt but not everywhere
    # the same file gets reused (the .icns build reads it as a PNG).
    assert ICON_PATH.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
