"""sigmoid deve ser numerica e numericamente estavel em todo o dominio real."""

import math

import pytest

from utils import sigmoid


def test_extreme_negative_returns_almost_zero_without_overflow():
    assert sigmoid(-1000) == pytest.approx(0.0, abs=1e-300)


def test_extreme_positive_returns_almost_one():
    assert sigmoid(1000) == pytest.approx(1.0, abs=1e-300)


def test_zero_is_one_half():
    assert sigmoid(0) == pytest.approx(0.5)


@pytest.mark.parametrize("x", [-1e6, -800, -710, -100, 100, 710, 800, 1e6])
def test_no_overflow_on_extremes(x):
    value = sigmoid(x)
    assert math.isfinite(value)
    assert 0.0 <= value <= 1.0


def test_common_range_unchanged():
    for x in (-5.0, -1.5, -0.5, 0.0, 0.5, 1.5, 5.0):
        assert sigmoid(x) == pytest.approx(1 / (1 + math.exp(-x)), rel=1e-15, abs=1e-15)
