"""The kinds of thing a board can hold, one module each.

A subfolder rather than a filename prefix, per CLAUDE.md, and it starts as one
because three more kinds land on top of it: boxes (#51), notes (#52) and arrows
(#53). They all share `BoardItem` -- one id space, one `kind` string, one
serialised shape -- and share nothing else, which is exactly the split.

Importing the kinds from the package rather than from their modules keeps the
call sites reading `from .items import ImageItem`, so adding a kind never
reshuffles anyone else's imports.
"""

from .base import BoardItem
from .box import BoxItem, parse_color
from .image import SUPPORTED_SUFFIXES, ImageItem, is_supported_image

__all__ = ["SUPPORTED_SUFFIXES", "BoardItem", "BoxItem", "ImageItem",
           "is_supported_image", "parse_color"]
