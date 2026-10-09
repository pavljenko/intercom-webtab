"""Tests of the UTCI polynomial (intercom_webtab.weather.utci).

The implementation was cross-checked once, outside this repository, against an independent
port of the same published Fortran routine (UTCI_a002): on 23,486 random and grid points
inside the valid ranges the largest difference was 1.4e-12 degC. The golden values below were
produced by the cross-checked code and pin the polynomial against accidental edits.
"""
import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from intercom_webtab.weather import utci as U  # noqa: E402

# (air temperature, mean radiant temperature, wind at 10 m, relative humidity) -> UTCI, degC
GOLDEN = [
    ((25, 25, 1.0, 50), 24.6121),
    ((-10, -10, 5, 70), -27.5389),
    ((0, 10, 2, 80), 0.0262),
    ((30, 50, 1.5, 40), 34.4098),
    ((35, 60, 0.5, 30), 40.8268),
    ((-30, -35, 10, 60), -58.3472),
    ((15, 40, 3, 50), 18.5304),
    ((40, 40, 4, 20), 39.5803),
    ((5, 5, 12, 90), -20.2410),
    ((-45, -45, 0.5, 50), -44.4705),
    ((48, 70, 17, 10), 57.5432),
    ((20, 80, 8, 60), 27.5096),
]


class Polynomial(unittest.TestCase):
    def test_table_shape(self):
        # a 6th-order polynomial in 4 variables has C(10, 4) = 210 terms
        self.assertEqual(sum(len(r) for r in U._ROWS), 210)
        self.assertEqual(len(U._ROWS), 84)

    def test_golden_values(self):
        for args, want in GOLDEN:
            self.assertTrue(U.in_valid_range(*args), args)
            self.assertAlmostEqual(U.utci(*args), want, places=3, msg=args)

    def test_documented_example(self):
        # the usual worked example: 25 degC air and radiant temperature, 1 m/s, 50 % -> 24.6
        self.assertEqual(round(U.utci(25, 25, 1.0, 50), 1), 24.6)

    def test_reference_condition(self):
        # by definition UTCI equals the air temperature in the reference environment (Tmrt = Ta,
        # 0.5 m/s at 10 m, 50 % RH); the regression reproduces it within about 1 K
        for ta in range(-40, 30):
            self.assertLess(abs(U.utci(ta, ta, 0.5, 50) - ta), 1.5, ta)

    def test_monotonic(self):
        R = random.Random(7)
        for _ in range(500):
            ta = R.uniform(-40, 25)
            rh = R.uniform(10, 90)
            va = R.uniform(0.5, 8)
            # more radiation feels warmer
            self.assertGreater(U.utci(ta, ta + 20, va, rh), U.utci(ta, ta, va, rh))
            # in the cold more wind feels colder
            self.assertGreater(U.utci(ta, ta, va, rh), U.utci(ta, ta, va + 6, rh))
        for ta in range(28, 46, 3):
            # in the heat humidity feels hotter
            self.assertGreater(U.utci(ta, ta, 1, 80), U.utci(ta, ta, 1, 20))

    def test_wind_clamped_to_fitted_range(self):
        self.assertEqual(U.utci(10, 15, 0.0, 50), U.utci(10, 15, 0.5, 50))
        self.assertEqual(U.utci(10, 15, 0.2, 50), U.utci(10, 15, 0.5, 50))
        self.assertEqual(U.utci(10, 15, 30, 50), U.utci(10, 15, 17, 50))
        self.assertNotEqual(U.utci(10, 15, 3, 50), U.utci(10, 15, 4, 50))

    def test_humidity_clamped(self):
        self.assertEqual(U.utci(10, 10, 3, 120), U.utci(10, 10, 3, 100))
        self.assertEqual(U.utci(10, 10, 3, -5), U.utci(10, 10, 3, 0))

    def test_valid_range(self):
        self.assertTrue(U.in_valid_range(20, 20, 3, 50))
        self.assertFalse(U.in_valid_range(55, 55, 3, 50))         # air too hot
        self.assertFalse(U.in_valid_range(-55, -55, 3, 50))       # air too cold
        self.assertFalse(U.in_valid_range(20, 100, 3, 50))        # Tmrt more than 70 K above
        self.assertFalse(U.in_valid_range(20, -15, 3, 50))        # Tmrt more than 30 K below
        self.assertFalse(U.in_valid_range(20, 20, 0.3, 50))       # wind below 0.5 m/s
        self.assertFalse(U.in_valid_range(45, 45, 3, 100))        # vapour pressure above 50 hPa

    def test_bad_input(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                U.utci(bad, 20, 3, 50)
            with self.assertRaises(ValueError):
                U.utci(20, 20, bad, 50)
        with self.assertRaises((TypeError, ValueError)):
            U.utci("warm", 20, 3, 50)

    def test_saturation_vapour_pressure(self):
        # ITS-90 (Hardy 1998) values over water
        for t, hpa in ((0, 6.112), (20, 23.393), (30, 42.470), (100, 1014.18)):
            self.assertAlmostEqual(U.saturation_vapour_pressure(t), hpa, delta=0.01 * max(1, hpa / 100))

    def test_finite_over_valid_grid(self):
        n = 0
        for ta in range(-50, 51, 10):
            for d in (-30, 0, 35, 70):
                for va in (0.5, 3, 9, 17):
                    for rh in (0, 40, 100):
                        if U.in_valid_range(ta, ta + d, va, rh):
                            v = U.utci(ta, ta + d, va, rh)
                            self.assertTrue(math.isfinite(v))
                            self.assertTrue(-150 < v < 100, (ta, d, va, rh, v))
                            n += 1
        self.assertGreater(n, 300)


if __name__ == "__main__":
    unittest.main()
