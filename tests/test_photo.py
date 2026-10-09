"""Tests of the weather photos (intercom_webtab.weather.photo). No network: the loader is a fake
and the pictures are small synthetic images."""
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from intercom_webtab.weather import brain  # noqa: E402
from intercom_webtab.weather import photo as wp  # noqa: E402

SCENES = brain.SCENE_NAMES


def jpeg_bytes(w=320, h=200, seed=0):
    """A small synthetic picture: a gradient, so the mean colour is not grey."""
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 255 // w + seed * 40) % 256, y * 255 // h, (90 + seed * 17) % 256)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def mini_catalog(groups=None, n=3):
    cat = {}
    for g in groups or wp.GROUP_LABELS:
        cat[g] = [{"id": "%s-photo-%d" % (g, i), "source": "test", "title": "t", "creator": "c",
                   "license": "cc0", "url": "https://example.invalid/%s-photo-%d.jpg" % (g, i)}
                  for i in range(n)]
    return cat


class FakeLoader:
    """Fake loader: returns synthetic pictures, can fail on chosen ids."""

    def __init__(self, fail_ids=(), big=False):
        self.fail_ids = set(fail_ids)
        self.big = big
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for pid in self.fail_ids:
            if pid in url:
                raise IOError("no connection")
        if self.big:
            return b"\xff" * (wp.MAX_BYTES + 1)
        return jpeg_bytes(seed=len(self.calls) % 5)


def store(loader=None, cat=None, **kw):
    return wp.PhotoStore(catalog=cat or mini_catalog(), loader=loader or FakeLoader(),
                         sleep=lambda s: None, **kw)


class TestScenes(unittest.TestCase):
    def test_all_scenes_mapped(self):
        self.assertEqual(len(SCENES), 39)
        self.assertEqual(set(SCENES), set(wp.SCENE_GROUP))
        for sc in SCENES:
            for day in (1, 0, None):
                for winter in (False, True):
                    g = wp.group_for(sc, day, winter)
                    self.assertIn(g, wp.GROUP_LABELS, (sc, day, winter))

    def test_catalog_covers_groups(self):
        cat = wp.load_catalog()
        self.assertEqual(set(cat), set(wp.GROUP_LABELS))
        ids = set()
        for g, photos in cat.items():
            self.assertGreaterEqual(len(photos), 3, g)
            for p in photos:
                for f in ("id", "source", "title", "creator", "license", "url"):
                    self.assertIn(f, p, (g, p.get("id")))
                self.assertIn(p["license"], ("cc0", "pdm"))
                self.assertTrue(p["url"].startswith("https://"), p["url"])
                self.assertNotIn("rawpixel", (p["source"] + p["url"]).lower())
                self.assertNotIn(p["id"], ids, "a photo in two groups")
                ids.add(p["id"])
        with open(wp.IDS_FILE, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), set(wp.GROUP_LABELS))

    def test_season_and_night(self):
        s = store()
        self.assertTrue(s.pick("overcast", 1, -5, "2026-01-10").startswith("overcast-winter-"))
        self.assertTrue(s.pick("clear-day", 1, -5, "2026-01-10").startswith("clear-"))   # the sky has no season
        s = store()
        self.assertTrue(s.pick("overcast", 1, 20, "2026-07-10").startswith("overcast-warm-"))
        self.assertTrue(s.pick("smoke", 1, 20, "2026-07-10").startswith("smoke-"))
        self.assertTrue(s.pick("overcast", 0, 20, "2026-07-10").startswith("cloudy-night-"))
        self.assertTrue(s.pick("not-available", None, None, "2026-07-10").startswith("overcast-warm-"))
        self.assertTrue(s.pick("unknown-scene", 1, 20, "2026-07-10").startswith("overcast-warm-"))
        # at night "no data" and an unknown scene are a cloudy night, not the daytime overcast
        self.assertEqual(wp.group_for("not-available", 0, False), "cloudy-night")
        self.assertEqual(wp.group_for("unknown-scene", 0, True), "cloudy-night")
        self.assertEqual(wp.group_for("not-available", None, True), "overcast-winter")
        self.assertEqual(wp.group_for("mostly-cloudy-day", 1, True), "overcast-winter")
        self.assertEqual(wp.group_for("mostly-cloudy-day", 1, False), "cloudy-day")

    def test_season_hysteresis(self):
        s = store()
        self.assertTrue(s.season(5.0, "2026-10-09") is False)
        self.assertFalse(s.season(2.5, "2026-10-09"))     # within the margin -- no change
        self.assertTrue(s.season(1.9, "2026-10-09"))
        self.assertTrue(s.season(3.5, "2026-10-09"))      # within the margin -- no change
        self.assertFalse(s.season(4.1, "2026-10-09"))
        s = store()
        self.assertTrue(s.season(None, date(2026, 1, 5)))
        s = store()
        self.assertFalse(s.season(None, date(2026, 7, 5)))

    def test_season_without_t_follows_month(self):
        s = store()
        self.assertFalse(s.season(None, "2026-10-09"))    # a start without temperature in October
        self.assertFalse(s.season(None, "2026-10-31"))
        self.assertTrue(s.season(None, "2027-01-15"))     # still no temperature in January
        s = store()
        self.assertTrue(s.season(1.0, "2026-10-30"))      # winter by temperature
        self.assertTrue(s.season(None, "2026-10-30"))     # a data gap the same day -- kept
        self.assertFalse(s.season(None, "2026-10-31"))    # a new day without temperature -- by month

    def test_southern_hemisphere_months(self):
        s = store(lat=-33.9)
        self.assertTrue(s.season(None, "2026-07-10"))
        s = store(lat=-33.9)
        self.assertFalse(s.season(None, "2027-01-15"))


