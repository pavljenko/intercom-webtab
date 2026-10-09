"""Offline tests for intercom_webtab.radiation.

Provider responses come from tests/fx/rad_*.json: trimmed real answers recorded on
2026-10-09 for neutral places (Berlin, Helsinki, Boston/Denver, Toronto) and a synthetic
Safecast device list modelled on the real schema. Network access is replaced by a fake
``base.http_get``; only the HTTP helper tests talk to a local server on 127.0.0.1.
"""
import calendar
import gzip
import http.server
import json
import os
import re
import threading
import time
import unittest
import urllib.parse
from unittest import mock

from intercom_webtab import radiation
from intercom_webtab.radiation import (base, bfs_odl, epa_radnet, hc_fps, safecast,
                                       stuk_fmi)

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fx")
NOW = calendar.timegm((2026, 10, 9, 18, 30, 0))          # fixtures were recorded ~17:45-18:10Z

BERLIN = (52.52, 13.405)
HELSINKI = (60.17, 24.94)
BOSTON = (42.358, -71.06)
DENVER = (39.74, -104.99)
TORONTO = (43.65, -79.38)
GREENWICH = (51.4779, -0.0015)
STATION_KEYS = {"name", "lat", "lon", "km", "usvh", "ts", "kind"}


def fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return json.load(f)


def fx_bytes(name):
    return json.dumps(fx(name)).encode("utf-8")


class FakeNet(object):
    """Routes provider URLs to fixture bodies; records the URLs asked for."""

    def __init__(self, **over):
        self.urls = []
        self.over = over
        self.radnet = fx("rad_radnet.json")["files"]

    def __call__(self, url, timeout, max_bytes=base.MAX_BYTES):
        self.urls.append(url)
        host = urllib.parse.urlsplit(url).hostname
        if host in self.over:
            v = self.over[host]
            if isinstance(v, Exception):
                raise v
            return v(url) if callable(v) else v
        if host == "www.imis.bfs.de":
            return fx_bytes("rad_bfs_berlin.json")
        if host == "opendata.fmi.fi":
            return fx("rad_stuk_helsinki.json")["text"].encode("utf-8")
        if host == "maps-cartes.services.geo.ca":
            return fx_bytes("rad_fps_toronto.json")
        if host == "tt.safecast.org":
            return fx_bytes("rad_safecast_denver.json")
        if host == "radnet.epa.gov":
            m = re.search(r"/fixed/([A-Z]{2})/(.+)$", url)
            key = "%s/%s" % (m.group(1), urllib.parse.unquote(m.group(2)))
            if key in self.radnet:
                return self.radnet[key].encode("utf-8")
            raise base.RadiationError("radnet.epa.gov: HTTP 404")
        raise base.RadiationError("%s: unexpected host in test" % host)


def run(lat_lon, radius=100, provider="auto", now=NOW, net=None):
    net = net or FakeNet()
    with mock.patch.object(base, "http_get", net):
        return radiation.fetch(lat_lon[0], lat_lon[1], radius, provider=provider, now=now)


# --------------------------------------------------------------------------- helpers

