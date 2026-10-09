# -*- coding: utf-8 -*-
"""Weather panel v3: turns raw source responses into the station panel (GET /api/panel).

Pure logic: no network, no files, no system clock. ``now`` is an argument and local time
always comes from the configured time zone ([location] timezone), never from the process.
State between calls (hysteresis, phrase choice, radiation history, the held top line) lives
in the ``mem`` dict owned by the caller; it stays JSON-serialisable.

Entry point:
    build_panel(raw, src, mem, now, cfg) -> dict          # never raises

    raw = {"fc":  Open-Meteo forecast JSON (sources.fetch_forecast),
           "air": Open-Meteo air-quality JSON (sources.fetch_air),
           "rad": radiation.fetch() result {"source", "stations": [{name, lat, lon, km,
                  usvh, ts, kind}, ...]},
           "cam": {panel_id: {"ts": unix time of the last camera frame, "was": bool}}}
    src = {"fc" | "air" | "rad": {"ok": unix time of the last success, "fail": failures in
           a row, "err": text}}

Units: decisions use degC, m/s and mmHg as internal base units;
everything shown is converted to [units]. Numeric fields ``now.t`` and ``now.utci`` stay in
degC; the ``*Txt`` fields and all texts use the configured units.

``now.scene`` is one of the 39 dot-icon names (SCENE_NAMES); the order of the checks in
``_scene`` is the priority of the rules.
"""
import math
import string
import zlib
from datetime import date as _date
from datetime import datetime, timedelta, timezone

from . import phrases as T
from . import read_settings
from . import units as U

try:
    from .utci import utci as _utci
except Exception:  # pragma: no cover -- the panel is still built without "feels like"
    _utci = None

MAXLEN = T.MAXLEN
NOTELEN = T.NOTELEN
RAIN_COLS = 9            # precipitation chart: current hour + 8

# hazard = black ice, fog, thunderstorm: danger underfoot outranks "feels like"
PRIO_LINE = ["rad", "air", "hazard", "heat", "wind", "uv", "press", "precip", "other"]
FAIL_ORDER = ["wx", "rad", "air", "cam"]
TONE = {0: "crit", 1: "fail", 2: "warn", 3: "info", 4: "info", 5: "calm"}

# seconds: (stale, dead)
THRESH = {
    "wx": (45 * 60, 3 * 3600),
    "air": (3 * 3600, 12 * 3600),
    "rad": (18 * 3600, 48 * 3600),
}
RAD_DATA_STALE = 48 * 3600   # newest radiation reading older than this -> stale data
CAM_DEAD = 60
RAIN_GRAY = 2 * 3600
HOLD_TOP = 10 * 60       # the first line changes at most this often (unless a more urgent class)
HYST_TTL = 3 * 3600      # hysteresis memory older than this is forgotten

# Harm scales: (edges, "from" inclusive, hysteresis margin, margin as a fraction of the edge)
SCALE = {
    "aqi": ([40, 60, 80, 100], False, 3.0, False),
    "pm25": ([15, 25, 37.5, 75], False, 0.10, True),
    "pm10": ([45, 50, 100, 150], False, 0.10, True),
    "o3": ([100, 130, 160, 240], False, 0.10, True),
    "no2": ([25, 50, 120, 230], False, 0.10, True),
    "wmean": ([6, 10, 15, 20], True, 1.0, False),       # m/s
    "wgust": ([10, 15, 20, 25], True, 1.5, False),      # m/s
    "pabs": ([10, 15], True, 0.5, False),               # mmHg from the normal
    "p3": ([2.7, 4.5], True, 0.5, False),               # mmHg in 3 h
    "p24": ([3.5, 6, 9], True, 0.5, False),             # mmHg in 24 h
    "rad": ([0.20, 0.30, 0.60, 1.0], False, 0.02, False),   # uSv/h
    "uv": ([2.5, 5.5, 7.5, 10.5], False, 0.5, False),
    "heat": ([26, 32, 38, 46], True, 1.0, False),       # UTCI >= 26 "warm", as utci_category
    "cold": ([-9, 0, 13, 27], False, 1.0, False),       # on -UTCI: < 9, < 0, < -13, < -27
    "dew": ([18, 21, 24], True, 0.5, False),            # dew point degC (humidity widget)
}

# How many new updates below "edge - margin" lower a level. Radiation readings are slow:
# one new reading is enough to refute the previous one.
HYST_NEED = {"rad": 1}

POLL = ["pm25", "pm10", "o3", "no2"]              # order when levels tie
POLL_FIELD = {"aqi": "european_aqi", "pm25": "pm2_5", "pm10": "pm10", "o3": "ozone",
              "no2": "nitrogen_dioxide"}
POLL_SUBIDX = {"pm25": "european_aqi_pm2_5", "pm10": "european_aqi_pm10",
               "o3": "european_aqi_ozone", "no2": "european_aqi_nitrogen_dioxide"}

SNOW_CODES = (71, 73, 75, 77, 85, 86)
FRZ_CODES = (56, 57, 66, 67)
SHOWER_CODES = (80, 81, 82)
HAIL_CODES = (96, 99)
DRIZZLE_CODES = (51, 53, 55)
LIQUID_CODES = (51, 53, 55, 61, 63, 65, 80, 81, 82)

SCENES = T.SCENES
SCENE_NAMES = [k for k, _ in SCENES]

# Scene thresholds (WMO-No. 8 and the usual synoptic practice; see docs).
SC = {
    "storm_gust": 20.0,      # m/s: code 95 -> severe; gusts of wind level L3
    "blizzard_gust": 15.0,   # m/s: blizzard from 15 m/s
    "wet_min": 0.1,          # mm/h: less is a trace (Open-Meteo rounding step)
    "snow_min": 0.07,        # cm/h: snow from 0.1 mm/h water equivalent, like rain
    "snow_heavy": 5.0,       # mm/h water equivalent: heavy snow, WMO-No. 8 ch. 14
    "snow_heavy_vis": 400.0,  # m: heavy snow by visibility, same source
    "snow_vis_min": 0.5,     # cm/h: visibility counts as snow-driven without a snow code from here
    "rain_heavy": 10.0,      # mm/h: heavy rain, WMO-No. 8 ch. 14
    "rain_12h": 15.0,        # mm in 12 h: a heavy-rain spell
    "sleet_t": 0.5,          # degC: a snow code above this with liquid precipitation -> sleet
    "clearing": 80.0,        # %: cloud below this -> "with clear spells"
    "mostly_cloudy": 65.0,   # %: code 2 and cloud from this -> "mostly cloudy"
    "drift_gust": 12.0,      # m/s: drifting snow (no official threshold)
    "drift_depth": 0.01,     # m: snow is lying
    "drift_fresh": 1.0,      # cm in 12 h: fresh (mobile) snow
    "dust_storm": 40.0,      # ug/m3 dust together with gusts from 15 m/s
    "dust_storm_gust": 15.0,
    "dust_haze": 50.0,       # ug/m3 dust and PM10
    "smoke_pm25": 75.0,      # ug/m3: WHO 2021 interim target 1; EAQI "extremely poor"
    "smoke_dust": 20.0,      # ug/m3: less dust -> the particles are not dust
    "smoke_ratio": 0.6,      # PM2.5/PM10 from 0.6 -> fine particles (smoke); dust is coarser
    "haze_vis": 5000.0,      # m: haze -- visibility below 5 km (METAR, ICAO Annex 3)
    "haze_rh": 80.0,         # %: dry haze -- humidity below ~80 % (WMO-No. 8)
    "fog_vis": 1000.0,       # m: fog -- visibility below 1 km (WMO-No. 8)
    "dry_t": 25.0, "dry_rh": 30.0, "dry_wind": 5.0,   # hot dry wind (agrometeorological)
    "heat_t": 35.0, "heat_utci": 38.0,                # heat: 35 degC / UTCI "very strong"
    "frost_t": -15.0, "frost_utci": -27.0,            # frost: -15 degC / UTCI "very strong cold"
    "ice_wet": 0.3,          # mm of liquid precipitation in 12 h before a freeze
    "past_h": 12,            # hours of history for black ice, drifting snow, rain sums
}

WET_SCENES = ("fog", "rime-fog", "drizzle")      # "damp": fog and drizzle above zero
ATTR_WEATHER = "Weather: Open-Meteo.com"
ATTR_AIR = "Air: CAMS"


# ---------------------------------------------------------------- small helpers
_num = U.num
_rnd = U.rnd


def _int(x):
    f = _num(x)
    return None if f is None else int(round(f))


def _d(x, k):
    v = x.get(k) if isinstance(x, dict) else None
    return v if isinstance(v, dict) else {}


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _dt(c, ts):
    return datetime.fromtimestamp(ts, c["tz"])


def _hh(c, ts):
    return "%02d:%02d" % (_dt(c, ts).hour, _dt(c, ts).minute)


def _hm(c, ts):
    d = _dt(c, ts)
    return "%02d:%02d" % (d.hour, d.minute)


def _hour_start(ts, tz):
    """Start of the local hour containing ts (also right for half-hour time zones)."""
    ti = int(ts)
    d = datetime.fromtimestamp(ti, tz)
    return ti - d.minute * 60 - d.second


def _fit(opts, n=NOTELEN):
    for o in opts:
        if len(o) <= n:
            return o
    return opts[-1][:n]


def resolve_tz(settings, fc=None):
    """tzinfo for the panel: the configured zone; if the zone database does not know it,
    the fixed offset Open-Meteo reported; otherwise UTC."""
    tz = settings.tzinfo() if settings is not None else None
    if tz is not None:
        return tz
    off = _num(fc.get("utc_offset_seconds")) if isinstance(fc, dict) else None
    if off is not None and abs(off) <= 18 * 3600:
        return timezone(timedelta(seconds=int(off)))
    return timezone.utc


def press_norm(elevation_m):
    """Standard-atmosphere pressure at the given elevation (ICAO), mmHg; None if unknown."""
    h = _num(elevation_m)
    if h is None or not -500 <= h <= 9000:
        return None
    hpa = 1013.25 * (1.0 - 2.25577e-5 * h) ** 5.25588
    return hpa * U.MM_PER_HPA


def _lvl(v, edges, ge=False):
    n = 0
    for e in edges:
        if (v >= e) if ge else (v > e):
            n += 1
        else:
            break
    return n


def level(name, v):
    """Harm level without hysteresis."""
    edges, ge = SCALE[name][0], SCALE[name][1]
    return _lvl(v, edges, ge)


def _hyst(mem, key, name, v, upd, now):
    """Level with hysteresis: up at once; down only after two updates in a row with a lower
    level and the last value below "edge - margin". ``upd`` identifies the data update:
    rebuilding the panel from the same data is not an update."""
    if v is None:
        return None
    edges, ge, margin, pct = SCALE[name]
    raw = _lvl(v, edges, ge)
    hy = mem.setdefault("hy", {})
    st = hy.get(key)
    if not isinstance(st, dict) or not isinstance(st.get("t"), (int, float)) \
            or now - st.get("t", 0) > HYST_TTL:
        st = {}
    prev = st.get("L")
    if not isinstance(prev, int) or isinstance(prev, bool) or not 0 <= prev <= len(edges):
        prev = 0
    n = st.get("n") if isinstance(st.get("n"), int) else 0
    if raw >= prev:
        L, n = raw, 0
    else:
        if st.get("u") != upd:
            n += 1
        L = prev
        if n >= HYST_NEED.get(name, 2):
            low = [e - (e * margin if pct else margin) for e in edges]
            L = max(raw, min(prev, _lvl(v, low, ge)))
            if L < prev:
                n = 0
    hy[key] = {"L": L, "n": n, "u": upd, "t": int(now)}
    return L


