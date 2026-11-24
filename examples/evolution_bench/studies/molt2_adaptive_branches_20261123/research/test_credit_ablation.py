"""Mathematical properties only; no search-performance claims or paid calls."""
import pytest
from credit_ablation import normalized_credit


def test_positive_affine_objective_transformation():
    before, after = -0.63, -0.6295
    history = [0.0002, 0.001, 0.003]
    expected = normalized_credit(after-before, history, noise_tolerance=0.0001)
    for scale, offset in [(0.01, -4.0), (1.0, 30.0), (100.0, -500.0)]:
        gain = (scale*after+offset)-(scale*before+offset)
        actual = normalized_credit(gain, [scale*x for x in history], noise_tolerance=scale*0.0001)
        assert actual == pytest.approx(expected, abs=1e-8)


def test_empty_history_and_noise_gate():
    assert normalized_credit(0, [], noise_tolerance=0) == 0
    assert normalized_credit(-1, [], noise_tolerance=0) == 0
    assert normalized_credit(0.02, [], noise_tolerance=0.02) == 0
    assert normalized_credit(0.03, [], noise_tolerance=0.02) == 1


def test_invalid_and_nonfinite_results_never_earn_credit():
    for gain in [float('nan'), float('inf'), -float('inf')]:
        assert normalized_credit(gain, [1], noise_tolerance=0) == 0
    assert normalized_credit(100, [1], noise_tolerance=0, valid=False) == 0


def test_bounded_credit_and_historical_scale():
    assert normalized_credit(0.5, [1, 2, 3], noise_tolerance=0) == 0.25
    assert normalized_credit(100, [1, 2, 3], noise_tolerance=0) == 1


def test_extreme_finite_scales_do_not_overflow_the_median():
    assert normalized_credit(1e308, [1e308, 1e308], noise_tolerance=0) == 1
    assert normalized_credit(5e-324, [5e-324, 5e-324], noise_tolerance=0) == 1


@pytest.mark.parametrize('history,noise', [([0], 0), ([-1], 0), ([float('nan')], 0), ([], -1), ([], float('inf'))])
def test_rejects_invalid_configuration(history, noise):
    with pytest.raises(ValueError):
        normalized_credit(1, history, noise_tolerance=noise)