class HelperTest(unittest.TestCase):
    def test_haversine(self):
        km = base.haversine_km(51.5074, -0.1278, 48.8566, 2.3522)    # London - Paris
        self.assertAlmostEqual(km, 343.5, delta=1.0)
        self.assertEqual(base.haversine_km(10, 20, 10, 20), 0.0)

    def test_parse_iso(self):
        t = calendar.timegm((2026, 10, 9, 17, 0, 0))
        self.assertEqual(base.parse_iso("2026-10-09T17:00:00Z"), t)
        self.assertEqual(base.parse_iso("2026-10-09T17:00:00+0000"), t)
        self.assertEqual(base.parse_iso("2026-10-09T19:00:00+02:00"), t)
        self.assertEqual(base.parse_iso("2026-10-09T17:00:00"), t)
        for bad in ("2044-00-00T09:42:35Z", "", None, 5, "yesterday"):
            self.assertIsNone(base.parse_iso(bad))

    def test_num(self):
        self.assertEqual(base.num("0.5"), 0.5)
        for bad in (None, True, "x", float("nan"), float("inf"), [1]):
            self.assertIsNone(base.num(bad))

    def test_bbox_contains_radius(self):
        w, s, e, n = base.bbox(GREENWICH[0], GREENWICH[1], 100)
        self.assertLess(w, GREENWICH[1])
        self.assertGreater(e, GREENWICH[1])
        self.assertGreaterEqual(base.haversine_km(GREENWICH[0], GREENWICH[1], n, GREENWICH[1]),
                                99)
        self.assertGreaterEqual(base.haversine_km(GREENWICH[0], GREENWICH[1], GREENWICH[0], e),
                                99)

    def test_rings(self):
        square = base.parse_rings(("0,0 1,0 1,1 0,1",))
        self.assertEqual(base.distance_to_rings_km(0.5, 0.5, square), 0.0)
        d = base.distance_to_rings_km(0.5, 2.0, square)               # 1 degree east
        self.assertAlmostEqual(d, 111.3, delta=1.5)

    def test_station_record(self):
        s = base.station("X", 1.123456, 2.654321, 0.123456, 1791567000.7, base.KIND_DOSE,
                         12.3456)
        self.assertEqual(set(s), STATION_KEYS)
        self.assertEqual((s["lat"], s["lon"], s["km"], s["usvh"], s["ts"]),
                         (1.1235, 2.6543, 12.3, 0.1235, 1791567000))


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body, headers, code = b'{"ok": 1}', {}, 200
        if self.path == "/gz":
            body, headers = gzip.compress(b'{"gz": true}'), {"Content-Encoding": "gzip"}
        elif self.path == "/big":
            body = b"x" * 5000
        elif self.path == "/bomb":
            body, headers = gzip.compress(b"0" * 200000), {"Content-Encoding": "gzip"}
        elif self.path == "/missing":
            body, code = b"no", 404
        elif self.path == "/ua":
            body = json.dumps({"ua": self.headers.get("User-Agent")}).encode()
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_plain_and_gzip(self):
        self.assertEqual(base.get_json(self.base + "/", 5, "t"), {"ok": 1})
        self.assertEqual(base.get_json(self.base + "/gz", 5, "t"), {"gz": True})

    def test_user_agent(self):
        self.assertEqual(base.get_json(self.base + "/ua", 5, "t")["ua"], base.USER_AGENT)

    def test_limits_and_errors(self):
        with self.assertRaises(base.RadiationError):
            base.http_get(self.base + "/big", 5, max_bytes=1000)
        with self.assertRaises(base.RadiationError):
            base.http_get(self.base + "/bomb", 5, max_bytes=10000)
        with self.assertRaisesRegex(base.RadiationError, "HTTP 404"):
            base.http_get(self.base + "/missing", 5)
        with self.assertRaises(base.RadiationError):
            base.get_json(self.base + "/big", 5, "t")                 # not JSON

    def test_deadline(self):
        d = base.Deadline(0.1)
        time.sleep(0.15)
        with self.assertRaisesRegex(base.RadiationError, "timed out"):
            base.http_get(self.base + "/", d)


# --------------------------------------------------------------------------- choice