class _Series(object):
    """Hourly Open-Meteo series (timeformat=unixtime): label -> value."""

    def __init__(self, hourly):
        self.h = hourly if isinstance(hourly, dict) else {}
        self.idx = {}
        times = self.h.get("time")
        if isinstance(times, list):
            for i, t in enumerate(times):
                ti = _int(t)
                if ti is not None and ti not in self.idx:
                    self.idx[ti] = i
        # labels sit on local hour starts; in half-hour zones they are not UTC hour starts
        self.off = (min(self.idx) % 3600) if self.idx else 0

    def at(self, name, ts):
        i = self.idx.get(int(ts))
        if i is None:
            return None
        arr = self.h.get(name)
        if not isinstance(arr, list) or i >= len(arr):
            return None
        return _num(arr[i])

    def interp(self, name, ts):
        a = (int(ts) - self.off) // 3600 * 3600 + self.off
        va, vb = self.at(name, a), self.at(name, a + 3600)
        if va is not None and vb is not None:
            return va + (vb - va) * (ts - a) / 3600.0
        return va if va is not None else vb


# ---------------------------------------------------------------- input parsing
def _parse(raw, src, now, s):
    fc = raw.get("fc") if isinstance(raw.get("fc"), dict) else None
    tz = resolve_tz(s, fc)
    c = {"now": now, "tz": tz, "s": s, "tu": s.temp, "wu": s.wind, "pu": s.press}
    H = _hour_start(now, tz)
    nd = datetime.fromtimestamp(now, tz)
    c.update(H=H, dt=nd, hour=nd.hour, date="%04d-%02d-%02d" % (nd.year, nd.month, nd.day))

    sv = {}
    for k in ("fc", "air", "rad"):
        v = src.get(k) if isinstance(src, dict) else None
        v = v if isinstance(v, dict) else {}
        ok = _num(v.get("ok")) or 0.0
        fail = _int(v.get("fail")) or 0
        err = v.get("err") if isinstance(v.get("err"), str) else ""
        sv[k] = {"ok": ok, "fail": max(0, fail), "err": err}
    c["src"] = sv

    c["has_fc"] = fc is not None
    cur = _d(fc, "current")
    S = _Series(fc.get("hourly") if fc else None)
    c["S"] = S
    for k, f in (("t", "temperature_2m"), ("rh", "relative_humidity_2m"), ("prec", "precipitation"),
                 ("cloud", "cloud_cover"), ("ps", "surface_pressure"), ("wind", "wind_speed_10m"),
                 ("wdir", "wind_direction_10m"), ("gust", "wind_gusts_10m"),
                 ("sw", "shortwave_radiation"), ("uv", "uv_index"), ("vis", "visibility")):
        c[k] = _num(cur.get(f))
    c["code"] = _int(cur.get("weather_code"))
    c["day"] = _int(cur.get("is_day"))
    c["cur_ts"] = _int(cur.get("time"))
    # current precipitation is a sum over its interval (15 min; outside some regions an
    # interpolation of hourly values): x 3600/interval gives mm/h (snow: cm/h)
    iv = _num(cur.get("interval"))
    k_h = 3600.0 / iv if (iv is not None and 0 < iv <= 3600) else 4.0
    c["prec15"] = c["prec"]          # the interval sum itself, used for the black-ice line
    c["prec"] = c["prec"] * k_h if c["prec"] is not None else None
    for k, f in (("rain_h", "rain"), ("shw_h", "showers"), ("snow_h", "snowfall")):
        v = _num(cur.get(f))
        c[k] = max(0.0, v * k_h) if v is not None else None
    c["depth"] = _num(cur.get("snow_depth"))
    # gaps in current -> take the hourly series
    for k, f in (("t", "temperature_2m"), ("wind", "wind_speed_10m"), ("gust", "wind_gusts_10m"),
                 ("ps", "surface_pressure"), ("uv", "uv_index")):
        if c[k] is None:
            c[k] = S.interp(f, now)
    if c["code"] is None:
        c["code"] = _int(S.at("weather_code", H + 3600))
    elev = _num(fc.get("elevation")) if fc else None
    c["elev"] = elev if elev is not None else _num(s.elevation)

    # daily values: today; sunrises and sunsets of all days; UV maximum by date
    c["tmax"] = c["tmin"] = None
    c["suns"], c["uvmax"] = [], {}
    dy = _d(fc, "daily")
    times = dy.get("time") if isinstance(dy.get("time"), list) else []

    def col(f, i):
        arr = dy.get(f)
        return arr[i] if isinstance(arr, list) and i < len(arr) else None

    for i, t in enumerate(times):
        ti = _int(t)
        if ti is None:
            continue
        dd = _dt(c, ti).date()
        for f, kind in (("sunrise", "rise"), ("sunset", "set")):
            v = _int(col(f, i))
            if v is not None and v > 0:
                c["suns"].append((v, kind))
        um = _num(col("uv_index_max", i))
        if um is not None:
            c["uvmax"][dd] = um
        if dd == nd.date():
            c["tmax"], c["tmin"] = _num(col("temperature_2m_max", i)), _num(col("temperature_2m_min", i))
    c["suns"].sort()
    if c["day"] not in (0, 1):
        c["day"] = _is_light(c, now)

    # dew point (Magnus)
    c["dew"] = None
    if c["t"] is not None and c["rh"] is not None:
        try:
            g = 17.62 * c["t"] / (243.12 + c["t"]) + math.log(max(1.0, c["rh"]) / 100.0)
            c["dew"] = 243.12 * g / (17.62 - g)
        except Exception:
            pass

    # UTCI with a simple mean radiant temperature: Tmrt = t + min(0.022 * shortwave, 18)
    c["utci"] = c["utci_f"] = None
    if _utci is not None and None not in (c["t"], c["rh"], c["wind"]):
        try:
            tmrt = c["t"] + min(0.022 * max(0.0, c["sw"] or 0.0), 18.0)
            u = _utci(c["t"], tmrt, c["wind"], c["rh"])
            if _num(u) is not None and -80 < u < 80:
                c["utci"], c["utci_f"] = _rnd(u), u
        except Exception:
            pass

    air = raw.get("air") if isinstance(raw.get("air"), dict) else None
    ac = _d(air, "current")
    c["has_air"] = air is not None
    c["a"] = {k: _num(ac.get(f)) for k, f in POLL_FIELD.items()}
    c["a_sub"] = {k: _num(ac.get(f)) for k, f in POLL_SUBIDX.items()}
    c["a_dust"] = _num(ac.get("dust"))
    c["a_aod"] = _num(ac.get("aerosol_optical_depth"))
    c["air_ts"] = _int(ac.get("time"))

    rad = raw.get("rad") if isinstance(raw.get("rad"), dict) else None
    c["has_rad"] = rad is not None
    c["stations"] = clean_stations(rad.get("stations") if rad else None, tz)
    # credit line: the provider's attribution (carries the licence), its name as a fallback
    cred = (rad.get("attribution") or rad.get("source")) if rad else ""
    c["rad_source"] = cred if isinstance(cred, str) else ""

    cam = raw.get("cam") if isinstance(raw.get("cam"), dict) else {}
    c["cam"] = cam
    labels = dict(s.panels)
    ids = [p for p, _ in s.panels] + sorted(k for k in cam if isinstance(k, str) and k not in labels)
    c["cams"] = []
    for pid in ids:
        lab = labels.get(pid)
        cm = cam.get(pid) if isinstance(cam.get(pid), dict) else {}
        if not lab and isinstance(cm.get("label"), str) and cm["label"].strip():
            lab = cm["label"].strip()
        c["cams"].append((pid, lab or (pid[:1].upper() + pid[1:])))
    return c


def _is_light(c, ts):
    """Daylight at ts: from daily sunrises/sunsets; without them 07:00-19:00 local time."""
    last = None
    for v, kind in c.get("suns") or []:
        if v > ts:
            if last is None:          # before the first event of the series
                return 0 if kind == "rise" else 1
            break
        last = kind
    if last is not None and any(v > ts for v, _ in c["suns"]):
        return 1 if last == "rise" else 0
    return 1 if 7 <= _dt(c, ts).hour < 19 else 0


def _parse_ts(x):
    """Unix seconds from a number or an ISO 8601 string (naive = UTC); None otherwise."""
    v = _num(x)
    if v is not None:
        return v / 1000.0 if v > 1e11 else v          # milliseconds tolerated
    if isinstance(x, str) and x.strip():
        s = x.strip().replace("Z", "+00:00").replace(" ", "T")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    return None


def clean_stations(lst, tz=timezone.utc):
    """radiation.fetch() stations -> [{id, name, km, v, ts, date}] sorted by distance.
    Stations without a distance or a positive dose rate are dropped."""
    out = []
    if not isinstance(lst, list):
        return out
    for s in lst:
        if not isinstance(s, dict):
            continue
        km = _num(s.get("km"))
        v = _num(s.get("usvh"))
        if v is None:
            v = _num(s.get("v"))
        if km is None or v is None or v <= 0 or km < 0:
            continue
        name = s.get("name")
        name = name.strip() if isinstance(name, str) and name.strip() else "?"
        ts = _parse_ts(s.get("ts"))
        day = None
        if ts is not None:
            try:
                day = datetime.fromtimestamp(ts, tz).date().isoformat()
            except Exception:
                ts = None
        la, lo = _num(s.get("lat")), _num(s.get("lon"))
        sid = name if la is None or lo is None else "%s@%.3f,%.3f" % (name, la, lo)
        out.append({"id": sid, "name": name, "km": km, "v": v, "ts": ts, "date": day})
    out.sort(key=lambda x: x["km"])
    return out


def _pdate(s):
    """'2026-10-08' -> date or None."""
    try:
        y, m, d = str(s).strip().split("-")
        return _date(int(y), int(m), int(d))
    except Exception:
        return None


# ---------------------------------------------------------------- freshness
def _st(age, stale, dead):
    if age is None:
        return "none"
    if age > dead:
        return "dead"
    if age > stale:
        return "stale"
    return "ok"


def _fresh(c):
    now, s = c["now"], c["src"]
    out = {}
    for k, sk in (("wx", "fc"), ("air", "air")):
        ok = s[sk]["ok"]
        age = int(max(0, now - ok)) if ok > 0 else None
        out[k] = {"st": _st(age, *THRESH[k]), "age": age, "fail": s[sk]["fail"]}

    ok = s["rad"]["ok"]
    age = int(max(0, now - ok)) if ok > 0 else None
    st = _st(age, *THRESH["rad"])
    tss = [x["ts"] for x in c["stations"] if x["ts"] is not None]
    newest = max(tss) if tss else None
    c["rad_newest"] = newest
    c["rad_data_age"] = int(max(0, now - newest)) if newest is not None else None
    if st == "ok" and c["rad_data_age"] is not None and c["rad_data_age"] > RAD_DATA_STALE:
        st = "stale"
    day = _dt(c, newest).date().isoformat() if newest is not None else None
    out["rad"] = {"st": st, "age": age, "fail": s["rad"]["fail"], "date": day}

    cams = {}
    for pid, _ in c["cams"]:
        cm = c["cam"].get(pid) if isinstance(c["cam"].get(pid), dict) else {}
        ts = _num(cm.get("ts"))
        was = bool(cm.get("was"))
        a = int(max(0, now - ts)) if ts else None
        if a is not None and a <= CAM_DEAD:
            cst = "ok"
        elif was:
            cst = "dead"
        else:
            cst = "none"
        cams[pid] = {"st": cst, "age": a}
    out["cam"] = cams
    return out


def _data_age(c, fresh, k):
    """Age of a source's data, s. For radiation also the age of the newest reading: the
    download can be fresh while the provider serves old readings."""
    a = fresh[k]["age"]
    if k == "rad" and c.get("rad_data_age"):
        a = max(a or 0, c["rad_data_age"])
    return a


