# Build brief: `kinesis` — a PureRef-style reference board driven by webcam hand tracking

Build this from scratch, then launch it. I'm on a **MacBook M4 (Apple Silicon, macOS)**. This is a
personal tool, not a product — no telemetry, no auth, no cloud, no packaging for distribution.

Work through the milestones in order. **Stop and let me test at each checkpoint** rather than
building everything and hoping. Tell me exactly what to run and what I should see.

---

## 1. What it is

An infinite canvas for reference images — same core idea as PureRef. Drop images in, arrange them,
pan and zoom around. The twist: my webcam tracks my hands, and I can **pinch in the air to grab an
image and move it**, and **pinch with both hands to scale and rotate it**.

Mouse and keyboard must work fully and independently. Hand tracking is an *additional* input mode I
toggle on, not a replacement. If the camera dies the app keeps working.

Gestures only affect this app's canvas. Do **not** move the real macOS cursor, do not use
`pyautogui`, do not request Accessibility permissions. Camera permission is the only one needed.

---

## 2. Stack — this is decided, don't re-litigate it

| Layer | Choice |
|---|---|
| Language | Python 3.12 (see §7 — do not use 3.13) |
| GUI + canvas | **PySide6**, `QGraphicsView` / `QGraphicsScene` with a `QOpenGLWidget` viewport |
| Hand tracking | **MediaPipe Tasks** `HandLandmarker` (`mediapipe` package) |
| Camera | `opencv-python` (`cv2.VideoCapture(0)`, AVFoundation backend) |
| Concurrency | Tracker runs in a **separate process** (`multiprocessing`), not a thread |
| Env | `uv` if available, otherwise `python3.12 -m venv` |

Rationale, so you don't wander: `QGraphicsScene` already gives us the scene graph, per-item affine
transforms, z-order, hit-testing and selection that this app is 80% made of — rebuilding that in a
Rust immediate-mode GUI would be weeks of work for no benefit at this scale, and there's no mature
Rust MediaPipe binding, so a Rust frontend would need a Python sidecar and an IPC bridge *anyway*.
One language, one debug loop. The hard part of this project is gesture feel, which is pure
iteration — optimize for iteration speed.

---

## 3. Process architecture

```
main process (PySide6)                      tracker process
┌────────────────────────────┐             ┌──────────────────────────┐
│ QApplication               │             │ cv2.VideoCapture(0)      │
│ QGraphicsScene / View      │  Queue      │ MediaPipe HandLandmarker │
│ 60Hz QTimer:               │ <────────── │ OneEuroFilter smoothing  │
│   drain queue → take last  │ (maxsize=2) │ pinch state machine      │
│   apply to scene           │             │ emit HandFrame @ ~30fps  │
│ overlay: preview + cursors │             └──────────────────────────┘
└────────────────────────────┘
```

- Separate **process**, not a thread: the capture+inference loop must never be able to stutter the
  UI, and a camera crash shouldn't take down the canvas. It also lets me restart tracking without
  restarting the app.
- Queue is `maxsize=2` and the producer **drops the oldest frame when full** — never block on a full
  queue, stale hand data is worse than no hand data.
- The UI timer **drains the queue completely and uses only the last item**. Never process a backlog.
- Send small dataclasses of floats over the queue, **not video frames**. Only when the debug preview
  is enabled, additionally send a 320×240 JPEG-encoded frame (quality ~60).
- All smoothing and pinch detection happens in the **tracker process**, so the UI just consumes
  clean, already-decided state.

### IPC message shape

```python
@dataclass(frozen=True)
class Hand:
    handedness: str        # "Left" | "Right"
    pinch_xy: tuple[float, float]   # smoothed, normalized 0..1, ALREADY mirrored
    pinch_ratio: float     # raw normalized pinch distance, for the tuning UI
    pinching: bool         # post-hysteresis boolean
    hand_scale: float      # wrist->middle-MCP distance, proxy for depth
    landmarks: list[tuple[float, float]] | None   # only when preview enabled

@dataclass(frozen=True)
class HandFrame:
    t: float               # time.perf_counter() at capture
    hands: list[Hand]      # 0, 1 or 2
    fps: float
    jpeg: bytes | None
```

---

## 4. The gesture layer — this is the part that matters

Most naive implementations of this feel awful. These specifics are why. Implement them as written,
then let me tune.

### 4.1 Smoothing: One Euro filter (non-negotiable)

Raw MediaPipe landmark output jitters by several pixels at rest. A plain moving average or a fixed
low-pass adds visible lag. Implement the **One Euro filter** (Casiez, Roussel, Vogel 2012) — adaptive
cutoff: heavy smoothing when the hand is still, light smoothing when it moves fast.

