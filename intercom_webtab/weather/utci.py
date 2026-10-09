"""Universal Thermal Climate Index (UTCI): the operational polynomial approximation.

UTCI is the air temperature of a reference environment that would cause the same
physiological strain as the actual one (air temperature, mean radiant temperature, wind
and humidity). The full model is a multi-node thermoregulation model; for operational use
its output is approximated by a 6th-order polynomial in four variables:

    Bröde P., Fiala D., Błażejczyk K., Holmér I., Jendritzky G., Kampmann B., Tinz B.,
    Havenith G. (2012). Deriving the operational procedure for the Universal Thermal
    Climate Index (UTCI). International Journal of Biometeorology 56(3): 481-494.

The 210 regression constants below are the published ones from the public reference
program UTCI_a002 (P. Bröde, version a 0.002, October 2009, distributed at utci.org,
released for public use). The code around them is an independent implementation for this
project (MIT licence).

Valid input ranges of the approximation:
    air temperature             -50 ... +50 degC
    mean radiant temperature    30 degC below ... 70 degC above air temperature
    wind speed at 10 m           0.5 ... 17 m/s  (clamped into this range here)
    water vapour pressure       up to 50 hPa (relative humidity 0 ... 100 %)
"""
import math

__all__ = ["utci", "saturation_vapour_pressure", "in_valid_range", "WIND_MIN", "WIND_MAX"]

WIND_MIN = 0.5
WIND_MAX = 17.0

# Hardy (1998), ITS-90 formulation for saturation vapour pressure over water, as used by
# the reference program: ln(es[Pa]) = sum(g[i] * T**(i - 2), i = 0..6) + g[7] * ln(T).
_HARDY = (-2.8365744e3, -6.028076559e3, 1.954263612e1, -2.737830188e-2,
          1.6261698e-5, 7.0229056e-10, -1.8680009e-13, 2.7150305)