class TestPick(unittest.TestCase):
    def test_stable_within_day(self):
        s = store(cat=mini_catalog(n=5))
        a = s.pick("rain", 1, 10, "2026-10-09")
        for _ in range(5):
            self.assertEqual(s.pick("rain", 0, 10, date(2026, 10, 9)), a)
        self.assertRegex(a, r"^rain-[0-9a-f]{4}$")
        days = {s.pick("rain", 1, 10, "2026-10-%02d" % d) for d in range(1, 31)}
        self.assertGreater(len(days), 2)                   # the variant changes from day to day

    def test_key_has_photo_hash(self):
        self.assertNotEqual(wp.photo_key("rain", "a"), wp.photo_key("rain", "b"))
        self.assertEqual(wp.photo_key("rain", "a"), wp.photo_key("rain", "a"))

    def test_choose_from_panel(self):
        s = store()
        key, ahead = s.choose({"scene": "rain", "day": 1, "t": 10, "ahead": ["snow", "not-a-scene"]},
                              "2026-10-09")
        self.assertTrue(key.startswith("rain-"))
        self.assertEqual(len(ahead), 2)
        self.assertTrue(ahead[0].startswith("snow-"))
        self.assertEqual(s.choose(None, "2026-10-09")[1], [])
        self.assertEqual(s.credits(key)["license"], "cc0")
        self.assertIsNone(s.credits("no-such-key"))


