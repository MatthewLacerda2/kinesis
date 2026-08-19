#!/usr/bin/env python3
"""M0 checkpoint: camera -> MediaPipe HandLandmarker -> pinch ratio per hand.

Throwaway rig to prove the tracking stack works before any app code exists.
No Qt, no smoothing, no filtering -- raw numbers so you can see the real jitter.

    ./run.sh m0              # or: .venv/bin/python scripts/m0_pinch_check.py
    ./run.sh m0 --preview    # also open a camera window with the skeleton drawn

Ctrl-C to quit (or `q` in the preview window).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kinesis.tracking.model import ensure_model, quiet_mediapipe, quiet_native_stderr

quiet_mediapipe()  # before mediapipe import, or glog spams the readout

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402
import numpy as np  # noqa: E402
from mediapipe.tasks import python as mp_python  # noqa: E402
from mediapipe.tasks.python import vision  # noqa: E402

# Landmark indices (MediaPipe hand model)
THUMB_TIP, INDEX_TIP, WRIST, MIDDLE_MCP = 4, 8, 0, 9

# Schmitt trigger thresholds (BUILD.md 4.2). Live-tunable later; CLI flags for now.
PINCH_CLOSE = 0.30
PINCH_OPEN = 0.45

CAM_W, CAM_H, CAM_FPS = 640, 480, 30

# Skeleton connections for the preview window.
CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),           # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),           # index
    (5, 9), (9, 10), (10, 11), (11, 12),      # middle
    (9, 13), (13, 14), (14, 15), (15, 16),    # ring
    (13, 17), (17, 18), (18, 19), (19, 20),   # pinky
    (0, 17),                                  # palm
]


def pinch_ratio(lm) -> tuple[float, float, tuple[float, float]]:
    """Return (ratio, hand_scale, pinch_midpoint) for one hand's landmarks.

    ratio = |thumb_tip - index_tip| / |wrist - middle_mcp|
    Dividing by hand size makes it depth-invariant (BUILD.md 4.2).
    """
    d = math.dist((lm[THUMB_TIP].x, lm[THUMB_TIP].y), (lm[INDEX_TIP].x, lm[INDEX_TIP].y))
    scale = math.dist((lm[WRIST].x, lm[WRIST].y), (lm[MIDDLE_MCP].x, lm[MIDDLE_MCP].y))
    ratio = d / scale if scale > 1e-6 else 999.0
    mid = ((lm[THUMB_TIP].x + lm[INDEX_TIP].x) / 2, (lm[THUMB_TIP].y + lm[INDEX_TIP].y) / 2)
    return ratio, scale, mid


def open_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_H)
    cap.set(cv2.CAP_PROP_FPS, CAM_FPS)
    if not cap.isOpened():
        die_no_camera("cv2.VideoCapture(0) would not open.")
    # A denied camera often *opens* fine and then yields nothing -- probe before trusting it.
    for _ in range(15):
        ok, frame = cap.read()
        if ok and frame is not None and frame.size:
            return cap
        time.sleep(0.05)
    cap.release()
    die_no_camera("The camera opened but returned no frames.")


def die_no_camera(detail: str) -> None:
    print(
        f"\n[kinesis] Camera unavailable — {detail}\n"
        "  Check System Settings → Privacy & Security → Camera and enable the app you\n"
        "  launched this from (Terminal / iTerm — not 'Python'). macOS attaches the\n"
        "  camera permission to the parent app. You may need to relaunch after granting it.\n",
        file=sys.stderr,
    )
    sys.exit(1)


def bar(ratio: float, width: int = 12) -> str:
    """Ratio 0..0.8 as a text meter, so pinch motion is visible at a glance."""
    filled = max(0, min(width, int(round(ratio / 0.8 * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def draw_preview(frame, hands: dict) -> None:
    h, w = frame.shape[:2]
    for label, info in hands.items():
        lm = info["landmarks"]
        pts = [(int(p.x * w), int(p.y * h)) for p in lm]
        color = (0, 255, 0) if info["pinching"] else (200, 200, 200)
        for a, b in CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], color, 1, cv2.LINE_AA)
        for x, y in pts:
            cv2.circle(frame, (x, y), 2, (255, 180, 0), -1, cv2.LINE_AA)
        cv2.line(frame, pts[THUMB_TIP], pts[INDEX_TIP], color, 3, cv2.LINE_AA)
        mx, my = int(info["mid"][0] * w), int(info["mid"][1] * h)
        cv2.circle(frame, (mx, my), 10, color, -1 if info["pinching"] else 2, cv2.LINE_AA)
        cv2.putText(frame, f"{label} {info['ratio']:.3f}", (mx + 14, my),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def main() -> None:
    ap = argparse.ArgumentParser(description="M0 pinch check")
    ap.add_argument("--preview", action="store_true", help="show the mirrored camera window")
    ap.add_argument("--close", type=float, default=PINCH_CLOSE, help="pinch-close threshold")
    ap.add_argument("--open", dest="open_", type=float, default=PINCH_OPEN,
                    help="pinch-open threshold")
    ap.add_argument("--num-hands", type=int, default=2)
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after N seconds (0 = run until Ctrl-C)")
    args = ap.parse_args()

    # When stdout is a pipe/file the in-place redraw becomes escape-code soup,
    # so fall back to appending a line periodically.
    live = sys.stdout.isatty()

    model_path = ensure_model()
    options = vision.HandLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=args.num_hands,
        min_hand_detection_confidence=0.5,
        min_hand_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    print("[kinesis] Opening camera…")
    cap = open_camera()
    print(
        f"[kinesis] M0 pinch check — close<{args.close:.2f}  open>{args.open_:.2f}\n"
        "  Hold a hand up to the camera and pinch thumb+index together.\n"
        "  Ctrl-C to quit.\n"
    )

    pinching: dict[str, bool] = {}   # handedness -> post-hysteresis state, kept across frames
    fps_ema = 0.0
    infer_ema = 0.0
    last_t = time.perf_counter()
    printed_lines = 0

    # Session stats, so a non-interactive run can still report what happened.
    started = time.perf_counter()
    deadline = started + args.seconds if args.seconds > 0 else None
    frames = 0
    frames_with_hand = 0
    pinch_events: dict[str, int] = {"Left": 0, "Right": 0}
    min_ratio: dict[str, float] = {}
    max_ratio: dict[str, float] = {}
    last_report = 0.0

    with quiet_native_stderr():
        landmarker = vision.HandLandmarker.create_from_options(options)

    with landmarker:
        try:
            first_inference = True
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.01)
                    continue

                # Mirror BEFORE inference. Two reasons: MediaPipe assigns handedness
                # assuming a selfie-view image, and it means landmark x is already in
                # the mirrored space the cursor needs -- no separate x = 1-x step.
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))

                t0 = time.perf_counter()
                if first_inference:
                    # The lazily-built inference subgraph logs on its first run too.
                    with quiet_native_stderr():
                        result = landmarker.detect_for_video(image, int(t0 * 1000))
                    first_inference = False
                else:
                    result = landmarker.detect_for_video(image, int(t0 * 1000))
                infer_ms = (time.perf_counter() - t0) * 1000

                now = time.perf_counter()
                dt = now - last_t
                last_t = now
                if dt > 0:
                    inst = 1.0 / dt
                    fps_ema = inst if fps_ema == 0 else fps_ema * 0.9 + inst * 0.1
                infer_ema = infer_ms if infer_ema == 0 else infer_ema * 0.9 + infer_ms * 0.1

                hands: dict[str, dict] = {}
                for lm, handed in zip(result.hand_landmarks, result.handedness):
                    # Key by handedness label, never list index (BUILD.md 4.4).
                    label = handed[0].category_name
                    ratio, scale, mid = pinch_ratio(lm)
                    was = pinching.get(label, False)
                    now_p = ratio < args.close if not was else ratio <= args.open_
                    pinching[label] = now_p
                    if now_p and not was:
                        pinch_events[label] = pinch_events.get(label, 0) + 1
                    min_ratio[label] = min(min_ratio.get(label, 9.9), ratio)
                    max_ratio[label] = max(max_ratio.get(label, 0.0), ratio)
                    hands[label] = {"ratio": ratio, "scale": scale, "mid": mid,
                                    "pinching": now_p, "landmarks": lm,
                                    "conf": handed[0].score}
                for gone in set(pinching) - set(hands):
                    pinching[gone] = False

                lines = [
                    f"  camera {fps_ema:5.1f} fps   inference {infer_ema:5.1f} ms   "
                    f"hands {len(hands)}"
                ]
                for label in ("Left", "Right"):
                    info = hands.get(label)
                    if info is None:
                        lines.append(f"  {label:<5}  {'—':<7}                    ")
                    else:
                        mark = "● PINCH" if info["pinching"] else "○      "
                        lines.append(
                            f"  {label:<5}  ratio {info['ratio']:.3f} {bar(info['ratio'])} "
                            f"{mark}  scale {info['scale']:.3f}"
                        )

                frames += 1
                if hands:
                    frames_with_hand += 1

                if live:
                    if printed_lines:
                        sys.stdout.write(f"\033[{printed_lines}A")
                    sys.stdout.write("\n".join(f"\033[2K{ln}" for ln in lines) + "\n")
                    sys.stdout.flush()
                    printed_lines = len(lines)
                elif now - last_report >= 0.5:
                    # Piped/redirected: one flat line every 0.5s.
                    last_report = now
                    state = "  ".join(
                        f"{lbl}:{info['ratio']:.3f}{'*' if info['pinching'] else ' '}"
                        for lbl, info in sorted(hands.items())
                    ) or "no hands"
                    print(f"t={now - started:5.1f}s  {fps_ema:4.1f}fps  {state}", flush=True)

                if args.preview:
                    draw_preview(frame, hands)
                    try:
                        cv2.imshow("kinesis M0 — mirrored camera (q to quit)", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                    except cv2.error as exc:
                        # No window server (headless/background launch) -- keep the
                        # numbers flowing rather than killing the run.
                        print(f"\n[kinesis] preview window unavailable ({exc}); "
                              f"continuing without it.", flush=True)
                        args.preview = False

                if deadline and now >= deadline:
                    break
        except (KeyboardInterrupt, BrokenPipeError):
            pass
        finally:
            cap.release()
            if args.preview:
                cv2.destroyAllWindows()
            elapsed = time.perf_counter() - started
            print(
                f"\n[kinesis] stopped after {elapsed:.1f}s — {frames} frames "
                f"({frames / elapsed if elapsed else 0:.1f} fps), "
                f"hand visible in {frames_with_hand}/{frames}."
            )
            for label in ("Left", "Right"):
                if label in min_ratio:
                    print(
                        f"  {label:<5}  ratio {min_ratio[label]:.3f}–{max_ratio[label]:.3f}"
                        f"   pinches detected: {pinch_events.get(label, 0)}"
                    )
            if not min_ratio:
                print("  No hand was ever detected — was one in frame?")


if __name__ == "__main__":
    main()