# Polynomial coefficients. Each row belongs to one combination of exponents of water vapour
# pressure Pa (kPa), the radiant offset dTmrt = Tmrt - Ta (K) and wind speed va (m/s), and
# holds the coefficients of Ta**0, Ta**1, ... up to the total degree 6. Rows are ordered by
# Pa exponent, then dTmrt exponent, then va exponent (all ascending).
_ROWS = (
    # Pa^0 dTmrt^0 va^0
    (6.07562052e-01, -2.27712343e-02, 8.06470249e-04, -1.54271372e-04,
     -3.24651735e-06, 7.32602852e-08, 1.35959073e-09),
    # Pa^0 dTmrt^0 va^1
    (-2.25836520e+00, 8.80326035e-02, 2.16844454e-03, -1.53347087e-05,
     -5.72983704e-07, -2.55090145e-09),
    # Pa^0 dTmrt^0 va^2
    (-7.51269505e-01, -4.08350271e-03, -5.21670675e-05, 1.94544667e-06,
     1.14099531e-08),
    # Pa^0 dTmrt^0 va^3
    (1.58137256e-01, -6.57263143e-05, 2.22697524e-07, -4.16117031e-08),
    # Pa^0 dTmrt^0 va^4
    (-1.27762753e-02, 9.66891875e-06, 2.52785852e-09),
    # Pa^0 dTmrt^0 va^5
    (4.56306672e-04, -1.74202546e-07),
    # Pa^0 dTmrt^0 va^6
    (-5.91491269e-06,),
    # Pa^0 dTmrt^1 va^0
    (3.98374029e-01, 1.83945314e-04, -1.73754510e-04, -7.60781159e-07,
     3.77830287e-08, 5.43079673e-10),
    # Pa^0 dTmrt^1 va^1
    (-2.00518269e-02, 8.92859837e-04, 3.45433048e-06, -3.77925774e-07,
     -1.69699377e-09),
    # Pa^0 dTmrt^1 va^2
    (1.69992415e-04, -4.99204314e-05, 2.47417178e-07, 1.07596466e-08),
    # Pa^0 dTmrt^1 va^3
    (8.49242932e-05, 1.35191328e-06, -6.21531254e-09),
    # Pa^0 dTmrt^1 va^4
    (-4.99410301e-06, -1.89489258e-08),
    # Pa^0 dTmrt^1 va^5
    (8.15300114e-08,),
    # Pa^0 dTmrt^2 va^0
    (7.55043090e-04, -5.65095215e-05, -4.52166564e-07, 2.46688878e-08,
     2.42674348e-10),
    # Pa^0 dTmrt^2 va^1
    (1.54547250e-04, 5.24110970e-06, -8.75874982e-08, -1.50743064e-09),
    # Pa^0 dTmrt^2 va^2
    (-1.56236307e-05, -1.33895614e-07, 2.49709824e-09),
    # Pa^0 dTmrt^2 va^3
    (6.51711721e-07, 1.94960053e-09),
    # Pa^0 dTmrt^2 va^4
    (-1.00361113e-08,),
    # Pa^0 dTmrt^3 va^0
    (-1.21206673e-05, -2.18203660e-07, 7.51269482e-09, 9.79063848e-11),
    # Pa^0 dTmrt^3 va^1
    (1.25006734e-06, -1.81584736e-09, -3.52197671e-10),
    # Pa^0 dTmrt^3 va^2
    (-3.36514630e-08, 1.35908359e-10),
    # Pa^0 dTmrt^3 va^3
    (4.17032620e-10,),
    # Pa^0 dTmrt^4 va^0
    (-1.30369025e-09, 4.13908461e-10, 9.22652254e-12),
    # Pa^0 dTmrt^4 va^1
    (-5.08220384e-09, -2.24730961e-11),
    # Pa^0 dTmrt^4 va^2
    (1.17139133e-10,),
    # Pa^0 dTmrt^5 va^0
    (6.62154879e-10, 4.03863260e-13),
    # Pa^0 dTmrt^5 va^1
    (1.95087203e-12,),
    # Pa^0 dTmrt^6 va^0
    (-4.73602469e-12,),
    # Pa^1 dTmrt^0 va^0
    (5.12733497e+00, -3.12788561e-01, -1.96701861e-02, 9.99690870e-04,
     9.51738512e-06, -4.66426341e-07),
    # Pa^1 dTmrt^0 va^1
    (5.48050612e-01, -3.30552823e-03, -1.64119440e-03, -5.16670694e-06,
     9.52692432e-07),
    # Pa^1 dTmrt^0 va^2
    (-4.29223622e-02, 5.00845667e-03, 1.00601257e-06, -1.81748644e-06),
    # Pa^1 dTmrt^0 va^3
    (-1.25813502e-03, -1.79330391e-04, 2.34994441e-06),
    # Pa^1 dTmrt^0 va^4
    (1.29735808e-04, 1.29064870e-06),
    # Pa^1 dTmrt^0 va^5
    (-2.28558686e-06,),
    # Pa^1 dTmrt^1 va^0
    (-3.69476348e-02, 1.62325322e-03, -3.14279680e-05, 2.59835559e-06,
     -4.77136523e-08),
    # Pa^1 dTmrt^1 va^1
    (8.64203390e-03, -6.87405181e-04, -9.13863872e-06, 5.15916806e-07),
    # Pa^1 dTmrt^1 va^2
    (-3.59217476e-05, 3.28696511e-05, -7.10542454e-07),
    # Pa^1 dTmrt^1 va^3
    (-1.24382300e-05, -7.38584400e-09),
    # Pa^1 dTmrt^1 va^4
    (2.20609296e-07,),
    # Pa^1 dTmrt^2 va^0
    (-7.32469180e-04, -1.87381964e-05, 4.80925239e-06, -8.75492040e-08),
    # Pa^1 dTmrt^2 va^1
    (2.77862930e-05, -5.06004592e-06, 1.14325367e-07),
    # Pa^1 dTmrt^2 va^2
    (2.53016723e-06, -1.72857035e-08),
    # Pa^1 dTmrt^2 va^3
    (-3.95079398e-08,),
    # Pa^1 dTmrt^3 va^0
    (-3.59413173e-07, 7.04388046e-07, -1.89309167e-08),
    # Pa^1 dTmrt^3 va^1
    (-4.79768731e-07, 7.96079978e-09),
    # Pa^1 dTmrt^3 va^2
    (1.62897058e-09,),
    # Pa^1 dTmrt^4 va^0
    (3.94367674e-08, -1.18566247e-09),
    # Pa^1 dTmrt^4 va^1
    (3.34678041e-10,),
    # Pa^1 dTmrt^5 va^0
    (-1.15606447e-10,),
    # Pa^2 dTmrt^0 va^0
    (-2.80626406e+00, 5.48712484e-01, -3.99428410e-03, -9.54009191e-04,
     1.93090978e-05),
    # Pa^2 dTmrt^0 va^1
    (-3.08806365e-01, 1.16952364e-02, 4.95271903e-04, -1.90710882e-05),
    # Pa^2 dTmrt^0 va^2
    (2.10787756e-03, -6.98445738e-04, 2.30109073e-05),
    # Pa^2 dTmrt^0 va^3
    (4.17856590e-04, -1.27043871e-05),
    # Pa^2 dTmrt^0 va^4
    (-3.04620472e-06,),
    # Pa^2 dTmrt^1 va^0
    (5.14507424e-02, -4.32510997e-03, 8.99281156e-05, -7.14663943e-07),
    # Pa^2 dTmrt^1 va^1
    (-2.66016305e-04, 2.63789586e-04, -7.01199003e-06),
    # Pa^2 dTmrt^1 va^2
    (-1.06823306e-04, 3.61341136e-06),
    # Pa^2 dTmrt^1 va^3
    (2.29748967e-07,),
    # Pa^2 dTmrt^2 va^0
    (3.04788893e-04, -6.42070836e-05, 1.16257971e-06),
    # Pa^2 dTmrt^2 va^1
    (7.68023384e-06, -5.47446896e-07),
    # Pa^2 dTmrt^2 va^2
    (-3.59937910e-08,),
    # Pa^2 dTmrt^3 va^0
    (-4.36497725e-06, 1.68737969e-07),
    # Pa^2 dTmrt^3 va^1
    (2.67489271e-08,),
    # Pa^2 dTmrt^4 va^0
    (3.23926897e-09,),
    # Pa^3 dTmrt^0 va^0
    (-3.53874123e-02, -2.21201190e-01, 1.55126038e-02, -2.63917279e-04),
    # Pa^3 dTmrt^0 va^1
    (4.53433455e-02, -4.32943862e-03, 1.45389826e-04),
    # Pa^3 dTmrt^0 va^2
    (2.17508610e-04, -6.66724702e-05),
    # Pa^3 dTmrt^0 va^3
    (3.33217140e-05,),
    # Pa^3 dTmrt^1 va^0
    (-2.26921615e-03, 3.80261982e-04, -5.45314314e-09),
    # Pa^3 dTmrt^1 va^1
    (-7.96355448e-04, 2.53458034e-05),
    # Pa^3 dTmrt^1 va^2
    (-6.31223658e-06,),
    # Pa^3 dTmrt^2 va^0
    (3.02122035e-04, -4.77403547e-06),
    # Pa^3 dTmrt^2 va^1
    (1.73825715e-06,),
    # Pa^3 dTmrt^3 va^0
    (-4.09087898e-07,),
    # Pa^4 dTmrt^0 va^0
    (6.14155345e-01, -6.16755931e-02, 1.33374846e-03),
    # Pa^4 dTmrt^0 va^1
    (3.55375387e-03, -5.13027851e-04),
    # Pa^4 dTmrt^0 va^2
    (1.02449757e-04,),
    # Pa^4 dTmrt^1 va^0
    (-1.48526421e-03, -4.11469183e-05),
    # Pa^4 dTmrt^1 va^1
    (-6.80434415e-06,),
    # Pa^4 dTmrt^2 va^0
    (-9.77675906e-06,),
    # Pa^5 dTmrt^0 va^0
    (8.82773108e-02, -3.01859306e-03),
    # Pa^5 dTmrt^0 va^1
    (1.04452989e-03,),
    # Pa^5 dTmrt^1 va^0
    (2.47090539e-04,),
    # Pa^6 dTmrt^0 va^0
    (1.48348065e-03,),
)


