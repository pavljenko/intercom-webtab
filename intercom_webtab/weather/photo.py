# -*- coding: utf-8 -*-
"""Weather photos: a large picture on the left of the page and a blurred panel on the right.

The catalogue is ``photo_ids.json`` next to this module: group -> Openverse photos (CC0 or
public-domain mark only, so no credit line is required; creators and licences are kept in the
JSON anyway). A picture is downloaded through the Openverse proxy, processed into the
station look (a 1024x748 frame, a darkening gradient in the photo's own tone from the
bottom-left corner, the panel is the blurred right edge) and lives ONLY in memory: the
current state plus the states expected in the next hours; everything else is evicted. The
module never writes to disk.

Pillow 9.0 compatible: no Image.Resampling and nothing newer; numpy is not needed.

How the server uses it (on every panel build):
    key, ahead = store.choose(panel["now"])          # or store.pick(scene, day, t, date)
    store.want([key] + ahead)
    loop.run_in_executor(pool, store.fetch_missing)  # in the background, do NOT wait
    panel["photo"] = store.block(key, ahead)         # at once -> dict or None
    GET /ph/<key>/bg.jpg | panel.jpg  ->  store.get(key, "bg" | "panel")

fetch_missing goes to the network and may take minutes when addresses do not answer (the
limit is FETCH_BUDGET plus one photo), so nobody waits for it: a second call while the first
one runs returns 0 at once, and it never raises. While the photo of a key is not ready
(downloading, failed, the daily variant changed at midnight) block() returns the last shown
one, so the screen never goes dark; the new photo arrives with the next panel build.
"""
import colorsys
import io
import json
import os
import threading
import time
import urllib.request
import zlib
from datetime import datetime

from PIL import Image, ImageEnhance, ImageFilter, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
IDS_FILE = os.path.join(HERE, "photo_ids.json")

W, H = 1024, 748          # left half + panel: the station screen in landscape
PANEL_X = 640             # the panel is the right edge of the frame from this x
QUALITY = 88
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12 * 1000 * 1000   # bigger: JPEG is decoded downscaled, other formats refused
TIMEOUT = 20              # s per socket operation
DEADLINE = 30             # s for a whole download (checked before every read)
RETRY_PAUSE = 4.0         # s between attempts
FETCH_BUDGET = 60         # s: fetch_missing starts no new photo after this; the rest next time
BAD_TTL = 30 * 60         # a photo that failed is not offered again for this long
FALLBACKS = 1             # how many other photos of the group are tried instead of a failed one
MAX_KEEP = 6              # entries in memory (one place is kept for the last shown photo)
WINTER_T = 3.0            # degC: colder -> the winter variant of a group
WINTER_HYST = 1.0         # margin so the picture does not flip at the threshold
WINTER_MONTHS_NORTH = (11, 12, 1, 2, 3)   # season by month when no temperature is known
WINTER_MONTHS_SOUTH = (5, 6, 7, 8, 9)
UA = {"User-Agent": "IntercomWebTab/0.1.0"}
PROXY = "https://api.openverse.org/v1/images/%s/thumb/?full_size=true&compressed=true"

# ---------------------------------------------------------------- photo groups
GROUP_LABELS = {
    "clear": "Clear sky",
    "clear-night": "Clear night",
    "cloudy-day": "Partly cloudy",
    "cloudy-night": "Cloudy night",
    "overcast-warm": "Overcast",
    "overcast-winter": "Overcast, winter",
    "fog": "Fog",
    "rain": "Rain",
    "downpour": "Downpour",
    "storm": "Thunderstorm",
    "snow": "Snowfall",
    "blizzard": "Blizzard, drifting snow",
    "ice": "Ice, rime",
    "frost": "Frost",
    "haze": "Haze, dust",
    "smoke": "Smoke",
    "heat": "Heat",
    "wind": "Wind",
}

