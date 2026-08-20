"""Tracker process entry: camera + MediaPipe + smoothing + pinch state.

Runs as a separate process, not a thread, so capture/inference can never stutter
the UI and a camera crash can't take the canvas down with it.

All smoothing and pinch decisions happen here, so the UI consumes clean,
already-decided state.

The camera is a shared device: opening it here while the canvas background has
it open too means macOS picks one format for both, and the second client to
open is the one that decides. Asking for the same size the background asks for
is what keeps that harmless -- so the frames are watched and any other size is
reported, rather than silently cropping the tracked field of view.

Holding the camera also means this process must never outlive the UI: an orphan
keeps the webcam light on with no window to explain it, and no way for a person
to tell what is holding the device. So the parent is watched directly.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import threading
import time

from .protocol import HandFrame, SetTuning, Stop, TrackerStatus, Tuning

CAM_W, CAM_H, CAM_FPS = 640, 480, 30
PREVIEW_W, PREVIEW_H = 320, 240
PREVIEW_QUALITY = 60

# How many consecutive bad reads before we call the camera dead.
MAX_BAD_READS = 60

# How many consecutive frames of an unexpected size before we say so. macOS
# spends about half a second reconfiguring a shared camera and pushes a few
# odd-sized frames through on the way; only a size that outlives that is real.
SHAPE_WARN_FRAMES = 15


def _parent_gone(parent_pid: int) -> bool:
    """True once the process that spawned us is gone.

    `daemon=True` only covers the parent leaving through Python's own shutdown.
    A crash, a Force Quit or a SIGTERM skips that, and the tracker is then an
    orphan holding the camera open. Being reparented away from the spawning
    process is the one signal common to all of those.
    """
    return os.getppid() != parent_pid


def _publish(q: mp.Queue, item) -> None:
    """Put without ever blocking, dropping the oldest item when full.

    Stale hand data is worse than no hand data, so the producer never waits.
    """
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except (queue.Empty, OSError):
        pass
    try:
        q.put_nowait(item)
    except (queue.Full, OSError):
        pass


class _Latest:
    """Newest camera frame, published by the capture thread.

    Capture and inference must overlap. Done sequentially, each cycle costs
    read() (~33ms, it blocks for the next frame) + inference (~11ms), which
    caps throughput near 22fps and hands the model a frame that is already a
    frame-interval old. With a capture thread the inference loop always picks
    up the freshest frame and runs at the camera's full 30fps.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.bgr = None          # mirrored, for the preview JPEG
        self.rgb = None          # mirrored, what the model consumes
        self.t = 0.0
        self.seq = 0
        self.error: str | None = None
        self.warning: str | None = None   # non-fatal; drained by the inference loop
        self.stop = False


def _capture_loop(cap, cv2, shared: _Latest) -> None:
    """Read, mirror and colour-convert, all off the inference thread.

    This thread is blocked in read() ~90% of the time, so the flip and the
    BGR->RGB conversion are free here and would otherwise sit on the critical
    path between a frame arriving and landmarks coming out.
    """
    bad = 0
    off_size = 0
    while not shared.stop:
        ok, frame = cap.read()
        if not ok or frame is None or not frame.size:
            bad += 1
            if bad > MAX_BAD_READS:
                with shared.lock:
                    shared.error = "Lost the camera feed — was the camera unplugged?"
                shared.ready.set()
                return
            time.sleep(0.01)
            continue
        bad = 0

        # The only trustworthy report of the format we actually got.
        h, w = frame.shape[:2]
        if (w, h) == (CAM_W, CAM_H):
            off_size = 0
        else:
            off_size += 1
            if off_size == SHAPE_WARN_FRAMES:
                with shared.lock:
                    shared.warning = (
                        f"Camera is sending {w}x{h}, not the {CAM_W}x{CAM_H} asked for — "
                        "another client (most likely the camera background) reformatted "
                        "the shared camera. Tracking works but the field of view is cropped."
                    )

        stamp = time.perf_counter()
        # Mirror before inference: MediaPipe assigns handedness assuming a
        # selfie-view image, and this also puts landmark x straight into the
        # mirrored space the cursor needs.
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        with shared.lock:
            shared.bgr = frame
            shared.rgb = rgb
            shared.t = stamp
            shared.seq += 1
        shared.ready.set()


