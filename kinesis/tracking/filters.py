"""One Euro filter (Casiez, Roussel & Vogel, CHI 2012).

Adaptive cutoff: heavy smoothing when the hand is still (kills the several-pixel
resting jitter of raw landmarks), light smoothing when it moves fast (so fast
motion doesn't lag). A fixed low-pass can only trade one for the other.
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

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._t_prev = None

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
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self._x(x, smoothing_alpha(cutoff, dt))


class Vec2Filter:
    """Two One Euro filters, applied independently to x and y."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.007,
                 d_cutoff: float = 1.0) -> None:
        self.x = OneEuroFilter(min_cutoff, beta, d_cutoff)
        self.y = OneEuroFilter(min_cutoff, beta, d_cutoff)

    def __call__(self, xy: tuple[float, float], t: float) -> tuple[float, float]:
        return self.x(xy[0], t), self.y(xy[1], t)

    def set_params(self, min_cutoff: float, beta: float, d_cutoff: float) -> None:
        for f in (self.x, self.y):
            f.min_cutoff, f.beta, f.d_cutoff = min_cutoff, beta, d_cutoff

    def reset(self) -> None:
        self.x.reset()
        self.y.reset()