# ---------------------------------------------------------------- measures and levels
def _eval_air(c, mem):
    a = c["a"]
    if not c["has_air"] or all(a[k] is None for k in a):
        return None
    now = c["now"]
    upd = c["air_ts"] or int(c["src"]["air"]["ok"]) or int(now)
    lv = {}
    for k in ("aqi",) + tuple(POLL):
        if a[k] is not None:
            lv[k] = _hyst(mem, "air." + k, k, a[k], upd, now)
    L = max(lv.values())
    polls = [k for k in POLL if k in lv]
    culprit = None
    if polls:
        culprit = max(polls, key=lambda k: (lv[k], c["a_sub"].get(k) or 0, -POLL.index(k)))
    dust = (c["a_dust"] or 0) > 50 or (culprit == "pm10" and (c["a_aod"] or 0) > 0.5)
    aqi = a["aqi"]
    return {"k": "air", "L": L, "lv": lv, "culprit": culprit, "dust": dust,
            "aqi": int(round(aqi)) if aqi is not None else None,
            "aqi_L": level("aqi", aqi) if aqi is not None else None,
            "pm10": int(round(a["pm10"])) if a["pm10"] is not None else None}


def _eval_wind(c, mem):
    S, H, now = c["S"], c["H"], c["now"]
    mean, gust = c["wind"], c["gust"]
    pts = []          # (label, mean, gust); label None = current
    if mean is not None or gust is not None:
        pts.append((None, mean, gust))
    for i in (1, 2, 3):
        lab = H + i * 3600
        m, g = S.at("wind_speed_10m", lab), S.at("wind_gusts_10m", lab)
        if lab > now and (m is not None or g is not None):
            pts.append((lab, m, g))
    if not pts:
        return None
    ms = [p[1] for p in pts if p[1] is not None]
    gs = [p[2] for p in pts if p[2] is not None]
    mx_m = max(ms) if ms else None
    mx_g = max(gs) if gs else None
    upd = c["cur_ts"] or int(c["src"]["fc"]["ok"]) or int(now)
    lm = _hyst(mem, "wind.mean", "wmean", mx_m, upd, now) if mx_m is not None else 0
    lg = _hyst(mem, "wind.gust", "wgust", mx_g, upd, now) if mx_g is not None else 0
    L = max(lm or 0, lg or 0)
    raw_now = max(level("wmean", mean) if mean is not None else 0,
                  level("wgust", gust) if gust is not None else 0)
    raw_win = max(level("wmean", mx_m) if mx_m is not None else 0,
                  level("wgust", mx_g) if mx_g is not None else 0)
    fc_only, fc_ts = False, None
    if raw_win > raw_now:
        fc_only = True
        # when the window level is first reached
        for lab, m, g in pts:
            if lab is None:
                continue
            lv = max(level("wmean", m) if m is not None else 0, level("wgust", g) if g is not None else 0)
            if lv >= raw_win:
                fc_ts = lab
                break
    g_say = mx_g if mx_g is not None else mx_m
    # the number goes into a phrase only if it supports the level by itself (hysteresis can
    # hold a level after a sharp drop, and "gusts up to 9 m/s -- hold the door" is silly)
    return {"k": "wind", "L": L, "fc_only": fc_only, "fc_ts": fc_ts,
            "g": g_say, "g_ok": raw_win >= L, "raw_now": raw_now}


def _eval_press(c, mem):
    S, now = c["S"], c["now"]
    hpa = c["ps"]
    if hpa is None:
        return None
    k = U.MM_PER_HPA
    pu = c["pu"]
    p = hpa * k
    pm3, pm24 = S.interp("surface_pressure", now - 3 * 3600), S.interp("surface_pressure", now - 86400)
    pp24 = S.interp("surface_pressure", now + 86400)
    if pp24 is None:      # the series ends before +24 h: take its last label
        last = [t for t in S.idx if t > now]
        if last:
            pp24 = S.at("surface_pressure", max(last))
    d3 = p - pm3 * k if pm3 is not None else None
    d24p = p - pm24 * k if pm24 is not None else None
    d24f = pp24 * k - p if pp24 is not None else None
    N = press_norm(c["elev"])
    dev = p - N if N is not None else None
    upd = c["cur_ts"] or int(c["src"]["fc"]["ok"]) or int(now)
    la = _hyst(mem, "press.abs", "pabs", abs(dev), upd, now) if dev is not None else 0
    l3 = _hyst(mem, "press.3h", "p3", abs(d3), upd, now) if d3 is not None else 0
    d24 = max([abs(x) for x in (d24p, d24f) if x is not None] or [0])
    l24 = _hyst(mem, "press.24h", "p24", d24, upd, now) if (d24p is not None or d24f is not None) else 0
    L = min(3, max(la, l3 or 0, l24 or 0))
    # what is stronger: the 3-hour change (on a tie it is the sharper one), the 24-hour change
    # (past or coming) or the deviation from the normal
    if l3 and l3 >= (l24 or 0) and l3 >= la:
        drv = "3h"
    elif l24 and l24 >= la:
        past = d24p is not None and (d24f is None or abs(d24p) >= abs(d24f))
        drv = "d24" if past else "fc"
    elif la:
        drv = "abs"
    else:
        drv = None
    fc_ts = None
    if drv == "fc":
        best = 0.0
        for t in sorted(S.idx):
            if now < t <= now + 86400:
                v = S.at("surface_pressure", t)
                if v is not None and abs(v * k - p) > best and (v * k - p) * d24f > 0:
                    best, fc_ts = abs(v * k - p), t
    # widget note: what sets the level; without changes -- the change over 24 hours
    if drv == "d24":
        delta = d24p
        note = T.press_note("d24", U.fmt_press_delta(delta, pu))
    elif drv == "fc":
        delta = d24f
        if fc_ts and fc_ts - now <= 18 * 3600:
            # "by 06:00" means the change by 06:00, not by the end of the day
            vf = S.at("surface_pressure", fc_ts)
            if vf is not None:
                delta = vf * k - p
            note = T.press_note("fc_at", U.fmt_press_delta(delta, pu), _hh(c, fc_ts))
        else:      # "by 05:00" a day ahead would read as this morning
            note = T.press_note("fc_day", U.fmt_press_delta(delta, pu))
    elif drv == "3h":
        delta = d3
        note = T.press_note("3h", ("+" if delta > 0 else U.MINUS) + U.fmt_press_fast(delta, pu))
    elif drv == "abs":
        delta = dev
        note = T.press_note("below" if dev < 0 else "above", U.fmt_press_delta(abs(dev), pu, sign=False))
    else:
        delta = 0.0
        if d24p is None:
            note = ""
        elif U.fmt_press_delta(d24p, pu) in ("0", "0.00"):
            note = T.press_note("same")
        else:
            note = T.press_note("d24", U.fmt_press_delta(d24p, pu))
    return {"k": "pressure", "L": L, "drv": drv, "delta": delta, "fc_ts": fc_ts, "p_mm": p,
            "note": note}


def _rad_assess(stations, mem):
    """Stations -> maximum, confirmed value and flags. Stores readings by date in mem.

    A level above the first edge needs confirmation: at least two stations at that level
    (the second highest value), or the same station at that level on two different days."""
    sts = stations
    if not sts:
        return None
    hist = mem.get("radh")
    if not isinstance(hist, dict):
        hist = {}
    ids = set()
    for s in sts:
        h = hist.get(s["id"])
        if not isinstance(h, dict):
            h = {}
        if s["date"]:
            h[s["date"]] = s["v"]
        keep = sorted((d for d in h if _pdate(d) is not None), key=_pdate)[-3:]   # last 3 dates
        hist[s["id"]] = {d: h[d] for d in keep if _num(h[d]) is not None}
        ids.add(s["id"])
    for n in list(hist):
        if n not in ids:
            del hist[n]
    mem["radh"] = hist
    vs = sorted((s["v"] for s in sts), reverse=True)
    mx = max(sts, key=lambda s: s["v"])
    conf, how = 0.0, ""
    if len(vs) >= 2:
        conf, how = vs[1], "multi"
    for s in sts:
        other = [_num(v) for d, v in hist.get(s["id"], {}).items() if d != s["date"]]
        other = [v for v in other if v is not None]
        if other:
            cv = min(s["v"], max(other))
            if cv > conf:
                conf, how = cv, "repeat"
    over = [s for s in sts if s["v"] > SCALE["rad"][0][0]]
    tss = [s["ts"] for s in sts if s["ts"] is not None]
    return {"sts": sts, "mx": mx, "conf": conf, "how": how, "near_st": sts[0], "n": len(sts),
            "over": over, "upd": max(tss) if tss else None}


def _eval_rad(c, mem):
    ra = _rad_assess(c["stations"], mem)
    if ra is None:
        return None
    now = c["now"]
    # an update is a newer reading OR revised values of the same readings (a station dropped
    # out, a value was corrected); downloading the same data again is not an update
    upd = "%s|%.3f" % (int(ra["upd"]) if ra["upd"] else int(c["src"]["rad"]["ok"]) or int(now), ra["conf"])
    lc = _hyst(mem, "rad.conf", "rad", ra["conf"], upd, now)
    lmax = min(1, level("rad", ra["mx"]["v"]))
    L = max(lc, lmax)
    single = L == 1 and len(ra["over"]) == 1
    n = ra["n"]
    if L >= 2:
        v = ra["conf"]
        if ra["how"] == "repeat":
            sub = T.RAD_REPEAT
        else:
            k = len([s for s in ra["sts"] if s["v"] >= ra["conf"]])
            sub = T.of_stations(k, n)
    elif L == 1:
        v = ra["mx"]["v"]
        if single:
            s1 = ra["over"][0]
            sub = _fit([T.of_stations(1, n) + ", " + s1["name"], "1 of %d, %s" % (n, s1["name"]),
                        T.of_stations(1, n)])
        elif ra["over"]:
            sub = T.of_stations(len(ra["over"]), n)
        else:      # the hysteresis margin holds the level; nobody above 0.20 any more
            sub = T.RAD_EDGE
    else:
        v = ra["near_st"]["v"]
        sub = T.stations_normal(n)
    return {"k": "radiation", "L": L, "val": U.fmt_rad(v), "sub": sub, "v": v, "single": single,
            "lc": lc, "newest": ra["upd"]}


def _eval_uv(c, mem):
    S, H, now, hour = c["S"], c["H"], c["now"], c["hour"]
    uv = c["uv"]
    vals = [uv] if uv is not None else []
    if 8 <= hour <= 18:
        vals += [x for x in (S.at("uv_index", H + 3600), S.at("uv_index", H + 7200)) if x is not None]
    if not vals:
        return None
    mx = max(vals)
    upd = c["cur_ts"] or int(c["src"]["fc"]["ok"]) or int(now)
    L = _hyst(mem, "uv", "uv", mx, upd, now)
    until = None
    for t in sorted(S.idx):
        if t > now and _dt(c, t).date() == c["dt"].date():
            v = S.at("uv_index", t)
            if v is not None and v > 5.5:      # edge of L2, shown as "6"
                until = t
    if until is None and mx > 5.5:
        until = H + 3600
    return {"L": L, "uv": _uv_round(mx), "until": _hh(c, until) if until else None}


def _eval_utci(c, mem):
    u = c["utci"]
    if u is None:
        return None
    now = c["now"]
    upd = c["cur_ts"] or int(c["src"]["fc"]["ok"]) or int(now)
    lh = _hyst(mem, "utci.heat", "heat", u, upd, now)
    lc = _hyst(mem, "utci.cold", "cold", -u, upd, now)
    if lh >= lc and lh > 0:
        return {"L": lh, "kind": "heat"}
    if lc > 0:
        return {"L": lc, "kind": "cold"}
    return {"L": 0, "kind": ""}