class CoverageTest(unittest.TestCase):
    def first(self, lat, lon):
        return radiation.candidates(lat, lon)[0]

    def test_registry(self):
        idre = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")            # config.py rule
        self.assertEqual(radiation.FALLBACK, "safecast")
        for pid, mod in radiation.PROVIDERS.items():
            self.assertEqual(pid, mod.ID)
            self.assertTrue(idre.match(pid))
            for attr in ("NAME", "SOURCE_URL", "LICENSE", "LICENSE_URL", "ATTRIBUTION"):
                self.assertTrue(getattr(mod, attr).strip(), attr)
            self.assertTrue(mod.SOURCE_URL.startswith("https://"))
            self.assertTrue(callable(mod.fetch))
            self.assertIn(mod.KIND, base.KINDS)
        self.assertIsNone(safecast.COVERAGE)

    def test_national_first(self):
        cases = [(BERLIN, "bfs_odl"), (HELSINKI, "stuk_fmi"), (BOSTON, "epa_radnet"),
                 (DENVER, "epa_radnet"), (TORONTO, "hc_fps"),
                 ((48.58, 7.75), "bfs_odl"),           # Strasbourg: across the Rhine
                 ((21.31, -157.86), "epa_radnet"),     # Honolulu
                 ((61.22, -149.90), "epa_radnet"),     # Anchorage
                 ((18.47, -66.11), "epa_radnet"),      # San Juan
                 ((60.72, -135.05), "hc_fps"),         # Whitehorse
                 ((63.75, -68.52), "hc_fps"),          # Iqaluit
                 ((45.50, -73.57), "hc_fps"),          # Montreal
                 ((49.28, -123.12), "hc_fps"),         # Vancouver
                 ((47.61, -122.33), "epa_radnet"),     # Seattle
                 ((42.33, -83.05), "epa_radnet"),      # Detroit
                 ((60.10, 19.94), "stuk_fmi")]         # Mariehamn
        for (lat, lon), want in cases:
            self.assertEqual(self.first(lat, lon), want, (lat, lon))
            self.assertEqual(radiation.pick(lat, lon), want)
            self.assertEqual(radiation.candidates(lat, lon)[-1], "safecast")

    def test_fallback_only(self):
        for lat, lon in (GREENWICH, (35.68, 139.69), (-33.87, 151.21), (48.86, 2.35)):
            self.assertEqual(radiation.candidates(lat, lon), ["safecast"], (lat, lon))

    def test_border_lists_both(self):
        self.assertEqual(radiation.candidates(*TORONTO), ["hc_fps", "epa_radnet", "safecast"])

    def test_bad_point(self):
        with self.assertRaises(ValueError):
            radiation.candidates(95, 0)
        with self.assertRaises(ValueError):
            radiation.pick("x", 0)


# --------------------------------------------------------------------------- providers

