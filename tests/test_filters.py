import math

import pytest

from kinesis.tracking.filters import (
    LowPassFilter,
    OneEuroFilter,
    Vec2Filter,
    smoothing_alpha,
)


def ramp_lag(f, velocity, dt=1 / 30.0, samples=2000):
    """Steady-state lag, in seconds, of a filter fed a constant-velocity ramp.

    Measured, not derived: the output trails the input by a fixed distance
    once the filter settles, and that distance over the velocity is a time.
    """
    t = 0.0
    for _ in range(samples):
        out = f(velocity * t, t)
        t += dt
    return (velocity * (t - dt) - out) / velocity



def test_alpha_bounds():
    # Higher cutoff -> larger alpha -> less smoothing.
    assert smoothing_alpha(1.0, 1 / 30) < smoothing_alpha(10.0, 1 / 30)
    assert 0.0 < smoothing_alpha(1.0, 1 / 30) < 1.0


def test_lowpass_first_sample_passes_through():
    lp = LowPassFilter()
    assert lp(5.0, 0.5) == 5.0
    assert lp.initialized


def test_first_sample_is_unfiltered():
    f = OneEuroFilter()
    assert f(3.25, 0.0) == 3.25


def test_constant_signal_converges():
    f = OneEuroFilter()
    t = 0.0
    for _ in range(60):
        out = f(2.0, t)
        t += 1 / 30
    assert abs(out - 2.0) < 1e-6


def test_jitter_is_attenuated():
    """Alternating noise around a constant should be smoothed substantially."""
    f = OneEuroFilter(min_cutoff=1.0, beta=0.007)
    t, outputs = 0.0, []
    for i in range(80):
        noisy = 1.0 + (0.05 if i % 2 == 0 else -0.05)
        outputs.append(f(noisy, t))
        t += 1 / 30
    tail = outputs[-20:]
    amplitude = max(tail) - min(tail)
    assert amplitude < 0.02, f"jitter not attenuated: {amplitude}"


def test_fast_motion_lags_less_than_slow_smoothing_would():
    """The adaptive cutoff must track a fast ramp better than beta=0 does."""
    def run(beta):
        f = OneEuroFilter(min_cutoff=1.0, beta=beta)
        t, out = 0.0, 0.0
        for i in range(30):
            out = f(float(i) * 0.1, t)   # steadily moving target
            t += 1 / 30
        return out

    target = 29 * 0.1
    adaptive_err = abs(target - run(0.05))
    fixed_err = abs(target - run(0.0))
    assert adaptive_err < fixed_err


def test_non_monotonic_time_does_not_explode():
    f = OneEuroFilter()
    f(1.0, 1.0)
    held = f(2.0, 1.0)      # duplicate timestamp
    assert math.isfinite(held)
    assert abs(held - 1.0) < 1e-9


def test_reset_clears_state():
    f = OneEuroFilter()
    f(10.0, 0.0)
    f(10.0, 1 / 30)
    f.reset()
    assert f(-4.0, 0.0) == -4.0


# --- group delay: the number the HUD reports for One Euro lag (#38) ---


@pytest.mark.parametrize("min_cutoff,beta,velocity,dt", [
    (1.0, 0.0, 0.5, 1 / 30.0),      # no adaptation: pure low-pass
    (2.5, 0.05, 0.5, 1 / 30.0),     # the shipped defaults, slow hand
    (2.5, 0.05, 10.0, 1 / 30.0),    # the shipped defaults, fast hand
    (2.5, 0.5, 2.0, 1 / 30.0),      # heavy adaptation
    (2.5, 0.05, 2.0, 1 / 120.0),    # tau must not depend on the sample rate
    (5.0, 0.05, 0.1, 1 / 15.0),
])
def test_group_delay_matches_the_measured_ramp_lag(min_cutoff, beta, velocity, dt):
    """The closed form is the real lag, not an approximation of it.

    This is the test that makes the HUD number honest: if 1/(2*pi*cutoff) did
    not match what the filter actually does, the readout would be wrong in a
    new way, which is worse than the narrow one it replaces.
    """
    f = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)
    measured = ramp_lag(f, velocity, dt)
    assert measured == pytest.approx(f.group_delay, rel=1e-9)


def test_group_delay_at_rest_is_the_min_cutoff_delay():
    f = OneEuroFilter(min_cutoff=2.5, beta=0.05)
    t = 0.0
    for _ in range(120):
        f(1.0, t)
        t += 1 / 30
    assert f.group_delay == pytest.approx(1.0 / (2 * math.pi * 2.5), rel=1e-9)


def test_group_delay_collapses_as_beta_rises():
    """Raising beta must visibly shrink the reported lag on a moving hand.

    This is the property that would have caught #35 by eye: beta is the lever
    for perceived lag, so the readout has to move when it does.
    """
    delays = []
    for beta in (0.0, 0.05, 0.5):
        f = OneEuroFilter(min_cutoff=2.5, beta=beta)
        ramp_lag(f, velocity=5.0)
        delays.append(f.group_delay)
    assert delays[0] > delays[1] > delays[2]


def test_group_delay_before_any_sample_is_finite():
    f = OneEuroFilter(min_cutoff=2.5, beta=0.05)
    assert f.group_delay == pytest.approx(1.0 / (2 * math.pi * 2.5))
    f(1.0, 0.0)
    f(2.0, 1 / 30)
    f.reset()
    assert f.group_delay == pytest.approx(1.0 / (2 * math.pi * 2.5))


def test_vec2_group_delay_follows_the_moving_axis():
    """A purely horizontal sweep reports the horizontal lag, not the still y."""
    f = Vec2Filter(min_cutoff=2.5, beta=0.05)
    t = 0.0
    for _ in range(600):
        f((4.0 * t, 1.0), t)
        t += 1 / 30
    assert f.group_delay == pytest.approx(f.x.group_delay, rel=1e-9)
    assert f.group_delay < f.y.group_delay


def test_vec2_group_delay_at_rest_is_the_slower_axis():
    f = Vec2Filter(min_cutoff=2.5, beta=0.05)
    t = 0.0
    for _ in range(60):
        f((1.0, 2.0), t)
        t += 1 / 30
    assert f.group_delay == pytest.approx(f.x.group_delay, rel=1e-9)
    assert f.group_delay == pytest.approx(f.y.group_delay, rel=1e-9)


def test_vec2_group_delay_is_between_the_two_axes_when_both_move():
    f = Vec2Filter(min_cutoff=2.5, beta=0.05)
    t = 0.0
    for _ in range(600):
        f((6.0 * t, 0.5 * t), t)     # fast x, slow y
        t += 1 / 30
    assert f.x.group_delay < f.group_delay < f.y.group_delay