class TestStore(unittest.TestCase):
    def test_fetch_get_block(self):
        s = store()
        k = s.pick("rain", 1, 10, "2026-10-09")
        a = s.pick("snow", 1, 10, "2026-10-09")
        self.assertIsNone(s.block(k, [a]))                 # not downloaded yet
        s.want([k, a])
        self.assertEqual(s.fetch_missing(), 2)
        bg, panel = s.get(k, "bg"), s.get(k, "panel")
        self.assertEqual(Image.open(io.BytesIO(bg)).size, (1024, 748))
        self.assertEqual(Image.open(io.BytesIO(panel)).size, (384, 748))
        self.assertNotIn(b"Exif", bg[:4096])
        self.assertIsNone(s.get(k, "other"))
        b = s.block(k, [a, k])
        self.assertEqual(b["key"], k)
        self.assertEqual(b["bg"], "/ph/%s/bg.jpg" % k)
        self.assertEqual(b["panel"], "/ph/%s/panel.jpg" % k)
        self.assertRegex(b["tone"], r"^#[0-9a-f]{6}$")
        self.assertEqual(b["next"], [{"bg": "/ph/%s/bg.jpg" % a, "panel": "/ph/%s/panel.jpg" % a}])
        self.assertEqual(s.fetch_missing(), 0)             # everything is in memory already

    def test_evict_after_state_change(self):
        s = store()
        k1 = s.pick("rain", 1, 10, "2026-10-09")
        k2 = s.pick("fog", 1, 10, "2026-10-09")
        s.want([k1, k2])
        s.fetch_missing()
        k3 = s.pick("snow", 1, 10, "2026-10-09")
        s.want([k3])
        self.assertIsNotNone(s.get(k1, "bg"))              # the old one lives until the new one is ready
        s.fetch_missing()
        self.assertIsNone(s.get(k1, "bg"))
        self.assertIsNone(s.get(k2, "bg"))
        self.assertIsNotNone(s.get(k3, "bg"))
        self.assertEqual(set(s.items), {k3})

    def test_limit_entries(self):
        s = store()
        keys = [s.pick(sc, 1, 10, "2026-10-09") for sc in
                ("rain", "fog", "snow", "heat", "wind", "frost", "ice", "storm")]
        s.want(keys)
        s.fetch_missing()
        self.assertLessEqual(len(s.items), wp.MAX_KEEP)
        # one place is kept for the last shown photo
        self.assertEqual(set(s.items), set(keys[:wp.MAX_KEEP - 1]))

    def test_limit_entries_while_loading(self):
        # a complete change of the set: never more than MAX_KEEP entries, even while loading
        s = store()
        peak = []
        inner = FakeLoader()

        def loader(url):
            peak.append(len(s.items))
            return inner(url)
        s.loader = loader
        old = [s.pick(sc, 1, 10, "2026-10-09") for sc in ("rain", "fog", "snow", "heat", "wind")]
        s.want(old)
        s.fetch_missing()
        s.block(old[0])
        new = [s.pick(sc, 1, 10, "2026-10-09") for sc in ("frost", "ice", "storm", "haze", "downpour")]
        s.want(new)
        s.fetch_missing()
        self.assertLessEqual(max(peak), wp.MAX_KEEP)
        self.assertLessEqual(len(s.items), wp.MAX_KEEP)
        self.assertEqual(set(new) | {old[0]}, set(s.items))   # the shown one stays until replaced

    def test_keep_shown_when_new_fails(self):
        # the state changed and the new photo failed: the screen does not go dark
        s = store()
        k1 = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k1])
        s.fetch_missing()
        b1 = s.block(k1)
        s.loader = FakeLoader(fail_ids=["snow-photo"])
        k2 = s.pick("snow", 1, 10, "2026-10-09")
        s.want([k2])
        self.assertEqual(s.fetch_missing(), 0)
        b = s.block(k2)
        self.assertEqual(b["key"], k1)
        self.assertEqual(b["tone"], b1["tone"])
        self.assertIsNotNone(s.get(k1, "bg"))             # and the old URLs still work
        self.assertEqual(s.fetch_missing(), 0)             # retries do not evict it
        self.assertEqual(s.block(k2)["key"], k1)

    def test_midnight_switch_no_gap(self):
        # at midnight the variant changed and the new one is loading -- block() returns yesterday's
        s = store(cat=mini_catalog(n=5))
        days = ["2026-10-%02d" % d for d in range(1, 31)]
        pairs = [(a, b) for a, b in zip(days, days[1:])
                 if s.pick("rain", 1, 10, a) != s.pick("rain", 1, 10, b)]
        d1, d2 = pairs[0]
        k1 = s.pick("rain", 1, 10, d1)
        s.want([k1])
        s.fetch_missing()
        s.block(k1)
        k2 = s.pick("rain", 1, 10, d2)
        s.want([k2])                                       # not loaded yet
        self.assertEqual(s.block(k2)["key"], k1)
        s.fetch_missing()
        self.assertEqual(s.block(k2)["key"], k2)           # ready -- show the new one
        s.want([k2])
        s.fetch_missing()
        self.assertIsNone(s.get(k1, "bg"))                 # and release the old one
        self.assertEqual(set(s.items), {k2})

    def test_parallel_fetch_returns_at_once(self):
        loader = FakeLoader()
        s = store(loader=loader)
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k])
        s.busy.acquire()                                   # the first call is still running
        try:
            self.assertEqual(s.fetch_missing(), 0)
        finally:
            s.busy.release()
        self.assertEqual(loader.calls, [])
        self.assertEqual(s.fetch_missing(), 1)

    def test_fetch_budget(self):
        # every URL hangs for 20 s: one call takes no longer than FETCH_BUDGET + one photo
        now = [0.0]

        def slow(url):
            now[0] += 20
            raise IOError("no answer")
        s = wp.PhotoStore(catalog=mini_catalog(), loader=slow, sleep=lambda x: now.__setitem__(0, now[0] + x),
                          clock=lambda: now[0])
        keys = [s.pick(sc, 1, 10, "2026-10-09") for sc in ("rain", "fog", "snow", "heat", "wind")]
        s.want(keys)
        self.assertEqual(s.fetch_missing(), 0)
        per_photo = 3 * 20 + 2 * wp.RETRY_PAUSE
        self.assertLessEqual(now[0], wp.FETCH_BUDGET + per_photo)
        tried = {pid for pid in s.bad}
        self.assertLess(len(tried), len(keys) * 2)         # the queue did not reach some photos
        # photos that were not started are not marked bad -- they are tried next time
        later = [k for k in keys if s.by_key[k][1]["id"] not in tried]
        self.assertTrue(later)

    def test_size_limit_in_store(self):
        s = store(loader=FakeLoader(big=True))
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k])
        self.assertEqual(s.fetch_missing(), 0)
        self.assertIsNone(s.block(k))

    def test_fallback_photo(self):
        cat = mini_catalog()
        s = store(cat=cat)
        k = s.pick("rain", 1, 10, "2026-10-09")
        pid = s.by_key[k][1]["id"]
        s.loader = FakeLoader(fail_ids=[pid])
        s.want([k])
        self.assertEqual(s.fetch_missing(), 1)
        b = s.block(k)
        self.assertIsNotNone(b)
        self.assertNotEqual(b["key"], k)                   # another photo of the same group
        self.assertTrue(b["key"].startswith("rain-"))
        self.assertIsNotNone(s.get(k, "bg"))               # also under the old key
        self.assertEqual(s.pick("rain", 1, 10, "2026-10-09"), b["key"])   # the bad one is skipped
        # proxy twice with a pause, then the original URL
        tried = [u for u in s.loader.calls if pid in u]
        self.assertEqual(len(tried), 3)

    def test_all_fail(self):
        s = store(loader=FakeLoader(fail_ids=["rain-photo"]))
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k])
        self.assertEqual(s.fetch_missing(), 0)
        self.assertIsNone(s.block(k))
        self.assertEqual(s.fetch_missing(), 0)             # the third photo of the group fails too
        n = len(s.loader.calls)
        self.assertEqual(n, 9)
        self.assertEqual(s.fetch_missing(), 0)             # all bad: no hammering until BAD_TTL
        self.assertEqual(len(s.loader.calls), n)

    def test_retry_after_bad_ttl(self):
        now = [1000.0]
        s = wp.PhotoStore(catalog=mini_catalog(), loader=FakeLoader(fail_ids=["rain-photo"]),
                          sleep=lambda x: None, clock=lambda: now[0])
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k])
        s.fetch_missing()
        s.loader = FakeLoader()                             # the network is back
        now[0] += wp.BAD_TTL + 1
        k2 = s.pick("rain", 1, 10, "2026-10-09")
        self.assertEqual(k2, k)
        s.want([k2])
        self.assertEqual(s.fetch_missing(), 1)
        self.assertIsNotNone(s.block(k2))

    def test_want_ignores_junk(self):
        s = store()
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([None, "no-such-key", k, k])
        self.assertEqual(s.wanted, [k])

    def test_garbage_image(self):
        s = store(loader=lambda url: b"not an image")
        k = s.pick("rain", 1, 10, "2026-10-09")
        s.want([k])
        self.assertEqual(s.fetch_missing(), 0)

    def test_broken_catalog(self):
        # a broken edit of the JSON: entries without an id and non-dicts are dropped, the server starts
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "ids.json")
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"rain": [{"id": "ok-1", "url": "https://example.invalid/1.jpg"},
                                    {"url": "no id"}, "a string", {"id": ""}, {"id": 5}],
                           "fog": "not a list", "no-such-group": [{"id": "x"}], "snow": []}, fh)
            cat = wp.load_catalog(f)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("[1, 2]")
            self.assertEqual(wp.load_catalog(f), {})
        self.assertEqual(cat, {"rain": [{"id": "ok-1", "url": "https://example.invalid/1.jpg"}]})
        s = store(cat=cat)
        self.assertTrue(s.pick("rain", 1, 10, "2026-10-09").startswith("rain-"))
        self.assertIsNone(s.pick("fog", 1, 10, "2026-10-09"))   # neither the group nor "overcast"