def _exponents():
    """(va, dTmrt, Pa) exponents of each row of _ROWS, in table order."""
    out = []
    for l in range(7):
        for k in range(7 - l):
            for j in range(7 - l - k):
                out.append((j, k, l))
    return tuple(out)


_EXP = _exponents()
assert len(_EXP) == len(_ROWS)
assert all(len(r) == 7 - sum(e) for r, e in zip(_ROWS, _EXP))


def saturation_vapour_pressure(ta):
    """Saturation vapour pressure over water, hPa, for air temperature ``ta`` in degC."""
    tk = ta + 273.15
    s = _HARDY[7] * math.log(tk)
    for i in range(7):
        s += _HARDY[i] * tk ** (i - 2)
    return math.exp(s) * 0.01


def in_valid_range(ta, tmrt, va, rh):
    """True when all inputs lie inside the range the approximation was fitted for."""
    try:
        e = saturation_vapour_pressure(ta) * rh / 100.0
    except (ValueError, OverflowError):
        return False
    return (-50.0 <= ta <= 50.0 and -30.0 <= tmrt - ta <= 70.0 and
            WIND_MIN <= va <= WIND_MAX and 0.0 <= rh <= 100.0 and e <= 50.0)


def utci(ta, tmrt, va, rh):
    """UTCI equivalent temperature, degC.

    ta    air temperature, degC
    tmrt  mean radiant temperature, degC
    va    wind speed 10 m above ground, m/s (clamped to 0.5 ... 17 m/s, the fitted range)
    rh    relative humidity, % (clamped to 0 ... 100)

    Outside the fitted temperature ranges the polynomial is evaluated anyway; check
    ``in_valid_range`` first when that matters.
    """
    ta, tmrt, va, rh = float(ta), float(tmrt), float(va), float(rh)
    for x in (ta, tmrt, va, rh):
        if x != x or x in (float("inf"), float("-inf")):
            raise ValueError("UTCI input is not a finite number")
    va = min(WIND_MAX, max(WIND_MIN, va))
    rh = min(100.0, max(0.0, rh))
    pa = saturation_vapour_pressure(ta) * rh / 100.0 / 10.0      # kPa
    d = tmrt - ta
    total = 0.0
    for row, (j, k, l) in zip(_ROWS, _EXP):
        acc = 0.0
        for c in reversed(row):             # Horner scheme in Ta
            acc = acc * ta + c
        total += acc * va ** j * d ** k * pa ** l
    return ta + total