# scene (one of the 39 dot-icon names) -> group or (warm, winter)
SCENE_GROUP = {
    "clear-day": "clear",                                    # the sky has no season
    "mostly-clear-day": "clear",
    "partly-cloudy-day": "cloudy-day",
    "mostly-cloudy-day": ("cloudy-day", "overcast-winter"),
    "clear-night": "clear-night",
    "mostly-clear-night": "clear-night",
    "partly-cloudy-night": "cloudy-night",
    "mostly-cloudy-night": "cloudy-night",
    "overcast": ("overcast-warm", "overcast-winter"),        # at night: cloudy night
    "fog": "fog",
    "rime-fog": "ice",                                       # rime on branches
    "drizzle": "rain",
    "rain": "rain",
    "sleet": "rain",
    "heavy-rain": "downpour",
    "showers-day": "downpour",
    "showers-night": "downpour",
    "freezing-drizzle": "ice",
    "freezing-rain": "ice",
    "ice": "ice",
    "snow": "snow",
    "heavy-snow": "snow",
    "snow-grains": "snow",
    "snow-showers-day": "snow",
    "snow-showers-night": "snow",
    "thunderstorm": "storm",
    "thunderstorm-heavy": "storm",
    "thunderstorm-hail": "storm",
    "wind": "wind",
    "blizzard": "blizzard",
    "drifting-snow": "blizzard",
    "dust-haze": "haze",
    "dust-storm": "haze",
    "smoke": "smoke",
    "haze": "haze",
    "dry-wind": "heat",
    "heat": "heat",
    "frost": "frost",
    "not-available": ("overcast-warm", "overcast-winter"),
}
# scenes without a day/night suffix that have their own night photo
NIGHT_GROUP = {"overcast": "cloudy-night", "not-available": "cloudy-night"}


def group_for(scene, day, winter):
    """Photo group for a scene; an unknown scene counts as "no data"."""
    if scene not in SCENE_GROUP:
        scene = "not-available"
    if day == 0 and scene in NIGHT_GROUP:
        return NIGHT_GROUP[scene]
    g = SCENE_GROUP[scene]
    if isinstance(g, tuple):
        return g[1] if winter else g[0]
    return g


def load_catalog(path=IDS_FILE):
    """Group -> photos. Unknown groups and entries without an id are dropped: a broken edit of
    the JSON must not stop the server."""
    with open(path, encoding="utf-8") as f:
        cat = json.load(f)
    out = {}
    if not isinstance(cat, dict):
        return out
    for g, v in cat.items():
        if g in GROUP_LABELS and isinstance(v, list):
            ps = [p for p in v if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]]
            if ps:
                out[g] = ps
    return out


def photo_key(group, pid):
    # a short hash of the id in the key: another photo -> another URL, the browser cache stays right
    return "%s-%04x" % (group, zlib.crc32(pid.encode("utf-8")) & 0xffff)


def _day_str(date, tz=None):
    if date is None:
        return datetime.now(tz).strftime("%Y-%m-%d")
    if hasattr(date, "strftime"):
        return date.strftime("%Y-%m-%d")
    return str(date)[:10]


# ---------------------------------------------------------------- processing
_mask = None
_mask_lock = threading.Lock()


def _alpha(xx, yy):
    """Darkening at pixel (xx, yy) before the blur: strongest at the bottom-left corner."""
    dx = xx / 1080
    dy = (H - yy) / 430
    a = max(0, 1 - (dx * dx + dy * dy) ** .5)
    return int(250 * min(1, a * 2.1) ** 0.75)


def make_mask():
    """Darkening mask. Computed on a 4x4 grid and stretched bilinearly (0.8 million formula
    calls otherwise); after the radius-36 blur it differs from the per-pixel mask by at
    most a few levels (tests/test_photo.py)."""
    k = 4
    cw, ch = W // k, (H + k - 1) // k
    m = Image.new("L", (cw, ch))
    # grid node i lands on the centre k*i + (k-1)/2 of the full-size picture
    m.putdata([_alpha(k * i + (k - 1) / 2, k * j + (k - 1) / 2) for j in range(ch) for i in range(cw)])
    m = m.resize((cw * k, ch * k), Image.BILINEAR).crop((0, 0, W, H))
    return m.filter(ImageFilter.GaussianBlur(36))


def mask():
    global _mask
    with _mask_lock:
        if _mask is None:
            _mask = make_mask()
        return _mask