def _eval_hum(c, mem):
    if c["rh"] is None:
        return None
    upd = c["cur_ts"] or int(c["src"]["fc"]["ok"]) or int(c["now"])
    L = _hyst(mem, "dew", "dew", c["dew"], upd, c["now"]) if c["dew"] is not None else 0
    if c["rh"] < 25:
        L = max(L or 0, 1)        # very dry air
    return {"L": L or 0}


# ---------------------------------------------------------------- precipitation
def _kind(code, t, liq=None, snw=None):
    """Kind of precipitation: rain | snow | sleet | frz. liq (mm) and snw (cm) are the split
    from the fields (_wet) when there is one: Open-Meteo rewrites ICON rain codes into snow
    codes below -1 degC, and freezing rain then arrives under a snow code -- trust the fields,
    not the code. Freezing only for liquid without snow; liquid with snow at T <= 0 is sleet."""
    if code in FRZ_CODES:
        return "frz"
    snowy = code in SNOW_CODES
    if liq is not None and snw is not None and (liq >= SC["wet_min"] or snw >= SC["snow_min"]):
        wet, sn = liq >= SC["wet_min"], snw >= SC["snow_min"]
        if wet and not sn and t is not None and t <= 0:
            return "frz"
        if wet and sn:
            return "sleet"
        if sn:
            return "sleet" if (snowy and t is not None and 0.5 < t <= 2.5) else "snow"
        return "rain"
    if t is not None:
        if snowy and 0.5 < t <= 2.5:
            return "sleet"
        if t <= 0.5:
            return "snow"
    return "snow" if snowy else "rain"


def _wet(mm, r, sh, sn, code, t):
    """Precipitation (mm per hour) -> (liquid mm, snow cm, split by the fields?).

    The amount is always ``precipitation``: with the best_match model the rain/showers/
    snowfall fields disagree with it (0.1-0.4 mm of rain at 0 mm precipitation, a dry code
    and 0 % probability), so the fields only split the amount into liquid and frozen parts
    (snow in mm of water ~ cm / 0.7). Below 0.1 mm it is dry whatever the fields say. Without
    the fields (or with traces only) the split follows the code, and without a precipitation
    code the temperature, as in _kind."""
    mm = max(0.0, mm or 0.0)
    if mm < SC["wet_min"]:
        return 0.0, 0.0, r is not None or sh is not None or sn is not None
    lf = max(0.0, r or 0.0) + max(0.0, sh or 0.0)
    sf = max(0.0, sn or 0.0) / 0.7
    if lf + sf >= SC["wet_min"]:
        liq = mm * lf / (lf + sf)
        return liq, (mm - liq) * 0.7, True
    if code in SNOW_CODES:
        snowy = True
    elif code is not None and code >= 51:
        snowy = False
    else:
        snowy = t is not None and t <= SC["sleet_t"]
    return (0.0, mm * 0.7, False) if snowy else (mm, 0.0, False)


def _hour_wet(S, lab, code, t):
    """_wet for the hour with label ``lab``."""
    return _wet(S.at("precipitation", lab), S.at("rain", lab), S.at("showers", lab),
                S.at("snowfall", lab), code, t)


def _frz_fields(code, t, liq, snw):
    """Freezing rain by the fields: liquid without snow at T <= 0 under any code except drizzle."""
    return liq is not None and liq >= SC["wet_min"] and (snw or 0.0) < SC["snow_min"] and \
        t is not None and t <= 0 and code not in DRIZZLE_CODES + (56, 57)


def _cols_info(c, n=25):
    """The interval [H+i, H+i+1) takes the values labelled H+i+1: Open-Meteo reports
    precipitation for the preceding hour."""
    S, H = c["S"], c["H"]
    out = []
    for i in range(-6, n):
        start = H + i * 3600
        lab = start + 3600
        pp, mm = S.at("precipitation_probability", lab), S.at("precipitation", lab)
        code = _int(S.at("weather_code", lab))
        t = S.at("temperature_2m", lab)
        liq, snw, byf = _hour_wet(S, lab, code, t)
        if not byf:             # split by the code -> the kind by the code as well
            liq = snw = None
        wet = mm is not None and mm >= 0.3 and (pp is None or pp >= 40)
        maybe = (not wet) and pp is not None and (pp >= 50 or (pp >= 40 and (mm or 0) >= 0.1) or
                                                   ((mm or 0) >= 0.3 and pp >= 25))
        out.append({"i": i, "start": start, "lab": lab, "pp": pp, "mm": mm, "code": code, "t": t,
                    "wet": wet, "maybe": maybe, "frzf": _frz_fields(code, t, liq, snw),
                    "kind": _kind(code, t if t is not None else c["t"], liq, snw)})
    return {x["i"]: x for x in out}


def _kcond(k, extra=None):
    base = {"rain": k in ("rain", "frz"), "snow": k == "snow", "sleet": k == "sleet"}
    if extra:
        base.update(extra)
    return base


def _precip(c):
    """Precipitation facts on the hourly grid."""
    C = _cols_info(c)
    now, H = c["now"], c["H"]
    code, prec = c["code"], c["prec"]
    col0 = C[0]
    r = {"C": C, "facts": []}
    precip_code = code is not None and code >= 51
    raining = (prec is not None and prec >= 0.1) or \
        (precip_code and col0["mm"] is not None and col0["mm"] >= 0.3)
    r["raining"] = raining
    liq_now, snw_now, byf = _wet(prec, c["rain_h"], c["shw_h"], c["snow_h"], code, c["t"])
    if not byf:
        liq_now = snw_now = None
    if precip_code or (liq_now or 0) >= SC["wet_min"] or (snw_now or 0) >= SC["snow_min"]:
        knd_now = _kind(code, c["t"], liq_now, snw_now)
    else:
        knd_now = "snow" if (c["t"] is not None and c["t"] <= 0.5) else "rain"
    frz_now = code in (66, 67) or _frz_fields(code, c["t"], liq_now, snw_now)
    facts = r["facts"]

    # thunderstorm, hail, freezing rain -- now or soon
    def soon_code(codes, upto, now_also=False, col_also=None):
        if code in codes or now_also:
            return "now", None
        for i in range(0, upto + 1):
            if C[i]["code"] in codes or (col_also and C[i][col_also]):
                return ("soon0" if i == 0 else "soon"), (C[i]["start"] if i >= 1 else H + 3600)
        return None, None

    w, ts = soon_code(HAIL_CODES, 1)
    if w:
        facts.append(("hail", 0, 4, "precip", {"hh": _hh(c, ts) if ts else None},
                      {"now": w == "now", "soon0": w == "soon0", "soon": w in ("soon", "soon0")}))
    # freezing rain -- by codes 66/67 and by the fields (liquid at T <= 0 under a snow code)
    w, ts = soon_code((66, 67), 1, frz_now, "frzf")
    if w:
        facts.append(("frz_rain", 0, 4, "precip", {"hh": _hh(c, ts) if ts else None},
                      {"now": w == "now", "soon": w != "now"}))
    w, ts = soon_code((95,), 3)
    if w and not any(f[0] == "hail" for f in facts):
        facts.append(("storm", 2, 2, "precip", {"hh": _hh(c, ts) if ts else None},
                      {"now": w == "now", "soon": w != "now"}))

    if raining:
        _rain_now(c, r, C, H, now, prec, code, knd_now)
    else:
        _rain_dry(c, r, C, H, now)
    storm = [f for f in facts if f[0] in ("storm", "hail")]
    if storm:
        hh = storm[0][4].get("hh")
        r["facts"] = [f for f in facts if not (f[0] in ("rain_at", "rain_now0") and f[4].get("hh") == hh)]
    return r


def _rain_now(c, r, C, H, now, prec, code, kind):
    """Precipitating now: when it ends (the first hour below 0.1 mm)."""
    facts = r["facts"]
    end = None
    for i in range(1, 13):
        mm = C[i]["mm"]
        if mm is None:
            break
        if mm < 0.1:
            end = i
            break
    mx = max([C[i]["mm"] or 0 for i in range(0, end or 13)] + [prec or 0])
    if mx >= 30:
        facts.append(("downpour", 0, 4, "precip", {}, {"now": True}))
    elif code in SHOWER_CODES and mx >= 1.9:
        facts.append(("shower", 3, 2, "precip", {}, {"now": True}))
    elif end is not None and end <= 3:
        ets = H + end * 3600
        if 50 <= (ets - now) / 60.0 <= 70:
            facts.append(("rain_end", 3, 1, "precip", {}, _kcond(kind)))
        else:
            facts.append(("rain_end_t", 3, 1, "precip", {"hh": _hh(c, ets)}, _kcond(kind)))
    else:
        hh = _hh(c, H + end * 3600) if end else None
        facts.append(("rain_long", 3, 1 if mx < 1.9 else 2, "precip", {"hh": hh}, _kcond(kind)))
    r["dry3"] = False


def part_label(c, ev_ts, now):
    """Part-of-day phrase for an event: 'this morning', 'tomorrow morning', or 'in the
    morning' when it is said late in the evening; the night after midnight is 'tonight'."""
    e, n = _dt(c, ev_ts), _dt(c, now)
    p = T.part_of_day(e.hour)
    if e.date() > n.date() and e.hour >= 5:
        return T.part_phrase(p, "tomorrow" if n.hour < 21 else "next")
    if e.date() > n.date():
        return T.part_phrase(p, "next")
    return T.part_phrase(p, "today")


def _rain_dry(c, r, C, H, now):
    """Dry now: starts within 3 h, possible, has just ended, later within a day."""
    facts = r["facts"]
    first = None
    for i in range(0, 4):
        if C[i]["wet"]:
            first = i
            break
    if first is not None:
        span = []
        for i in range(first, 13):
            if not C[i]["wet"]:
                break
            span.append(C[i])
        mx = max(x["mm"] or 0 for x in span)
        kind = C[first]["kind"]
        shower = any(x["code"] in SHOWER_CODES for x in span) and mx >= 1.9
        hh = _hh(c, C[first]["start"]) if first >= 1 else _hh(c, H + 3600)
        rainw = T.cap(T.precip_adj(mx) + "rain")
        if mx >= 30:
            facts.append(("downpour", 0, 4, "precip", {"hh": hh}, {"soon": True}))
        elif shower and first >= 1 and kind == "rain":
            facts.append(("shower", 3, 2, "precip", {"hh": hh}, {"soon": True}))
        elif first == 0:
            q = (H + 3600 - now) <= 20 * 60
            cd = _kcond(kind, {"rain_q": kind in ("rain", "frz") and q, "snow_q": kind == "snow" and q})
            facts.append(("rain_now0", 3, 1 if mx < 1.9 else 2, "precip", {"hh": hh, "Rainw": rainw}, cd))
        else:
            facts.append(("rain_at", 3, 1 if mx < 1.9 else 2, "precip", {"hh": hh, "Rainw": rainw},
                          _kcond(kind)))
        r["dry3"] = False
        return

    mb = [i for i in range(0, 4) if C[i]["maybe"]]
    if mb:
        i0 = mb[0]
        pp = int(round(max(C[i]["pp"] or 0 for i in mb)))
        mx = max(C[i]["mm"] or 0 for i in mb)
        kind = C[i0]["kind"]
        adj = T.precip_adj(mx, heavy=False)
        facts.append(("rain_maybe", 3, 1, "precip",
                      {"hh": _hh(c, C[i0]["start"]) if i0 >= 1 else None, "pp": str(pp),
                       "Rainw": T.cap(adj + "rain"), "Snoww": T.cap(adj + "snow")}, _kcond(kind)))
        r["dry3"] = False
        return

    r["dry3"] = all((C[i]["mm"] or 0) < 0.1 for i in range(0, 4))
    # has just ended
    past = C[-1]
    if past["mm"] is not None and past["mm"] >= 0.3 and r["dry3"]:
        facts.append(("rain_over", 3, 1, "precip", {},
                      {"rain": past["kind"] in ("rain", "frz"), "snow": past["kind"] in ("snow", "sleet")}))
    # later, within a day
    for i in range(4, 24):
        x = C[i]
        if x["pp"] is None:
            continue
        if x["wet"] or x["pp"] >= 50:
            pl = part_label(c, x["start"], now)
            block = [C[j] for j in range(i, min(24, i + 6))
                     if part_label(c, C[j]["start"], now) == pl and C[j]["pp"] is not None]
            pp = int(round(max(b["pp"] for b in block)))
            kind = x["kind"]
            hi = pp >= 75
            facts.append(("rain_later", 3, 1, "precip", {"part": pl, "pp": str(pp)},
                          {"rain_hi": kind in ("rain", "frz") and hi,
                           "rain_mid": kind in ("rain", "frz") and not hi,
                           "snow_hi": kind == "snow" and hi, "snow_mid": kind == "snow" and not hi,
                           "sleet": kind == "sleet"}))
            break


