"""Tests of units and settings (intercom_webtab.weather.units, weather.read_settings) and of the
panel in every unit combination: decisions never depend on the display units."""
import configparser
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from intercom_webtab.weather import Settings, brain as wb, read_settings, units as U  # noqa: E402
from intercom_webtab.weather import sources  # noqa: E402

MINUS = u"−"
DEG = u"°"


class Convert(unittest.TestCase):
    def test_temperature(self):
        self.assertEqual(U.temp(0, "C"), 0)
        self.assertEqual(U.temp(0, "F"), 32)
        self.assertEqual(U.temp(-40, "F"), -40)
        self.assertEqual(U.temp(100, "F"), 212)
        self.assertIsNone(U.temp(None, "F"))
        self.assertEqual(U.fmt_deg(20, "F"), "68" + DEG)
        self.assertEqual(U.fmt_deg(-20, "F"), MINUS + "4" + DEG)
        self.assertEqual(U.fmt_deg(-17.9, "F"), "0" + DEG)          # -0.2 F -> "0", never "-0"
        self.assertEqual(U.fmt_deg(None, "F"), u"—")
        self.assertIsNone(U.fmt_temp(None))
        self.assertEqual(U.fmt_temp(-3.2), MINUS + "3" + DEG)
        self.assertEqual(U.fmt_temp(55, "F"), "131" + DEG)

    def test_wind(self):
        self.assertEqual(U.fmt_wind(10, "m/s"), "10")
        self.assertEqual(U.fmt_wind(10, "km/h"), "36")
        self.assertEqual(U.fmt_wind(10, "mph"), "22")
        self.assertEqual(U.fmt_wind(0.2, "km/h"), "1")
        self.assertEqual(U.fmt_wind(-1, "m/s"), "0")
        self.assertIsNone(U.fmt_wind(None))
        self.assertAlmostEqual(U.wind(1, "mph"), 2.2369, places=3)

    def test_pressure(self):
        mm = 760.0
        self.assertEqual(U.fmt_press(mm, "mmHg"), "760")
        self.assertEqual(U.fmt_press(mm, "hPa"), "1013")
        self.assertEqual(U.fmt_press(mm, "inHg"), "29.92")
        self.assertEqual(U.fmt_press_delta(-4.2, "mmHg"), MINUS + "4")
        self.assertEqual(U.fmt_press_delta(-4.2, "hPa"), MINUS + "6")
        self.assertEqual(U.fmt_press_delta(-4.2, "inHg"), MINUS + "0.17")
        self.assertEqual(U.fmt_press_delta(7.2, "hPa"), "+10")
        self.assertEqual(U.fmt_press_delta(0.1, "hPa"), "0")
        self.assertEqual(U.fmt_press_delta(0.1, "inHg"), "0.00")
        self.assertEqual(U.fmt_press_delta(-7.2, "hPa", sign=False), "10")
        self.assertEqual(U.fmt_press_fast(-4.8, "mmHg"), "4.8")
        self.assertEqual(U.fmt_press_fast(-4.8, "hPa"), "6.4")
        self.assertEqual(U.fmt_press_fast(-12.1, "mmHg"), "12")
        self.assertEqual(U.fmt_press_fast(-3.0, "mmHg"), "3")
        self.assertEqual(U.fmt_press_fast(-4.8, "inHg"), "0.19")

    def test_numbers(self):
        self.assertEqual([U.rnd(x) for x in (12.5, -12.5, 2.5, 0.49, -0.5)], [13, -13, 3, 0, -1])
        self.assertEqual(U.signed(-4), MINUS + "4")
        self.assertEqual(U.signed(0), "0")
        self.assertEqual(U.fmt_dec(-0.04, 1), "0.0")
        self.assertEqual(U.fmt_dec(-1.25, 1), MINUS + "1.2")
        self.assertEqual([U.fmt_rad(x) for x in (0.123, 0.1, 9.994, 12.34)], ["0.12", "0.10", "9.99", "12.3"])
        for junk in (None, True, "x", float("nan"), float("inf"), [], {}):
            self.assertIsNone(U.num(junk))
        self.assertEqual(U.num("1.5"), 1.5)

    def test_visibility(self):
        self.assertEqual(U.fmt_vis(123, "m/s"), ("120", "m"))
        self.assertEqual(U.fmt_vis(480, "km/h"), ("500", "m"))
        self.assertEqual(U.fmt_vis(120, "mph"), ("400", "ft"))
        self.assertEqual(U.fmt_vis(None, "mph"), (None, "ft"))

    def test_unit_names(self):
        for v, want in (("c", "C"), ("degF", "F"), ("Fahrenheit", "F"), (u"°C", "C"), ("kelvin", "C")):
            self.assertEqual(U.temp_unit(v), want, v)
        for v, want in (("ms", "m/s"), ("KMH", "km/h"), ("kph", "km/h"), ("MPH", "mph"), ("knots", "m/s")):
            self.assertEqual(U.wind_unit(v), want, v)
        for v, want in (("hpa", "hPa"), ("mbar", "hPa"), ("mmHg", "mmHg"), ("torr", "mmHg"), ("inHg", "inHg"),
                        (None, "hPa")):
            self.assertEqual(U.press_unit(v), want, v)


