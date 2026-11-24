"""Unlaunched credit-only prototype. Not imported by any evolution worker.

Inputs are global-frontier gains, not raw objective values or parent gains.
The caller must supply only prior valid positive gains from a fixed window.
Noise tolerance must be frozen from public reproducibility measurements before
an experimental launch. This helper does not allocate work or account costs.
"""
import math


def normalized_credit(gain, prior_positive_gains, *, noise_tolerance, valid=True):
    """Bounded, positive-affine-invariant progress credit, given scaled noise.

    Cold start: the first gain above the noise gate gets credit 1. Later gains
    use the median prior positive gain as scale. Translation of objective
    scores cancels when forming gains. Multiplication by a positive constant
    must also scale prior gains and the noise tolerance.

    This is a heuristic fractional reward, not a calibrated probability.
    Failed/invalid evaluations get zero credit but still cost evaluations in
    the eventual allocator; that accounting is outside this helper.
    """
    if not math.isfinite(noise_tolerance) or noise_tolerance < 0:
        raise ValueError('noise_tolerance must be finite and nonnegative')
    history = list(prior_positive_gains)
    if any(not math.isfinite(x) or x <= 0 for x in history):
        raise ValueError('history must contain finite strictly positive gains')
    if not valid or not math.isfinite(gain) or gain <= noise_tolerance:
        return 0.0
    if not history:
        return 1.0
    ordered = sorted(history)
    n = len(ordered)
    median = ordered[n // 2]
    if n % 2 == 0:
        lower = ordered[n // 2 - 1]
        median = lower + (median - lower) / 2
    scale = max(median, noise_tolerance)
    return min(1.0, max(0.0, (gain - noise_tolerance) / scale))