# ---------------------------------------------------------------- status-line facts
def _fact(key, P, L, cat, params=None, conds=None, bank=None):
    return {"key": key, "P": P, "L": L, "cat": cat, "params": params or {}, "conds": conds or {},
            "bank": bank or key}


def _facts(c, ev, fresh):
    F = []
    now, hour = c["now"], c["hour"]
    tu, wu, pu = c["tu"], c["wu"], c["pu"]

    # P1 -- source failures
    for k, sk in (("wx", "fc"), ("air", "air"), ("rad", "rad")):
        st = fresh[k]["st"]
        if st in ("stale", "dead"):
            F.append(_fact(k + "_stale", 1, 0, k, {"ago": T.ago(_data_age(c, fresh, k) or 0)}))
        elif st == "none" and c["src"][sk]["fail"] >= 2:
            F.append(_fact(k + "_none", 1, 0, k))
    for pid, label in c["cams"]:
        cm = fresh["cam"].get(pid) or {}
        if cm.get("st") == "dead":
            a = cm["age"]
            ts = now - a if a is not None else None
            # "since 15:00" without a date reads as today; yesterday's failure -> "for 19 hours"
            today = ts is not None and _dt(c, ts).date() == c["dt"].date()
            F.append(_fact("cam_" + pid, 1, 0, "cam",
                           {"cam": T.cap(label), "hm": _hm(c, ts) if ts else None,
                            "ago": T.ago(a) if a is not None else None},
                           {"today": today, "long": not today}, bank="cam_dead"))

    wx_use = c["has_fc"] and fresh["wx"]["st"] != "dead"

    def usable(k, L):
        st = fresh[k]["st"]
        return st != "dead" and not (st == "stale" and (L or 0) <= 1)

    t, utci = c["t"], c["utci"]
    ut, uv, wind, press = ev.get("utci"), ev.get("uv"), ev.get("wind"), ev.get("press")
    air, rad = ev.get("air"), ev.get("rad")
    pr = ev.get("precip") or {"facts": [], "C": {}, "dry3": False}
    utxt = U.fmt_temp(c["utci_f"], tu)

    if wx_use:
        # UTCI
        if ut and ut["L"] >= 2 and utci is not None:
            key = ut["kind"] + str(ut["L"])
            F.append(_fact(key, 0 if ut["L"] >= 4 else 2, ut["L"], "heat", {"utci": utxt}))
        # wind
        if wind and wind["L"] >= 1:
            L = wind["L"]
            g = U.fmt_wind(wind["g"], wu) if (wind["g"] is not None and wind.get("g_ok", True)) else None
            hh = _hh(c, wind["fc_ts"]) if wind["fc_ts"] else None
            prm = {"g": g, "wu": wu, "hh": hh}
            if L >= 4:
                F.append(_fact("wind_crit", 0, 4, "wind", prm,
                               {"now": not wind["fc_only"], "fc": wind["fc_only"]}))
            elif wind["fc_only"] and hh:
                F.append(_fact("wind_rise", 2 if L >= 2 else 4, L, "wind", prm))
            else:
                F.append(_fact("wind%d" % L, 2 if L >= 2 else 4, L, "wind", prm))
        # UV -- only a line; silent in rain; moderate only when it is warm and skin is bare;
        # silent in the dark too (the hysteresis may still hold the level)
        if uv and uv["L"] >= 1 and not pr.get("raining") and (uv["L"] >= 2 or (t is not None and t >= 18)) \
                and (c["day"] == 1 or (c["uv"] or 0) >= 1):
            L = uv["L"]
            cd = {"morning": hour < 12, "afternoon": hour >= 15}
            F.append(_fact("uv%d" % L, 0 if L >= 4 else 2 if L >= 2 else 4, L, "uv",
                           {"uv": str(uv["uv"]), "hh": uv["until"]}, cd))
        # pressure
        if press and press["L"] >= 1:
            L, drv, dl = press["L"], press["drv"], press["delta"]
            cd = {"fall": dl < 0, "rise": dl > 0, "low": dl < 0, "high": dl > 0}
            p_txt = U.fmt_press(press["p_mm"], pu)
            if L >= 2:
                if drv == "d24":
                    F.append(_fact("press_d24", 2, L, "press",
                                   {"dp": U.fmt_press_delta(abs(dl), pu, sign=False), "pu": pu}, cd))
                elif drv == "fc":
                    ft = press["fc_ts"]
                    when = T.WHEN[None]
                    if ft and ft - now <= 18 * 3600:
                        # "by afternoon" covers 16:00 too; "by noon" would not be true
                        when = T.WHEN[T.part_of_day(_dt(c, ft).hour)]
                    F.append(_fact("press_fc", 2, L, "press",
                                   {"dp": U.fmt_press_delta(abs(dl), pu, sign=False), "pu": pu,
                                    "when": when}, cd))
                elif drv == "3h":
                    F.append(_fact("press_3h", 2, L, "press", {"dp3": U.fmt_press_fast(dl, pu), "pu": pu}, cd))
                else:
                    F.append(_fact("press_abs", 2, L, "press", {"p": p_txt, "pu": pu}, cd))
            else:
                if drv == "abs":
                    cd = {"low": dl < 0, "high": dl > 0}
                else:
                    cd = {"fall": dl < 0, "rise": dl > 0}
                F.append(_fact("press1", 4, 1, "press", {"p": p_txt, "pu": pu}, cd))
        # precipitation, thunderstorm, hail
        for key, P, L, cat, params, cd in pr["facts"]:
            F.append(_fact(key, P, L, "hazard" if P == 2 else cat, params, cd))
        # black ice
        C = pr.get("C") or {}
        # sum in mm: 6 past hours + the current interval (not mm/h, or light rain counts x4)
        past6 = sum((C[i]["mm"] or 0) for i in range(-6, 0) if i in C) + max(0.0, c["prec15"] or 0)
        if t is not None and -3 <= t <= 1 and (past6 >= 0.5 or c["code"] in FRZ_CODES):
            wasrain = t < 0 and any((C[i]["mm"] or 0) >= 0.3 and (C[i]["t"] if C[i]["t"] is not None
                                                                    else 99) > 0.5
                                    for i in range(-6, 0) if i in C)
            F.append(_fact("ice", 2, 2, "hazard", {}, {"wasrain": wasrain}))
        # fog: code 45/48 and visibility below 1 km
        vis = c["vis"]
        if c["code"] in (45, 48) and (vis is None or vis < 1000):
            vtxt, vu = U.fmt_vis(vis, wu)
            if vis is not None and vis < 200:
                F.append(_fact("fog_dense", 2, 2, "hazard", {"vis": vtxt, "vu": vu}))
            else:
                F.append(_fact("fog", 4, 1, "other", {"vis": vtxt, "vu": vu}))
        # muggy, raw
        if c["dew"] is not None and t is not None and c["dew"] >= 20 and t >= 22:
            F.append(_fact("muggy", 4, 1, "other"))
        if c["rh"] is not None and t is not None and c["wind"] is not None and \
                c["rh"] >= 85 and 0 < t <= 7 and c["wind"] >= 4:
            F.append(_fact("raw", 4, 1, "other", {"t": U.fmt_temp(t, tu)}))
        # night frost and a big daily swing
        tmin_n = ev.get("tmin_night")
        if t is not None and tmin_n is not None and tmin_n <= 0 and t > 3:
            F.append(_fact("frost", 4, 1, "other", {"tmin": U.fmt_temp(tmin_n, tu)},
                           {"evening": hour >= 9, "night": hour < 9}))
        if t is not None and c["tmax"] is not None and c["tmin"] is not None and hour < 12 \
                and c["tmax"] - c["tmin"] >= 12 and c["tmax"] - t >= 8:
            F.append(_fact("swing", 4, 1, "other", {"tmax": U.fmt_temp(c["tmax"], tu)}))

    # air
    if air and air["L"] >= 1 and usable("air", air["L"]):
        L, cu = air["L"], air["culprit"]
        # AQI goes into the phrase only if the index itself is at this level: "Poor air, AQI 45"
        # contradicts the scale (45 is "moderate") when particles set the level
        aqi_ok = air.get("aqi_L") is not None and air["aqi_L"] >= L
        prm = {"aqi": str(air["aqi"]) if (air["aqi"] is not None and aqi_ok) else None,
               "pm10": str(air["pm10"]) if (air["pm10"] is not None and
                                            level("pm10", air["pm10"]) >= min(L, 2)) else None}
        if L >= 4:
            F.append(_fact("air_crit", 0, 4, "air", prm))
        elif L >= 2:
            key = "dust2" if (cu == "pm10" and air["dust"]) else cu if cu in ("o3", "no2") \
                else "air%d" % L
            F.append(_fact(key, 2, L, "air", prm))
        else:
            F.append(_fact("air1", 4, 1, "air", prm))
    # radiation
    if rad and rad["L"] >= 1 and usable("rad", rad["L"]):
        L = rad["L"]
        prm = {"v": U.fmt_rad(rad["v"])}
        if L >= 3:
            F.append(_fact("rad_crit", 0, L, "rad", prm))
        elif L == 2:
            F.append(_fact("rad2", 2, 2, "rad", prm))
        else:
            F.append(_fact("rad1", 4, 1, "rad", prm, {"single": rad["single"], "multi": not rad["single"]}))

    F = _combos(F, c, ev)

    # P5 -- calm, when nothing more important (except failures) is there
    if wx_use and t is not None and not any(f["P"] in (0, 2, 3, 4) for f in F):
        F.append(_calm(c, ev, fresh, pr))
    if not c["has_fc"] and not any(f["key"].startswith("wx_") for f in F):
        F.append(_fact("wx_wait", 5, 0, "other"))
    return F