def render(im):
    """Source picture (PIL) -> (background 1024x748, panel 384x748, tone (r, g, b))."""
    im = im.convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    left = (im.width - W) // 2
    top = (im.height - H) // 2
    im = im.crop((left, top, left + W, top + H))
    bg = ImageEnhance.Brightness(im).enhance(0.8)
    r, g, b = ImageStat.Stat(im).mean[:3]
    h, sat, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    tr, tg, tb = colorsys.hsv_to_rgb(h, min(1, sat * 1.6 + 0.08), 0.10)
    rgb = (int(tr * 255), int(tg * 255), int(tb * 255))
    bg = Image.composite(Image.new("RGB", (W, H), rgb), bg, mask())
    # panel: blur, then bring to a dark brightness whatever the photo
    p = im.crop((PANEL_X, 0, W, H)).filter(ImageFilter.GaussianBlur(22))
    L = ImageStat.Stat(p.convert("L")).mean[0]
    p = ImageEnhance.Brightness(p).enhance(min(0.55, 70 / max(L, 1)))
    p = ImageEnhance.Color(p).enhance(0.8)
    return bg, p, rgb


def _jpeg(im):
    out = io.BytesIO()
    im.save(out, "JPEG", quality=QUALITY, optimize=True)     # no EXIF is written
    return out.getvalue()


def process(data):
    """Image bytes -> {"bg": jpeg, "panel": jpeg, "tone": "#rrggbb"}. Raises on junk."""
    im = Image.open(io.BytesIO(data))
    if im.width * im.height > MAX_PIXELS:
        im.draft("RGB", (W, H))         # JPEG: decode at 1/2..1/8 but not below the frame
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("picture too large: %dx%d" % im.size)
    bg, p, rgb = render(im)
    return {"bg": _jpeg(bg), "panel": _jpeg(p), "tone": "#%02x%02x%02x" % rgb}


# ---------------------------------------------------------------- download
def http_get(url, now=time.monotonic):
    """Bytes from a URL: timeouts, the MAX_BYTES limit (by header and in fact), own User-Agent.
    TIMEOUT applies per socket operation and DEADLINE to the whole URL: the deadline is checked
    before every read, and read1 returns what has arrived without waiting for a full chunk,
    so a trickling server is cut off no later than DEADLINE + TIMEOUT."""
    end = now() + DEADLINE
    rq = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(rq, timeout=TIMEOUT) as r:
        n = r.headers.get("Content-Length")
        if n and n.isdigit() and int(n) > MAX_BYTES:
            raise ValueError("more than %d bytes: %s" % (MAX_BYTES, n))
        read = getattr(r, "read1", None) or r.read
        buf = []
        got = 0
        while True:
            if now() > end:
                raise TimeoutError("download took longer than %d s" % DEADLINE)
            chunk = read(65536)
            if not chunk:
                break
            got += len(chunk)
            if got > MAX_BYTES:
                raise ValueError("more than %d bytes" % MAX_BYTES)
            buf.append(chunk)
    return b"".join(buf)


