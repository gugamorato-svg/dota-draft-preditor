"""Testes da sigmoid usada no treino.

O treino calcula a predição como sigmoid(intercept + Σ w·x). Com coeficientes
grandes e features desbalanceadas o argumento pode ficar abaixo de -710, e a
forma ingênua 1/(1+exp(-v)) estoura com OverflowError.
"""

import math
import unittest

from train_model import sigmoid


class SigmoidTest(unittest.TestCase):
    def test_boundaries(self):
        self.assertAlmostEqual(sigmoid(0), 0.5, places=12)
        self.assertAlmostEqual(sigmoid(1000), 1.0, places=12)
        self.assertAlmostEqual(sigmoid(-1000), 0.0, places=12)

    def test_extreme_values_do_not_raise(self):
        for value in (710, 800, 1000, 1e6, -710, -800, -1000, -1e6):
            with self.subTest(value=value):
                result = sigmoid(value)
                self.assertTrue(math.isfinite(result))
                self.assertGreaterEqual(result, 0.0)
                self.assertLessEqual(result, 1.0)

    def test_matches_original_formula_on_normal_range(self):
        for value in [x / 4 for x in range(-20, 21)]:
            expected = 1 / (1 + math.exp(-value))
            self.assertAlmostEqual(sigmoid(value), expected, places=12)


if __name__ == "__main__":
    unittest.main()