def _combos(F, c, ev):
    """A combination absorbs its parts and takes the best class among them."""
    by = {}
    for f in F:
        by.setdefault(f["key"], f)

    def take(keys):
        return [by.pop(k) for k in keys if k in by]

    def best(parts, P):
        return min([P] + [p["P"] for p in parts])

    def maxl(parts, L):
        return max([L] + [p["L"] for p in parts])

    new = []
    air, wind, uv, ut = ev.get("air"), ev.get("wind"), ev.get("uv"), ev.get("utci")
    t, utci = c["t"], c["utci"]
    utxt = U.fmt_temp(c["utci_f"], c["tu"])
    if (ev.get("precip") or {}).get("raining") or (c["day"] != 1 and (c["uv"] or 0) < 1):
        uv = None
    # dust storm: air L>=3 + dust + wind L>=2
    if air and wind and air["L"] >= 3 and air["dust"] and wind["L"] >= 2:
        parts = take(["air_crit", "air3", "air2", "dust2", "o3", "no2", "wind2", "wind3", "wind_rise"])
        new.append(_fact("dust_storm", 0, maxl(parts, 3), "air"))
    # heat and UV
    if ut and uv and ut["kind"] == "heat" and 2 <= ut["L"] <= 3 and uv["L"] >= 2 and uv["L"] < 4:
        parts = take(["heat2", "heat3", "uv2", "uv3"])
        new.append(_fact("heat_uv", best(parts, 2), maxl(parts, 2), "heat",
                         {"utci": utxt, "uv": str(uv["uv"]), "hh": uv["until"]}))
    # hot and dry while UTCI shows no heat (L<=1)
    elif t is not None and uv and t >= 28 and c["dew"] is not None and c["dew"] < 14 \
            and uv["uv"] >= 6 and uv["L"] < 4 and (ut is None or ut["kind"] != "heat" or ut["L"] <= 1):
        parts = take(["uv2", "uv3", "uv1"])
        new.append(_fact("hot_dry_uv", best(parts, 4), maxl(parts, 1), "heat"))
    # cold wind
    if t is not None and utci is not None and c["wind"] is not None and t <= 5 and c["wind"] >= 6 \
            and ut and ut["L"] < 4 and ((ut["kind"] == "cold" and ut["L"] >= 2) or utci <= t - 6):
        parts = take(["cold2", "cold3", "wind1"])
        strong = ut["kind"] == "cold" and ut["L"] >= 2
        new.append(_fact("cold_wind", best(parts, 2 if strong else 4), maxl(parts, 1), "heat",
                         {"utci": utxt}))
    # freezing rain already talks about the ice rink
    if "frz_rain" in by and "ice" in by:
        take(["ice"])
    # fog and black ice
    if ("fog" in by or "fog_dense" in by) and "ice" in by:
        parts = take(["fog", "fog_dense", "ice"])
        new.append(_fact("fog_ice", best(parts, 2), maxl(parts, 2), "hazard"))
    kept = [f for f in F if by.get(f["key"]) is f]
    return kept + new


def _calm(c, ev, fresh, pr):
    t, hour, cloud = c["t"], c["hour"], c["cloud"]
    C = pr.get("C") or {}

    def quiet(i):
        # promise dryness only if the chart is white/green too (< 25 %)
        x = C[i]
        return not x["wet"] and not x["maybe"] and (x["pp"] is None or x["pp"] < 25)

    end_day = [i for i in C if i >= 0 and _dt(c, C[i]["start"]).date() == c["dt"].date()]
    dry_day = all(quiet(i) for i in end_day)
    dry_to18 = all(quiet(i) for i in end_day if _dt(c, C[i]["start"]).hour < 18)
    dry3 = all(quiet(i) for i in range(0, 4) if i in C)
    known24 = [i for i in range(0, 24) if i in C and C[i]["pp"] is not None]
    dry24 = len(known24) >= 20 and all(quiet(i) for i in known24)
    air, rad, ut = ev.get("air"), ev.get("rad"), ev.get("utci")
    air0 = air is not None and air["L"] == 0 and fresh["air"]["st"] == "ok"
    rad0 = rad is not None and rad["L"] == 0 and fresh["rad"]["st"] == "ok"
    gust = c["gust"]
    tmin_n = ev.get("tmin_night")
    conds = {
        "still": gust is not None and gust < 5 and (c["wind"] or 0) < 3,
        "dry_day": 12 <= hour < 22 and dry_day,
        "cloudy_dry": cloud is not None and cloud > 70 and hour < 16 and dry_to18,
        "walk": ut is not None and ut["L"] == 0 and c["day"] == 1 and 9 <= hour < 20 and
        (air is None or air["L"] == 0),
        "airing": air0 and t >= 15 and (gust or 0) < 8,
        "clean": air0 and rad0,
        "morning": 5 <= hour <= 10 and t < 15,
        "clear_eve": 17 <= hour <= 22 and cloud is not None and cloud < 30 and dry3,
        "late": (hour >= 21 or hour < 3) and tmin_n is not None,
        "clear_night": (hour >= 23 or hour < 5) and cloud is not None and cloud < 30 and dry3,
        "frosty": t <= -3,
        "warm": t >= 20,
        "cool": 0 < t < 12,
        "dry24": dry24,
        "grey": cloud is not None and cloud > 80 and (gust or 0) < 8,
        "sunny": c["day"] == 1 and cloud is not None and cloud < 30 and (c["code"] or 0) <= 1,
    }
    return _fact("calm", 5, 0, "other", {"t": U.fmt_temp(t, c["tu"]), "tmin": U.fmt_temp(tmin_n, c["tu"])},
                 conds)


# ---------------------------------------------------------------- choosing the text
class _Params(dict):
    def __missing__(self, k):
        raise KeyError(k)


def fmt_template(tpl, params):
    """Fill a template; None when a placeholder has no value."""
    p = _Params()
    for k, v in params.items():
        if v is not None:
            p[k] = v
    try:
        s = string.Formatter().vformat(tpl, (), p)
    except (KeyError, IndexError, ValueError, AttributeError):
        return None
    s = " ".join(s.split())
    return T.cap(s) if s else None


def _pick(mem, f, now_dt):
    """Phrase variant: start index crc32(date:key) + window number (2 h; 3 h for P5). Within a
    window a key keeps its variant. P5 variants are not repeated within a day."""
    key, P = f["key"], f["P"]
    variants = T.BANK.get(f["bank"]) or []
    date = "%04d-%02d-%02d" % (now_dt.year, now_dt.month, now_dt.day)
    win = now_dt.hour // (3 if P == 5 else 2)
    wid = "%s:%d" % (date, win)
    picks = mem.setdefault("pick", {})
    said = mem.setdefault("said", {"d": date, "k": []})
    if said.get("d") != date or not isinstance(said.get("k"), list):
        said["d"], said["k"] = date, []
    n = len(variants)

    def text(i, check_said):
        tpl, cond = variants[i]
        if cond and not f["conds"].get(cond):
            return None
        s = fmt_template(tpl, f["params"])
        if s is None or len(s) > MAXLEN:
            return None
        if check_said and P == 5 and ("%s:%d" % (key, i)) in said["k"]:
            return None
        return s

    prev = picks.get(key)
    if isinstance(prev, dict) and prev.get("w") == wid and isinstance(prev.get("i"), int) \
            and 0 <= prev["i"] < n:
        s = text(prev["i"], False)
        if s:
            return s
    if n:
        start = zlib.crc32(("%s:%s" % (date, key)).encode("utf-8")) + win
        for check in (True, False):
            for k in range(n):
                i = (start + k) % n
                s = text(i, check)
                if s:
                    picks[key] = {"w": wid, "i": i}
                    if P == 5 and ("%s:%d" % (key, i)) not in said["k"]:
                        said["k"].append("%s:%d" % (key, i))
                    return s
    for fb in T.FALLBACK.get(f["bank"]) or T.FALLBACK.get(key) or []:
        s = fmt_template(fb, f["params"])
        if s and len(s) <= MAXLEN:
            return s
    return None


def _sortkey(f):
    if f["P"] == 1:
        pr = FAIL_ORDER.index(f["cat"]) if f["cat"] in FAIL_ORDER else len(FAIL_ORDER)
    else:
        pr = PRIO_LINE.index(f["cat"]) if f["cat"] in PRIO_LINE else len(PRIO_LINE)
    return (f["P"], -f["L"], pr)


def _status(c, mem, F):
    F = sorted(F, key=_sortkey)
    now = c["now"]
    # the first line is held for 10 minutes unless a fact of a more urgent class appears
    top = mem.get("top") if isinstance(mem.get("top"), dict) else {}
    if F:
        keys = [f["key"] for f in F]
        old = top.get("k")
        if old in keys and old != F[0]["key"] and isinstance(top.get("t"), (int, float)) \
                and now - top["t"] < HOLD_TOP:
            fo = F[keys.index(old)]
            if fo["P"] <= F[0]["P"]:
                F.remove(fo)
                F.insert(0, fo)
        if F[0]["key"] != old:
            mem["top"] = {"k": F[0]["key"], "t": int(now), "P": F[0]["P"]}
    else:
        mem.pop("top", None)
    out = []
    for f in F:
        if len(out) >= 3:
            break
        s = _pick(mem, f, c["dt"])
        if not s:
            continue
        out.append({"text": s, "tone": TONE[f["P"]], "key": f["key"], "P": f["P"], "L": f["L"]})
    return out


# ---------------------------------------------------------------- scene: now and 3 hours ahead
def _ge(v, edge):
    return v is not None and v >= edge


def _past(c, end, n):
    """Hours labelled end, end - 1 h, ... (n of them): [(t, liquid mm, snow cm)], known only."""
    S, out = c["S"], []
    for j in range(n):
        lab = end - j * 3600
        t, code = S.at("temperature_2m", lab), _int(S.at("weather_code", lab))
        if t is None and code is None and S.at("precipitation", lab) is None:
            continue
        liq, snw, _ = _hour_wet(S, lab, code, t)
        out.append((t, liq, snw))
    return out


def _x_hist(c, fresh, end):
    """Scene inputs shared by "now" and a forecast hour: history up to label ``end``, snow on
    the ground, air (no hourly air series -- the current reading, unless it is dead)."""
    h12, h24 = _past(c, end, SC["past_h"]), _past(c, end, 24)
    t12 = [h[0] for h in h12 if h[0] is not None]
    t24 = [h[0] for h in h24 if h[0] is not None]
    air_ok = c["has_air"] and fresh["air"]["st"] != "dead"
    return {"rain12": sum(h[1] for h in h12), "snow12": sum(h[2] for h in h12),
            "tmax12": max(t12) if t12 else None, "tmax24": max(t24) if len(t24) >= 20 else None,
            "depth": c["depth"], "dust": c["a_dust"] if air_ok else None,
            "pm25": c["a"]["pm25"] if air_ok else None, "pm10": c["a"]["pm10"] if air_ok else None}


def _x_now(c, ev, fresh):
    liq, snw, _ = _wet(c["prec"], c["rain_h"], c["shw_h"], c["snow_h"], c["code"], c["t"])
    w = ev.get("wind")
    x = {"code": c["code"], "t": c["t"], "rh": c["rh"], "liq": liq, "snw": snw,
         "cloud": c["cloud"], "vis": c["vis"], "gust": c["gust"], "wind": c["wind"], "day": c["day"],
         "utci": c["utci"],
         # the icon is about "now": wind expected in 3 h does not change it; strong wind now
         # is "wind" even if it gets stronger later
         "windy": bool(w and w["L"] >= 2 and (not w.get("fc_only") or (w.get("raw_now") or 0) >= 2))}
    x.update(_x_hist(c, fresh, c["H"]))
    return x


def _x_hour(c, fresh, lab):
    """Scene inputs for the hour [lab - 1 h, lab) from the hourly series; None without data."""
    S = c["S"]
    code, t = _int(S.at("weather_code", lab)), S.at("temperature_2m", lab)
    if code is None and t is None:
        return None
    liq, snw, _ = _hour_wet(S, lab, code, t)
    gust, wind = S.at("wind_gusts_10m", lab), S.at("wind_speed_10m", lab)
    lv = max(level("wgust", gust) if gust is not None else 0, level("wmean", wind) if wind is not None else 0)
    x = {"code": code, "t": t, "rh": S.at("relative_humidity_2m", lab), "liq": liq, "snw": snw,
         "cloud": S.at("cloud_cover", lab), "vis": S.at("visibility", lab), "gust": gust, "wind": wind,
         "day": _is_light(c, lab - 1800), "utci": None, "windy": lv >= 2}
    x.update(_x_hist(c, fresh, lab - 3600))
    return x


