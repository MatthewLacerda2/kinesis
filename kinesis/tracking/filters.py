"""One Euro filter (Casiez, Roussel & Vogel, CHI 2012).

Adaptive cutoff: heavy smoothing when the hand is still (kills the several-pixel
resting jitter of raw landmarks), light smoothing when it moves fast (so fast
motion doesn't lag). A fixed low-pass can only trade one for the other.

The filters also report the lag they are adding, because that lag is the single
largest term in the capture-to-screen budget and nothing else in the app can
see it: it is not a queue you can time with a clock, it is a property of the
cutoff the filter chose on that frame. Leaving it invisible is how a constant
sat two orders of magnitude off without anyone noticing (#35, #38).

Pure: no camera, no Qt, no MediaPipe -- plain numbers in, plain numbers out.
"""

from __future__ import annotations

import math


def smoothing_alpha(cutoff: float, dt: float) -> float:
    """Exponential-smoothing factor for a given cutoff frequency and timestep."""
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class LowPassFilter:
    """Plain exponential low-pass that remembers its last output."""

    def __init__(self) -> None:
        self.y: float | None = None

    def __call__(self, x: float, alpha: float) -> float:
        self.y = x if self.y is None else alpha * x + (1.0 - alpha) * self.y
        return self.y

    @property
    def initialized(self) -> bool:
        return self.y is not None

    def reset(self) -> None:
        self.y = None


class OneEuroFilter:
    """Filter one scalar signal sampled at irregular intervals.

    Uses real elapsed time between samples rather than an assumed dt -- the
    camera does not deliver a steady rate (it halves in dim light), and an
    assumed dt would make the smoothing wrong exactly when frames are dropping.
    """

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x = LowPassFilter()
        self._dx = LowPassFilter()
        self._t_prev: float | None = None
        # The cutoff actually used on the last sample. Kept rather than
        # discarded because the group delay below can only be honest if it is
        # derived from the number the filter really applied.
        self.cutoff = float(min_cutoff)

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._t_prev = None
        self.cutoff = self.min_cutoff

    @property
    def velocity(self) -> float:
        """Low-passed rate of change the adaptive cutoff was derived from.

        Not the true signal velocity -- the derivative is taken against the
        filter's own last output, so it reads high by the lag itself. It is
        exposed because it is what the cutoff was computed from, and combining
        two axes' delays needs the same weights the filter used.
        """
        return self._dx.y or 0.0

    @property
    def group_delay(self) -> float:
        """Seconds this filter trails a steadily moving signal by.

        Exact, not an approximation: for the exponential smoothing stage with
        alpha = 1/(1 + tau/dt), a constant-velocity input settles to an output
        exactly tau behind it, independent of dt. So the lag the user feels is
        1/(2*pi*cutoff) at the cutoff this filter last chose -- which is why
        raising `beta` visibly collapses it the moment the hand moves.
        """
        return 1.0 / (2.0 * math.pi * self.cutoff)

    def __call__(self, x: float, t: float) -> float:
        if self._t_prev is None:
            self._t_prev = t
            self._dx(0.0, 1.0)
            return self._x(x, 1.0)

        dt = t - self._t_prev
        if dt <= 0:
            # Duplicate or out-of-order timestamp: hold, don't divide by zero.
            return self._x.y if self._x.y is not None else x
        self._t_prev = t

        # Derivative, itself low-passed at a fixed cutoff.
        prev = self._x.y if self._x.y is not None else x
        dx = (x - prev) / dt
        edx = self._dx(dx, smoothing_alpha(self.d_cutoff, dt))

        # The adaptive part: faster motion -> higher cutoff -> less smoothing.
        self.cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x(x, smoothing_alpha(self.cutoff, dt))


class Vec2Filter:
    """Two One Euro filters, applied independently to x and y."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0) -> None:
        self.x = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.y = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def __call__(self, xy: tuple[float, float], t: float) -> tuple[float, float]:
        return self.x(xy[0], t), self.y(xy[1], t)

    @property
    def group_delay(self) -> float:
        """Seconds the smoothed point trails the hand by, as one number.

        The axes filter independently and so lag by different amounts. What a
        person sees is the length of the position error divided by the speed --
        how many milliseconds of travel the cursor is behind. Weighting each
        axis' delay by that axis' velocity is what stops a still axis, parked
        at the resting lag, from masking the moving one: a purely horizontal
        sweep should report the horizontal lag and nothing else.
        """
        vx, vy = self.x.velocity, self.y.velocity
        speed = math.hypot(vx, vy)
        if speed <= 1e-9:
            return max(self.x.group_delay, self.y.group_delay)
        return math.hypot(self.x.group_delay * vx, self.y.group_delay * vy) / speed

    def set_params(self, min_cutoff: float, beta: float, d_cutoff: float) -> None:
        for f in (self.x, self.y):
            f.min_cutoff, f.beta, f.d_cutoff = min_cutoff, beta, d_cutoff

    def reset(self) -> None:
        self.x.reset()
        self.y.reset()