INI = """
[location]
latitude = 51.4779
longitude = -0.0015
timezone = Europe/London
[units]
temperature = F        ; C | F
wind = km/h
pressure = inHg
[weather]
photos = off
attribution = on
[radiation]
provider = none
radius_km = 50
[ui]
widgets = wind, air, humidity, pressure, sun   ; six slots
[panel:gate]
label = Gate
[panel:door]
label = Entrance
"""


class ReadSettings(unittest.TestCase):
    def test_defaults(self):
        s = read_settings(None)
        self.assertEqual((s.lat, s.lon, s.tz_name), (51.4779, -0.0015, "Europe/London"))
        self.assertEqual((s.temp, s.wind, s.press), ("C", "m/s", "hPa"))
        self.assertEqual(s.widgets, ("wind", "air", "uv", "pressure", "radiation", "sun"))
        self.assertTrue(s.attribution and s.photos)
        self.assertIsNotNone(s.tzinfo())

    def test_ini(self):
        cp = configparser.ConfigParser()
        cp.read_string(INI)
        s = read_settings(cp)
        self.assertEqual((s.temp, s.wind, s.press), ("F", "km/h", "inHg"))
        self.assertEqual(s.widgets, ("wind", "air", "humidity", "pressure", "sun", "uv"))
        self.assertFalse(s.photos)
        self.assertEqual((s.rad_provider, s.rad_radius_km), ("none", 50.0))
        self.assertEqual(s.panels, (("gate", "Gate"), ("door", "Entrance")))

    def test_object_shapes(self):
        class Obj(object):
            pass
        cfg = Obj()
        cfg.location = Obj()
        cfg.location.latitude, cfg.location.longitude, cfg.location.timezone = 35.68, 139.69, "Asia/Tokyo"
        cfg.units = {"temperature": "F", "wind": "mph", "pressure": "mmHg"}
        cfg.ui = Obj()
        cfg.ui.widgets = ["sun", "wind", "pres", "rad", "bogus", "sun"]
        p1, p2 = Obj(), Obj()
        p1.id, p1.label, p2.id, p2.label = "gate", "Gate", "door", "Entrance"
        cfg.panels = [p1, p2]
        s = read_settings(cfg)
        self.assertEqual((s.lat, s.lon, s.tz_name), (35.68, 139.69, "Asia/Tokyo"))
        self.assertEqual((s.temp, s.wind, s.press), ("F", "mph", "mmHg"))
        self.assertEqual(s.widgets, ("sun", "wind", "pressure", "radiation", "air", "uv"))
        self.assertEqual(s.panels, (("gate", "Gate"), ("door", "Entrance")))
        flat = {"latitude": "10", "longitude": "20", "timezone": "UTC", "panels": {"gate": {"label": "Gate"}}}
        s = read_settings(flat)
        self.assertEqual((s.lat, s.lon, s.tz_name, s.panels), (10.0, 20.0, "UTC", (("gate", "Gate"),)))

    def test_junk_is_safe(self):
        for junk in ("x", 5, [], {"location": {"latitude": 99, "longitude": 0}}, {"units": None}):
            s = read_settings(junk)
            self.assertEqual((s.lat, s.lon), (51.4779, -0.0015))
        self.assertIsNone(Settings(tz_name="No/Such_Zone").tzinfo())

    def test_source_urls_use_location(self):
        s = Settings(lat=35.6812, lon=139.7671, tz_name="Asia/Tokyo")
        u = sources.forecast_url(s.lat, s.lon, s.tz_name)
        self.assertIn("latitude=35.6812&longitude=139.7671", u)
        self.assertIn("timezone=Asia%2FTokyo", u)
        self.assertIn("timeformat=unixtime", u)
        self.assertIn("wind_speed_unit=ms", u)
        for f in sources.FORECAST_CURRENT + sources.FORECAST_HOURLY + sources.FORECAST_DAILY:
            self.assertIn(f, u)
        a = sources.air_url(s.lat, s.lon, s.tz_name)
        self.assertIn("timezone=Asia%2FTokyo", a)
        for f in sources.AIR_CURRENT:
            self.assertIn(f, a)

    def test_fetch_with_fake_opener(self):
        import io
        import json

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener(object):
            def __init__(self, body):
                self.body, self.urls = body, []

            def open(self, rq, timeout=None):
                self.urls.append(rq.full_url)
                return Resp(self.body)

        op = Opener(json.dumps({"current": {"time": 1}, "hourly": {"time": []}}).encode())
        d = sources.fetch_forecast(Settings(tz_name="UTC"), opener=op)
        self.assertEqual(d["current"]["time"], 1)
        self.assertIn("timezone=UTC", op.urls[0])
        with self.assertRaises(ValueError):
            sources.fetch_air(None, opener=Opener(b'{"error": true, "reason": "bad"}'))
        with self.assertRaises(ValueError):
            sources.fetch_forecast(None, opener=Opener(b"not json"))
        with self.assertRaises(ValueError):
            sources.fetch_forecast(None, opener=Opener(b'{"current": {}}'))


