import math

from kinesis.tracking.filters import LowPassFilter, OneEuroFilter, smoothing_alpha


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
