"""Display units and number formatting for the weather panel.

The decision logic always works in fixed internal base units (degC, m/s, mmHg for pressure
thresholds); this module only converts and formats values for the screen:

* temperature: ``C`` or ``F``
* wind speed: ``m/s``, ``km/h`` or ``mph``
* pressure: ``hPa``, ``mmHg`` or ``inHg``

Formatting rules shared by every text on the panel: integers are rounded half away from
zero (12.5 -> 13, -12.5 -> -13), negative numbers use the real minus sign U+2212, large
temperature digits never carry a "+" and never show "-0".
"""
import math

MINUS = u"−"
DEG = u"°"

TEMP_UNITS = ("C", "F")
WIND_UNITS = ("m/s", "km/h", "mph")
PRESS_UNITS = ("hPa", "mmHg", "inHg")

MM_PER_HPA = 0.750062          # 1 hPa in mmHg
MM_PER_INHG = 25.4             # 1 inHg in mmHg

_TEMP_ALIASES = {"c": "C", "degc": "C", "celsius": "C", "f": "F", "degf": "F",
                 "fahrenheit": "F"}
_WIND_ALIASES = {"m/s": "m/s", "ms": "m/s", "mps": "m/s", "km/h": "km/h", "kmh": "km/h",
                 "kph": "km/h", "km/hr": "km/h", "mph": "mph", "mi/h": "mph"}
_PRESS_ALIASES = {"hpa": "hPa", "mbar": "hPa", "mb": "hPa", "mmhg": "mmHg", "mm": "mmHg",
                  "torr": "mmHg", "inhg": "inHg", "in": "inHg", "inches": "inHg"}


def _norm(value, aliases, default):
    if not isinstance(value, str):
        return default
    key = value.strip().lower().replace(DEG, "").replace(" ", "")
    return aliases.get(key, default)


def temp_unit(value, default="C"):
    """'C' or 'F' from a config value such as 'c', 'degF' or 'Fahrenheit'."""
    return _norm(value, _TEMP_ALIASES, default)


def wind_unit(value, default="m/s"):
    return _norm(value, _WIND_ALIASES, default)


def press_unit(value, default="hPa"):
    return _norm(value, _PRESS_ALIASES, default)


# ---------------------------------------------------------------- numbers
def num(x):
    """Float or None (bool, NaN, infinities and junk -> None)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except Exception:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def rnd(v):
    """Round half away from zero: 12.5 -> 13, -12.5 -> -13 (Python's round() is banker's)."""
    n = int(math.floor(abs(v) + 0.5))
    return n if v >= 0 else -n


def signed(n):
    """Integer change with an explicit sign: '+7', U+2212 '4', '0'."""
    return ("+%d" % n) if n > 0 else (MINUS + "%d" % -n) if n < 0 else "0"


def neg(s):
    """Replace a leading ASCII hyphen with the minus sign."""
    return MINUS + s[1:] if s.startswith("-") else s


def fmt_dec(v, nd=1):
    """Fixed decimals with a decimal point; never '-0.0'."""
    v = num(v)
    if v is None:
        return None
    s = ("%." + str(nd) + "f") % v
    if s.startswith("-") and float(s) == 0:
        s = s[1:]
    return neg(s)


# ---------------------------------------------------------------- temperature
def temp(v_c, unit="C"):
    """degC -> display unit (float)."""
    v = num(v_c)
    if v is None:
        return None
    return v * 9.0 / 5.0 + 32.0 if unit == "F" else v


def fmt_deg(v_c, unit="C"):
    """Large panel degrees: '12°', U+2212 '9°'; no '+', no '-0°'; missing -> em dash."""
    v = temp(v_c, unit)
    if v is None:
        return u"—"
    n = rnd(v)
    return ("%d" % n + DEG) if n >= 0 else (MINUS + "%d" % -n + DEG)


def fmt_temp(v_c, unit="C"):
    """Temperature inside a sentence: same as fmt_deg, but None when missing."""
    if num(v_c) is None:
        return None
    return fmt_deg(v_c, unit)


# ---------------------------------------------------------------- wind
def wind(v_ms, unit="m/s"):
    v = num(v_ms)
    if v is None:
        return None
    if unit == "km/h":
        return v * 3.6
    if unit == "mph":
        return v / 0.44704
    return v


def fmt_wind(v_ms, unit="m/s"):
    """Wind speed as an integer string in the display unit, or None."""
    v = wind(v_ms, unit)
    return None if v is None else "%d" % rnd(max(0.0, v))


# ---------------------------------------------------------------- pressure
def press(v_mm, unit="hPa"):
    """mmHg -> display unit (float)."""
    v = num(v_mm)
    if v is None:
        return None
    if unit == "hPa":
        return v / MM_PER_HPA
    if unit == "inHg":
        return v / MM_PER_INHG
    return v


def fmt_press(v_mm, unit="hPa"):
    """Absolute pressure: '1013' hPa, '760' mmHg, '29.92' inHg."""
    v = press(v_mm, unit)
    if v is None:
        return None
    return fmt_dec(v, 2) if unit == "inHg" else "%d" % rnd(v)


def fmt_press_delta(d_mm, unit="hPa", sign=True):
    """Pressure change: '+7' / U+2212 '4' (hPa, mmHg), '+0.21' (inHg); sign=False -> magnitude."""
    v = press(d_mm, unit)
    if v is None:
        return None
    if unit == "inHg":
        s = fmt_dec(abs(v), 2)
        if not sign or s == "0.00":
            return s
        return ("+" if v > 0 else MINUS) + s
    n = rnd(v)
    return signed(n) if sign else "%d" % abs(n)


def fmt_press_fast(d_mm, unit="hPa"):
    """Magnitude of a 3-hour change: one decimal below 10 ('4.8'), integer above; inHg 2 dp."""
    v = press(d_mm, unit)
    if v is None:
        return None
    v = abs(v)
    if unit == "inHg":
        return fmt_dec(v, 2)
    v = round(v, 1)
    if v >= 9.95 or v == int(v):
        return "%d" % rnd(v)
    return fmt_dec(v, 1)


def press_resolution_mm(unit="hPa"):
    """Smallest change (in mmHg) that shows as a non-zero number in the display unit."""
    return {"hPa": 0.5 * MM_PER_HPA, "inHg": 0.005 * MM_PER_INHG}.get(unit, 0.5)


# ---------------------------------------------------------------- distance (visibility)
def fmt_vis(v_m, wind_unit_="m/s"):
    """Visibility for fog lines: metres (rounded to 10 / 50 m by size) or, when wind is shown
    in mph, feet (rounded to 50 ft). -> (number string, unit) or (None, unit)."""
    v = num(v_m)
    imperial = wind_unit_ == "mph"
    unit = "ft" if imperial else "m"
    if v is None:
        return None, unit
    if imperial:
        return "%d" % (rnd(v / 0.3048 / 50.0) * 50), unit
    step = 10.0 if v < 200 else 50.0
    return "%d" % (rnd(v / step) * step), unit


def fmt_rad(v):
    """Dose rate: two decimals below 10 uSv/h, one above."""
    v = num(v)
    if v is None:
        return None
    return fmt_dec(v, 2 if v < 10 else 1)