class PanelUnits(unittest.TestCase):
    """The same weather in every unit combination: identical levels, scenes and keys;
    only the numbers on the screen change."""

    def test_levels_independent_of_units(self):
        import test_brain as TB
        combos = [Settings(panels=TB.PANELS, temp=t, wind=w, press=p)
                  for t in U.TEMP_UNITS for w in U.WIND_UNITS for p in U.PRESS_UNITS]
        for raw, src, now, _ in TB.grid(120, seed=23):
            base = None
            for cfg in combos:
                p = wb.build_panel(raw, src, {}, now, cfg)
                TB.check_panel(self, p, cfg=cfg)
                sig = (p["now"]["scene"], p["now"]["L"], p["now"]["utci"], p["now"]["t"],
                       [(w["k"], w["L"]) for w in p["widgets"]], [s["key"] for s in p["status"]], p["rain"])
                if base is None:
                    base = sig
                self.assertEqual(sig, base)

    def test_display_values(self):
        import test_brain as TB
        now = TB.T14
        raw, src = TB.make(now, fckw=dict(t=20, rh=50, wind=5, gust=10, p=760.0))
        got = {}
        for t, w, pr in (("C", "m/s", "hPa"), ("F", "mph", "inHg"), ("C", "km/h", "mmHg")):
            cfg = Settings(panels=TB.PANELS, temp=t, wind=w, press=pr)
            p = wb.build_panel(raw, src, {}, now, cfg)
            got[t + w + pr] = (p["now"]["tTxt"], TB.wv(p, "wind")[1:], TB.wv(p, "pressure")[1:3])
        self.assertEqual(got["Cm/shPa"], ("20" + DEG, ("5", "m/s", "gusts 10, west"), ("1013", "hPa")))
        self.assertEqual(got["FmphinHg"],
                         ("68" + DEG, ("11", "mph", "gusts 22, west"), ("29.92", "inHg")))
        self.assertEqual(got["Ckm/hmmHg"], ("20" + DEG, ("18", "km/h", "gusts 36, west"), ("760", "mmHg")))
        # the big "feels like" figure follows the unit too, the numeric field stays in degC
        c = wb.build_panel(raw, src, {}, now, Settings(panels=TB.PANELS))["now"]
        f = wb.build_panel(raw, src, {}, now, Settings(panels=TB.PANELS, temp="F"))["now"]
        self.assertEqual(c["utci"], f["utci"])
        self.assertAlmostEqual(int(f["utciTxt"].rstrip(DEG).replace(MINUS, "-")),
                               c["utci"] * 9 / 5.0 + 32, delta=1.0)


if __name__ == "__main__":
    unittest.main()