def _clouds(x):
    """Cloudiness: codes 0-3; otherwise (fog with good visibility, an unknown code) from
    cloud_cover the way Open-Meteo derives a code for models without one: <20/<50/<80/>=80 %."""
    code, cl = x["code"], x["cloud"]
    dn = "-night" if x["day"] == 0 else "-day"
    if code in (0, 1, 2, 3):
        base = code
    elif cl is not None:
        base = 0 if cl < 20 else 1 if cl < 50 else 2 if cl < 80 else 3
    elif code in (45, 48):
        base = 3                    # fog without cloud data -> overcast
    else:
        return "not-available"
    if base == 3:
        return "overcast"
    if base == 2:
        return ("mostly-cloudy" if _ge(cl, SC["mostly_cloudy"]) else "partly-cloudy") + dn
    return ("mostly-clear" if base == 1 else "clear") + dn


def scene_of(x):
    """Inputs of one moment -> scene name. The order of the checks is the priority: the
    dangerous and the more specific first."""
    code, t, gust, vis = x["code"], x["t"], x["gust"], x["vis"]
    if code is None and t is None:
        return "not-available"
    liq, snw = x["liq"] or 0.0, x["snw"] or 0.0
    sn = snw >= SC["snow_min"]
    dn = "-night" if x["day"] == 0 else "-day"
    clearing = x["cloud"] is None or x["cloud"] < SC["clearing"]
    # 1. thunderstorms: hail, severe (97, or 95 with gusts of level L3), ordinary
    if code in HAIL_CODES:
        return "thunderstorm-hail"
    if code == 97 or (code == 95 and _ge(gust, SC["storm_gust"])):
        return "thunderstorm-heavy"
    if code == 95:
        return "thunderstorm"
    # 2. freezing precipitation -- by code and by the fields: liquid without snow at T <= 0
    #    also comes under a snow code; liquid together with snow is sleet (step 3)
    rainy = liq >= SC["wet_min"] or (code in LIQUID_CODES and not sn)
    if code in (66, 67):
        return "freezing-rain"
    if code in (56, 57):
        return "freezing-drizzle"
    if rainy and not sn and t is not None and t <= 0:
        return "freezing-drizzle" if code in DRIZZLE_CODES else "freezing-rain"
    # 3. mixed: rain with snow; a snow code above zero with liquid precipitation -> sleet
    if rainy and (sn or (code in SNOW_CODES and t is not None and t > SC["sleet_t"])):
        return "sleet"
    # 4. snow: blizzard, heavy, grains, showers with clear spells, ordinary. Visibility marks
    #    heavy snow only when snow lowers it: not fog 45/48 and not a trace of snow without a code
    if sn or code in SNOW_CODES:
        if _ge(gust, SC["blizzard_gust"]):
            return "blizzard"
        vis_snow = vis is not None and vis < SC["snow_heavy_vis"] and code not in (45, 48) and \
            (code in SNOW_CODES or snw >= SC["snow_vis_min"])
        if code in (75, 86) or snw / 0.7 >= SC["snow_heavy"] or vis_snow:
            return "heavy-snow"
        if code == 77:
            return "snow-grains"
        if code == 85 and clearing:
            return "snow-showers" + dn
        return "snow"
    # 5. rain: heavy (code, mm/h or the 12-hour sum -- but not when only drizzle is left),
    #    showers with clear spells, drizzle, ordinary
    if rainy:
        if code in (65, 82) or liq >= SC["rain_heavy"] or \
                (x["rain12"] >= SC["rain_12h"] and code not in DRIZZLE_CODES):
            return "heavy-rain"
        if code in (80, 81) and clearing:
            return "showers" + dn
        if code in DRIZZLE_CODES:
            return "drizzle"
        return "rain"
    # 6. no precipitation -- danger underfoot and in the air
    if _ge(x["dust"], SC["dust_storm"]) and _ge(gust, SC["dust_storm_gust"]):
        return "dust-storm"
    if t is not None and t <= 0 and (x["rain12"] >= SC["ice_wet"] or
                                     (_ge(x["depth"], SC["drift_depth"]) and x["tmax12"] is not None and
                                      x["tmax12"] > 0)):
        return "ice"            # it was wet (rain or a thaw on snow), now it is at or below zero
    lying = x["snow12"] >= SC["drift_fresh"] or \
        (_ge(x["depth"], SC["drift_depth"]) and x["tmax24"] is not None and x["tmax24"] <= 0)
    if t is not None and t < 0 and lying and _ge(gust, SC["drift_gust"]):
        return "drifting-snow"  # loose snow not crusted by a thaw, and the wind lifts it
    if x["windy"] and (code is None or code < 51):
        return "wind"
    # fog only with visibility below 1 km: ICON reports code 45 even at 24 km
    if code in (45, 48) and (vis is None or vis < SC["fog_vis"]):
        return "rime-fog" if (code == 48 or (t is not None and t < 0)) else "fog"
    if _ge(x["dust"], SC["dust_haze"]) and (x["pm10"] is None or x["pm10"] >= SC["dust_haze"]):
        return "dust-haze"
    pm25, pm10 = x["pm25"], x["pm10"]
    # smoke -- carefully: a false alarm is worse than a miss, so both dust and PM10 are needed
    if _ge(pm25, SC["smoke_pm25"]) and x["dust"] is not None and x["dust"] < SC["smoke_dust"] and \
            pm10 is not None and pm10 > 0 and pm25 >= SC["smoke_ratio"] * pm10:
        return "smoke"
    if t is not None and (t <= SC["frost_t"] or _ge(-x["utci"] if x["utci"] is not None else None,
                                                     -SC["frost_utci"])):
        return "frost"
    if t is not None and (t >= SC["heat_t"] or _ge(x["utci"], SC["heat_utci"])):
        return "heat"
    if _ge(t, SC["dry_t"]) and x["rh"] is not None and x["rh"] <= SC["dry_rh"] and _ge(x["wind"], SC["dry_wind"]):
        return "dry-wind"
    if vis is not None and x["rh"] is not None and SC["fog_vis"] <= vis < SC["haze_vis"] and x["rh"] < SC["haze_rh"]:
        return "haze"
    # 7. cloudiness
    return _clouds(x)


def _scene_now(c, ev, fresh):
    if not c["has_fc"] or fresh["wx"]["st"] == "dead":
        return "not-available"
    return scene_of(_x_now(c, ev, fresh))


def _ahead(c, fresh, cur):
    """Scenes of the next 3 hours from the hourly series (unique, without the current one):
    the server preloads photos for them."""
    if not c["has_fc"] or fresh["wx"]["st"] == "dead":
        return []
    out = []
    for i in range(1, 5):
        lab = c["H"] + i * 3600
        if lab - 3600 >= c["now"] + 3 * 3600:      # the hour starts more than 3 h from now
            break
        x = _x_hour(c, fresh, lab)
        sc = scene_of(x) if x else None
        if sc and sc not in (cur, "not-available") and sc not in out:
            out.append(sc)
    return out


# ---------------------------------------------------------------- condition word
def utci_category(u):
    """UTCI assessment-scale category id."""
    for lo, incl, cid in T.UTCI_BANDS:
        if (u >= lo) if incl else (u > lo):
            return cid
    return T.UTCI_BOTTOM


def condition_id(u, rh, t=None, wind=None, scene=None):
    """Condition category: the UTCI scale refined by the data. Below zero in the shade cold
    is called frost; raw (damp, windy, 0..7 degC) and damp (fog or drizzle above zero) on the
    cool side; comfortable below UTCI 15 is fresh; warm is refined by humidity, and humid
    with no wind is muggy."""
    cat = utci_category(u)
    frost = t is not None and t <= 0
    raw = (t is not None and 0 < t <= 7 and rh is not None and rh >= 85
           and wind is not None and wind >= 4)
    if cat == "warm" and rh is not None:
        if rh >= 60:
            return "muggy" if wind is not None and wind < 2 else "hot_humid"
        if rh <= 30:
            return "hot_dry"
        return cat
    if cat == "very_cold":
        return "hard_frost" if frost else cat
    if cat == "bitter_cold":
        return "severe_frost" if frost else cat
    if cat == "cold":
        return "frosty" if frost else "raw" if raw else cat
    if cat == "cool":
        if raw:
            return "raw"
        return "damp" if scene in WET_SCENES and t is not None and t > 0 else cat
    if cat == "comfortable" and u < 15:
        return "damp" if scene in WET_SCENES else "fresh"
    return cat


def utci_word(u, rh, t=None, wind=None, scene=None):
    return T.WORDS[condition_id(u, rh, t, wind, scene)]


def _now_block(c, ev, fresh):
    scene = _scene_now(c, ev, fresh)
    ok = scene != "not-available" or (c["has_fc"] and fresh["wx"]["st"] != "dead" and c["t"] is not None)
    t = c["t"] if ok else None
    u = c["utci"] if (ok and t is not None) else None
    ut = ev.get("utci") or {}
    if u is not None:
        word = utci_word(u, c["rh"], t, c["wind"], scene)
    else:               # no UTCI -- the scene title
        word = T.SCENE_TITLES.get(scene) or T.SCENE_TITLES["not-available"]
    tu = c["tu"]
    return {"scene": scene, "day": c["day"] if ok else None,
            "utci": u, "utciTxt": U.fmt_deg(c["utci_f"] if u is not None else None, tu), "word": word,
            "L": ut.get("L") if u is not None else None,
            "t": round(t, 1) if t is not None else None, "tTxt": U.fmt_deg(t, tu),
            "stale": fresh["wx"]["st"] != "ok", "ahead": _safe(lambda: _ahead(c, fresh, scene), [])}


# ---------------------------------------------------------------- widgets
def _w(k, L, val, unit, note):
    return {"k": k, "name": T.WIDGET_NAMES[k], "L": L, "val": val, "unit": unit,
            "note": _fit([note or ""])}


def _w_none(k, note=T.NO_DATA):
    return {"k": k, "name": T.WIDGET_NAMES[k], "L": None, "val": u"—", "unit": "", "note": note}


def _since(c, when):
    """'as of 12:30' (today) or 'as of 6 Oct'; ``when`` is unix time or a date."""
    if when is None:
        return T.NO_DATA
    if isinstance(when, (int, float)):
        d = _dt(c, when)
        if d.date() == c["dt"].date():
            return T.as_of_time(d.hour, d.minute)
        when = d.date()
    return T.as_of_date(when)


def _w_fresh(c, fresh, sk, w, when):
    """Dead data: a dash and the reading time; stale: the same, but levels from 2 stay
    visible -- a dangerous value keeps its place and the note shows the reading time."""
    st = fresh[sk]["st"]
    if st == "dead" or (st == "stale" and not (isinstance(w["L"], int) and w["L"] >= 2)):
        return _w_none(w["k"], _since(c, when))
    if st == "stale":
        w["note"] = _since(c, when)
    return w


def _src_ts(c, ts, sk):
    return ts or (int(c["src"][sk]["ok"]) if c["src"][sk]["ok"] > 0 else None)