Put it in `tracking/filters.py` as a small standalone class with unit tests. Starting params:
`min_cutoff=1.0`, `beta=0.007`, `d_cutoff=1.0`. Apply it independently to `pinch_xy.x`,
`pinch_xy.y`, and `hand_scale`. Use real elapsed time between frames, not an assumed dt.

### 4.2 Pinch detection: normalized + hysteresis

```
d       = ||landmark[4] - landmark[8]||        # thumb tip to index tip
scale   = ||landmark[0] - landmark[9]||        # wrist to middle-finger MCP
ratio   = d / scale                            # depth-invariant
```

Dividing by hand size is what makes the pinch work at any distance from the camera. Then a **Schmitt
trigger**, not a single threshold:

- not pinching → pinching when `ratio < 0.30`
- pinching → released when `ratio > 0.45`

A single threshold flickers on and off around the boundary and makes you drop images constantly.
Both numbers must be live-tunable (§4.7).

### 4.3 Cursor point and coordinate mapping

- The cursor is the **midpoint of landmarks 4 and 8** (the pinch point) — that's what my eye is
  aiming with. Not the wrist, not the palm centroid.
- **Mirror X** (`x = 1.0 - x`). The webcam image is mirrored relative to my body; without this,
  moving right moves the cursor left and it's unusable.
- Map an **active sub-rectangle** of the frame to the full canvas viewport — default
  `x ∈ [0.20, 0.80]`, `y ∈ [0.15, 0.85]` — then clamp. Without this I'd have to stretch my arms to
  the edge of the camera's view to reach the corners of the screen. Make the rect tunable.

### 4.4 Hand identity

Track hands by MediaPipe's **handedness label**, not by list index. Indices swap between frames when
hands cross or one drops out, and index-based tracking makes objects teleport between hands.

### 4.5 Interaction model

**One hand pinches** over an image → grab the topmost image under the cursor. Store the grab offset
so the image doesn't snap its center to the cursor. Image follows the pinch point until release.
Bring it to the front on grab.

**Second hand pinches while the first is holding the same image** → two-point transform:
- scale factor = `current_distance_between_pinch_points / distance_at_the_moment_the_second_pinch_started`
- rotation = `current_angle - angle_at_that_same_moment`
- anchor at the midpoint of the two pinch points

Capture that reference state **at the transition frame**, so the image doesn't jump when the second
hand joins. When either hand releases, hand back to single-hand drag using the remaining hand,
again without a jump.

**Both hands pinch over empty canvas** → pan the view by the midpoint delta, zoom by the distance
ratio. Same no-jump rule.

**No hands detected** → hold the last state for ~300ms before releasing any grab. Hands briefly
leaving frame or a dropped detection shouldn't fling my images around.

### 4.6 Frame rate

Camera gives ~30fps; the UI runs at 60. In the UI timer, exponentially interpolate toward the latest
received position (`alpha ≈ 0.5`) so motion reads as smooth. Keep this light — One Euro is doing the
real work and stacking more smoothing here just adds lag.

### 4.7 Live tuning panel (build this early, it pays for itself)

A dockable panel, toggled with `T`, with live sliders for: pinch close threshold, pinch open
threshold, One Euro `min_cutoff` and `beta`, active-rect margins, UI lerp alpha. Show current
`pinch_ratio` per hand as a live number and a small rolling plot. Persist values to
`~/.config/kinesis/tuning.json`.

I will spend real time in this panel. Don't hardcode the constants anywhere else.

### 4.8 Debug overlay

Picture-in-picture in a corner (toggle `P`): the mirrored camera feed with the hand skeleton drawn,
plus on-canvas cursor rings — one per hand, colored by handedness, filled when pinching. Show
tracker FPS and end-to-end latency (`now - HandFrame.t`).

---

## 5. The canvas

- Infinite scrolling scene. Scroll wheel / trackpad pinch = zoom to cursor. Space+drag or
  middle-drag = pan.
- Drop image files onto the window to add them. `Cmd+V` pastes images from the clipboard.
  Support png, jpg, webp, gif (first frame is fine), bmp, tiff.
- Per-image: move, scale (corner handles, uniform), rotate (`Cmd`+drag on a handle), z-order
  (`Cmd+]` / `Cmd+[`), delete, duplicate.
- Multi-select with marquee and `Shift`-click; move/scale a selection as a group.
- **LOD**: on load, keep the source path and cache a pixmap capped at 2048px on the long edge.
  Regenerate at full res only when the on-screen scale exceeds 1:1. A board of large images should
  stay at 60fps while panning.
- Scene save/load: `Cmd+S` / `Cmd+O` to a `.kinesis` JSON file — image paths plus per-item transform,
  z-order, canvas viewport. Add a "pack" option that copies the images into a sibling folder so the
  scene is portable.
- `Cmd+0` fit-all, `Cmd+1` zoom 100%.
- `H` toggles hand tracking on/off, and it must start **off**.

Keep it visually plain — dark neutral background, thin selection outlines. No theming work.