# ---------------------------------------------------------------- in-memory store
class PhotoStore:
    """Photos by "group-hash" keys. get/block/pick/want are called from the event loop,
    fetch_missing from an executor; shared state changes only under the lock, network and
    processing happen outside it.

    tz   -- tzinfo for the local date when ``date`` is not given (photo of the day)
    lat  -- latitude; on the southern hemisphere the month-based winter is May..September
    """

    def __init__(self, catalog=None, loader=None, sleep=time.sleep, clock=time.time, tz=None, lat=None):
        if catalog is None:
            try:
                catalog = load_catalog()
            except Exception:       # without a catalogue the station lives without photos
                catalog = {}
        self.cat = catalog
        self.loader = loader or http_get
        self.sleep = sleep
        self.clock = clock
        self.tz = tz
        self.winter_months = WINTER_MONTHS_SOUTH if (lat is not None and lat < 0) else WINTER_MONTHS_NORTH
        self.by_key = {}                  # key -> (group, photo)
        for g, photos in self.cat.items():
            for p in photos:
                self.by_key[photo_key(g, p["id"])] = (g, p)
        self.lock = threading.Lock()
        self.busy = threading.Lock()      # fetch_missing never runs in two threads
        self.items = {}                   # key -> {"bg", "panel", "tone", "id", "at"}
        self.wanted = []                  # wanted keys, the current one first
        self.alias = {}                   # key of a failed photo -> key of its replacement
        self.bad = {}                     # id -> until when it is not offered
        self.shown = None                 # key block() returned last
        self.winter = None
        self.season_day = None            # the day the season was decided for

    # ---- choice
    def season(self, t, date):
        """Winter or not: the WINTER_T threshold with a margin. Without a temperature -- by
        month, but a temperature-based decision holds until the end of its day (a data gap
        does not repaint the picture)."""
        day = _day_str(date, self.tz)
        if t is None:
            if self.winter is None or day != self.season_day:
                try:
                    month = int(day[5:7])
                except ValueError:
                    month = datetime.now(self.tz).month
                self.winter = month in self.winter_months
        elif self.winter is None:
            self.winter = t < WINTER_T
        elif self.winter and t > WINTER_T + WINTER_HYST:
            self.winter = False
        elif not self.winter and t < WINTER_T - WINTER_HYST:
            self.winter = True
        self.season_day = day
        return self.winter

    def pick(self, scene, day, t, date=None):
        """Photo key for a scene: the variant is stable for a day, unavailable photos are
        skipped. ``t`` is the air temperature in degC (now["t"] of the panel)."""
        g = group_for(scene, day, self.season(t, date))
        photos = self.cat.get(g) or self.cat.get("overcast-warm") or []
        if not photos:
            return None
        if g not in self.cat:
            g = "overcast-warm"
        n = len(photos)
        i0 = zlib.crc32((_day_str(date, self.tz) + g).encode("utf-8")) % n
        now = self.clock()
        with self.lock:
            for j in range(n):
                p = photos[(i0 + j) % n]
                if self.bad.get(p["id"], 0) <= now:
                    return photo_key(g, p["id"])
        return photo_key(g, photos[i0]["id"])

    def choose(self, now_block, date=None):
        """(key, ahead_keys) for the ``now`` block of a panel."""
        n = now_block if isinstance(now_block, dict) else {}
        key = self.pick(n.get("scene"), n.get("day"), n.get("t"), date)
        ahead = [self.pick(s, n.get("day"), n.get("t"), date) for s in (n.get("ahead") or [])]
        return key, [a for a in ahead if a]

    def want(self, keys):
        """Wanted set (current + ahead), at most MAX_KEEP - 1: one place stays for the last
        shown photo. The rest is evicted by fetch_missing at its end: until the page gets the
        new panel it may still request the old /ph/... URLs."""
        ks = []
        for k in keys:
            if k and k in self.by_key and k not in ks:
                ks.append(k)
        with self.lock:
            self.wanted = ks[:MAX_KEEP - 1]
            self._trim(MAX_KEEP)

    # ---- loading (executor, one thread)
    def fetch_missing(self):
        """Download and process what is wanted and missing, evict the rest. Returns how many
        were loaded. Starts no new photo later than FETCH_BUDGET after the start."""
        if not self.busy.acquire(False):
            return 0
        try:
            done = 0
            stop = self.clock() + FETCH_BUDGET
            with self.lock:
                todo = [k for k in self.wanted if self._real(k) not in self.items]
            for k in todo:
                if self.clock() > stop:
                    break
                with self.lock:
                    if k not in self.wanted or self._real(k) in self.items:
                        continue
                try:
                    got = self._load(k, stop)
                except Exception:           # nobody catches an exception in the background
                    got = None
                if got is None:
                    continue
                real, item = got
                with self.lock:
                    self._make_room()
                    self.items[real] = item
                    if real != k:
                        self.alias[k] = real
                done += 1
            with self.lock:
                self._trim(0)
            return done
        finally:
            self.busy.release()

    def _load(self, key, stop):
        """Photo of the key, on failure the next one of the group -> (key, entry) or None.
        Recently failed photos (bad) are skipped until BAD_TTL runs out -- no hammering
        without a network. A started photo goes through all its URLs (or it could not be
        marked bad); no new photo starts after ``stop``."""
        g, first = self.by_key[key]
        photos = self.cat[g]
        i0 = photos.index(first)
        tries = 0
        for j in range(len(photos)):
            if tries > FALLBACKS or self.clock() > stop:
                break
            p = photos[(i0 + j) % len(photos)]
            if self.bad.get(p["id"], 0) > self.clock():
                continue
            tries += 1
            item = self._fetch_one(p)
            if item is not None:
                with self.lock:
                    self.bad.pop(p["id"], None)
                return photo_key(g, p["id"]), item
            with self.lock:
                self.bad[p["id"]] = self.clock() + BAD_TTL
        return None

    def _fetch_one(self, p):
        """Openverse proxy, a pause, the proxy again, then the original URL."""
        urls = [PROXY % p["id"], PROXY % p["id"]]
        if p.get("url"):
            urls.append(p["url"])
        for n, u in enumerate(urls):
            if n:
                self.sleep(RETRY_PAUSE)
            try:
                data = self.loader(u)
                if len(data) > MAX_BYTES:
                    raise ValueError("more than %d bytes" % MAX_BYTES)
                item = process(data)
            except Exception:
                continue
            item["id"] = p["id"]
            item["at"] = self.clock()
            return item
        return None

    # ---- serving (event loop)
    def _real(self, key):
        return self.alias.get(key, key)

    def _needed(self):
        """What must stay (under the lock): wanted entries already present and the last shown
        one -- block() returns it until its replacement is ready."""
        need = []
        for k in self.wanted + [self.shown]:
            r = self._real(k)
            if r in self.items and r not in need:
                need.append(r)
        return need

    def _make_room(self):
        """Before a new entry: evict the oldest unneeded ones so that at most MAX_KEEP remain
        with it."""
        need = set(self._needed())
        old = sorted((k for k in self.items if k not in need), key=lambda k: self.items[k]["at"])
        while len(self.items) >= MAX_KEEP and old:
            del self.items[old.pop(0)]

    def _trim(self, extra):
        """Keep what is needed plus at most ``extra`` other recent entries; MAX_KEEP in total."""
        keep = self._needed()
        rest = sorted((k for k in self.items if k not in keep), key=lambda k: -self.items[k]["at"])
        keep += rest[:max(0, min(extra, MAX_KEEP - len(keep)))]
        keep = keep[:MAX_KEEP]
        self.items = {k: self.items[k] for k in keep}
        live = set(self.wanted) | set(keep) | {self.shown}
        self.alias = {a: b for a, b in self.alias.items() if a in live and b in self.items}

    def get(self, key, kind):
        """JPEG bytes ('bg' | 'panel') or None."""
        if kind not in ("bg", "panel"):
            return None
        with self.lock:
            it = self.items.get(self._real(key))
            return it[kind] if it else None

    def block(self, key, ahead_keys=()):
        """The ``photo`` field of /api/panel. While the key's photo is not ready -- the last
        shown one (with its tone), so the screen does not go dark; None only if there is
        nothing to show yet."""
        with self.lock:
            real = self._real(key)
            it = self.items.get(real)
            if it is None and self.shown is not None:
                real = self._real(self.shown)
                it = self.items.get(real)
            if it is None:
                return None
            self.shown = real
            nxt, seen = [], {real}
            for k in ahead_keys or ():
                r = self._real(k)
                if r in self.items and r not in seen:
                    seen.add(r)
                    nxt.append({"bg": "/ph/%s/bg.jpg" % r, "panel": "/ph/%s/panel.jpg" % r})
            return {"key": real, "bg": "/ph/%s/bg.jpg" % real, "panel": "/ph/%s/panel.jpg" % real,
                    "tone": it["tone"], "next": nxt}

    def credits(self, key):
        """{title, creator, license, source} of the photo behind a key, or None."""
        g_p = self.by_key.get(self._real(key))
        if not g_p:
            return None
        p = g_p[1]
        return {k: p.get(k) for k in ("title", "creator", "license", "source")}