def _wind_dir(deg):
    return T.DIRECTIONS[int((deg % 360 + 22.5) // 45) % 8] if deg is not None else None


def _w_wind(c, ev, fresh):
    w = ev.get("wind")
    mean, gust = c["wind"], c["gust"]
    if not w or mean is None:
        return _w_none("wind")
    wu = c["wu"]
    dname = T.CALM if mean < 0.5 else _wind_dir(c["wdir"])
    if w["fc_only"] and w["fc_ts"] and w["g"] is not None:
        # the forecast sets the level: the note explains an orange dot over "3 m/s"
        note = T.gusts_by(U.fmt_wind(w["g"], wu), _hh(c, w["fc_ts"]))
    elif gust is not None:
        note = T.gusts(U.fmt_wind(gust, wu)) + (", " + dname if dname else "")
    else:
        note = dname or ""
    return _w_fresh(c, fresh, "wx", _w("wind", w["L"], U.fmt_wind(mean, wu), wu, note),
                    _src_ts(c, c["cur_ts"], "fc"))


def _w_air(c, ev, fresh):
    a = ev.get("air")
    if not a:
        return _w_none("air")
    L, cu = a["L"], a["culprit"]
    if L == 0:
        note = T.air_note(None, 0)
    elif cu and (a["lv"].get(cu) or 0) >= 1:
        note = T.air_note(cu)
    else:
        note = T.air_note(None, L)
    val = str(a["aqi"]) if a["aqi"] is not None else u"—"
    return _w_fresh(c, fresh, "air", _w("air", L, val, "AQI", note), _src_ts(c, c["air_ts"], "air"))


def _uv_round(v):
    return _rnd(max(0.0, v))


def _w_uv(c, fresh):
    uv = c["uv"]
    if not c["has_fc"] or uv is None:
        return _w_none("uv")
    n = _uv_round(uv)
    L = 0 if n <= 2 else 1 if n <= 5 else 2 if n <= 7 else 3 if n <= 10 else 4     # WHO scale
    # daily maximum: after sunset tomorrow's; at night before sunrise already today's
    tomorrow = c["day"] == 0 and c["hour"] >= 12
    d = c["dt"].date() + timedelta(days=1 if tomorrow else 0)
    mx = c["uvmax"].get(d)
    if mx is None:
        S = c["S"]
        hv = [S.at("uv_index", t) for t in S.idx if _dt(c, t).date() == d]
        hv = [v for v in hv if v is not None]
        mx = max(hv) if hv else None
    if mx is not None and not tomorrow:
        mx = max(mx, uv)
    note = T.uv_peak(_uv_round(mx), tomorrow) if mx is not None else ""
    return _w_fresh(c, fresh, "wx", _w("uv", L, str(n), T.UV_UNIT, note), _src_ts(c, c["cur_ts"], "fc"))


def _w_pressure(c, ev, fresh):
    p = ev.get("press")
    if not p:
        return _w_none("pressure")
    return _w_fresh(c, fresh, "wx", _w("pressure", p["L"], U.fmt_press(p["p_mm"], c["pu"]), c["pu"], p["note"]),
                    _src_ts(c, c["cur_ts"], "fc"))


def _w_radiation(c, ev, fresh):
    r = ev.get("rad")
    if not r:
        if c["has_rad"] and fresh["rad"]["st"] == "ok":
            return _w_none("radiation", "no stations nearby")
        return _w_none("radiation")
    return _w_fresh(c, fresh, "rad", _w("radiation", r["L"], r["val"], T.RAD_UNIT, r["sub"]),
                    r["newest"] if r.get("newest") else _src_ts(c, None, "rad"))


def _w_sun(c):
    """Next sun event; after sunset tomorrow's sunrise. The note is the other event of that
    day. Astronomical times do not depend on the forecast freshness."""
    now = c["now"]
    nxt = [(v, k) for v, k in c["suns"] if v > now]
    if not nxt:
        return _w_none("sun")
    v, k = nxt[0]
    other = [x for x, kk in c["suns"] if kk != k and _dt(c, x).date() == _dt(c, v).date()]
    note = T.sun_note("set" if k == "rise" else "rise", _hm(c, other[0])) if other else ""
    return _w("sun", -1, _hm(c, v), T.sun_unit(k), note)


def _w_humidity(c, ev, fresh):
    h = ev.get("hum")
    if not c["has_fc"] or not h or c["rh"] is None:
        return _w_none("humidity")
    note = T.dew_note(U.fmt_temp(c["dew"], c["tu"])) if c["dew"] is not None else ""
    val = "%d" % _rnd(min(100.0, max(0.0, c["rh"])))
    return _w_fresh(c, fresh, "wx", _w("humidity", h["L"], val, "%", note), _src_ts(c, c["cur_ts"], "fc"))


def widget_keys(s):
    """Six widget keys from the settings; with radiation turned off its slot goes to the
    first widget not on the list."""
    keys = list(s.widgets)
    if s.rad_provider == "none" and "radiation" in keys:
        spare = [k for k in ("humidity", "wind", "air", "uv", "pressure", "sun") if k not in keys]
        if spare:
            keys[keys.index("radiation")] = spare[0]
    return keys


def _widgets(c, ev, fresh):
    fn = {"wind": lambda: _w_wind(c, ev, fresh), "air": lambda: _w_air(c, ev, fresh),
          "uv": lambda: _w_uv(c, fresh), "pressure": lambda: _w_pressure(c, ev, fresh),
          "radiation": lambda: _w_radiation(c, ev, fresh), "sun": lambda: _w_sun(c),
          "humidity": lambda: _w_humidity(c, ev, fresh)}
    return [_safe(fn[k], None) or _w_none(k) for k in widget_keys(c["s"])]


# ---------------------------------------------------------------- precipitation by hour
def _rain_block(c, fresh):
    """9 columns: the current hour + 8. Column [H, H+1) takes the values labelled H+1."""
    H = c["H"]
    C = ((c.get("_ev") or {}).get("precip") or {}).get("C") or {}
    cols = []
    for i in range(RAIN_COLS):
        x = C.get(i) or {}
        pp, mm = x.get("pp"), x.get("mm")
        ppi = max(0, min(100, int(round(pp)))) if pp is not None else None
        snow = 1 if (x.get("kind") in ("snow", "sleet") and ((mm or 0) > 0 or (ppi or 0) >= 5)) else 0
        cols.append({"h": "%02d" % _dt(c, H + i * 3600).hour, "pp": ppi, "snow": snow})
    age = fresh["wx"]["age"]
    gray = (not c["has_fc"]) or age is None or age > RAIN_GRAY
    return {"cols": cols, "gray": bool(gray)}


def _tmin_night(c):
    """Minimum temperature until the next 09:00 local time (tomorrow's if it is past 9)."""
    S, now, nd = c["S"], c["now"], c["dt"]
    end = nd.replace(hour=9, minute=0, second=0, microsecond=0)
    if nd.hour >= 9:
        end = (nd + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    end_ts = end.timestamp()
    vals = [S.at("temperature_2m", t) for t in S.idx if now < t <= end_ts]
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def attribution(source=""):
    """Data credit line shown on the page (Open-Meteo data is CC BY 4.0)."""
    parts = [ATTR_WEATHER, ATTR_AIR]
    if isinstance(source, str) and source.strip():
        parts.append("Radiation: " + source.strip()[:60])
    return u" · ".join(parts)


# ---------------------------------------------------------------- assembly
def _empty_fresh(s=None):
    ids = [p for p, _ in s.panels] if s is not None else []
    return {"wx": {"st": "none", "age": None, "fail": 0}, "air": {"st": "none", "age": None, "fail": 0},
            "rad": {"st": "none", "age": None, "fail": 0, "date": None},
            "cam": {p: {"st": "none", "age": None} for p in ids}}


def _empty_now():
    return {"scene": "not-available", "day": None, "utci": None, "utciTxt": u"—",
            "word": T.SCENE_TITLES["not-available"], "L": None, "t": None, "tTxt": u"—",
            "stale": True, "ahead": []}


def _empty_widgets(s=None):
    keys = widget_keys(s) if s is not None else list(DEFAULT_KEYS)
    return [_w_none(k) for k in keys]


DEFAULT_KEYS = ("wind", "air", "uv", "pressure", "radiation", "sun")


def _empty_rain(now, tz):
    H = _hour_start(now, tz)
    return {"cols": [{"h": _safe(lambda i=i: "%02d" % datetime.fromtimestamp(H + i * 3600, tz).hour, ""),
                      "pp": None, "snow": 0} for i in range(RAIN_COLS)], "gray": True}


def _empty_panel(now, s=None):
    now = _num(now) or 0.0
    tz = _safe(lambda: resolve_tz(s), timezone.utc)
    return {"v": 3, "ts": int(now), "now": _empty_now(), "status": [], "rot": False,
            "widgets": _safe(lambda: _empty_widgets(s), None) or _empty_widgets(),
            "rain": _safe(lambda: _empty_rain(now, tz), {"cols": [{"h": "", "pp": None, "snow": 0}
                                                                 for _ in range(RAIN_COLS)], "gray": True}),
            "photo": None, "fresh": _safe(lambda: _empty_fresh(s), None) or _empty_fresh(),
            "attribution": attribution()}


def _clean_mem(mem):
    for k, typ in (("hy", dict), ("pick", dict), ("said", dict), ("radh", dict), ("top", dict)):
        if k in mem and not isinstance(mem[k], typ):
            del mem[k]
    rh = mem.get("radh")
    if isinstance(rh, dict):
        for k in list(rh):
            if not isinstance(rh[k], dict):
                del rh[k]


def _prune_picks(mem, date):
    p = mem.get("pick")
    if isinstance(p, dict):
        for k in list(p):
            v = p[k]
            if not isinstance(v, dict) or not str(v.get("w", "")).startswith(date):
                del p[k]


def _build(raw, src, mem, now, s):
    c = _parse(raw, src, now, s)
    fresh = _safe(lambda: _fresh(c), None) or _empty_fresh(s)
    ev = {}
    c["_ev"] = ev
    for k, fn in (("air", _eval_air), ("wind", _eval_wind), ("press", _eval_press),
                  ("rad", _eval_rad), ("uv", _eval_uv), ("utci", _eval_utci), ("hum", _eval_hum)):
        ev[k] = _safe(lambda fn=fn: fn(c, mem), None)
    ev["precip"] = _safe(lambda: _precip(c), None)
    ev["tmin_night"] = _safe(lambda: _tmin_night(c), None)
    _prune_picks(mem, c["date"])
    F = _safe(lambda: _facts(c, ev, fresh), [])
    status = _safe(lambda: _status(c, mem, F), [])
    rot = len(status) > 1 and status[1]["P"] <= 3
    return {"v": 3, "ts": int(now),
            "now": _safe(lambda: _now_block(c, ev, fresh), None) or _empty_now(),
            "status": status, "rot": bool(rot),
            "widgets": _safe(lambda: _widgets(c, ev, fresh), None) or _empty_widgets(s),
            "rain": _safe(lambda: _rain_block(c, fresh), None) or _empty_rain(now, c["tz"]),
            "photo": None,
            "fresh": fresh,
            "attribution": attribution(c["rad_source"])}


def build_panel(raw, src, mem, now, cfg=None):
    """Raw data -> panel v3 (see the module docstring). Never raises. ``photo`` is left None
    for the caller to fill from weather.photo.PhotoStore."""
    s = None
    try:
        s = read_settings(cfg)
        n = _num(now)
        if n is None or not -1e10 < n < 1e11:
            n = 0.0
        if not isinstance(mem, dict):
            mem = {}
        _clean_mem(mem)
        return _build(raw if isinstance(raw, dict) else {}, src if isinstance(src, dict) else {}, mem, n, s)
    except Exception:
        try:
            return _empty_panel(now, s)
        except Exception:
            return {"v": 3, "ts": 0, "now": _empty_now(), "status": [], "rot": False,
                    "widgets": _empty_widgets(),
                    "rain": {"cols": [{"h": "", "pp": None, "snow": 0} for _ in range(RAIN_COLS)], "gray": True},
                    "photo": None, "fresh": _empty_fresh(), "attribution": attribution()}
