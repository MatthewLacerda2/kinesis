"""Locate (and if needed, download) the MediaPipe hand_landmarker.task model.

Kept out of the tracker loop so setup can call it up front and fail loudly,
rather than discovering a missing model once the camera is already open.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_DIR = Path.home() / ".cache" / "kinesis" / "models"
MODEL_PATH = MODEL_DIR / "hand_landmarker.task"

# The float16 bundle is ~7.8MB. We only sanity-check the magnitude: enough to
# catch a truncated download or an HTML error page saved as .task.
MIN_BYTES = 4_000_000


def quiet_mediapipe() -> None:
    """Silence TF-Lite / glog startup spam. Must run before importing mediapipe."""
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("GLOG_logtostderr", "0")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("ABSL_MIN_LOG_LEVEL", "2")


@contextlib.contextmanager
def quiet_native_stderr():
    """Swallow C++-level glog/TF-Lite chatter written straight to fd 2.

    Env vars alone don't silence MediaPipe's graph-init logging, and it scribbles
    over any live terminal readout. Scoped narrowly (graph construction), and if
    the wrapped block raises, everything captured is replayed first -- a genuine
    init failure must never be hidden.
    """
    sys.stderr.flush()
    saved = os.dup(2)
    tmp = tempfile.TemporaryFile()
    try:
        os.dup2(tmp.fileno(), 2)
        yield
    except BaseException:
        os.dup2(saved, 2)
        tmp.seek(0)
        sys.stderr.buffer.write(tmp.read())
        sys.stderr.flush()
        raise
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        tmp.close()


def ensure_model(*, download: bool = True) -> Path:
    """Return the path to hand_landmarker.task, downloading it once if absent.

    Raises RuntimeError with an actionable message rather than returning a bad path.
    """
    if MODEL_PATH.exists():
        size = MODEL_PATH.stat().st_size
        if size >= MIN_BYTES:
            return MODEL_PATH
        print(
            f"[kinesis] {MODEL_PATH} is only {size} bytes — looks truncated, re-downloading.",
            file=sys.stderr,
        )
        MODEL_PATH.unlink()

    if not download:
        raise RuntimeError(
            f"Hand landmarker model missing at {MODEL_PATH}.\n"
            f"Run: python -m kinesis.tracking.model"
        )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_PATH.with_suffix(".task.partial")
    print(f"[kinesis] Downloading hand_landmarker.task -> {MODEL_PATH}")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not download the hand landmarker model.\n"
            f"  url:   {MODEL_URL}\n"
            f"  error: {exc}\n"
            f"Download it manually and save it as {MODEL_PATH}"
        ) from exc

    size = tmp.stat().st_size
    if size < MIN_BYTES:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Downloaded model is only {size} bytes (expected >{MIN_BYTES}). "
            f"The URL may have returned an error page:\n  {MODEL_URL}"
        )
    tmp.replace(MODEL_PATH)
    print(f"[kinesis] Model ready ({size / 1e6:.1f} MB)")
    return MODEL_PATH


def verify_loadable(path: Path) -> None:
    """Prove the bundle actually opens in MediaPipe. The real integrity check."""
    quiet_mediapipe()
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    options = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(path)),
        running_mode=vision.RunningMode.IMAGE,
        num_hands=2,
    )
    with quiet_native_stderr():
        with vision.HandLandmarker.create_from_options(options):
            pass


if __name__ == "__main__":
    p = ensure_model()
    verify_loadable(p)
    print(f"[kinesis] OK: {p} loads in MediaPipe.")