---

## 6. Repo layout

```
kinesis/
  pyproject.toml
  run.sh                     # one command: ensure venv, install, launch
  README.md                  # setup + full keybinding table
  kinesis/
    __main__.py
    app.py                   # QApplication, main window, mode toggle
    canvas/
      scene.py
      view.py                # mouse/keyboard interaction
      items.py               # ImageItem (+ LOD)
      persistence.py         # .kinesis save/load
    tracking/
      worker.py              # tracker process entry: camera + mediapipe loop
      filters.py             # OneEuroFilter
      gestures.py            # pinch detection + hand state machine
      protocol.py            # the dataclasses above
    ui/
      overlay.py             # PIP preview + hand cursors
      tuning.py              # live sliders
  tests/
    test_filters.py
    test_gestures.py         # feed synthetic landmark sequences, assert transitions
```

`tracking/gestures.py` must be **pure** — landmark sequences in, state transitions out, no camera
and no Qt. That's what makes it testable, and it's the code most likely to need fixing.

---

## 7. macOS / Apple Silicon setup notes — read before you start

- **Use Python 3.12.** MediaPipe wheel coverage for 3.13 is unreliable; `pip install mediapipe` will
  fail to resolve on newer interpreters. If `python3.12` isn't present, install it
  (`brew install python@3.12` or `uv python install 3.12`) rather than falling back to whatever's
  default.
- MediaPipe's macOS wheels have had **arm64 tagging problems** (wheels tagged `x86_64` for
  `macosx_14`). If pip refuses to find a wheel on this M4, don't start building MediaPipe from
  source — tell me, and try in this order: (1) a slightly older `mediapipe` version, (2) forcing the
  platform tag, (3) the community `mediapipe-silicon` build. Report which worked.
- Download the `hand_landmarker.task` model file at setup time into `~/.cache/kinesis/models/`,
  verify it, and don't re-download if present. Fail loudly with a clear message if it's missing.
- **Camera permission (TCC) attaches to the parent app**, so launch from Terminal or iTerm and expect
  the permission prompt to name that app, not Python. First run may need me to approve it and
  relaunch — say so in the README. Handle `VideoCapture` returning empty frames gracefully: show a
  clear "camera unavailable — check System Settings → Privacy & Security → Camera" message in the
  UI, don't crash and don't spin.
- Set `cv2.CAP_AVFOUNDATION` explicitly and request 640×480 @ 30fps. Higher resolution buys nothing
  for landmark accuracy and costs latency.
- Suppress MediaPipe's noisy TF-Lite/glog startup spam so real errors are visible.

---

## 8. Milestones — checkpoint with me at each

**M0 — Environment.** Venv, deps, model download. A throwaway script that opens the camera, runs
HandLandmarker, and prints pinch ratio per hand at ~30fps. *Checkpoint: I run it and confirm my
pinches register.* Do not proceed until this works — everything downstream depends on it.

**M1 — Canvas, mouse only.** Drop images, pan, zoom, move, scale, rotate, select, z-order, save/load.
No tracking code at all. *Checkpoint: I use it as a plain reference board and it feels solid.*

**M2 — Tracker process + overlay.** Wire up the worker process, One Euro filter, pinch state machine,
PIP preview, cursor rings, tuning panel. Cursors move on canvas but **grab nothing yet**.
*Checkpoint: I watch the cursors and tune thresholds until pinch detection is crisp and lag is low.*

**M3 — One-hand grab.** Pinch to grab, drag, release. The 300ms hold-on-lost-hands rule.
*Checkpoint: I move images around with one hand.*

**M4 — Two-hand transform.** Scale + rotate an image with two pinches; two-hand canvas pan/zoom.
All the no-jump transition rules. *Checkpoint: I scale and rotate without anything snapping.*

**M5 — Tuning pass.** Fix whatever feels wrong from my feedback. Write the README keybinding table.

---

## 9. Definition of done

- `./run.sh` sets up everything and launches the app on a clean checkout.
- App starts with tracking **off** and is fully usable by mouse alone.
- `H` turns tracking on; within ~2 seconds my hands appear as cursors.
- I can pinch-grab an image, move it, and two-hand scale/rotate it without visible jitter,
  without accidental drops, and with latency that doesn't feel disconnected from my hand.
- Unplugging/denying the camera degrades to a clear message, never a crash.
- `pytest` passes; `tests/test_gestures.py` covers the hysteresis transitions and the
  single↔two-hand handoffs with synthetic input.
- README documents setup, every keybinding, and every tunable parameter.

## 10. Working style

- Show me code as you go; don't disappear for twenty tool calls.
- If something in this brief turns out to be wrong once you're in the code, **say so and propose the
  alternative** — don't silently work around it.
- Prefer boring, readable code. This is a toy I want to keep hacking on.
- Launch the app yourself at each checkpoint so I can just look at it.