def tracker_main(frames_q: mp.Queue, control_q: mp.Queue, tuning: Tuning) -> None:
    """Process entry point. Owns the camera for its whole lifetime."""
    from .model import ensure_model, quiet_mediapipe, quiet_native_stderr

    quiet_mediapipe()

    import cv2
    import mediapipe as mp_lib
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    from .gestures import GestureEngine

    engine = GestureEngine(tuning)

    try:
        model_path = ensure_model(download=False)
    except RuntimeError as exc:
        _publish(frames_q, TrackerStatus("error", str(exc)))
        return

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
            _publish(frames_q, TrackerStatus(
                "error",
                "Camera unavailable — check System Settings → Privacy & Security → Camera",
            ))
            return

        # A denied camera opens fine and then yields nothing; probe before trusting it.
        ok = False
        for _ in range(20):
            ok, probe = cap.read()
            if ok and probe is not None and probe.size:
                break
            time.sleep(0.05)
        if not ok:
            _publish(frames_q, TrackerStatus(
                "error",
                "Camera opened but returned no frames — check System Settings → "
                "Privacy & Security → Camera, then restart kinesis",
            ))
            return

        # set() returns True even when the device ignores the request, and get()
        # echoes back what was asked rather than what is arriving -- measured, both
        # stayed clean while a second client had the camera reformatted. They are
        # still checked here because they do catch a size the camera cannot do at
        # all; the frame sizes _capture_loop watches catch everything else.
        got = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
               int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        if not set_ok or got != (CAM_W, CAM_H):
            _publish(frames_q, TrackerStatus(
                "warning",
                f"Camera would not take {CAM_W}x{CAM_H}; it reports {got[0]}x{got[1]}."))

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        with quiet_native_stderr():
            landmarker = vision.HandLandmarker.create_from_options(options)

        _publish(frames_q, TrackerStatus("running", "tracking"))

        shared = _Latest()
        grabber = threading.Thread(target=_capture_loop, args=(cap, cv2, shared),
                                   daemon=True, name="kinesis-capture")
        grabber.start()

        fps_ema = 0.0
        last_t = time.perf_counter()
        last_seq = -1
        first = True
        parent_pid = os.getppid()

        with landmarker:
            while True:
                # Drain control messages; apply only the newest tuning.
                stop = False
                while True:
                    try:
                        msg = control_q.get_nowait()
                    except (queue.Empty, OSError):
                        break
                    if isinstance(msg, SetTuning):
                        tuning = msg.tuning
                        engine.set_tuning(tuning)
                    elif isinstance(msg, Stop):
                        stop = True
                # Leaving on parent death uses the same exit as Stop, so the
                # camera is released by one path rather than two.
                if stop or _parent_gone(parent_pid):
                    break

                # Take the freshest frame the capture thread has; never re-run
                # inference on one already processed. Clear before checking, so
                # a frame arriving mid-check can't be missed.
                shared.ready.clear()
                with shared.lock:
                    if shared.error:
                        _publish(frames_q, TrackerStatus("error", shared.error))
                        break
                    warning, shared.warning = shared.warning, None
                    if shared.seq == last_seq:
                        rgb = None
                    else:
                        rgb = shared.rgb
                        frame = shared.bgr
                        t_capture = shared.t
                        last_seq = shared.seq
                if warning:
                    _publish(frames_q, TrackerStatus("warning", warning))
                if rgb is None:
                    shared.ready.wait(0.05)   # woken the moment one lands
                    continue

                # cvtColor already returns a contiguous buffer; no copy needed.
                image = mp_lib.Image(image_format=mp_lib.ImageFormat.SRGB, data=rgb)

                if first:
                    with quiet_native_stderr():
                        result = landmarker.detect_for_video(image, int(t_capture * 1000))
                    first = False
                else:
                    result = landmarker.detect_for_video(image, int(t_capture * 1000))

                detections = []
                for lm, handed in zip(result.hand_landmarks, result.handedness):
                    detections.append((
                        handed[0].category_name,
                        [(p.x, p.y) for p in lm],
                    ))

                hands = engine.update(detections, t_capture,
                                      include_landmarks=tuning.preview)

                now = time.perf_counter()
                dt = now - last_t
                last_t = now
                if dt > 0:
                    inst = 1.0 / dt
                    fps_ema = inst if fps_ema == 0 else fps_ema * 0.9 + inst * 0.1

                jpeg = None
                if tuning.preview:
                    small = cv2.resize(frame, (PREVIEW_W, PREVIEW_H))
                    enc, buf = cv2.imencode(
                        ".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_QUALITY])
                    if enc:
                        jpeg = buf.tobytes()

                _publish(frames_q, HandFrame(t=t_capture, hands=hands,
                                             fps=fps_ema, jpeg=jpeg))

    except Exception as exc:  # noqa: BLE001 - must never take the UI down
        _publish(frames_q, TrackerStatus("error", f"Tracker failed: {exc!r}"))
    finally:
        try:
            shared.stop = True
        except (NameError, UnboundLocalError):
            pass
        if cap is not None:
            cap.release()
        _publish(frames_q, TrackerStatus("stopped", "tracker stopped"))


def start_tracker(tuning: Tuning) -> tuple[mp.Process, mp.Queue, mp.Queue]:
    """Spawn the tracker. Queue is maxsize=2 with drop-oldest on the producer."""
    ctx = mp.get_context("spawn")
    frames_q: mp.Queue = ctx.Queue(maxsize=2)
    control_q: mp.Queue = ctx.Queue(maxsize=8)
    proc = ctx.Process(target=tracker_main, args=(frames_q, control_q, tuning),
                       daemon=True, name="kinesis-tracker")
    proc.start()
    return proc, frames_q, control_q
