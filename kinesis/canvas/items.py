"""Board items. Currently just ImageItem, which owns the level-of-detail logic."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

# Cap the working pixmap on the long edge; full res is loaded only past 1:1.
PREVIEW_MAX_EDGE = 2048

# LOD hysteresis: upgrade above 1:1, drop the full-res copy well below it, so
# nudging around the boundary can't thrash decode/free every frame.
LOD_UPGRADE = 1.0
LOD_DOWNGRADE = 0.8

SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def is_supported_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


class ImageItem(QGraphicsItem):
    """An image on the board.

    Geometry is centred on the item origin (boundingRect spans -w/2..+w/2) so
    setScale/setRotation pivot about the image centre without extra transform
    origin bookkeeping, and setPos places the centre directly.

    boundingRect is always in *source pixels*, independent of which pixmap is
    currently resident, so swapping preview <-> full res never moves anything.
    """

    def __init__(self, path: str | None, image: QImage | None = None,
                 item_id: str | None = None):
        super().__init__()
        self.item_id = item_id or uuid.uuid4().hex[:12]
        self.source_path = str(path) if path else None

        self._full: QPixmap | None = None
        self._full_failed = False

        if image is not None:
            src = image
            self._natural_w, self._natural_h = src.width(), src.height()
            self._preview = QPixmap.fromImage(self._downscale(src))
        else:
            self._preview, self._natural_w, self._natural_h = self._load_preview(self.source_path)

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    # ---------- loading ----------

    @staticmethod
    def _downscale(img: QImage) -> QImage:
        if max(img.width(), img.height()) <= PREVIEW_MAX_EDGE:
            return img
        return img.scaled(
            PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _load_preview(path: str | None) -> tuple[QPixmap, int, int]:
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
        return QPixmap.fromImage(img), nat_w, nat_h

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

    # ---------- persistence ----------

    def to_dict(self) -> dict:
        return {
            "id": self.item_id,
            "path": self.source_path,
            "x": self.pos().x(),
            "y": self.pos().y(),
            "scale": self.scale(),
            "rotation": self.rotation(),
            "z": self.zValue(),
        }
