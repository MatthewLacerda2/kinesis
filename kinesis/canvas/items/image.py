"""The image kind: level-of-detail, and a hit shape that follows the alpha.

The shape lives here because it is a per-item fact, and putting it here fixes
every entry point at once: the mouse (`view.itemAt`) and the pinch
(`HandControl._hits`) both end up in `QGraphicsItem.contains`, which defers to
`shape()`. A transparent PNG is a cut-out, so a pinch aimed through its hole has
to reach whatever is behind it.

Identity, z-order and the common half of the serialised form are `BoardItem`'s
(base.py) and are not repeated here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBitmap,
    QColor,
    QImage,
    QImageReader,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

from .base import BoardItem

# Cap the working pixmap on the long edge; full res is loaded only past 1:1.
PREVIEW_MAX_EDGE = 2048

# LOD hysteresis: upgrade above 1:1, drop the full-res copy well below it, so
# nudging around the boundary can't thrash decode/free every frame.
LOD_UPGRADE = 1.0
LOD_DOWNGRADE = 0.8

# The alpha mask is built at this resolution, not the image's. A grab wants a
# forgiving answer, not a pixel-exact one, and the cost of a QRegion grows with
# the number of edges in it -- 512 keeps a big cut-out under a few thousand rects.
MASK_MAX_EDGE = 512
# Below this a pixel is invisible, so it is not something the user can aim at.
# Deliberately low: a 40%-opaque image is faint but still there, and still grabbable.
MASK_ALPHA_MIN = 8
# Past this the alpha is noise (dither, scatter, soft grain) rather than a
# cut-out with holes worth pinching through. Keep the box and the memory.
MASK_MAX_RECTS = 20_000

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def is_supported_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def _alpha_channel(img: QImage) -> np.ndarray | None:
    """The image's alpha as an (h, w) uint8 array, copied off Qt's buffer.

    None when there is nothing to read: every caller then falls back to the
    bounding box, which is the pre-alpha behaviour and never wrong, only coarse.
    """
    a = img.convertToFormat(QImage.Format.Format_Alpha8)
    if a.isNull() or a.width() <= 0 or a.height() <= 0:
        return None
    raw = np.frombuffer(a.constBits(), dtype=np.uint8)
    if raw.size < a.bytesPerLine() * a.height():
        return None
    return raw.reshape(a.height(), a.bytesPerLine())[:, :a.width()].copy()


def _region_from_mask(opaque: np.ndarray) -> QRegion | None:
    """A QRegion covering the True pixels of a boolean (h, w) mask.

    Goes through a 1-bpp QImage because Qt merges the runs into rectangles in
    C++; doing that in Python would be the one slow step in an otherwise
    vectorised path. QBitmap.fromImage reads the colour table directly and
    *segfaults* on an image that has none, so the mono image is built and
    checked here rather than handed around half-made.
    """
    h, w = opaque.shape
    packed = np.packbits(opaque, axis=1, bitorder="little")
    stride = (packed.shape[1] + 3) // 4 * 4  # QImage wants 32-bit aligned rows
    buf = np.zeros((h, stride), dtype=np.uint8)
    buf[:, :packed.shape[1]] = packed
    raw = buf.tobytes()  # named: the QImage below borrows it, it must outlive the copy
    mono = QImage(raw, w, h, stride, QImage.Format.Format_MonoLSB)
    # QBitmap keeps the pixels that are *black*, so the set bits (the opaque
    # ones) have to be the black entry or the region comes out inverted.
    mono.setColorTable([0xFFFFFFFF, 0xFF000000])
    if mono.isNull() or mono.colorCount() != 2:
        return None
    solid = mono.copy()
    if solid.isNull() or solid.format() != QImage.Format.Format_MonoLSB:
        return None
    return QRegion(QBitmap.fromImage(solid))


class ImageItem(BoardItem):
    """An image on the board.

    Geometry is centred on the item origin (boundingRect spans -w/2..+w/2) so
    setScale/setRotation pivot about the image centre without extra transform
    origin bookkeeping, and setPos places the centre directly.

    boundingRect is always in *source pixels*, independent of which pixmap is
    currently resident, so swapping preview <-> full res never moves anything.

    `description` is free text nobody on this side of the screen ever sees: it
    is written by whatever is driving the board over MCP, once, after looking at
    the picture. It starts empty and empty is meaningful -- it says nobody has
    looked yet, which is why nothing here ever defaults it to the file name. A
    filename sitting in this field would be indistinguishable from an actual
    reading of the image, and telling those apart is the whole point of it.
    """

    kind = "image"

    def __init__(self, path: str | None, image: QImage | None = None,
                 item_id: str | None = None):
        super().__init__(item_id)
        self.source_path = str(path) if path else None
        self.description = ""

        self._full: QPixmap | None = None
        self._full_failed = False

        if image is not None:
            src = image
            self._natural_w, self._natural_h = src.width(), src.height()
        else:
            src, self._natural_w, self._natural_h = self._load_source(self.source_path)
        self._preview = QPixmap.fromImage(self._downscale(src, PREVIEW_MAX_EDGE))
        self._shape = self._build_shape(src)

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    # ---------- loading ----------

    @staticmethod
    def _downscale(img: QImage, edge: int) -> QImage:
        if max(img.width(), img.height()) <= edge:
            return img
        return img.scaled(
            edge, edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _load_source(path: str | None) -> tuple[QImage, int, int]:
        """Decode straight to preview size — never materialise full res on load."""
        if not path:
            raise ValueError("ImageItem needs a path or a QImage")
        reader = QImageReader(path)
        reader.setAutoTransform(True)  # honour EXIF orientation
        size = reader.size()
        nat_w, nat_h = size.width(), size.height()
        if nat_w <= 0 or nat_h <= 0:
            raise OSError(f"Unreadable image: {path}")
        long_edge = max(nat_w, nat_h)
        if long_edge > PREVIEW_MAX_EDGE:
            f = PREVIEW_MAX_EDGE / long_edge
            reader.setScaledSize(size * f)
        img = reader.read()
        if img.isNull():
            raise OSError(f"Could not decode {path}: {reader.errorString()}")
        return img, nat_w, nat_h

    def _ensure_full(self) -> None:
        if self._full is not None or self._full_failed or not self.source_path:
            return
        if max(self._natural_w, self._natural_h) <= PREVIEW_MAX_EDGE:
            self._full = self._preview  # preview already is full res
            return
        reader = QImageReader(self.source_path)
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            self._full_failed = True
            return
        self._full = QPixmap.fromImage(img)

    # ---------- geometry / painting ----------

    def natural_size(self) -> tuple[int, int]:
        return self._natural_w, self._natural_h

    def boundingRect(self) -> QRectF:
        return QRectF(-self._natural_w / 2, -self._natural_h / 2,
                      self._natural_w, self._natural_h)

    def shape(self) -> QPainterPath:
        """What can be grabbed — the visible pixels, not the box around them.

        Everything that hit-tests an item lands here (contains(), and through it
        the mouse and the pinch), so a cut-out PNG lets a grab fall through to
        whatever is behind it. Built once at load; the item's geometry never
        changes afterwards, so there is nothing to invalidate.

        Deliberately *not* what the selection outline or the trash hover follow:
        an outline tracing a ragged alpha edge is noise, and the bin wants to be
        forgiving. Both stay on boundingRect.
        """
        return self._shape

    def _box_shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def _build_shape(self, src: QImage) -> QPainterPath:
        """Derive the hit shape from alpha, once, at load.

        The overwhelmingly common image (jpg, opaque png) has no alpha channel
        at all and takes the first return: one rectangle, no scan, no mask. The
        mask is built from a MASK_MAX_EDGE copy rather than the source, so a
        40-megapixel PNG costs the same as a small one. Every giving-up branch
        falls back to the box, so a shape that cannot be derived degrades to the
        old behaviour instead of failing.
        """
        if not src.hasAlphaChannel():
            return self._box_shape()
        alpha = _alpha_channel(self._downscale(src, MASK_MAX_EDGE))
        if alpha is None or alpha.size == 0:
            return self._box_shape()
        opaque = alpha >= MASK_ALPHA_MIN
        if opaque.all() or not opaque.any():
            # Solid, or nothing visible anywhere: the box is the honest answer,
            # and it keeps an all-transparent image reachable rather than a ghost
            # that can be seen in the file but never picked up.
            return self._box_shape()
        region = _region_from_mask(opaque)
        if region is None or region.isEmpty() or region.rectCount() > MASK_MAX_RECTS:
            return self._box_shape()

        mask = QPainterPath()
        mask.addRegion(region)
        rect = self.boundingRect()
        h, w = opaque.shape
        to_item = (QTransform.fromTranslate(rect.x(), rect.y())
                   .scale(rect.width() / w, rect.height() / h))
        return to_item.map(mask)

    def paint(self, painter, option: QStyleOptionGraphicsItem, widget=None) -> None:
        rect = self.boundingRect()
        lod = option.levelOfDetailFromTransform(painter.worldTransform())

        if lod > LOD_UPGRADE:
            self._ensure_full()
        elif lod < LOD_DOWNGRADE and self._full is not None and self._full is not self._preview:
            self._full = None  # zoomed back out; hand the memory back

        pixmap = self._full if (self._full is not None and lod > LOD_UPGRADE) else self._preview
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))

        if self.isSelected():
            pen = QPen(QColor(120, 190, 255), 0)  # width 0 == cosmetic, 1px at any zoom
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

    # ---------- search ----------

    def matches(self, query: str) -> str | None:
        """Which field a search for `query` hit: "description", "path" or None.

        Case-insensitive substring, and the *which* is returned rather than a
        bare yes: a description hit is something that looked at this image and
        wrote down what it saw, a file-name hit is a guess about an image nobody
        has read yet. A caller that cannot tell the two apart is back to trusting
        file names, so the distinction survives all the way out to the reply.

        An empty description matches nothing, including an empty query -- "not
        looked at yet" is a state, never a wildcard.
        """
        needle = query.strip().lower()
        if not needle:
            return None
        if needle in self.description.lower():
            return "description"
        if self.source_path and needle in Path(self.source_path).name.lower():
            return "path"
        return None

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "path": self.source_path,
            "description": self.description,
        }

    def apply_dict(self, record: dict) -> None:
        super().apply_dict(record)
        self.description = record.get("description") or ""