class ProviderTest(unittest.TestCase):
    def check(self, res, provider, kind):
        self.assertEqual(res["provider"], provider)
        mod = radiation.PROVIDERS[provider]
        self.assertEqual((res["source"], res["source_url"], res["license"]),
                         (mod.NAME, mod.SOURCE_URL, mod.LICENSE))
        st = res["stations"]
        self.assertTrue(st)
        self.assertLessEqual(len(st), radiation.MAX_STATIONS)
        self.assertEqual([s["km"] for s in st], sorted(s["km"] for s in st))
        for s in st:
            self.assertEqual(set(s), STATION_KEYS)
            self.assertIsInstance(s["ts"], int)
            self.assertIsInstance(s["usvh"], float)
            self.assertEqual(s["kind"], kind)
            self.assertTrue(0 < s["usvh"] < 1, s)                   # background levels
            self.assertTrue(NOW - base.STALE_S <= s["ts"] <= NOW + base.FUTURE_S)
        json.dumps(res)                                              # goes to the state file
        return st

    def test_bfs_berlin(self):
        net = FakeNet()
        st = self.check(run(BERLIN, provider="bfs_odl", net=net), "bfs_odl", "dose")
        self.assertEqual(st[0]["name"], "Berlin-Tempelhof")
        self.assertEqual(st[0]["usvh"], 0.086)
        self.assertEqual(st[0]["ts"], calendar.timegm((2026, 10, 9, 17, 0, 0)))
        self.assertNotIn("Berlin-Gatow", [s["name"] for s in st])   # test operation, no value
        self.assertEqual(len(st), 10)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(net.urls[0]).query))
        self.assertEqual(q["typeName"], "opendata:odlinfo_odl_1h_latest")
        w, s, e, n = (float(v) for v in q["bbox"].split(","))
        self.assertTrue(w < BERLIN[1] < e and s < BERLIN[0] < n)

    def test_bfs_status_and_units(self):
        doc = fx("rad_bfs_berlin.json")
        doc["features"][0]["properties"].update(site_status=2)      # defective probe
        doc["features"][1]["properties"].update(value=104, unit="nSv/h")
        doc["features"][3]["properties"].update(unit="mR/h")        # unknown unit: skipped
        st = bfs_odl.parse(doc, BERLIN[0], BERLIN[1], 100)
        names = {s["name"]: s["usvh"] for s in st}
        self.assertNotIn(doc["features"][0]["properties"]["name"], names)
        self.assertNotIn(doc["features"][3]["properties"]["name"], names)
        self.assertEqual(names[doc["features"][1]["properties"]["name"]], 0.104)

    def test_stuk_helsinki(self):
        st = self.check(run(HELSINKI, provider="stuk_fmi"), "stuk_fmi", "dose")
        self.assertEqual(len(st), 9)
        self.assertEqual(st[0]["name"], "Helsinki Töölö")
        self.assertEqual(st[0]["ts"], 1791567000)
        self.assertAlmostEqual(st[0]["usvh"], 0.12, delta=0.03)

    def test_stuk_nan_and_bad_xml(self):
        text = fx("rad_stuk_helsinki.json")["text"]
        first = re.search(r"<gml:doubleOrNilReasonTupleList>\s*(\S+)", text).group(1)
        nan = text.replace(first + " ", "NaN ", 1).encode()
        self.assertEqual(len(stuk_fmi.parse(nan, HELSINKI[0], HELSINKI[1], 100)), 8)
        for bad in (b"<html>oops", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>',
                    b"<ExceptionReport/>"):
            with self.assertRaises(base.RadiationError):
                stuk_fmi.parse(bad, HELSINKI[0], HELSINKI[1], 100)

    def test_radnet_boston(self):
        net = FakeNet()
        st = self.check(run(BOSTON, provider="epa_radnet", net=net), "epa_radnet", "dose")
        self.assertEqual([s["name"] for s in st],
                         ["Boston, MA", "Worcester, MA", "Providence, RI"])
        self.assertEqual(st[0]["usvh"], 0.068)
        self.assertEqual(st[0]["ts"], calendar.timegm((2026, 10, 9, 17, 22, 0)))
        self.assertEqual(len(net.urls), 3)                          # Portsmouth not needed
        self.assertTrue(all(u.startswith(epa_radnet.CSV_URL.split("{")[0]) for u in net.urls))

    def test_radnet_station_without_dose(self):
        net = FakeNet()
        st = self.check(run(DENVER, radius=150, provider="epa_radnet", net=net),
                        "epa_radnet", "dose")
        self.assertEqual([s["name"] for s in st], ["Colorado Springs, CO"])
        self.assertEqual(st[0]["usvh"], 0.132)
        self.assertTrue(any("/CO/DENVER" in u for u in net.urls))
        with self.assertRaises(radiation.NoStationsError):
            run(DENVER, radius=50, provider="epa_radnet")

    def test_radnet_csv_details(self):
        head = ("LOCATION_NAME,SAMPLE COLLECTION TIME,DOSE EQUIVALENT RATE (nSv/h),"
                "GAMMA COUNT RATE R02 (CPM),STATUS\n")
        body = head + ("X,10/09/2026 16:00:00,60.00,2000,APPROVED\n"
                       "X,10/09/2026 17:00:00,900.00,2000,REJECTED\n"
                       "X,10/09/2026 18:00:00,,2000,APPROVED\n")
        self.assertEqual(epa_radnet.latest_dose(body.encode()),
                         (60.0, calendar.timegm((2026, 10, 9, 16, 0, 0))))
        self.assertIsNone(epa_radnet.latest_dose(b"SAMPLE COLLECTION TIME,GAMMA (CPM)\n1,2\n"))
        with self.assertRaises(base.RadiationError):
            epa_radnet.latest_dose(b"<html>maintenance</html>\n")
        self.assertEqual(epa_radnet.csv_url(2026, "NY", "NYC (EML)").split("/fixed/")[1],
                         "NY/NYC%20%28EML%29")
        self.assertEqual(epa_radnet.display_name("MN", "ST. PAUL"), "St. Paul, MN")
        self.assertEqual(len(epa_radnet.STATIONS), 140)

    def test_radnet_all_downloads_fail(self):
        net = FakeNet(**{"radnet.epa.gov": base.RadiationError("radnet.epa.gov: HTTP 503")})
        with self.assertRaises(base.RadiationError) as cm:
            run(BOSTON, provider="epa_radnet", net=net)
        self.assertNotIsInstance(cm.exception, radiation.NoStationsError)
        self.assertIn("503", str(cm.exception))

    def test_fps_toronto(self):
        st = self.check(run(TORONTO, provider="hc_fps"), "hc_fps", "terrestrial")
        self.assertEqual(st[0]["name"], "FPS 5000-4")
        self.assertEqual(st[0]["usvh"], 0.021)
        self.assertEqual(len(st), 25)
        self.assertEqual(hc_fps.display_name("7000_8_45.5049_-73.5749"), "FPS 7000-8")

    def test_fps_service_error(self):
        net = FakeNet(**{"maps-cartes.services.geo.ca":
                         b'{"error": {"code": 500, "message": "Error performing query"}}'})
        with self.assertRaisesRegex(base.RadiationError, "service error 500"):
            run(TORONTO, provider="hc_fps", net=net)

    def test_safecast_rules(self):
        st = self.check(run(DENVER, provider="safecast"), "safecast", "community")
        got = {s["name"]: s["usvh"] for s in st}
        self.assertEqual(set(got), {"Civic Center", "Lakewood", "Boulder", "Aurora",
                                    "Safecast sensor 90004"})
        self.assertEqual(got["Civic Center"], round(45 / 334.0, 4))
        self.assertEqual(got["Lakewood"], round(37.5 / 334.0, 4))   # newest entry wins
        self.assertEqual(got["Boulder"], round(40 / 334.0, 4))      # pancake tube preferred
        self.assertEqual(got["Safecast sensor 90004"], round(12.96 / 108.0, 4))
        self.assertEqual(st[0]["name"], "Civic Center")

    def test_safecast_request(self):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(safecast.request_url()).query))
        self.assertEqual(q["class"], safecast.CLASS_RE)
        self.assertIn("lnd_7318u", json.loads(q["template"]))


