"""Webcam feed for the canvas background.

Capture runs on a plain daemon thread in the main process -- unlike the tracker
this does no inference, so a thread is enough and keeping it in-process means
the frame lands as a QImage with no encode/decode round trip.

A main-thread QTimer polls the sequence counter and signals the UI, so nothing
Qt-owned is ever touched from the capture thread.

It asks for exactly the size the tracker asks for. The camera is one shared
device: with both open, macOS picks a single format and the second client to
open decides it, so a mismatched request silently reformats whichever opened
first -- and the usual order (H, then the camera button) is the one that
reformats the tracker. Matching sizes keeps both at 640x480 and 30fps, and the
background is a full-screen backdrop nothing is read from, so it loses nothing.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

# Must stay equal to tracking/worker.py's CAM_W/CAM_H -- see the module
# docstring. Raising it degrades hand tracking, not just this feed.
CAM_W, CAM_H, CAM_FPS = 640, 480, 30

# How many consecutive bad reads before we call the camera dead.
MAX_BAD_READS = 60

# How many consecutive frames of an unexpected size before we say so. macOS
# spends about half a second reconfiguring a shared camera and pushes a few
# odd-sized frames through on the way; only a size that outlives that is real.
SHAPE_WARN_FRAMES = 15

POLL_HZ = 60


class CameraFeed(QObject):
    """Latest webcam frame as a QImage, mirrored, or None while off."""

    frame_ready = Signal()
    failed = Signal(str)
    warning = Signal(str)      # feed still running, but not as asked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False

        self._lock = threading.Lock()
        self._image: QImage | None = None
        self._seq = 0
        self._error: str | None = None
        self._warning: str | None = None
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
            self._warning = None
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
            warning, self._warning = self._warning, None
        if warning:
            self.warning.emit(warning)
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

    def _warn(self, message: str) -> None:
        """Non-fatal: say something is off without tearing the feed down."""
        with self._lock:
            self._warning = message

    def _run(self) -> None:
        import cv2

        cap = None
        try:
            cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
            # Tuple, not a generator: all three sets must actually run.
            set_ok = all((
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W),
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H),
                cap.set(cv2.CAP_PROP_FPS, CAM_FPS),
            ))
            if not cap.isOpened():
                self._fail("Camera unavailable — check System Settings → "
                           "Privacy & Security → Camera")
                return

            # set() returns True even when the device ignores the request, and
            # get() echoes back what was asked rather than what is arriving --
            # measured, both stayed clean while a second client had the camera
            # reformatted. They are still checked because they do catch a size
            # the camera cannot do at all; the frame sizes below catch the rest.
            got = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                   int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            if not set_ok or got != (CAM_W, CAM_H):
                self._warn(f"Camera would not take {CAM_W}x{CAM_H}; "
                           f"it reports {got[0]}x{got[1]}.")

            bad = 0
            off_size = 0
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

                # The only trustworthy report of the format we actually got.
                if (w, h) == (CAM_W, CAM_H):
                    off_size = 0
                else:
                    off_size += 1
                    if off_size == SHAPE_WARN_FRAMES:
                        self._warn(
                            f"Camera background is sending {w}x{h}, not the "
                            f"{CAM_W}x{CAM_H} asked for — another client "
                            "reformatted the shared camera.")

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
