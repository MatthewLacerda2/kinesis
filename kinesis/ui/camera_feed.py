"""Webcam feed for the canvas background.

Capture runs on a plain daemon thread in the main process -- unlike the tracker
this does no inference, so a thread is enough and keeping it in-process means
the frame lands as a QImage with no encode/decode round trip.

A main-thread QTimer polls the sequence counter and signals the UI, so nothing
Qt-owned is ever touched from the capture thread.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

CAM_W, CAM_H, CAM_FPS = 1280, 720, 30

# How many consecutive bad reads before we call the camera dead.
MAX_BAD_READS = 60

POLL_HZ = 60


class CameraFeed(QObject):
    """Latest webcam frame as a QImage, mirrored, or None while off."""

    frame_ready = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False

        self._lock = threading.Lock()
        self._image: QImage | None = None
        self._seq = 0
        self._error: str | None = None
        self._seen_seq = -1

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._poll = QTimer(self)
        self._poll.setInterval(int(1000 / POLL_HZ))
        self._poll.timeout.connect(self._on_poll)

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self.active:
            return
        self._stop.clear()
        with self._lock:
            self._error = None
            self._image = None
            self._seq = 0
        self._seen_seq = -1
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="kinesis-bg-camera")
        self._thread.start()
        self._poll.start()

    def stop(self) -> None:
        if not self.active:
            return
        self._poll.stop()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self.active = False
        with self._lock:
            self._image = None
        self.frame_ready.emit()

    def toggle(self) -> bool:
        self.stop() if self.active else self.start()
        return self.active

    def latest(self) -> QImage | None:
        with self._lock:
            return self._image

    # ---------- main thread ----------

    def _on_poll(self) -> None:
        with self._lock:
            error, seq = self._error, self._seq
        if error:
            self.stop()
            self.failed.emit(error)
            return
        if seq != self._seen_seq:
            self._seen_seq = seq
            self.frame_ready.emit()

    # ---------- capture thread ----------

    def _fail(self, message: str) -> None:
        with self._lock:
            self._error = message

    def _run(self) -> None:
        import cv2

        cap = None
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
            cap.set(cv2.CAP_PROP_FPS, CAM_FPS)
            if not cap.isOpened():
                self._fail("Camera unavailable — check System Settings → "
                           "Privacy & Security → Camera")
                return

            bad = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None or not frame.size:
                    bad += 1
                    if bad > MAX_BAD_READS:
                        self._fail("Lost the camera feed.")
                        return
                    self._stop.wait(0.01)
                    continue
                bad = 0

                # Mirror so the background reads as a selfie view, matching the
                # mirrored frame the tracker works in.
                rgb = cv2.cvtColor(cv2.flip(frame, 1), cv2.COLOR_BGR2RGB)
                h, w, _ = rgb.shape
                # .copy() detaches the QImage from the numpy buffer, which the
                # next read() overwrites.
                image = QImage(rgb.data, w, h, rgb.strides[0],
                               QImage.Format.Format_RGB888).copy()
                with self._lock:
                    self._image = image
                    self._seq += 1
        except Exception as exc:  # noqa: BLE001 - must never take the UI down
            self._fail(f"Camera feed failed: {exc!r}")
        finally:
            if cap is not None:
                cap.release()