# --------------------------------------------------------------------------- fetch()

class FetchTest(unittest.TestCase):
    def test_auto_uses_national_network(self):
        res = run(BERLIN)
        self.assertEqual(res["provider"], "bfs_odl")
        self.assertEqual(res["attribution"], bfs_odl.ATTRIBUTION)

    def test_auto_falls_back_when_empty(self):
        res = run(DENVER, radius=50)                    # Denver's RadNet probe has no dose
        self.assertEqual(res["provider"], "safecast")

    def test_auto_falls_back_on_error(self):
        net = FakeNet(**{"radnet.epa.gov": base.RadiationError("radnet.epa.gov: timed out")})
        self.assertEqual(run(DENVER, radius=150, net=net)["provider"], "safecast")

    def test_auto_nothing_nearby(self):
        with self.assertRaises(radiation.NoStationsError) as cm:
            run(GREENWICH)
        self.assertIn("100 km", str(cm.exception))

    def test_auto_all_errors(self):
        net = FakeNet(**{"radnet.epa.gov": base.RadiationError("radnet.epa.gov: HTTP 500"),
                         "tt.safecast.org": base.RadiationError("tt.safecast.org: timed out")})
        with self.assertRaises(base.RadiationError) as cm:
            run(BOSTON, net=net)
        self.assertNotIsInstance(cm.exception, radiation.NoStationsError)
        self.assertIn("HTTP 500", str(cm.exception))
        self.assertIn("timed out", str(cm.exception))

    def test_stale_dropped(self):
        with self.assertRaises(radiation.NoStationsError):
            run(BERLIN, provider="bfs_odl", now=NOW + 4 * 86400)
        res = run(BERLIN, provider="bfs_odl", now=NOW + 3 * 86400 - 4 * 3600)
        self.assertTrue(res["stations"])
        with self.assertRaises(radiation.NoStationsError):             # clock far behind
            run(BERLIN, provider="bfs_odl", now=NOW - 86400)

    def test_safecast_stale_device_dropped(self):
        names = [s["name"] for s in run(DENVER, provider="safecast")["stations"]]
        self.assertNotIn("Stale", names)

    def test_radius(self):
        res = run(BERLIN, radius=12, provider="bfs_odl")
        self.assertTrue(all(s["km"] <= 12 for s in res["stations"]))
        self.assertEqual([s["name"] for s in res["stations"]],
                         ["Berlin-Tempelhof", "Berlin-Karlshorst"])

    def test_garbage_values_dropped(self):
        doc = fx("rad_bfs_berlin.json")
        for f in doc["features"]:
            f["properties"]["value"] = -1
        doc["features"][0]["properties"]["value"] = 1e9
        net = FakeNet(**{"www.imis.bfs.de": json.dumps(doc).encode()})
        with self.assertRaises(radiation.NoStationsError):
            run(BERLIN, provider="bfs_odl", net=net)

    def test_high_values_kept(self):
        doc = fx("rad_bfs_berlin.json")
        doc["features"][0]["properties"]["value"] = 2.5             # an event, not an outlier
        net = FakeNet(**{"www.imis.bfs.de": json.dumps(doc).encode()})
        st = run(BERLIN, provider="bfs_odl", net=net)["stations"]
        self.assertIn(2.5, [s["usvh"] for s in st])

    def test_malformed_responses(self):
        for host, body, pid, where in (
                ("www.imis.bfs.de", b"<html>busy</html>", "bfs_odl", BERLIN),
                ("www.imis.bfs.de", b'{"type": "FeatureCollection"}', "bfs_odl", BERLIN),
                ("tt.safecast.org", b'{"devices": []}', "safecast", DENVER),
                ("maps-cartes.services.geo.ca", b"[]", "hc_fps", TORONTO),
                ("opendata.fmi.fi", b"", "stuk_fmi", HELSINKI)):
            net = FakeNet(**{host: body})
            with self.assertRaises(base.RadiationError, msg=pid) as cm:
                run(where, provider=pid, net=net)
            self.assertNotIsInstance(cm.exception, radiation.NoStationsError)

    def test_empty_answers(self):
        for host, body, pid, where in (
                ("www.imis.bfs.de", b'{"type": "FeatureCollection", "features": []}',
                 "bfs_odl", BERLIN),
                ("tt.safecast.org", b"[]", "safecast", DENVER),
                ("maps-cartes.services.geo.ca", b'{"features": []}', "hc_fps", TORONTO)):
            net = FakeNet(**{host: body})
            with self.assertRaises(radiation.NoStationsError, msg=pid):
                run(where, provider=pid, net=net)

    def test_unexpected_exception_wrapped(self):
        with mock.patch.object(bfs_odl, "parse", side_effect=KeyError("value")):
            with self.assertRaisesRegex(base.RadiationError, "unexpected data"):
                run(BERLIN, provider="bfs_odl")

    def test_arguments(self):
        with self.assertRaises(base.RadiationError):
            run(BERLIN, provider="none")
        with self.assertRaises(ValueError):
            run(BERLIN, provider="geiger")
        with self.assertRaises(ValueError):
            run((91, 0))
        with self.assertRaises(ValueError):
            run(BERLIN, radius=0)
        self.assertEqual(run(BERLIN, provider=" BFS_ODL ")["provider"], "bfs_odl")

    def test_describe(self):
        d = radiation.describe("safecast")
        self.assertEqual(d["license_url"], "https://creativecommons.org/publicdomain/zero/1.0/")
        self.assertEqual(d["attribution"], "Safecast (CC0)")


if __name__ == "__main__":
    unittest.main()
