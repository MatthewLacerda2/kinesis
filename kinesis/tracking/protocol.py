"""Messages passed between the main process and the tracker process.

Only small dataclasses of floats cross the queue -- never video frames, except
an optional small JPEG when the debug preview is switched on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Hand:
    handedness: str                      # "Left" | "Right"
    pinch_xy: tuple[float, float]        # smoothed, mapped to canvas 0..1, already mirrored
    raw_xy: tuple[float, float]          # smoothed, still in frame space (for the preview)
    pinch_ratio: float                   # metric fingertip gap / palm length, for the tuning UI
    pinching: bool                       # post-hysteresis
    hand_scale: float                    # projected wrist->middle-MCP distance, proxy for depth
    landmarks: list[tuple[float, float]] | None = None   # only when preview enabled


@dataclass(frozen=True)
class HandFrame:
    t: float                             # time.perf_counter() at capture
    hands: list[Hand]
    fps: float
    jpeg: bytes | None = None


@dataclass(frozen=True)
class TrackerStatus:
    """Out-of-band news from the tracker: startup, camera loss, fatal errors.

    "warning" is non-fatal -- the tracker keeps running and the UI must not tear
    it down; it is how a camera that is running but not in the shape we asked
    for gets said out loud instead of silently costing frame rate.
    """
    state: str                           # "starting" | "running" | "warning" | "error" | "stopped"
    message: str = ""


@dataclass
class Tuning:
    """Every live-tunable number. Nothing here may be hardcoded elsewhere."""

    # The ratio these gate is now metric (#32), so the old 0.144/0.216 -- fitted
    # by hand to the projected ratio -- cannot carry over: the same gesture
    # reads higher in metres, and reusing them would make every pinch
    # impossible. Derived instead, two ways that agree. (1) Equivalence: a real
    # detected hand measures 94 mm wrist->middle-MCP, and in the pose the old
    # setting was tuned in -- hand side-on, fingers closing horizontally -- an
    # 18 mm gap projects to 0.138, so 0.144 was firing at a gap of 18.8 mm,
    # which is 0.199 in metres. (2) Physically: the thumb and index landmarks
    # sit at the centres of the fingertips, so finger pads touching is a gap of
    # about 18 mm, and 18/94 = 0.19. Rounded up to 0.20 rather than down,
    # because a slightly loose trigger costs an early grab you can see and
    # undo, while a slightly tight one is the bug this replaced. The band keeps
    # its 1.5x ratio, so the Schmitt trigger stays exactly as resistant to
    # flicker as before.
    pinch_close: float = 0.20
    pinch_open: float = 0.30

    # Lag control. cutoff = min_cutoff + beta*|velocity|: min_cutoff sets how
    # much smoothing survives when the hand is still, beta how fast that
    # smoothing lets go once it moves. beta is the lever that matters for
    # perceived lag, since lag is only noticeable while moving.
    min_cutoff: float = 2.5
    beta: float = 0.05
    d_cutoff: float = 1.0

    # Active sub-rectangle of the frame mapped to the whole canvas viewport.
    rect_x0: float = 0.20
    rect_x1: float = 0.80
    rect_y0: float = 0.15
    rect_y1: float = 0.85

    lerp_alpha: float = 0.9              # UI-side interpolation toward latest sample
    lost_hold_ms: float = 300.0          # keep a grab alive this long after hands vanish

    preview: bool = False                # send JPEG frames for the PIP

    def replace(self, **kw) -> Tuning:
        return replace(self, **kw)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> Tuning:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# Control messages, main -> tracker.
@dataclass(frozen=True)
class SetTuning:
    tuning: Tuning


@dataclass(frozen=True)
class Stop:
    pass
