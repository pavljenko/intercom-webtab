"""Configuration loading and validation."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intercom_webtab import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = open(os.path.join(ROOT, "config", "webtab.example.ini"), encoding="utf-8").read()
SECRETS = open(os.path.join(ROOT, "config", "secrets.example.ini"), encoding="utf-8").read()
TOKEN = "test-token-0123456789"

MINIMAL = """
[panel:gate]
number = 201
"""
MIN_SECRETS = "[asterisk]\nami_user = webtab\nami_secret = s3cret\n"


def parse(text=MINIMAL, secrets=MIN_SECRETS, token=TOKEN):
    return config.parse(text, path="/etc/iwt/webtab.ini", secrets_text=secrets, token=token)


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


class ExampleConfig(unittest.TestCase):
    def test_example_is_valid(self):
        cfg = parse(EXAMPLE, SECRETS)
        self.assertEqual(cfg.warnings, [])
        self.assertEqual(cfg.panel_ids, ["gate", "door"])
        self.assertEqual(cfg.title, "Intercom WebTab")
        self.assertEqual(cfg.http.port, 8080)
        self.assertEqual(cfg["location"]["timezone"], "Europe/London")
        self.assertAlmostEqual(cfg.latitude, 51.4779)
        self.assertEqual(cfg.units.wind, "m/s")
        self.assertEqual(cfg.ui.widgets, ("wind", "air", "uv", "pressure", "radiation", "sun"))
        self.assertEqual(cfg.asterisk.incoming_contexts, ("intercom-incoming",))
        self.assertEqual(cfg.asterisk.ami_user, "webtab")
        gate = cfg.panel("gate")
        self.assertEqual(gate.number, "201")
        self.assertEqual(gate.open_repeat, (0.7, 1.4, 2.1, 3.0))
        self.assertEqual(gate.hold, 12)
        self.assertEqual(gate.dtmf, "1")
        self.assertTrue(gate.rtsp_url.startswith("rtsp://viewer:"))
        self.assertIsNone(cfg.panel("nope"))

    def test_page_config_has_no_secrets(self):
        cfg = parse(EXAMPLE, SECRETS)
        pc = cfg.page_config()
        self.assertEqual(pc, {"title": "Intercom WebTab",
                              "panels": [{"id": "gate", "label": "Gate", "icon": "gate"},
                                         {"id": "door", "label": "Entrance", "icon": "house"}],
                              "ringtone": "mid", "attribution": True,
                              "widgets": ["wind", "air", "uv", "pressure", "radiation", "sun"]})

    def test_defaults(self):
        cfg = parse()
        self.assertEqual(cfg.sip.uas_port, 5080)
        self.assertEqual(cfg.asterisk.trunk, "provider")
        self.assertEqual(cfg.asterisk.own_callerid, "100")
        self.assertEqual(cfg.panel("gate").label, "Gate")
        self.assertTrue(cfg.weather.enabled)
        self.assertEqual(cfg.radiation.provider, "auto")
        self.assertEqual(cfg.panel("gate").rtsp_url, "")

    def test_inline_comments_but_literal_secrets(self):
        cfg = parse(MINIMAL + "dtmf = #1   ; hash then one\n",
                    "[asterisk]\nami_user = webtab\nami_secret = pa;ss #word\n")
        self.assertEqual(cfg.panel("gate").dtmf, "#1")
        self.assertEqual(cfg.asterisk.ami_secret, "pa;ss #word")

    def test_unknown_keys_warn(self):
        cfg = parse("[station]\ntitel = x\n[bogus]\na = 1\n" + MINIMAL)
        self.assertTrue(any("titel" in w for w in cfg.warnings))
        self.assertTrue(any("[bogus]" in w for w in cfg.warnings))

    def test_relative_paths(self):
        cfg = parse("[paths]\ntoken_file = token\nstate_dir = state\n" + MINIMAL)
        self.assertEqual(cfg.paths.token_file, "/etc/iwt/token")
        self.assertEqual(cfg.paths.state_dir, "/etc/iwt/state")
        self.assertTrue(cfg.paths.static_dir.endswith(os.path.join("intercom_webtab", "static")))


class Invalid(unittest.TestCase):
    def bad(self, text=MINIMAL, secrets=MIN_SECRETS, token=TOKEN, needle=""):
        with self.assertRaises(config.ConfigError) as cm:
            parse(text, secrets, token)
        if needle:
            self.assertIn(needle, str(cm.exception))
        return str(cm.exception)

    def test_no_panels(self):
        self.bad("[station]\ntitle = x\n", needle="no door panels")

    def test_three_panels(self):
        self.bad(MINIMAL + "[panel:b]\nnumber = 202\n[panel:c]\nnumber = 203\n",
                 needle="at most 2")

    def test_duplicate_numbers(self):
        self.bad(MINIMAL + "[panel:b]\nnumber = +201\n", needle="already used")

    def test_panel_number_equals_own(self):
        self.bad("[asterisk]\nown_callerid = 201\n" + MINIMAL, needle="own_callerid")

    def test_bad_panel_id(self):
        self.bad("[panel:Gate One]\nnumber = 201\n", needle="panel id")

    def test_missing_number(self):
        self.bad("[panel:gate]\nlabel = Gate\n", needle="[panel:gate] number")

    def test_dtmf(self):
        self.bad(MINIMAL + "dtmf = 1x\n", needle="dtmf")

    def test_open_repeat_order(self):
        self.bad(MINIMAL + "open_repeat = 1.0, 0.5\n", needle="open_repeat")
        self.bad(MINIMAL + "open_repeat = soon\n", needle="open_repeat")

    def test_hold_range(self):
        self.bad(MINIMAL + "hold = 1\n", needle="[panel:gate] hold")

    def test_widgets(self):
        self.bad("[ui]\nwidgets = wind, air\n" + MINIMAL, needle="exactly 6")
        self.bad("[ui]\nwidgets = wind, air, uv, pressure, radiation, rain\n" + MINIMAL,
                 needle="unknown widget")
        self.bad("[ui]\nwidgets = wind, wind, uv, pressure, radiation, sun\n" + MINIMAL,
                 needle="only once")

    def test_units(self):
        msg = self.bad("[units]\ntemperature = K\n" + MINIMAL)
        self.assertIn("[units] temperature", msg)
        self.bad("[units]\nwind = knots\n" + MINIMAL, needle="[units] wind")
        self.bad("[units]\npressure = bar\n" + MINIMAL, needle="[units] pressure")

    def test_location(self):
        self.bad("[location]\nlatitude = 91\n" + MINIMAL, needle="latitude")
        self.bad("[location]\nlongitude = east\n" + MINIMAL, needle="longitude")
        self.bad("[location]\ntimezone = Mars/Base\n" + MINIMAL, needle="time zone")

    def test_ports(self):
        self.bad("[sip]\nrtp_audio_port = 41001\n" + MINIMAL, needle="even")
        self.bad("[sip]\nrtp_video_port = 41000\n" + MINIMAL, needle="already used")
        self.bad("[http]\nport = 70000\n" + MINIMAL, needle="[http] port")
        self.bad("[http]\nport = 5080\n" + MINIMAL, needle="uas_port")

    def test_contexts(self):
        self.bad("[asterisk]\nincoming_contexts = intercom-view\n" + MINIMAL,
                 needle="incoming_contexts")
        self.bad("[asterisk]\ndemo_context = intercom-view\n" + MINIMAL, needle="demo_context")

    def test_no_trunk_needs_endpoint(self):
        self.bad("[asterisk]\ntrunk =\n" + MINIMAL, needle="endpoint")
        cfg = parse("[asterisk]\ntrunk =\n" + MINIMAL + "endpoint = gate-station\n")
        self.assertEqual(cfg.panel("gate").endpoint, "gate-station")

    def test_secrets(self):
        self.bad(secrets="", needle="[asterisk]")
        self.bad(secrets="[asterisk]\nami_user = x\n", needle="ami_secret")
        self.bad(secrets=MIN_SECRETS + "[panel:gate]\nrtsp_url = http://cam/\n", needle="rtsp")

    def test_token(self):
        self.bad(token="short", needle="at least 16")
        self.bad(token="has space in it 0123456789", needle="at least 16")


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_load_files(self):
        p = self.write("webtab.ini", "[paths]\ntoken_file = token\nsecrets_file = secrets.ini\n"
                       + MINIMAL)
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn("secrets file not found", str(cm.exception))
        self.write("secrets.ini", MIN_SECRETS)
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p)
        self.assertIn("token file not found", str(cm.exception))
        self.write("token", TOKEN + "\n")
        cfg = config.load(p)
        self.assertEqual(cfg.token, TOKEN)
        self.assertEqual(cfg.path, p)

    def test_missing_config(self):
        with self.assertRaises(config.ConfigError) as cm:
            config.load(os.path.join(self.dir, "nope.ini"))
        self.assertIn("config file not found", str(cm.exception))

    def test_syntax_error(self):
        p = self.write("webtab.ini", "this is not ini\n")
        with self.assertRaises(config.ConfigError):
            config.load(p)


class Helpers(unittest.TestCase):
    def test_rtsp_url(self):
        from urllib.parse import urlsplit
        u = urlsplit(config.build_rtsp_url("rtsp://camera-gate.local:554/s1", "us er", "p@ss:w"))
        self.assertEqual((u.scheme, u.hostname, u.port, u.path), ("rtsp", "camera-gate.local", 554, "/s1"))
        self.assertEqual((u.username, u.password), ("us%20er", "p%40ss%3Aw"))   # URL-encoded
        self.assertEqual(config.build_rtsp_url("rtsp://camera-gate.local/s1"),
                         "rtsp://camera-gate.local/s1")
        u = urlsplit(config.build_rtsp_url("rtsp://camera-gate.local/s1", "viewer"))
        self.assertEqual((u.username, u.password, u.hostname), ("viewer", None, "camera-gate.local"))

    def test_mask(self):
        url = config.build_rtsp_url("rtsp://camera-gate.local/s1", "viewer", "pw")
        self.assertEqual(config.mask_url("error opening %s" % url),
                         "error opening rtsp://***@camera-gate.local/s1")

    def test_same_number(self):
        self.assertTrue(config.same_number("+201", "201"))
        self.assertFalse(config.same_number("2010", "201"))
        self.assertFalse(config.same_number("", ""))

    def test_sections_are_read_only(self):
        cfg = parse()
        with self.assertRaises(AttributeError):
            cfg.http.port = 1
        self.assertEqual(cfg.http.get("port"), 8080)
        self.assertEqual(cfg.panel("gate").public(), {"id": "gate", "label": "Gate", "icon": "door"})
        self.assertNotIn("201", repr(cfg.panel("gate")))


if __name__ == "__main__":
    unittest.main()