class FakeResp:
    def __init__(self, body, length=None):
        self.body = io.BytesIO(body)
        self.headers = {"Content-Length": str(length if length is not None else len(body))}

    def read(self, n=-1):
        return self.body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DripResp:
    """A trickling server: read1 returns one byte per fake second; read (which waits for a
    full chunk) must not be called."""

    def __init__(self, clock):
        self.clock = clock
        self.headers = {}
        self.reads = 0

    def read(self, n=-1):
        raise AssertionError("read waits for a full chunk -- the deadline could not be checked")

    def read1(self, n=-1):
        self.reads += 1
        self.clock[0] += 1
        return b"x"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestHttp(unittest.TestCase):
    def setUp(self):
        self.orig = wp.urllib.request.urlopen

    def tearDown(self):
        wp.urllib.request.urlopen = self.orig

    def test_ok_and_user_agent(self):
        seen = {}

        def fake(rq, timeout=None):
            seen["ua"] = rq.get_header("User-agent")
            seen["timeout"] = timeout
            return FakeResp(b"abc")
        wp.urllib.request.urlopen = fake
        self.assertEqual(wp.http_get("https://example.invalid/x"), b"abc")
        self.assertTrue(seen["ua"].startswith("IntercomWebTab"))
        self.assertEqual(seen["timeout"], wp.TIMEOUT)

    def test_limit_by_header(self):
        wp.urllib.request.urlopen = lambda rq, timeout=None: FakeResp(b"abc", length=wp.MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            wp.http_get("https://example.invalid/x")

    def test_limit_by_body(self):
        # the header lies -- cut by the actual size
        wp.urllib.request.urlopen = lambda rq, timeout=None: FakeResp(b"\0" * (wp.MAX_BYTES + 10), length=10)
        with self.assertRaises(ValueError):
            wp.http_get("https://example.invalid/x")

    def test_deadline_on_drip(self):
        # one byte at a time: cut by the deadline, not by a full chunk
        clock = [0.0]
        resp = DripResp(clock)
        wp.urllib.request.urlopen = lambda rq, timeout=None: resp
        with self.assertRaises(TimeoutError):
            wp.http_get("https://example.invalid/x", now=lambda: clock[0])
        self.assertLessEqual(resp.reads, wp.DEADLINE + 1)


# ---------------------------------------------------------------- per-pixel reference of the look
def ref_mask():
    W, H = 1024, 748
    m = Image.new("L", (W, H))
    px = m.load()
    for yy in range(H):
        for xx in range(W):
            dx = xx / 1080
            dy = (H - yy) / 430
            a = max(0, 1 - (dx * dx + dy * dy) ** .5)
            px[xx, yy] = int(250 * min(1, a * 2.1) ** 0.75)
    return m.filter(ImageFilter.GaussianBlur(36))


def ref_render(im, m):
    import colorsys
    W, H = 1024, 748
    im = im.convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    left = (im.width - W) // 2
    top = (im.height - H) // 2
    im = im.crop((left, top, left + W, top + H))
    bg = ImageEnhance.Brightness(im).enhance(0.8)
    r, g, b = ImageStat.Stat(im).mean
    h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    tr, tg, tb = colorsys.hsv_to_rgb(h, min(1, sat * 1.6 + 0.08), 0.10)
    tone = Image.new("RGB", (W, H), (int(tr * 255), int(tg * 255), int(tb * 255)))
    out = Image.composite(tone, bg, m)
    p = im.crop((640, 0, W, H)).filter(ImageFilter.GaussianBlur(22))
    L = ImageStat.Stat(p.convert("L")).mean[0]
    p = ImageEnhance.Brightness(p).enhance(min(0.55, 70 / max(L, 1)))
    p = ImageEnhance.Color(p).enhance(0.8)
    return out, p, (int(tr * 255), int(tg * 255), int(tb * 255))


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref_mask = ref_mask()

    def test_mask_matches_per_pixel(self):
        d = ImageChops.difference(wp.mask(), self.ref_mask)
        self.assertLessEqual(d.getextrema()[1], 3)

    def test_render_matches_reference(self):
        for seed, size in ((1, (320, 200)), (3, (200, 320)), (4, (1500, 900))):
            im = Image.open(io.BytesIO(jpeg_bytes(*size, seed=seed)))
            bg, p, rgb = wp.render(im)
            rbg, rp, rrgb = ref_render(im, self.ref_mask)
            self.assertEqual(bg.size, (1024, 748))
            self.assertEqual(p.size, (384, 748))
            self.assertEqual(rgb, rrgb)
            self.assertLessEqual(max(e[1] for e in ImageChops.difference(bg, rbg).getextrema()), 3)
            self.assertEqual(ImageChops.difference(p, rp).getbbox(), None)

    def test_big_jpeg_decoded_small(self):
        # above MAX_PIXELS: JPEG is decoded downscaled at once, PNG is refused
        orig = wp.MAX_PIXELS
        wp.MAX_PIXELS = 2 * 1000 * 1000
        try:
            big = Image.new("RGB", (2100, 1500), (60, 90, 120))
            for fmt, ok in (("JPEG", True), ("PNG", False)):
                out = io.BytesIO()
                big.save(out, fmt)
                if ok:
                    r = wp.process(out.getvalue())
                    self.assertEqual(Image.open(io.BytesIO(r["bg"])).size, (1024, 748))
                else:
                    with self.assertRaises(ValueError):
                        wp.process(out.getvalue())
        finally:
            wp.MAX_PIXELS = orig

    def test_process_tone_hex(self):
        out = wp.process(jpeg_bytes(seed=2))
        bg, p, rgb = wp.render(Image.open(io.BytesIO(jpeg_bytes(seed=2))))
        self.assertEqual(out["tone"], "#%02x%02x%02x" % rgb)
        self.assertEqual(out["bg"][:2], b"\xff\xd8")
        self.assertEqual(out["panel"][:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
