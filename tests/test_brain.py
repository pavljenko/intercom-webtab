# -*- coding: utf-8 -*-
"""Tests of the weather brain (intercom_webtab.weather.brain), panel v3. Run from the repo root:

    python3 -m unittest discover -s tests -p "test_[bup]*.py"

No network: Open-Meteo answers are synthetic (timeformat=unixtime) or the Greenwich snapshots
in tests/fx. The synthetic builders are reused by tools/mock_dump.py.
"""
import json
import math
import os
import random
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from intercom_webtab.weather import Settings, brain as wb, phrases as ph, units as U  # noqa: E402

TZ = Settings().tzinfo()                 # Europe/London, the documented example location
HPA = 1 / 0.750062                       # mmHg -> hPa
ELEV = 10.0                              # synthetic elevation, m
NORM = round(wb.press_norm(ELEV), 1)     # standard-atmosphere pressure there, mmHg
PANELS = (("gate", "Gate"), ("door", "Entrance"))
CFG = Settings(panels=PANELS)
CFG_MM = Settings(panels=PANELS, press="mmHg")


# ---------------------------------------------------------------- raw answer builders
def ts_loc(y, mo, d, h, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp())


def _hf(ts):
    d = datetime.fromtimestamp(ts, TZ)
    return d.hour + d.minute / 60.0


def _split(mm, code):
    """Precipitation -> (rain, showers, snowfall) like Open-Meteo: snow cm ~ mm x 0.7."""
    mm = mm or 0.0
    if code in wb.SNOW_CODES:
        return 0.0, 0.0, round(mm * 0.7, 2)
    if code in wb.SHOWER_CODES:
        return 0.0, mm, 0.0
    return mm, 0.0, 0.0


def mk_fc(now, t=15.0, rh=60.0, code=None, day=None, cloud=20.0, p=NORM, wind=3.0, gust=6.0,
          wdir=270.0, sw=None, uv=None, uvmax=3.0, vis=24000.0, prec=0.0, amp=0.0,
          p_fn=None, t_fn=None, uv_fn=None, hr=None, cols=None, tmax=None, tmin=None,
          rain=None, showers=None, snowfall=None, depth=0.0, fields=True):
    """/v1/forecast answer. hr={label offset in hours: {...}}, cols={i: {...}} -- the column of
    the interval [H+i, H+i+1), i.e. label H+i+1. Keys: pp, mm, code, t, wind, gust, uv, p (mmHg),
    rain, showers, snowfall, cloud, vis, rh. Current prec/rain/showers/snowfall are 15-minute
    sums (interval 900); without explicit rain/showers/snowfall they follow prec and the code.
    fields=False -- an older answer without rain/showers/snowfall/snow_depth and the new series."""
    now = int(now)
    H = now // 3600 * 3600
    base = code if (code is not None and code < 45) else \
        (3 if cloud >= 80 else 2 if cloud >= 40 else 1 if cloud >= 15 else 0)
    ov = {}
    for k, v in (hr or {}).items():
        ov.setdefault(k, {}).update(v)
    for i, v in (cols or {}).items():
        ov.setdefault(i + 1, {}).update(v)

    def tf(off, ts):
        if t_fn:
            return t_fn(off)
        return t + amp * (math.cos(2 * math.pi * (_hf(ts) - 15) / 24) -
                          math.cos(2 * math.pi * (_hf(now) - 15) / 24))

    def uvf(h):
        if uv_fn:
            r = uv_fn(h)
            if r is not None:
                return r
        if not 6 <= h <= 20:
            return 0.0
        return round(uvmax * math.sin(math.pi * (h - 6) / 14.0) * (1 - 0.6 * cloud / 100.0), 2)

    def pf(off):
        return p_fn(off) if p_fn else p

    def swf(h):
        return round(750 * max(0.0, math.sin(math.pi * (h - 6) / 14.0)) * (1 - 0.7 * cloud / 100.0), 1) \
            if 6 <= h <= 20 else 0.0

    times, PP, MM, CODE, PS, WS, WG, UV, T = [], [], [], [], [], [], [], [], []
    RN, SH, SN, CL, VI, RH = [], [], [], [], [], []
    for k in range(-24, 25):
        lab = H + k * 3600
        o = ov.get(k, {})
        tt = o.get("t", round(tf(k, lab), 1))
        mm = o.get("mm", 0.0)
        times.append(lab)
        PP.append(o.get("pp", 0))
        MM.append(mm)
        if "code" in o:
            CODE.append(o["code"])
        elif mm is not None and mm >= 0.1:
            CODE.append(61 if (tt is None or tt > 0.5) else 71)
        else:
            CODE.append(base)
        r0, s0, n0 = _split(mm, CODE[-1])
        RN.append(o.get("rain", r0))
        SH.append(o.get("showers", s0))
        SN.append(o.get("snowfall", n0))
        CL.append(o.get("cloud", cloud))
        VI.append(o.get("vis", vis))
        RH.append(o.get("rh", rh))
        PS.append(round(o["p"] * HPA, 1) if "p" in o else round(pf(k) * HPA, 1))
        WS.append(o.get("wind", wind))
        WG.append(o.get("gust", gust))
        UV.append(o.get("uv", uvf(_hf(lab))))
        T.append(tt)
    hnow = _hf(now)
    ccode = code if code is not None else base
    r0, s0, n0 = _split(prec, ccode)
    cur = {"time": now - now % 900, "interval": 900, "temperature_2m": t, "relative_humidity_2m": rh,
           "precipitation": prec, "weather_code": ccode,
           "is_day": day if day is not None else (1 if 7 <= hnow < 19 else 0), "cloud_cover": cloud,
           "surface_pressure": round(pf(0) * HPA, 1) if p_fn else round(p * HPA, 1),
           "wind_speed_10m": wind, "wind_direction_10m": wdir, "wind_gusts_10m": gust,
           "shortwave_radiation": sw if sw is not None else swf(hnow),
           "uv_index": uv if uv is not None else uvf(hnow), "visibility": vis}
    hourly = {"time": times, "precipitation_probability": PP, "precipitation": MM,
              "weather_code": CODE, "surface_pressure": PS, "wind_speed_10m": WS,
              "wind_gusts_10m": WG, "uv_index": UV, "temperature_2m": T}
    if fields:
        cur.update(rain=r0 if rain is None else rain, showers=s0 if showers is None else showers,
                   snowfall=n0 if snowfall is None else snowfall, snow_depth=depth)
        hourly.update(rain=RN, showers=SH, snowfall=SN, cloud_cover=CL, visibility=VI,
                      relative_humidity_2m=RH)
    d0 = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    dts = [int(d0.timestamp()), int((d0 + timedelta(days=1)).timestamp())]
    tx, tn = [], []
    for dstart in dts:
        vals = [T[i] for i, lab in enumerate(times) if dstart <= lab < dstart + 86400 and T[i] is not None]
        tx.append(max(vals) if vals else t)
        tn.append(min(vals) if vals else t)
    if tmax is not None:
        tx[0] = tmax
    if tmin is not None:
        tn[0] = tmin
    off = int(datetime.fromtimestamp(now, TZ).utcoffset().total_seconds())
    return {"latitude": 51.4779, "longitude": -0.0015, "utc_offset_seconds": off,
            "timezone": "Europe/London", "elevation": ELEV, "current": cur, "hourly": hourly,
            "daily": {"time": dts, "temperature_2m_max": tx, "temperature_2m_min": tn,
                      "uv_index_max": [uvmax, uvmax], "sunrise": [dts[0] + 23400, dts[1] + 23400],
                      "sunset": [dts[0] + 63900, dts[1] + 63900]}}


def mk_air(now, aqi=25, pm25=8.0, pm10=15.0, o3=60.0, no2=10.0, dust=0.0, aod=0.1):
    H = int(now) // 3600 * 3600
    return {"current": {"time": H, "interval": 3600, "european_aqi": aqi,
                        "european_aqi_pm2_5": round(pm25 * 1.6) if pm25 is not None else None,
                        "european_aqi_pm10": round(pm10 * 0.8) if pm10 is not None else None,
                        "european_aqi_nitrogen_dioxide": round(no2 * 0.4) if no2 is not None else None,
                        "european_aqi_ozone": round(o3 * 0.4) if o3 is not None else None,
                        "pm2_5": pm25, "pm10": pm10, "ozone": o3, "nitrogen_dioxide": no2,
                        "dust": dust, "aerosol_optical_depth": aod}}


# (km, name, uSv/h): neutral sensor names around the example location
STATIONS = [(8.1, "Riverside", 0.12), (26.0, "Hilltop", 0.14), (61.5, "Harbour", 0.15),
            (62.0, "Old Mill", 0.15), (66.0, "Quarry", 0.15), (70.0, "Airfield", 0.16),
            (74.0, "Valley", 0.16), (78.0, "Ridge", 0.10)]


def mk_rad(now, stations=None, rts=None, over=None):
    """radiation.fetch() result. rts -- reading time (default: 2 h ago); over={name: value}."""
    rts = int(now) - 7200 if rts is None else int(rts)
    out = []
    for i, (km, name, v) in enumerate(stations if stations is not None else STATIONS):
        if over and name in over:
            v = over[name]
        out.append({"name": name, "lat": 51.4 + i * 0.01, "lon": 0.1 + i * 0.01, "km": km, "usvh": v,
                    "ts": rts, "kind": "fixed"})
    return {"source": "Test network", "source_url": "https://example.invalid/", "license": "CC0",
            "stations": out}


def mk_cam(now, gate=1, door=1, was=True):
    return {"gate": {"ts": now - gate if gate is not None else 0, "was": was},
            "door": {"ts": now - door if door is not None else 0, "was": was}}


def mk_src(now, fc=300, air=1800, rad=3600, fails=None):
    fails = fails or {}
    out = {}
    for k, a in (("fc", fc), ("air", air), ("rad", rad)):
        out[k] = {"ok": (now - a) if a is not None else 0, "fail": fails.get(k, 0),
                  "err": "HTTPError" if fails.get(k) else ""}
    return out


def make(now, fckw=None, airkw=None, over=None, stations=None, rts=None, srckw=None, camkw=None,
         fc_at=None):
    raw = {"fc": mk_fc(fc_at or now, **(fckw or {})), "air": mk_air(now, **(airkw or {})),
           "rad": mk_rad(now, stations, rts, over), "cam": mk_cam(now, **(camkw or {}))}
    return raw, mk_src(now, **(srckw or {}))


def panel(now, mem=None, cfg=CFG, **kw):
    raw, src = make(now, **kw)
    return wb.build_panel(raw, src, {} if mem is None else mem, now, cfg)


def p_linear(now_p, d3=0.0, d24=0.0, f24=0.0):
    """Pressure (mmHg) by label offset: d3/d24 -- "now minus then" (-10 = fell by 10);
    now_p + f24 a day ahead (piecewise linear)."""
    def f(off):
        if off >= 0:
            return now_p + f24 * min(off, 24) / 24.0
        if off >= -3:
            return now_p - d3 * (-off) / 3.0
        return now_p - d3 - (d24 - d3) * min(-off - 3, 21) / 21.0
    return f


# ---------------------------------------------------------------- 15 scenarios
OCT9 = (2026, 10, 9)


def scen(n):
    """The 15 reference situations -> (now, kwargs for make)."""
    if n == 1:
        now = ts_loc(*OCT9, 14, 0)
        return now, dict(fckw=dict(t=16, rh=55, uv=2.1, uvmax=2.2, wind=3, gust=6, cloud=20,
                                   p_fn=p_linear(NORM, d3=0.2, d24=1.5)))
    if n == 2:
        now = ts_loc(*OCT9, 15, 40)
        return now, dict(fckw=dict(t=14, cloud=60, cols={0: {"pp": 10, "mm": 0.0}, 1: {"pp": 70, "mm": 0.4},
                                                          2: {"pp": 60, "mm": 0.5}}))
    if n == 3:
        now = ts_loc(*OCT9, 15, 55)
        return now, dict(fckw=dict(t=12, rh=90, cloud=95, code=61, prec=0.2,
                                   cols={0: {"pp": 90, "mm": 0.8, "code": 61}, 1: {"pp": 80, "mm": 0.6, "code": 61},
                                         2: {"pp": 20, "mm": 0.0}}))
    if n == 4:
        now = ts_loc(*OCT9, 16, 0)
        return now, dict(fckw=dict(t=19, rh=50, wind=9, gust=17, wdir=315, cloud=70,
                                   cols={1: {"pp": 80, "mm": 1.0, "code": 61}}))
    if n == 5:
        now = ts_loc(*OCT9, 14, 0)
        return now, dict(fckw=dict(t=16, rh=55, wind=3, gust=6), srckw=dict(rad=29 * 3600),
                         rts=now - 30 * 3600)
    if n == 6:
        now = ts_loc(*OCT9, 14, 0)
        st = [s for s in STATIONS if s[1] != "Ridge"]
        return now, dict(fckw=dict(t=16, rh=55), stations=st, over={"Quarry": 0.34})
    if n == 7:
        now = ts_loc(*OCT9, 14, 0)
        return now, dict(fckw=dict(t=16, rh=55), over={"Riverside": 0.72, "Harbour": 0.65})
    if n == 8:
        now = ts_loc(2026, 7, 15, 13, 0)
        uvs = {14: 8.0, 15: 7.2, 16: 6.5, 17: 6.1, 18: 4.0, 19: 2.0, 20: 0.5}
        return now, dict(fckw=dict(t=35, rh=29, wind=1, gust=3, cloud=5, sw=850, uv=8.6, uvmax=8.6,
                                   uv_fn=lambda h: uvs.get(int(h)) if h >= 14 else None),
                         airkw=dict(aqi=55, pm25=12, pm10=20, o3=125, no2=12))
    if n == 9:
        now = ts_loc(2027, 1, 20, 8, 0)
        return now, dict(fckw=dict(t=-6, rh=80, wind=6, gust=12, cloud=90, p=NORM + 1))
    if n == 10:
        now = ts_loc(*OCT9, 12, 0)
        return now, dict(fckw=dict(t=14, rh=70, wind=5, gust=12, cloud=90,
                                   p_fn=p_linear(NORM - 12, d3=-3.5, d24=-10)))
    if n == 11:
        now = ts_loc(2026, 8, 20, 15, 0)
        return now, dict(fckw=dict(t=30, rh=25, wind=13, gust=21, cloud=10),
                         airkw=dict(aqi=105, pm25=45, pm10=160, o3=90, no2=20, dust=300, aod=1.2))
    if n == 12:
        now = ts_loc(*OCT9, 14, 0)
        return now, dict(fc_at=now - 4 * 3600, fckw=dict(t=16, rh=55, wind=7, gust=16),
                         srckw=dict(fc=4 * 3600))
    if n == 13:
        now = ts_loc(2026, 11, 10, 7, 0)
        past = {k: {"mm": 0.4, "pp": 80, "t": 0.8} for k in range(-5, 1)}
        return now, dict(fckw=dict(t=-1, rh=97, wind=1, gust=2, cloud=100, code=48, vis=300, hr=past))
    if n == 14:
        now = ts_loc(*OCT9, 23, 10)
        return now, dict(fckw=dict(t=11, rh=75, cloud=60,
                                   cols={i: {"pp": 80, "mm": 0.5, "code": 61} for i in range(8, 12)}))
    if n == 15:
        now = ts_loc(*OCT9, 13, 0)
        return now, dict(fckw=dict(t=17, rh=50, wind=8, gust=16, cloud=10, uv=6.5, uvmax=6.6,
                                   p_fn=p_linear(NORM - 4, d3=1.0, d24=7)),
                         airkw=dict(aqi=65, pm25=20, pm10=30, o3=80, no2=15),
                         over={"Harbour": 0.45, "Quarry": 0.45})
    raise ValueError(n)


SCEN_TITLES = {
    1: "calm day", 2: "rain starts within the hour", 3: "raining, ends soon", 4: "strong wind and rain",
    5: "radiation not updated for 29 h", 6: "radiation high at one station", 7: "radiation high, confirmed",
    8: "heat and high UV", 9: "frost and wind", 10: "pressure fell in 24 h", 11: "dust storm",
    12: "forecast out of date", 13: "fog and black ice, morning", 14: "night, rain in the morning",
    15: "everything at level 2",
}


def scen_14_evening():
    now = ts_loc(*OCT9, 19, 0)
    return now, dict(fckw=dict(t=13, rh=75, cloud=60,
                               cols={i: {"pp": 80, "mm": 0.5, "code": 61} for i in range(12, 16)}))


# ---------------------------------------------------------------- a grid of synthetic contexts
SEASON_T = {1: 4, 2: 5, 3: 7, 4: 10, 5: 13, 6: 16, 7: 19, 8: 18, 9: 16, 10: 12, 11: 8, 12: 5}
UVMAX = {1: 1, 2: 1.5, 3: 3, 4: 4.5, 5: 6, 6: 7, 7: 7, 8: 6, 9: 4, 10: 2.5, 11: 1.2, 12: 0.8}


def calm_kw(R, now):
    """Calm weather for the season: the base the perturbations start from."""
    d = datetime.fromtimestamp(now, TZ)
    t = round(SEASON_T[d.month] + R.uniform(-6, 6), 1)
    pn = round(NORM + R.uniform(-4, 4), 1)
    return dict(fckw=dict(t=t, rh=R.choice([45, 55, 65]), wind=R.choice([1, 2, 3, 4]),
                          gust=R.choice([3, 5, 7, 8]), cloud=R.choice([0, 10, 25, 50, 75, 90]),
                          wdir=R.choice([0, 45, 90, 135, 180, 225, 270, 315]), amp=4,
                          uvmax=UVMAX[d.month],
                          p_fn=p_linear(pn, d3=round(R.uniform(-1, 1), 1), d24=round(R.uniform(-2.5, 2.5), 1),
                                        f24=round(R.uniform(-2.5, 2.5), 1))),
                airkw={}, srckw={}, camkw={}, over=None)


def _fc(kw):
    return kw["fckw"]


def _cols(kw):
    return kw["fckw"].setdefault("cols", {})


def _hr(kw):
    return kw["fckw"].setdefault("hr", {})


def _span(kw, a, b, **v):
    for i in range(a, b):
        _cols(kw)[i] = dict(_cols(kw).get(i, {}), **v)


def _gusts(kw, g, mean=None):
    _fc(kw).update(gust=g)
    if mean is not None:
        _fc(kw).update(wind=mean)
    for j in range(-24, 25):
        _hr(kw)[j] = dict(_hr(kw).get(j, {}), gust=g, **({"wind": mean} if mean is not None else {}))


def _p(kw, now, now_p=None, d3=0.0, d24=0.0, f24=0.0):
    _fc(kw)["p_fn"] = p_linear(now_p if now_p is not None else NORM, d3, d24, f24)


def _past_rain(kw, mm=0.4, t=None):
    for k in range(-5, 1):
        v = {"mm": mm, "pp": 80}
        if t is not None:
            v["t"] = t
        _hr(kw)[k] = dict(_hr(kw).get(k, {}), **v)


# name -> (label, function(kw, now, R))
PERTURB = {
    "rain_soon0": ("rain this hour", lambda kw, now, R: _span(kw, 0, R.choice([1, 3]), pp=R.choice([60, 85]),
                                                              mm=R.choice([0.4, 1.0]))),
    "rain_soon": ("rain in 1-3 h", lambda kw, now, R: _span(kw, R.randint(1, 3), 6, pp=R.choice([55, 80]),
                                                            mm=R.choice([0.3, 0.8, 2.4]))),
    "rain_now_end": ("raining, ends soon", lambda kw, now, R: (
        _fc(kw).update(prec=0.3, code=61, cloud=95), _span(kw, 0, R.randint(1, 3), pp=90, mm=0.9))),
    "rain_now_long": ("raining for long", lambda kw, now, R: (
        _fc(kw).update(prec=0.4, code=63, cloud=100), _span(kw, 0, R.choice([6, 13]), pp=95, mm=1.2))),
    "rain_maybe": ("light rain possible", lambda kw, now, R: _span(kw, R.randint(0, 3), 4,
                                                                  pp=R.choice([50, 60]), mm=0.1)),
    "rain_later": ("rain later today", lambda kw, now, R: _span(kw, R.randint(5, 16), 24,
                                                                pp=R.choice([55, 80, 95]), mm=0.6)),
    "rain_over": ("rain just ended", lambda kw, now, R: _hr(kw).update({0: {"mm": 0.7, "pp": 85}})),
    "shower": ("shower in 1-2 h", lambda kw, now, R: _span(kw, R.randint(1, 2), 4, pp=85, mm=3.5, code=81)),
    "pour": ("torrential rain", lambda kw, now, R: _span(kw, 1, 2, pp=95, mm=34.0, code=82)),
    "storm": ("thunderstorm soon", lambda kw, now, R: _span(kw, R.randint(1, 3), 5, pp=70, mm=2.0, code=95)),
    "hail": ("hailstorm", lambda kw, now, R: (_fc(kw).update(code=96, prec=1.0),
                                             _span(kw, 0, 2, pp=90, mm=4.0, code=96))),
    "snow_now": ("snowing", lambda kw, now, R: (_fc(kw).update(t=-3, prec=0.3, code=73, cloud=100),
                                               _span(kw, 0, R.choice([2, 8]), pp=85, mm=0.6, code=73, t=-3))),
    "sleet_soon": ("sleet soon", lambda kw, now, R: (_fc(kw).update(t=1.8, cloud=95),
                                                     _span(kw, 1, 4, pp=75, mm=0.6, code=73, t=1.6))),
    "frz": ("freezing rain", lambda kw, now, R: (_fc(kw).update(t=-1, code=66, prec=0.4, cloud=100),
                                                 _span(kw, 0, 3, pp=90, mm=0.8, code=66, t=-1))),
    "fog": ("fog 500 m", lambda kw, now, R: _fc(kw).update(code=45, vis=500, rh=98, cloud=100)),
    "fog_dense": ("dense fog 120 m", lambda kw, now, R: _fc(kw).update(code=48, vis=120, rh=99, cloud=100)),
    "ice": ("black ice after rain", lambda kw, now, R: (_fc(kw).update(t=-1.5, cloud=90),
                                                        _past_rain(kw, 0.4, 1.0))),
    "fog_ice": ("fog and black ice", lambda kw, now, R: (_fc(kw).update(t=-1, code=48, vis=300, cloud=100),
                                                         _past_rain(kw, 0.4, 0.8))),
    "wind1": ("breezy (L1)", lambda kw, now, R: _gusts(kw, R.choice([11, 13]), 6)),
    "wind2": ("strong wind (L2)", lambda kw, now, R: _gusts(kw, R.choice([15, 17, 19]), 9)),
    "wind3": ("very strong wind (L3)", lambda kw, now, R: _gusts(kw, R.choice([21, 24]), 13)),
    "wind4": ("storm (L4)", lambda kw, now, R: _gusts(kw, R.choice([26, 33]), 18)),
    "wind_rise": ("wind rising in 2-3 h", lambda kw, now, R: [
        _hr(kw).update({j: dict(_hr(kw).get(j, {}), gust=R.choice([16, 22]), wind=9)}) for j in (2, 3)]),
    "air1": ("moderate air (PM2.5 20)", lambda kw, now, R: kw["airkw"].update(aqi=48, pm25=20)),
    "air2": ("poor air (PM2.5 33)", lambda kw, now, R: kw["airkw"].update(aqi=68, pm25=33)),
    "air3": ("very poor air (PM2.5 60)", lambda kw, now, R: kw["airkw"].update(aqi=88, pm25=60)),
    "o3": ("ozone 170", lambda kw, now, R: kw["airkw"].update(aqi=72, o3=170)),
    "no2": ("NO2 130", lambda kw, now, R: kw["airkw"].update(aqi=66, no2=130)),
    "dust": ("dusty (PM10 120, dust)", lambda kw, now, R: kw["airkw"].update(aqi=70, pm10=120, dust=180,
                                                                            aod=0.8)),
    "dust_storm": ("dust storm", lambda kw, now, R: (kw["airkw"].update(aqi=110, pm10=300, dust=600, aod=1.5),
                                                     _gusts(kw, 22, 13))),
    "air_crit": ("hazardous air (PM2.5 120)", lambda kw, now, R: kw["airkw"].update(aqi=150, pm25=120)),
    "rad_single": ("radiation 0.34 at one far station", lambda kw, now, R: kw.update(over={"Quarry": 0.34})),
    "rad_two": ("radiation 0.45 at two stations",
                lambda kw, now, R: kw.update(over={"Quarry": 0.45, "Harbour": 0.43})),
    "rad_near": ("radiation 0.33 at the nearest station", lambda kw, now, R: kw.update(over={"Riverside": 0.33})),
    "rad_crit": ("radiation 0.72 and 0.65", lambda kw, now, R: kw.update(over={"Riverside": 0.72, "Hilltop": 0.65})),
    "rad_l1": ("radiation 0.24 at two stations", lambda kw, now, R: kw.update(over={"Hilltop": 0.24, "Harbour": 0.22})),
    "press_fall24": ("pressure fell 10 mmHg in 24 h", lambda kw, now, R: _p(kw, now, None, -2.0, -10.0)),
    "press_rise24": ("pressure rose 8 mmHg in 24 h", lambda kw, now, R: _p(kw, now, None, 1.0, 8.0)),
    "press_fc": ("pressure to fall 9 mmHg in 24 h", lambda kw, now, R: _p(kw, now, None, 0.0, 0.5, -9.0)),
    "press_3h": ("pressure -6 mmHg in 3 h", lambda kw, now, R: _p(kw, now, None, -6.0, -6.0)),
    "press_low": ("pressure 21 mmHg below normal", lambda kw, now, R: _p(kw, now, NORM - 21)),
    "press_high": ("pressure 12 mmHg above normal", lambda kw, now, R: _p(kw, now, NORM + 12)),
    "press1": ("pressure falling (L1)", lambda kw, now, R: _p(kw, now, None, -1.0, -4.5)),
    "heat": ("heat 36", lambda kw, now, R: _fc(kw).update(t=36, rh=30, wind=1, cloud=5, uvmax=8.5)),
    "hot_dry": ("dry 30, UV 7", lambda kw, now, R: _fc(kw).update(t=30, rh=22, wind=4, gust=7, cloud=5,
                                                                 uvmax=7.2)),
    "muggy": ("muggy 27, humid", lambda kw, now, R: _fc(kw).update(t=27, rh=80, wind=1, gust=3)),
    "raw": ("raw 4, damp, wind 5", lambda kw, now, R: _fc(kw).update(t=4, rh=92, wind=5, gust=9, cloud=100)),
    "cold": ("cold -8, calm", lambda kw, now, R: _fc(kw).update(t=-8, rh=75, wind=1, gust=3)),
    "cold_wind": ("cold wind -4, 7 m/s", lambda kw, now, R: (_fc(kw).update(t=-4, rh=80), _gusts(kw, 12, 7))),
    "frost_hard": ("frost -22", lambda kw, now, R: _fc(kw).update(t=-22, rh=70, wind=3, gust=6)),
    "frost": ("night frost -2", lambda kw, now, R: _fc(kw).update(t=7, amp=0, t_fn=lambda off: 7 - 0.9 * off
                                                                  if 0 < off <= 14 else 7)),
    "swing": ("daily swing 14 degrees", lambda kw, now, R: _fc(kw).update(amp=7)),
    "uv_high": ("UV 7 (high)", lambda kw, now, R: _fc(kw).update(uvmax=7.2, cloud=5)),
    "wx_stale": ("weather 50 min old", lambda kw, now, R: kw["srckw"].update(fc=50 * 60)),
    "wx_dead": ("weather 5 h old", lambda kw, now, R: (kw["srckw"].update(fc=5 * 3600),
                                                       kw.update(fc_at=now - 5 * 3600))),
    "wx_none": ("weather never loaded", lambda kw, now, R: (kw["srckw"].update(fc=None, fails={"fc": 3}),
                                                            kw.update(nofc=True))),
    "air_stale": ("air 5 h old", lambda kw, now, R: kw["srckw"].update(air=5 * 3600)),
    "air_dead": ("air 14 h old", lambda kw, now, R: kw["srckw"].update(air=14 * 3600)),
    "rad_stale": ("radiation fetched 29 h ago", lambda kw, now, R: kw["srckw"].update(rad=29 * 3600)),
    "rad_old": ("radiation readings 4 days old", lambda kw, now, R: kw.update(rts=now - 4 * 86400)),
    "rad_none": ("radiation never loaded", lambda kw, now, R: (kw["srckw"].update(rad=None, fails={"rad": 116}),
                                                               kw.update(norad=True))),
    "cam_gate": ("gate camera silent 40 min", lambda kw, now, R: kw["camkw"].update(gate=2400)),
    "cam_door": ("entrance camera silent 21 h", lambda kw, now, R: kw["camkw"].update(door=21 * 3600)),
}


def build_kw(now, kw):
    kw = dict(kw)
    nofc, norad = kw.pop("nofc", False), kw.pop("norad", False)
    ages = dict(kw["srckw"])
    fails = ages.pop("fails", None)
    raw, src = make(now, fckw=kw["fckw"], airkw=kw["airkw"], over=kw.get("over"), rts=kw.get("rts"),
                    srckw=dict(ages, fails=fails), camkw=kw["camkw"], fc_at=kw.get("fc_at"))
    if nofc:
        raw["fc"] = None
    if norad:
        raw["rad"] = None
    return raw, src


def situation(now, names, seed=0):
    R = random.Random("%d:%s" % (now, ",".join(names)) if seed == 0 else seed)
    kw = calm_kw(R, now)
    for n in names:
        PERTURB[n][1](kw, now, R)
    return build_kw(now, kw)


def chaos(R, now):
    """A random mix of extreme values -- robustness check."""
    mo = datetime.fromtimestamp(now, TZ).month
    t = round(SEASON_T[mo] + R.uniform(-15, 15), 1)
    if R.random() < 0.1:
        t = R.choice([-30.0, -27.4, -0.4, 0.4, 0.5, 2.0, 38.0, 41.0, 45.0])
    cols = {}
    for _ in range(R.randint(0, 4)):
        a = R.randint(0, 20)
        for j in range(a, a + R.randint(1, 6)):
            cols[j] = {"pp": R.choice([None, 0, 4, 5, 25, 40, 50, 75, 100]),
                       "mm": R.choice([None, 0.0, 0.1, 0.3, 0.8, 2.0, 31.0]),
                       "code": R.choice([0, 3, 45, 51, 61, 65, 66, 71, 75, 80, 82, 95, 96])}
    hr = {}
    if R.random() < 0.3:
        for j in range(-6, 1):
            hr[j] = {"mm": R.choice([0.0, 0.3, 1.0]), "pp": 80}
    if R.random() < 0.2:
        for j in (1, 2, 3):
            hr[j] = dict(hr.get(j, {}), gust=R.choice([15, 21, 26, 34]), wind=R.choice([5, 11, 16, 21]))
    wind = R.choice([0.3, 1, 3, 5.9, 6, 9, 10, 13, 15, 19, 21])
    fckw = dict(t=t, rh=R.choice([15, 25, 40, 60, 80, 90, 99]), wind=wind,
                gust=max(wind, R.choice([2, 6, 9.9, 10, 12, 14.9, 15, 17, 20, 24, 25, 33, 40])),
                cloud=R.choice([0, 10, 30, 50, 75, 90, 100]),
                code=R.choice([None, 0, 1, 2, 3, 45, 48, 51, 55, 56, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80,
                               81, 82, 85, 86, 95, 96, 99]),
                vis=R.choice([50, 150, 199, 300, 950, 5000, 24000, None]), prec=R.choice([0.0, 0.0, 0.1, 1.0]),
                cols=cols, hr=hr, amp=R.choice([0, 3, 6, 9]), uvmax=R.choice([0.5, 2, 4, 6.5, 8.5, 11.5]),
                p_fn=p_linear(NORM + R.choice([-24, -15, -9, -3, 3, 11, 19]), d3=R.choice([-6, -3, 0, 1, 3, 5]),
                              d24=R.choice([-12, -7, -4, 0, 4, 8, 11]), f24=R.choice([-10, -5, 0, 5, 10])))
    airkw = R.choice([{}, dict(aqi=58, pm25=30), dict(aqi=75, o3=170), dict(aqi=65, no2=130),
                      dict(aqi=95, pm10=220, dust=400, aod=1.1), dict(aqi=88, pm25=60, pm10=120),
                      dict(aqi=140, pm25=110, pm10=260), dict(aqi=None, pm25=None, pm10=None, o3=None, no2=None)])
    over = R.choice([None, None, {"Quarry": 0.34}, {"Quarry": 0.45, "Harbour": 0.41},
                     {"Riverside": 0.33}, {"Riverside": 1.4, "Hilltop": 1.2}])
    ages = dict(fc=R.choice([60, 300, 300, 2000, 3000, 9000, 15000, None]),
                air=R.choice([600, 1800, 1800, 11000, 13000, 50000, None]),
                rad=R.choice([3600, 3600, 20000, 70000, 100000, 200000, None]))
    fails = {k: R.choice([0, 0, 1, 3, 116]) for k in ("fc", "air", "rad")}
    raw, src = make(now, fckw=fckw, airkw=airkw, over=over, stations=[] if R.random() < 0.05 else None,
                    rts=now - R.choice([600, 7200, 30000, 86400, 3 * 86400, 6 * 86400]),
                    srckw=dict(fails=fails, **ages),
                    camkw=dict(gate=R.choice([1, 1, 30, 75, 5400, 90000, None]),
                               door=R.choice([1, 75, 5400, None]), was=R.choice([True, True, False])))
    for k in ("fc", "air", "rad"):
        if R.random() < 0.04:
            raw[k] = None
    return raw, src


def grid(n=2200, seed=20261009):
    """Deterministic grid: 3/4 calm weather with 0-2 perturbations, 1/4 chaos.
    -> [(raw, src, now, series)]; within a series of 8 steps the mem is shared."""
    R = random.Random(seed)
    names = sorted(PERTURB)
    out = []
    for k in range(n):
        now = ts_loc(2026, R.randint(1, 12), R.randint(1, 28), R.randint(0, 23),
                     R.choice([0, 5, 10, 20, 30, 40, 50, 55, 59]))
        if R.random() < 0.25:
            raw, src = chaos(R, now)
        else:
            kw = calm_kw(R, now)
            for nm in R.sample(names, R.choice([0, 1, 1, 1, 2])):
                PERTURB[nm][1](kw, now, R)
            raw, src = build_kw(now, kw)
        out.append((raw, src, now, k // 8))
    return out


# ---------------------------------------------------------------- Greenwich snapshots
FX = os.path.join(HERE, "fx")


def load_fx():
    """Real Open-Meteo answers for Greenwich (tests/fx, fetched 2026-10-09 18:45 BST with
    sources.fetch_forecast / fetch_air). None when the files are missing."""
    out = {}
    for k, fn in (("fc", "forecast_greenwich.json"), ("air", "air_greenwich.json")):
        path = os.path.join(FX, fn)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            out[k] = json.load(f)
    return out


def fx_panel(fx, shift=0, mem=None, cfg=CFG):
    """Panel on the snapshot: fetched 2 min before current.time, built ``shift`` s later."""
    t0 = int(fx["fc"]["current"]["time"])
    now = t0 + shift
    raw = {"fc": fx["fc"], "air": fx["air"], "rad": mk_rad(t0), "cam": mk_cam(now)}
    src = {k: {"ok": t0 - 120, "fail": 0, "err": ""} for k in ("fc", "air", "rad")}
    return now, wb.build_panel(raw, src, {} if mem is None else mem, now, cfg)


def dump_json():
    """Panels of the scenarios, the snapshot and part of the grid -- for the TZ-independence test."""
    res = []
    fx = load_fx()
    if fx:
        for sh in (0, 15 * 3600):
            res.append(fx_panel(fx, sh)[1])
    for n in range(1, 16):
        now, kw = scen(n)
        res.append(panel(now, **kw))
    now, kw = scen_14_evening()
    res.append(panel(now, **kw)["status"])
    for raw, src, now, _ in grid(300, seed=7):
        p = wb.build_panel(raw, src, {}, now, CFG)
        res.append([[s["text"] for s in p["status"]], p["now"], p["widgets"], p["rain"]])
    return json.dumps(res, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- the v3 answer schema
SCENE_NAMES = wb.SCENE_NAMES
TOP_KEYS = {"v", "ts", "now", "status", "rot", "widgets", "rain", "photo", "fresh", "attribution"}
NOW_KEYS = {"scene", "day", "utci", "utciTxt", "word", "L", "t", "tTxt", "stale", "ahead"}
W_KEYS = {"k", "name", "L", "val", "unit", "note"}
ST_KEYS = {"text", "tone", "key", "P", "L"}
BAD_BITS = ("  ", u"−0°", "-0°", "+0°", "None", "{", "}", "nan", " ,", " .", "in 0 ")
DEG_RE = u"^(—|0°|[1-9][0-9]{0,2}°|−[1-9][0-9]{0,2}°)$"
HYPHEN_NUM = re.compile(r"(?<![A-Za-z0-9])-[0-9]")


def check_panel(tc, p, mem=None, cfg=CFG):
    tc.assertEqual(set(p), TOP_KEYS)
    tc.assertEqual(p["v"], 3)
    tc.assertIsInstance(p["ts"], int)
    tc.assertIsNone(p["photo"])
    tc.assertTrue(p["attribution"].startswith("Weather: Open-Meteo.com"))
    n = p["now"]
    tc.assertEqual(set(n), NOW_KEYS)
    tc.assertIn(n["scene"], SCENE_NAMES)
    tc.assertIn(n["day"], (0, 1, None))
    tc.assertIsInstance(n["stale"], bool)
    tc.assertIn(n["L"], (None, 0, 1, 2, 3, 4))
    for k in ("utciTxt", "tTxt"):
        tc.assertRegex(n[k], DEG_RE)
        tc.assertNotIn("+", n[k])
    tc.assertEqual(n["utciTxt"] == u"—", n["utci"] is None)
    tc.assertEqual(n["tTxt"] == u"—", n["t"] is None)
    if n["utci"] is None:
        tc.assertIsNone(n["L"])
    tc.assertTrue(n["word"] and n["word"][0].isupper() and len(n["word"]) <= wb.MAXLEN, n["word"])
    tc.assertIsInstance(n["ahead"], list)
    tc.assertEqual(len(n["ahead"]), len(set(n["ahead"])))
    for a in n["ahead"]:
        tc.assertIn(a, SCENE_NAMES)
        tc.assertNotIn(a, (n["scene"], "not-available"))
    if n["scene"] == "not-available" and n["t"] is None:
        tc.assertEqual(n["ahead"], [])
    # widgets: exactly 6, in the configured order
    w = p["widgets"]
    tc.assertEqual([x["k"] for x in w], wb.widget_keys(cfg))
    for x in w:
        tc.assertEqual(set(x), W_KEYS)
        tc.assertEqual(x["name"], ph.WIDGET_NAMES[x["k"]])
        tc.assertIn(x["L"], (None, -1, 0, 1, 2, 3, 4))
        tc.assertEqual(x["L"] == -1, x["k"] == "sun" and x["val"] != u"—", x)
        tc.assertIsInstance(x["val"], str)
        tc.assertIsInstance(x["unit"], str)
        tc.assertIsInstance(x["note"], str)
        tc.assertTrue(x["val"])
        tc.assertNotIn("+", x["val"])
        tc.assertNotIn(",", x["val"])
        tc.assertLessEqual(len(x["note"]), wb.NOTELEN, x)
        tc.assertFalse(HYPHEN_NUM.search(x["note"] + x["val"]), x)
        for bit in BAD_BITS:
            tc.assertNotIn(bit, x["note"], x)
        if x["L"] is None:
            tc.assertEqual((x["val"], x["unit"]), (u"—", ""))
            tc.assertTrue(x["note"] in ("no data", "no stations nearby") or x["note"].startswith("as of "), x)
    # status line
    tc.assertLessEqual(len(p["status"]), 3)
    for s in p["status"]:
        tc.assertEqual(set(s), ST_KEYS)
        txt = s["text"]
        tc.assertLessEqual(len(txt), wb.MAXLEN, txt)
        tc.assertTrue(txt and txt == txt.strip(), txt)
        tc.assertTrue(txt[0].isupper() or txt[0].isdigit(), txt)
        tc.assertFalse(HYPHEN_NUM.search(txt), txt)
        for bit in BAD_BITS:
            tc.assertNotIn(bit, txt, txt)
        tc.assertIn(s["tone"], ("crit", "fail", "warn", "info", "calm"))
        tc.assertIn(s["P"], (0, 1, 2, 3, 4, 5))
        tc.assertEqual(wb.TONE[s["P"]], s["tone"])
    tc.assertEqual(p["rot"], len(p["status"]) > 1 and p["status"][1]["P"] <= 3)
    # precipitation: exactly 9 columns, consecutive hours
    r = p["rain"]
    tc.assertEqual(set(r), {"cols", "gray"})
    tc.assertIsInstance(r["gray"], bool)
    tc.assertEqual(len(r["cols"]), 9)
    hs = []
    for col in r["cols"]:
        tc.assertEqual(set(col), {"h", "pp", "snow"})
        tc.assertIn(col["snow"], (0, 1))
        tc.assertTrue(col["pp"] is None or (isinstance(col["pp"], int) and 0 <= col["pp"] <= 100), col)
        if col["h"]:
            tc.assertRegex(col["h"], r"^([01][0-9]|2[0-3])$")
            hs.append(int(col["h"]))
    if len(hs) == 9:
        # consecutive local hours; a daylight-saving change repeats or skips one hour
        steps = [(b - a) % 24 for a, b in zip(hs, hs[1:])]
        tc.assertTrue(set(steps) <= {0, 1, 2} and sum(1 for x in steps if x != 1) <= 1, hs)
    f = p["fresh"]
    tc.assertEqual(set(f), {"wx", "air", "rad", "cam"})
    tc.assertEqual(set(f["wx"]), {"st", "age", "fail"})
    tc.assertEqual(set(f["air"]), {"st", "age", "fail"})
    tc.assertEqual(set(f["rad"]), {"st", "age", "fail", "date"})
    for v in [f["wx"], f["air"], f["rad"]] + list(f["cam"].values()):
        tc.assertIn(v["st"], ("ok", "stale", "dead", "none"))
    json.dumps(p)
    if mem is not None:
        _check_mem_types(tc, mem)
        json.dumps(mem)


def _check_mem_types(tc, x):
    if isinstance(x, dict):
        for k, v in x.items():
            tc.assertIsInstance(k, str)
            _check_mem_types(tc, v)
    elif isinstance(x, list):
        for v in x:
            _check_mem_types(tc, v)
    else:
        tc.assertTrue(x is None or isinstance(x, (str, int, float, bool)), repr(x))


def st0(p):
    return p["status"][0] if p["status"] else {"key": None, "text": "", "P": None}


def wg(p, k):
    """Widget by key."""
    return [x for x in p["widgets"] if x["k"] == k][0]


def wv(p, k):
    x = wg(p, k)
    return (x["L"], x["val"], x["unit"], x["note"])


def keys(p):
    return [s["key"] for s in p["status"]]


T14 = ts_loc(*OCT9, 14, 0)
T23 = ts_loc(*OCT9, 23, 0)


def scene_of(now=T14, check=None, **kw):
    p = panel(now, **kw)
    if check is not None:
        check_panel(check, p)
    return p["now"]["scene"]


def _gust_all(g, mean=None):
    hr = {j: {"gust": g} for j in range(-24, 25)}
    if mean is not None:
        for j in hr:
            hr[j]["wind"] = mean
    return hr


# ---------------------------------------------------------------- tests
class Scenarios(unittest.TestCase):
    def run_scen(self, n, mem=None, cfg=CFG):
        now, kw = scen(n)
        p = panel(now, mem=mem, cfg=cfg, **kw)
        check_panel(self, p, cfg=cfg)
        return p

    def test_01_calm(self):
        p = self.run_scen(1)
        self.assertEqual(p["now"]["scene"], "mostly-clear-day")
        self.assertEqual((p["now"]["tTxt"], p["now"]["word"], p["now"]["ahead"]), ("16°", "Comfortable", []))
        self.assertEqual(wv(p, "wind"), (0, "3", "m/s", "gusts 6, west"))
        self.assertEqual(wv(p, "air"), (0, "25", "AQI", "good"))
        self.assertEqual(wv(p, "uv"), (0, "2", "of 11", "up to 2 today"))
        self.assertEqual(wv(p, "radiation"), (0, "0.12", "µSv/h", "normal, 8 stations"))
        self.assertEqual(wv(p, "sun"), (-1, "17:45", "sunset", "sunrise 06:30"))
        self.assertEqual(wv(p, "pressure")[:3], (0, "1012", "hPa"))
        self.assertEqual((st0(p)["P"], st0(p)["tone"]), (5, "calm"))
        self.assertFalse(p["rot"])

    def test_02_rain_starts_hourly(self):
        p = self.run_scen(2)
        s = st0(p)
        self.assertEqual(s["key"], "rain_at")
        self.assertIn(s["text"], ("Rain starting around 16:00", "Light rain from around 16:00"))
        self.assertEqual(p["now"]["scene"], "partly-cloudy-day")
        self.assertEqual(p["now"]["ahead"], ["rain", "partly-cloudy-night"])
        self.assertEqual([(c["h"], c["pp"]) for c in p["rain"]["cols"][:3]], [("15", 10), ("16", 70), ("17", 60)])

    def test_03_rain_ends_in_hour(self):
        p = self.run_scen(3)
        self.assertEqual(p["now"]["scene"], "rain")
        self.assertEqual((st0(p)["key"], st0(p)["text"]), ("rain_end", "Dry within the hour"))
        self.assertEqual(p["now"]["ahead"], ["overcast"])

    def test_04_wind_and_rain_rotate(self):
        p = self.run_scen(4)
        self.assertEqual(p["now"]["scene"], "wind")
        self.assertEqual(wv(p, "wind"), (2, "9", "m/s", "gusts 17, north-west"))
        self.assertEqual(st0(p)["key"], "wind2")
        self.assertIn("17 m/s", st0(p)["text"])
        self.assertEqual(p["status"][1]["key"], "rain_at")
        self.assertIn("17:00", p["status"][1]["text"])
        self.assertTrue(p["rot"])
        self.assertIn("rain", p["now"]["ahead"])

    def test_05_rad_stale(self):
        p = self.run_scen(5)
        self.assertEqual(wv(p, "radiation"), (None, u"—", "", "as of 8 Oct"))
        self.assertEqual((st0(p)["key"], st0(p)["tone"]), ("rad_stale", "fail"))
        self.assertIn("30 hours", st0(p)["text"])
        self.assertEqual(p["fresh"]["rad"]["st"], "stale")

    def test_06_rad_single_station(self):
        p = self.run_scen(6)
        self.assertEqual(wv(p, "radiation"), (1, "0.34", "µSv/h", "1 of 7 stations, Quarry"))
        self.assertEqual(st0(p)["text"], "0.34 µSv/h at one station — watching")

    def test_07_rad_confirmed_high(self):
        p = self.run_scen(7)
        # two stations above 0.6: the second highest confirms the level
        self.assertEqual(wv(p, "radiation"), (3, "0.65", "µSv/h", "2 of 8 stations"))
        self.assertEqual((st0(p)["key"], st0(p)["tone"]), ("rad_crit", "crit"))
        self.assertIn("0.65", st0(p)["text"])

    def test_08_heat_and_uv(self):
        p = self.run_scen(8)
        n = p["now"]
        self.assertEqual((n["scene"], n["tTxt"], n["L"]), ("heat", "35°", 3))
        self.assertEqual(n["word"], wb.utci_word(n["utci"], 29))
        self.assertEqual(wv(p, "uv"), (3, "9", "of 11", "up to 9 today"))
        self.assertEqual(wv(p, "air")[::3], (1, "Ozone elevated"))
        self.assertEqual(st0(p)["key"], "heat_uv")
        self.assertEqual(st0(p)["text"], u"Heat %s and UV 9 — shade till 17:00" % n["utciTxt"])

    def test_09_cold_wind(self):
        p = self.run_scen(9)
        n = p["now"]
        self.assertEqual((n["scene"], n["tTxt"]), ("overcast", u"−6°"))
        self.assertTrue(n["utciTxt"].startswith(u"−"))
        self.assertEqual(wv(p, "wind"), (1, "6", "m/s", "gusts 12, west"))
        self.assertEqual((st0(p)["key"], st0(p)["P"]), ("cold_wind", 2))
        self.assertEqual(st0(p)["text"], u"Cold wind — feels like %s" % n["utciTxt"])
        self.assertGreaterEqual(n["L"], 2)

    def test_10_pressure_drop(self):
        p = self.run_scen(10, cfg=CFG_MM)
        self.assertEqual(wv(p, "pressure"), (3, "747", "mmHg", u"−10 in 24 h"))
        self.assertEqual(st0(p)["text"], "Pressure fell 10 mmHg in 24 hours")
        p = self.run_scen(10)
        self.assertEqual(wv(p, "pressure"), (3, "996", "hPa", u"−13 in 24 h"))
        self.assertEqual(st0(p)["text"], "Pressure fell 13 hPa in 24 hours")

    def test_11_dust_storm(self):
        p = self.run_scen(11)
        self.assertEqual(p["now"]["scene"], "dust-storm")
        self.assertEqual(wv(p, "air"), (4, "105", "AQI", "PM10 elevated"))
        self.assertEqual(wg(p, "wind")["L"], 3)
        self.assertEqual((st0(p)["key"], st0(p)["tone"]), ("dust_storm", "crit"))

    def test_12_weather_dead(self):
        p = self.run_scen(12)
        n = p["now"]
        self.assertEqual((n["scene"], n["t"], n["tTxt"], n["utciTxt"], n["word"], n["stale"], n["ahead"]),
                         ("not-available", None, u"—", u"—", "No data", True, []))
        for k in ("wind", "uv", "pressure"):
            self.assertEqual(wv(p, k), (None, u"—", "", "as of 10:00"), k)
        self.assertEqual(wg(p, "sun")["L"], -1)           # sun times do not depend on freshness
        self.assertEqual(wg(p, "air")["L"], 0)
        self.assertEqual(st0(p)["key"], "wx_stale")
        self.assertIn("4 hours", st0(p)["text"])
        self.assertEqual(p["fresh"]["wx"]["st"], "dead")
        self.assertTrue(p["rain"]["gray"])

    def test_13_fog_and_ice(self):
        p = self.run_scen(13)
        self.assertEqual(p["now"]["scene"], "ice")         # black ice outranks fog
        self.assertEqual(st0(p)["text"], u"Fog and black ice — mind the steps")

    def test_14_rain_tomorrow_morning(self):
        p = self.run_scen(14)
        self.assertEqual(st0(p)["text"], "Rain in the morning, 80%")
        self.assertEqual(p["now"]["scene"], "partly-cloudy-night")
        self.assertEqual(wv(p, "uv")[3], "up to 3 tomorrow")
        self.assertEqual(wv(p, "sun"), (-1, "06:30", "sunrise", "sunset 17:45"))
        now, kw = scen_14_evening()
        p2 = panel(now, **kw)
        check_panel(self, p2)
        self.assertEqual(st0(p2)["text"], "Rain tomorrow morning, 80%")

    def test_15_all_level2(self):
        p = self.run_scen(15, cfg=CFG_MM)
        self.assertEqual([wg(p, k)["L"] for k in ("wind", "air", "uv", "pressure", "radiation")], [2, 2, 2, 2, 2])
        self.assertEqual(wv(p, "radiation")[3], "2 of 8 stations")
        self.assertEqual(wv(p, "air")[3], "PM2.5 elevated")
        self.assertEqual(wv(p, "pressure")[3], "+7 in 24 h")
        self.assertEqual(keys(p), ["rad2", "air2", "wind2"])
        self.assertEqual(st0(p)["text"], "Radiation above usual: 0.45 µSv/h")
        self.assertTrue(p["rot"])


class Scenes(unittest.TestCase):
    """Each of the 39 scenes from a synthetic input; rule priorities."""

    CASES = {
        "clear-day": dict(fckw=dict(code=0, cloud=5)),
        "clear-night": dict(now=T23, fckw=dict(code=0, cloud=5)),
        "mostly-clear-day": dict(fckw=dict(code=1, cloud=30)),
        "mostly-clear-night": dict(now=T23, fckw=dict(code=1, cloud=30)),
        "partly-cloudy-day": dict(fckw=dict(code=2, cloud=50)),
        "partly-cloudy-night": dict(now=T23, fckw=dict(code=2, cloud=50)),
        "mostly-cloudy-day": dict(fckw=dict(code=2, cloud=70)),
        "mostly-cloudy-night": dict(now=T23, fckw=dict(code=2, cloud=70)),
        "overcast": dict(fckw=dict(code=3, cloud=100)),
        "fog": dict(fckw=dict(code=45, vis=500, t=5, rh=99, cloud=100)),
        "rime-fog": dict(fckw=dict(code=48, vis=300, t=-3, rh=99, cloud=100)),
        "drizzle": dict(fckw=dict(code=53, prec=0.05, t=8, cloud=100)),
        "rain": dict(fckw=dict(code=61, prec=0.2, t=8, cloud=100)),
        "heavy-rain": dict(fckw=dict(code=63, prec=3.0, t=12, cloud=100)),
        "freezing-drizzle": dict(fckw=dict(code=56, prec=0.05, t=-1, cloud=100)),
        "freezing-rain": dict(fckw=dict(code=66, prec=0.2, t=-1, cloud=100)),
        "showers-day": dict(fckw=dict(code=80, prec=0.3, t=18, cloud=60)),
        "showers-night": dict(now=T23, fckw=dict(code=80, prec=0.3, t=18, cloud=60)),
        "sleet": dict(fckw=dict(code=73, prec=0.3, t=1.0, rain=0.2, snowfall=0.1, cloud=100)),
        "snow": dict(fckw=dict(code=71, prec=0.1, t=-3, cloud=100)),
        "heavy-snow": dict(fckw=dict(code=75, prec=0.5, t=-4, cloud=100)),
        "snow-grains": dict(fckw=dict(code=77, prec=0.05, t=-2, cloud=100)),
        "snow-showers-day": dict(fckw=dict(code=85, prec=0.1, t=-2, cloud=60)),
        "snow-showers-night": dict(now=T23, fckw=dict(code=85, prec=0.1, t=-2, cloud=60)),
        "thunderstorm": dict(fckw=dict(code=95, prec=0.5, t=22, gust=12, cloud=90)),
        "thunderstorm-heavy": dict(fckw=dict(code=95, prec=0.5, t=22, gust=22, cloud=90)),
        "thunderstorm-hail": dict(fckw=dict(code=96, prec=1.0, t=22, cloud=90)),
        "wind": dict(fckw=dict(code=1, wind=9, gust=17, hr=_gust_all(17, 9))),
        "blizzard": dict(fckw=dict(code=73, prec=0.3, t=-5, wind=9, gust=17, cloud=100, hr=_gust_all(17, 9))),
        "drifting-snow": dict(fckw=dict(code=1, t=-6, depth=0.15, wind=7, gust=13, hr=_gust_all(13, 7))),
        "dust-haze": dict(airkw=dict(aqi=60, pm10=80, dust=60)),
        "dust-storm": dict(fckw=dict(wind=9, gust=17, hr=_gust_all(17, 9)), airkw=dict(aqi=90, pm10=120, dust=60)),
        "smoke": dict(airkw=dict(aqi=120, pm25=90, pm10=110, dust=2)),
        "dry-wind": dict(fckw=dict(code=0, cloud=0, t=30, rh=20, wind=6, gust=10, sw=200)),
        "haze": dict(fckw=dict(code=1, t=20, rh=50, vis=3000)),
        "heat": dict(fckw=dict(code=0, cloud=0, t=36, rh=30)),
        "frost": dict(fckw=dict(code=0, cloud=0, t=-17, wind=1, gust=2)),
        "ice": dict(fckw=dict(code=3, cloud=100, t=-2, hr={k: {"mm": 0.4, "pp": 80, "t": 1.0} for k in range(-5, 1)})),
        "not-available": dict(nofc=True),
    }

    def run_case(self, name, cfg=CFG):
        kw = dict(self.CASES[name])
        now = kw.pop("now", T14)
        nofc = kw.pop("nofc", False)
        raw, src = make(now, **kw)
        if nofc:
            raw["fc"] = None
        p = wb.build_panel(raw, src, {}, now, cfg)
        check_panel(self, p, cfg=cfg)
        return p

    def test_all_39(self):
        self.assertEqual(len(SCENE_NAMES), 39)
        self.assertEqual(len(set(SCENE_NAMES)), 39)
        self.assertEqual(set(self.CASES), set(SCENE_NAMES))
        for name in SCENE_NAMES:
            p = self.run_case(name)
            self.assertEqual(p["now"]["scene"], name, (name, p["now"]))

    def test_all_39_in_other_units(self):
        cfg = Settings(panels=PANELS, temp="F", wind="mph", press="inHg")
        for name in SCENE_NAMES:
            self.assertEqual(self.run_case(name, cfg)["now"]["scene"], name)

    def test_codes_without_new_fields(self):
        # an older answer (no rain/showers/snowfall) -- scenes by code and precipitation
        for name in ("rain", "snow", "freezing-rain", "heavy-snow", "drizzle", "fog", "thunderstorm-hail"):
            kw = json.loads(json.dumps({k: v for k, v in self.CASES[name].items() if k != "now"}))
            kw["fckw"]["fields"] = False
            self.assertEqual(scene_of(check=self, **kw), name)
        # a rain code at T <= 0 is freezing rain without the fields too
        self.assertEqual(scene_of(fckw=dict(code=61, prec=0.2, t=-0.5, fields=False, cloud=100)), "freezing-rain")

    def test_priority_freezing_over_snow(self):
        # ICON rewrites rain into snow below -1 degC: code 71 while the fields say liquid rain
        p = panel(T14, fckw=dict(code=71, prec=0.2, t=-0.5, rain=0.2, snowfall=0.0, cloud=100))
        check_panel(self, p)
        self.assertEqual(p["now"]["scene"], "freezing-rain")
        self.assertIn("frz_rain", keys(p))
        # rain together with snow at T <= 0 is sleet, not freezing rain (and no P0 ice-rink alarm)
        self.assertEqual(scene_of(fckw=dict(code=73, prec=0.3, t=-0.3, rain=0.2, snowfall=0.1)), "sleet")
        p = panel(T14, fckw=dict(code=71, prec=0.8, t=-3, rain=0.1, snowfall=0.5, cloud=100,
                                 cols={0: {"pp": 90, "mm": 2.4, "code": 71, "t": -3, "rain": 0.3, "snowfall": 1.5}}))
        check_panel(self, p)
        self.assertEqual(p["now"]["scene"], "sleet")
        self.assertNotIn("frz_rain", keys(p))
        self.assertEqual(p["rain"]["cols"][0]["snow"], 1)
        # a trace of snow (below 0.1 mm of water) does not cancel freezing rain
        self.assertEqual(scene_of(fckw=dict(code=71, prec=0.2, t=-1, rain=0.2, snowfall=0.01)), "freezing-rain")
        # drizzle at T <= 0 is freezing drizzle
        self.assertEqual(scene_of(fckw=dict(code=53, prec=0.05, t=-0.5)), "freezing-drizzle")
        self.assertEqual(scene_of(fckw=dict(code=56, prec=0.05, t=-1, rain=0.05)), "freezing-drizzle")

    def test_priority_sleet_by_snow_code(self):
        self.assertEqual(scene_of(fckw=dict(code=71, prec=0.2, t=1.2, rain=0.2, snowfall=0.0)), "sleet")
        self.assertEqual(scene_of(fckw=dict(code=71, prec=0.2, t=0.3, rain=0.2, snowfall=0.0)), "snow")
        self.assertEqual(scene_of(fckw=dict(code=61, prec=0.2, t=3.0, rain=0.1, snowfall=0.07)), "sleet")

    def test_priority_snow(self):
        hr = _gust_all(16)
        self.assertEqual(scene_of(fckw=dict(code=75, prec=0.5, t=-4, gust=16, hr=hr)), "blizzard")
        self.assertEqual(scene_of(fckw=dict(code=73, prec=0.3, t=-4, vis=300)), "heavy-snow")
        self.assertEqual(scene_of(fckw=dict(code=73, prec=1.4, t=-4, snowfall=1.0)), "heavy-snow")  # 5.7 mm/h
        self.assertEqual(scene_of(fckw=dict(code=73, prec=0.3, t=-4)), "snow")
        self.assertEqual(scene_of(fckw=dict(code=77, prec=0.05, t=-2, vis=300)), "heavy-snow")
        self.assertEqual(scene_of(fckw=dict(code=85, prec=0.1, t=-2, cloud=95)), "snow")       # no clear spells
        self.assertEqual(scene_of(fckw=dict(code=86, prec=0.3, t=-2, cloud=60)), "heavy-snow")
        # snowing is not "severe frost"
        self.assertEqual(scene_of(fckw=dict(code=71, prec=0.1, t=-17)), "snow")

    def test_priority_rain(self):
        self.assertEqual(scene_of(fckw=dict(code=80, prec=0.3, t=18, cloud=95)), "rain")       # no clear spells
        self.assertEqual(scene_of(fckw=dict(code=82, prec=1.0, t=18, cloud=60)), "heavy-rain")
        self.assertEqual(scene_of(fckw=dict(code=65, prec=1.0, t=12)), "heavy-rain")
        # heavy by the 12-hour sum (from 15 mm): 12 x 1.5 mm, and it is raining now
        hr = {k: {"mm": 1.5, "pp": 90, "code": 61, "t": 12} for k in range(-11, 1)}
        self.assertEqual(scene_of(fckw=dict(code=61, prec=0.2, t=12, cloud=100, hr=hr)), "heavy-rain")
        self.assertEqual(scene_of(fckw=dict(code=51, prec=0.05, t=12, cloud=100, hr=hr)), "drizzle")
        # with wind it is still rain (wind only without precipitation)
        self.assertEqual(scene_of(fckw=dict(code=61, prec=0.2, t=12, gust=17, hr=_gust_all(17))), "rain")
        self.assertEqual(scene_of(fckw=dict(code=97, prec=1.0, t=22)), "thunderstorm-heavy")
        self.assertEqual(scene_of(fckw=dict(code=96, prec=1.0, t=-1)), "thunderstorm-hail")

    def test_priority_dry_states(self):
        hr17 = _gust_all(17, 9)
        self.assertEqual(scene_of(fckw=dict(gust=17, wind=9, hr=hr17), airkw=dict(dust=45, pm10=60)), "dust-storm")
        self.assertEqual(scene_of(fckw=dict(gust=17, wind=9, hr=hr17), airkw=dict(dust=30, pm10=60)), "wind")
        # smoke is not confused with dust and needs PM10
        self.assertEqual(scene_of(airkw=dict(pm25=90, pm10=150, dust=60)), "dust-haze")
        self.assertNotEqual(scene_of(airkw=dict(pm25=90, pm10=None, dust=2)), "smoke")
        self.assertNotEqual(scene_of(airkw=dict(pm25=90, pm10=110, dust=None)), "smoke")
        self.assertNotEqual(scene_of(airkw=dict(pm25=60, pm10=70, dust=2)), "smoke")
        # dust is not seen when the air reading is dead
        self.assertNotEqual(scene_of(airkw=dict(dust=80, pm10=90), srckw=dict(air=14 * 3600)), "dust-haze")
        # haze -- only with dry air and visibility 1-5 km
        self.assertEqual(scene_of(fckw=dict(code=1, t=20, rh=90, vis=3000)), "mostly-clear-day")
        self.assertEqual(scene_of(fckw=dict(code=1, t=20, rh=50, vis=6000)), "mostly-clear-day")

    def test_fog_needs_low_visibility(self):
        self.assertEqual(scene_of(fckw=dict(code=45, vis=5000, cloud=90)), "overcast")
        self.assertEqual(scene_of(ts_loc(*OCT9, 5, 45), fckw=dict(code=45, vis=12940, day=0, cloud=63)),
                         "partly-cloudy-night")
        self.assertEqual(scene_of(fckw=dict(code=45, vis=None, cloud=100)), "fog")
        self.assertEqual(scene_of(fckw=dict(code=45, vis=600, t=-2, cloud=100)), "rime-fog")

    def test_ice_and_drifting(self):
        past = {k: {"mm": 0.4, "pp": 80, "t": 1.0} for k in range(-5, 1)}
        # black ice outranks fog and drifting snow
        self.assertEqual(scene_of(fckw=dict(code=45, vis=300, t=-1, cloud=100, hr=past)), "ice")
        # no wet past -- no black ice
        self.assertEqual(scene_of(fckw=dict(code=3, cloud=100, t=-2)), "overcast")
        # a thaw on lying snow within 12 h -- black ice
        thaw = dict(code=3, cloud=100, t=-2, depth=0.1, t_fn=lambda off: 2.0 if -8 <= off < -2 else -2.0)
        self.assertEqual(scene_of(fckw=thaw), "ice")
        # a thaw a day ago crusted the snow: no drifting, and nothing wet within 12 h
        crust = dict(code=1, t=-3, depth=0.1, wind=7, gust=13, hr=_gust_all(13, 7),
                     t_fn=lambda off: 2.0 if off <= -15 else -3.0)
        self.assertEqual(scene_of(fckw=crust), "mostly-clear-day")
        # fresh snow within 12 h drifts even without snow-depth data
        hr = _gust_all(13, 7)
        hr.update({k: {"gust": 13, "wind": 7, "mm": 0.5, "code": 71, "t": -6} for k in range(-8, -5)})
        fresh = dict(code=1, t=-6, depth=0.0, wind=7, gust=13, hr=hr)
        self.assertEqual(scene_of(fckw=fresh), "drifting-snow")
        # wind below the threshold -- no
        self.assertEqual(scene_of(fckw=dict(code=1, t=-6, depth=0.15, gust=9)), "mostly-clear-day")
        # no drifting above zero
        self.assertEqual(scene_of(fckw=dict(code=1, t=1, depth=0.15, gust=13, hr=_gust_all(13))), "mostly-clear-day")

    def test_wind_only_when_windy_now(self):
        # calm now, gusts of 18 expected in 3 h -- the icon is not wind
        now = ts_loc(*OCT9, 13, 0)
        self.assertEqual(scene_of(now, fckw=dict(code=0, wind=2, gust=4, hr={3: {"gust": 18, "wind": 9}})),
                         "clear-day")

    def test_heat_frost_by_utci(self):
        p = panel(T14, fckw=dict(code=0, cloud=0, t=33, rh=60, wind=0.5, gust=1, sw=900))
        self.assertGreaterEqual(p["now"]["utci"], 38)
        self.assertEqual(p["now"]["scene"], "heat")
        # wind 8 m/s (level L1, no wind scene) at -12 degC -- UTCI below -27
        p = panel(T14, fckw=dict(code=3, cloud=100, t=-12, rh=80, wind=8, gust=13, hr=_gust_all(13, 8)))
        self.assertLessEqual(p["now"]["utci"], -27)
        self.assertEqual(p["now"]["scene"], "frost")


class Widgets(unittest.TestCase):
    def test_wind(self):
        now = ts_loc(*OCT9, 13, 0)
        p = panel(now, fckw=dict(code=0, wind=2, gust=4, hr={3: {"gust": 18, "wind": 9}}))
        check_panel(self, p)
        self.assertEqual(wv(p, "wind"), (2, "2", "m/s", "gusts 18 by 16:00"))
        p = panel(now, fckw=dict(wind=0.3, gust=2))
        self.assertEqual(wv(p, "wind"), (0, "0", "m/s", "gusts 2, calm"))
        for deg, word in ((0, "north"), (44, "north-east"), (90, "east"), (135, "south-east"),
                          (180, "south"), (225, "south-west"), (270, "west"), (337, "north-west"), (350, "north")):
            self.assertEqual(wv(panel(now, fckw=dict(wdir=deg)), "wind")[3], "gusts 6, " + word)

    def test_air(self):
        self.assertEqual(wv(panel(T14), "air"), (0, "25", "AQI", "good"))
        self.assertEqual(wv(panel(T14, airkw=dict(aqi=66, no2=130)), "air")[::3], (3, u"NO₂ elevated"))
        # AQI "moderate" while no pollutant is above the WHO level -- the scale word
        self.assertEqual(wv(panel(T14, airkw=dict(aqi=45)), "air"), (1, "45", "AQI", "moderate"))
        p = panel(T14, airkw=dict(aqi=None, pm25=None, pm10=None, o3=None, no2=None))
        self.assertEqual(wv(p, "air"), (None, u"—", "", "no data"))

    def test_uv(self):
        for uv, val, L in ((0.0, "0", 0), (2.4, "2", 0), (2.5, "3", 1), (5.4, "5", 1), (5.6, "6", 2),
                           (7.4, "7", 2), (7.6, "8", 3), (10.4, "10", 3), (10.6, "11", 4), (12.0, "12", 4)):
            x = wg(panel(T14, fckw=dict(uv=uv, uvmax=12.0)), "uv")
            self.assertEqual((x["val"], x["L"]), (val, L), uv)
        self.assertEqual(wv(panel(T14, fckw=dict(uv=2.0, uvmax=4.4)), "uv")[3], "up to 4 today")
        # after sunset tomorrow's maximum; at night before sunrise today's
        self.assertEqual(wv(panel(T23, fckw=dict(uvmax=3.6)), "uv")[1:], ("0", "of 11", "up to 4 tomorrow"))
        self.assertEqual(wv(panel(ts_loc(*OCT9, 3, 0), fckw=dict(uvmax=3.6)), "uv")[3], "up to 4 today")

    def test_pressure(self):
        p0 = NORM - 2.5
        cases = [(dict(d24=-4.2), 1, u"−4 in 24 h"), (dict(d24=7.2), 2, "+7 in 24 h"),
                 (dict(d24=0.2), 0, "no change"), (dict(d24=-1.2), 0, u"−1 in 24 h"),
                 (dict(d3=-6.0, d24=-6.0), 2, u"−6 in 3 h")]
        for kw, L, note in cases:
            p = panel(T14, cfg=CFG_MM, fckw=dict(p_fn=p_linear(p0, **kw)))
            self.assertEqual(wv(p, "pressure")[::3], (L, note), kw)
        p = panel(T14, cfg=CFG_MM, fckw=dict(p_fn=p_linear(738.0)))
        self.assertEqual(wv(p, "pressure")[1], "738")
        self.assertRegex(wv(p, "pressure")[3], "^[0-9]+ below normal$")
        # the same in hPa and inHg
        p = panel(T14, fckw=dict(p_fn=p_linear(p0, d24=-4.2)))
        self.assertEqual(wv(p, "pressure"), (1, "1009", "hPa", u"−6 in 24 h"))
        p = panel(T14, cfg=Settings(panels=PANELS, press="inHg"), fckw=dict(p_fn=p_linear(p0, d24=-4.2)))
        self.assertEqual(wv(p, "pressure"), (1, "29.79", "inHg", u"−0.17 in 24 h"))

    def test_pressure_normal_follows_elevation(self):
        # 640 mmHg is normal at about 1400 m and very low at sea level
        fc = dict(p_fn=p_linear(640.0))
        raw, src = make(T14, fckw=fc)
        raw["fc"]["elevation"] = 1400.0
        p = wb.build_panel(raw, src, {}, T14, CFG_MM)
        self.assertEqual(wg(p, "pressure")["L"], 0)
        raw["fc"]["elevation"] = 0.0
        p = wb.build_panel(raw, src, {}, T14, CFG_MM)
        self.assertEqual(wg(p, "pressure")["L"], 2)
        self.assertIn("press_abs", keys(p))
        # unknown elevation: no deviation criterion at all
        del raw["fc"]["elevation"]
        p = wb.build_panel(raw, src, {}, T14, CFG_MM)
        self.assertEqual(wg(p, "pressure")["L"], 0)
        self.assertAlmostEqual(wb.press_norm(0), 760.0, delta=0.1)

    def test_rad_counts(self):
        def st(n):
            return [(5.0 + i, "Sensor %d" % i, 0.12) for i in range(n)]
        for n, note in ((1, "normal, 1 station"), (2, "normal, 2 stations"), (5, "normal, 5 stations"),
                        (21, "normal, 21 stations")):
            self.assertEqual(wv(panel(T14, stations=st(n)), "radiation")[3], note)

    def test_rad_hidden_safe(self):
        # no radiation data at all: a dash, no status line
        raw, src = make(T14)
        raw["rad"] = None
        src["rad"] = {"ok": 0, "fail": 0, "err": ""}
        p = wb.build_panel(raw, src, {}, T14, CFG)
        check_panel(self, p)
        self.assertEqual(wv(p, "radiation"), (None, u"—", "", "no data"))
        self.assertFalse([k for k in keys(p) if k.startswith("rad")])
        # the provider answered, but there is no station in range
        p = panel(T14, stations=[])
        self.assertEqual(wv(p, "radiation"), (None, u"—", "", "no stations nearby"))
        # radiation turned off: its slot shows humidity instead
        cfg = Settings(panels=PANELS, rad_provider="none")
        p = panel(T14, cfg=cfg)
        check_panel(self, p, cfg=cfg)
        self.assertEqual([w["k"] for w in p["widgets"]], ["wind", "air", "uv", "pressure", "humidity", "sun"])
        # the credit line names the radiation source only when there is one
        self.assertEqual(p["attribution"], u"Weather: Open-Meteo.com · Air: CAMS · Radiation: Test network")
        raw, src = make(T14)
        raw["rad"] = None
        self.assertEqual(wb.build_panel(raw, src, {}, T14, CFG)["attribution"],
                         u"Weather: Open-Meteo.com · Air: CAMS")

    def test_rad_timestamps(self):
        # ISO strings and milliseconds are accepted as reading times
        raw, src = make(T14)
        for i, s in enumerate(raw["rad"]["stations"]):
            s["ts"] = "2026-10-09T10:00:00Z" if i % 2 else (T14 - 3600) * 1000
        p = wb.build_panel(raw, src, {}, T14, CFG)
        check_panel(self, p)
        self.assertEqual(p["fresh"]["rad"]["date"], "2026-10-09")
        self.assertEqual(wg(p, "radiation")["L"], 0)

    def test_humidity(self):
        cfg = Settings(panels=PANELS, widgets="wind, air, humidity, pressure, sun, uv")
        p = panel(T14, cfg=cfg, fckw=dict(t=20, rh=60))
        check_panel(self, p, cfg=cfg)
        self.assertEqual([w["k"] for w in p["widgets"]], ["wind", "air", "humidity", "pressure", "sun", "uv"])
        self.assertEqual(wv(p, "humidity"), (0, "60", "%", u"dew point 12°"))
        self.assertEqual(wv(panel(T14, cfg=cfg, fckw=dict(t=30, rh=75)), "humidity")[0], 3)    # dew 25
        self.assertEqual(wv(panel(T14, cfg=cfg, fckw=dict(t=20, rh=20)), "humidity")[0], 1)    # very dry
        f = Settings(panels=PANELS, widgets=cfg.widgets, temp="F")
        self.assertEqual(wv(panel(T14, cfg=f, fckw=dict(t=20, rh=60)), "humidity")[3], u"dew point 54°")

    def test_sun(self):
        self.assertEqual(wv(panel(ts_loc(*OCT9, 5, 0)), "sun"), (-1, "06:30", "sunrise", "sunset 17:45"))
        self.assertEqual(wv(panel(ts_loc(*OCT9, 18, 0)), "sun"), (-1, "06:30", "sunrise", "sunset 17:45"))
        raw, src = make(T14)
        raw["fc"]["daily"]["sunrise"] = [None, None]
        raw["fc"]["daily"]["sunset"] = [None, None]
        p = wb.build_panel(raw, src, {}, T14, CFG)
        check_panel(self, p)
        self.assertEqual(wv(p, "sun"), (None, u"—", "", "no data"))

    def test_missing_sources(self):
        raw, src = make(T14)
        raw.update(fc=None, air=None, rad=None)
        p = wb.build_panel(raw, src, {}, T14, CFG)
        check_panel(self, p)
        for x in p["widgets"]:
            self.assertEqual((x["L"], x["val"], x["note"]), (None, u"—", "no data"), x)
        self.assertEqual(p["now"]["scene"], "not-available")
        self.assertTrue(p["rain"]["gray"])

    def test_stale_and_dead(self):
        # air 5 h old: L0 is hidden, the reading time is shown
        raw, src = make(T14, srckw=dict(air=5 * 3600))
        raw["air"]["current"]["time"] = T14 - 5 * 3600
        p = wb.build_panel(raw, src, {}, T14, CFG)
        check_panel(self, p)
        self.assertEqual(wv(p, "air"), (None, u"—", "", "as of 09:00"))
        # stale but dangerous (L>=2) -- the value stays
        raw, src = make(T14, airkw=dict(aqi=68, pm25=33), srckw=dict(air=5 * 3600))
        raw["air"]["current"]["time"] = T14 - 5 * 3600
        p = wb.build_panel(raw, src, {}, T14, CFG)
        self.assertEqual(wv(p, "air"), (2, "68", "AQI", "as of 09:00"))
        # dead (14 h) -- a dash even at L>=2
        raw, src = make(T14, airkw=dict(aqi=68, pm25=33), srckw=dict(air=14 * 3600))
        raw["air"]["current"]["time"] = T14 - 14 * 3600
        p = wb.build_panel(raw, src, {}, T14, CFG)
        self.assertEqual(wv(p, "air")[:2], (None, u"—"))
        # radiation readings 4 days old with a high level -- the value and the reading date
        p = panel(T14, over={"Harbour": 0.45, "Quarry": 0.45}, rts=ts_loc(2026, 10, 5, 12))
        self.assertEqual(wv(p, "radiation"), (2, "0.45", "µSv/h", "as of 5 Oct"))
        self.assertEqual(st0(p)["key"], "rad_stale")

    def test_notes_fit(self):
        worst = [wb._fit([ph.gusts(g) + ", " + d]) for g in ("9", "99", "162") for d in ph.DIRECTIONS]
        self.assertLessEqual(max(len(x) for x in worst), wb.NOTELEN)
        self.assertLessEqual(len(ph.gusts_by("162", "23:00")), wb.NOTELEN)
        self.assertLessEqual(len(ph.of_stations(21, 21) + ", "), wb.NOTELEN)
        for k in ("d24", "fc_day", "3h", "below", "above"):
            self.assertLessEqual(len(ph.press_note(k, u"−1.18", "23:00")), wb.NOTELEN)
        self.assertLessEqual(len(ph.press_note("fc_at", u"−1.18", "23:00")), wb.NOTELEN)
        self.assertLessEqual(len(ph.dew_note(u"−100°")), wb.NOTELEN)
        self.assertLessEqual(len(ph.uv_peak(15, True)), wb.NOTELEN)
        self.assertLessEqual(max(len(ph.air_note(c)) for c in ph.POLLUTANTS), wb.NOTELEN)
        self.assertLessEqual(max(len(v) for v in ph.AIR_WORDS.values()), wb.NOTELEN)
        self.assertLessEqual(len(ph.as_of_date(datetime(2026, 12, 31))), wb.NOTELEN)


class RainCols(unittest.TestCase):
    def test_hour_shift_and_labels(self):
        now = ts_loc(*OCT9, 21, 30)
        hr = {k: {"pp": 7 * k, "mm": round(0.1 * k, 1)} for k in range(1, 14)}
        p = panel(now, fckw=dict(hr=hr))
        check_panel(self, p)
        cols = p["rain"]["cols"]
        self.assertEqual([c["pp"] for c in cols], [7 * (i + 1) for i in range(9)])   # [H+i, H+i+1) <- H+i+1
        self.assertEqual([c["h"] for c in cols], ["21", "22", "23", "00", "01", "02", "03", "04", "05"])
        self.assertFalse(p["rain"]["gray"])

    def test_snow_flag(self):
        p = panel(T14, fckw=dict(t=-3, cols={i: {"pp": 80, "mm": 0.6, "code": 73, "t": -3} for i in range(0, 3)}))
        self.assertEqual([c["snow"] for c in p["rain"]["cols"][:4]], [1, 1, 1, 0])
        # freezing rain under a snow code is not snow
        p = panel(T14, fckw=dict(t=-1, cols={0: {"pp": 80, "mm": 0.6, "code": 71, "t": -1, "rain": 0.6,
                                                 "snowfall": 0.0}}))
        self.assertEqual(p["rain"]["cols"][0]["snow"], 0)

    def test_gray_when_old(self):
        p = panel(T14, srckw=dict(fc=3 * 3600), fc_at=T14 - 3 * 3600)
        check_panel(self, p)
        self.assertTrue(p["rain"]["gray"])


class Ahead(unittest.TestCase):
    def test_window(self):
        # rain in the hour [16, 17): at 13:00 it is the 4th hour -- outside; at 13:20 inside
        kw = dict(fckw=dict(code=0, cloud=5, cols={3: {"pp": 80, "mm": 0.8}}))
        p = panel(ts_loc(*OCT9, 13, 0), **kw)
        self.assertEqual(p["now"]["ahead"], [])
        p = panel(ts_loc(*OCT9, 13, 20), **kw)
        self.assertEqual(p["now"]["ahead"], ["rain"])

    def test_unique_and_not_current(self):
        p = panel(T14, fckw=dict(code=61, prec=0.2, cloud=100, cols={i: {"pp": 90, "mm": 0.8} for i in range(0, 4)}))
        check_panel(self, p)
        self.assertEqual((p["now"]["scene"], p["now"]["ahead"]), ("rain", []))
        p = panel(ts_loc(*OCT9, 16, 30), fckw=dict(code=0, cloud=5, cols={0: {"pp": 90, "mm": 0.8},
                                                                          1: {"pp": 90, "mm": 0.8}}))
        self.assertEqual(p["now"]["ahead"], ["rain", "clear-night"])


def fx_phantom(t=None):
    """The Greenwich snapshot with phantom best_match rain added: rain 0.2 mm in the next
    hours and 0.1 mm in the current interval while precipitation stays 0. t -- replace all
    temperatures (the winter version of the same fields)."""
    fx = load_fx()
    if not fx:
        return None, None
    fc = json.loads(json.dumps(fx["fc"]))
    now = int(fc["current"]["time"]) + 60
    H = now // 3600 * 3600
    h = fc["hourly"]
    for i, lab in enumerate(h["time"]):
        if H < lab <= H + 5 * 3600:
            h["precipitation"][i] = 0.0
            h["rain"][i] = 0.2
    fc["current"].update(rain=0.1, precipitation=0.0, showers=0.0, snowfall=0.0)
    if t is not None:
        fc["current"]["temperature_2m"] = t
        h["temperature_2m"] = [t] * len(h["temperature_2m"])
    raw = {"fc": fc, "air": fx["air"], "rad": mk_rad(now), "cam": mk_cam(now)}
    src = {k: {"ok": now - 120, "fail": 0, "err": ""} for k in ("fc", "air", "rad")}
    return wb.build_panel(raw, src, {}, now, CFG), fc


class PrecipFields(unittest.TestCase):
    """With best_match the rain/showers/snowfall fields disagree with precipitation (phantom
    0.1-0.4 mm of rain at 0 mm and 0 %). The amount is precipitation; the fields only split it."""

    def test_phantom_rain_in_snapshot(self):
        p, fc = fx_phantom()
        if p is None:
            self.skipTest("no tests/fx/*_greenwich.json")
        check_panel(self, p)
        self.assertFalse([a for a in p["now"]["ahead"] if "rain" in a or "drizzle" in a], p["now"]["ahead"])
        self.assertFalse([k for k in keys(p) if k.startswith("rain") or k in ("frz_rain", "shower")])
        # the same fields in winter: there would be a false P0 freezing-rain alarm
        p, _ = fx_phantom(t=-15.0)
        check_panel(self, p)
        self.assertFalse([a for a in p["now"]["ahead"] if a.startswith("freezing")], p["now"]["ahead"])
        self.assertNotIn("frz_rain", keys(p))
        # at -15 degC the columns are snow wherever precipitation is possible at all (pp >= 5):
        # the phantom liquid fields do not turn them into rain
        self.assertEqual([c["snow"] for c in p["rain"]["cols"]],
                         [1 if (c["pp"] or 0) >= 5 else 0 for c in p["rain"]["cols"]])

    def test_phantom_rain_now(self):
        # current: rain 0.1 per 15 min, but precipitation 0 and clear -- dry, no "freezing rain"
        p = panel(T14, fckw=dict(code=1, cloud=10, t=-2, prec=0.0, rain=0.1))
        check_panel(self, p)
        self.assertEqual(p["now"]["scene"], "mostly-clear-day")
        self.assertFalse([s for s in p["status"] if s["key"] in ("frz_rain", "rain_end", "rain_long")])
        self.assertEqual(scene_of(fckw=dict(code=2, cloud=50, t=8, prec=0.0, rain=0.2)), "partly-cloudy-day")

    def test_phantom_rain_over_real_snow(self):
        # snow 0.6 mm/h with an extra 0.1 mm of rain in the fields -- snow column, snow phrase
        cols = {i: {"pp": 85, "mm": 0.6, "code": 73, "t": -3, "rain": 0.1, "snowfall": 0.42} for i in range(1, 4)}
        p = panel(T14, fckw=dict(t=-3, code=3, cloud=100, cols=cols))
        check_panel(self, p)
        self.assertEqual([c["snow"] for c in p["rain"]["cols"][:5]], [0, 1, 1, 1, 0])
        self.assertEqual(p["now"]["ahead"], ["snow"])
        self.assertNotIn("frz_rain", keys(p))
        txt = " ".join(s["text"] for s in p["status"] if s["key"] == "rain_at").lower()
        self.assertIn("snow", txt)
        self.assertNotIn("rain", txt)

    def test_snow_threshold(self):
        # a trace of snow (0.01 cm) is not snow: not under a rain code, not in cloud, not in fog
        self.assertEqual(scene_of(fckw=dict(code=61, t=3, prec=0.2, rain=0.0, snowfall=0.01, cloud=100)), "rain")
        self.assertEqual(scene_of(fckw=dict(code=3, t=4, prec=0.0, snowfall=0.01, cloud=100)), "overcast")
        self.assertEqual(scene_of(fckw=dict(code=45, vis=300, t=-2, prec=0.0, snowfall=0.01, cloud=100)),
                         "rime-fog")
        # light snow in fog 45: fog lowers visibility -- not "heavy snow"
        self.assertEqual(scene_of(fckw=dict(code=45, vis=300, t=-2, prec=0.1, rain=0.0, snowfall=0.07,
                                            cloud=100)), "snow")
        # noticeable snowfall without a snow code at visibility < 400 m -- heavy
        self.assertEqual(scene_of(fckw=dict(code=3, vis=300, t=-4, prec=0.2, rain=0.0, snowfall=0.14,
                                            cloud=100)), "heavy-snow")

    def test_wind_now_and_rising(self):
        # gusts of 16 now, 21 in 2 h: the scene is wind, the phrase is about the rise
        hr = {j: {"gust": 16, "wind": 8} for j in range(-24, 2)}
        hr.update({j: {"gust": 21, "wind": 11} for j in range(2, 25)})
        p = panel(T14, fckw=dict(code=1, cloud=30, wind=8, gust=16, hr=hr))
        check_panel(self, p)
        self.assertEqual(p["now"]["scene"], "wind")
        self.assertEqual(wv(p, "wind"), (3, "8", "m/s", "gusts 21 by 16:00"))
        self.assertEqual(st0(p)["text"], "Wind rising to 21 m/s by 16:00")

    def test_ice_line_sums_millimetres(self):
        # +0.8 degC, light rain 0.15 mm per 15 min, dry before: 0.15 mm counts for 6 h,
        # not 0.6 mm/h -- no black-ice line
        p = panel(T14, fckw=dict(code=61, cloud=100, t=0.8, prec=0.15))
        check_panel(self, p)
        self.assertNotIn("ice", keys(p))
        # with 0.4 mm in the past hour the sum is 0.55 -- yes
        p = panel(T14, fckw=dict(code=61, cloud=100, t=0.8, prec=0.15, hr={0: {"mm": 0.4, "pp": 80, "t": 1.0}}))
        self.assertIn("ice", keys(p))

    def test_shower_by_current_intensity(self):
        # shower (1.9) and torrential (30) thresholds are mm/h; current is per 15 min x 4
        cols = {0: {"pp": 90, "mm": 1.0, "code": 81}, 1: {"pp": 10, "mm": 0.0}}
        p = panel(T14, fckw=dict(code=81, cloud=90, t=18, prec=0.6, cols=cols))
        check_panel(self, p)
        self.assertEqual((st0(p)["key"], st0(p)["text"]), ("shower", u"Heavy shower — best wait it out"))
        p = panel(T14, fckw=dict(code=82, cloud=100, t=18, prec=8.0, cols=cols))
        self.assertEqual((st0(p)["key"], st0(p)["P"]), ("downpour", 0))
        # light: 0.1 per 15 min = 0.4 mm/h -- ordinary rain, ends within the hour
        p = panel(T14, fckw=dict(code=61, cloud=100, t=12, prec=0.1, cols=cols))
        self.assertEqual(st0(p)["key"], "rain_end")


class Texts(unittest.TestCase):
    def test_degrees(self):
        self.assertEqual([U.fmt_deg(x) for x in (12.4, 0.4, -0.4, -9.2, None, 35.5)],
                         [u"12°", u"0°", u"0°", u"−9°", u"—", u"36°"])
        p = panel(T14, fckw=dict(t=-9.2, rh=80, cloud=100, code=3))
        self.assertEqual(p["now"]["tTxt"], u"−9°")
        self.assertTrue(p["now"]["utciTxt"].startswith(u"−"))
        # rounding half away from zero, not banker's
        self.assertEqual([U.fmt_deg(x) for x in (12.5, -12.5, 2.5, 0.5)],
                         [u"13°", u"−13°", u"3°", u"1°"])
        self.assertEqual(wv(panel(T14, fckw=dict(wind=2.5, gust=12.5, wdir=270)), "wind")[1::2], ("3", "gusts 13, west"))
        self.assertEqual(wg(panel(T14, fckw=dict(uv=8.5, uvmax=8.5)), "uv")["val"], "9")

    def test_word(self):
        W = wb.utci_word
        self.assertEqual(W(28, 65), "Hot and humid")
        self.assertEqual(W(28, 25), "Hot and dry")
        self.assertEqual(W(28, 45), "Warm")
        self.assertEqual(W(28, None), "Warm")
        self.assertEqual(W(35, 70), "Hot")
        self.assertEqual(W(40, 70), "Very hot")
        self.assertEqual(W(47, 20), "Extreme heat")
        self.assertEqual(W(5, 70), "Cool")
        self.assertEqual(W(-30, 70), "Bitter cold")
        self.assertEqual(W(-45, 70), "Extreme frost")
        # refinements by the data
        self.assertEqual(W(28, 70, 27, 1), "Muggy")
        self.assertEqual(W(28, 70, 27, 3), "Hot and humid")
        self.assertEqual(W(-5, 80, -2, 3), "Frosty")
        self.assertEqual(W(-18, 80, -9, 6), "Hard frost")
        self.assertEqual(W(-18, 80, 2, 12), "Very cold")            # above zero in the shade -- not frost
        self.assertEqual(W(-30, 70, -20, 8), "Severe frost")
        self.assertEqual(W(-3, 90, 4, 6), "Raw")
        self.assertEqual(W(4, 90, 6, 5), "Raw")
        self.assertEqual(W(2, 98, 3, 1, "fog"), "Damp")
        self.assertEqual(W(12, 98, 11, 1, "drizzle"), "Damp")
        self.assertEqual(W(8, 90, 12, 3, "rain"), "Cool")
        self.assertEqual(W(10, 60, 12, 2, "overcast"), "Fresh")
        self.assertEqual(W(20, 60, 20, 2, "clear-day"), "Comfortable")
        # category edges of the UTCI scale
        cats = [(46.1, "extreme_heat"), (46, "very_hot"), (38, "very_hot"), (37.9, "hot"), (32, "hot"),
                (31.9, "warm"), (26, "warm"), (25.9, "comfortable"), (9, "comfortable"), (8.9, "cool"),
                (0, "cool"), (-0.1, "cold"), (-13, "cold"), (-13.1, "very_cold"), (-27, "very_cold"),
                (-27.1, "bitter_cold"), (-40, "bitter_cold"), (-40.1, "extreme_cold")]
        for u, cid in cats:
            self.assertEqual(wb.utci_category(u), cid, u)
        for u in range(-60, 60):
            for t in (-20, -1, 3, 12, 30):
                for sc in ("fog", "clear-day"):
                    for rh in (20, 65, 95):
                        self.assertLessEqual(len(W(u, rh, t, 6, sc)), 20)
        self.assertEqual(set(ph.WORDS), set(wb.condition_id(u, rh, t, w, sc)
                                            for u in range(-60, 60) for rh in (20, 45, 65, 95)
                                            for t in (-20, -1, 3, 12, 30) for w in (0.5, 3, 6)
                                            for sc in ("fog", "clear-day")))
        for k, title in ph.SCENES:
            self.assertLessEqual(len(title), wb.MAXLEN)

    def test_no_plus_anywhere_big(self):
        for raw, src, now, _ in grid(400, seed=3):
            n = wb.build_panel(raw, src, {}, now, CFG)["now"]
            self.assertNotIn("+", n["utciTxt"] + n["tTxt"])


def worst_params(tu, wu, pu, cam="Front garden"):
    """Longest value of every placeholder for one unit set."""
    def longest(xs):
        return max(xs, key=len)
    temps = longest([U.fmt_temp(x, tu) for x in range(-50, 51)])
    p_mm = [600 + x / 2.0 for x in range(0, 401)]
    return {"t": temps, "tmin": temps, "tmax": temps,
            "utci": longest([U.fmt_temp(x, tu) for x in range(-60, 61)]),
            "g": longest([U.fmt_wind(x, wu) for x in range(0, 46)]), "wu": wu,
            "hh": "23:00", "hm": "23:59",
            "v": longest([U.fmt_rad(x / 100.0) for x in range(1, 3000)]),
            "aqi": "500", "pm10": "2000", "pp": "100", "uv": "15",
            "dp": longest([U.fmt_press_delta(x / 10.0, pu, sign=False) for x in range(0, 300)]),
            "dp3": longest([U.fmt_press_fast(x / 10.0, pu) for x in range(0, 300)]),
            "p": longest([U.fmt_press(x, pu) for x in p_mm]), "pu": pu,
            "vis": longest([U.fmt_vis(v, wu)[0] for v in range(0, 1000, 7)]), "vu": U.fmt_vis(1, wu)[1],
            "when": longest(list(ph.WHEN.values())),
            "part": longest([ph.part_phrase(p, r) for p in ("morning", "afternoon", "evening", "night")
                             for r in ("today", "tomorrow", "next")]),
            "Rainw": "Heavy rain", "Snoww": "Light snow", "cam": cam}


class Properties(unittest.TestCase):
    def test_plural_and_ago(self):
        self.assertEqual([ph.count(n, "minute", "minutes") for n in (1, 2, 21)], ["1 minute", "2 minutes", "21 minutes"])
        self.assertEqual(ph.ago(0), "1 minute")
        self.assertEqual(ph.ago(21 * 60), "21 minutes")
        self.assertEqual(ph.ago(29 * 3600), "29 hours")
        self.assertEqual(ph.ago(21 * 3600), "21 hours")
        self.assertEqual(ph.ago(3 * 86400), "3 days")
        self.assertEqual(ph.ago(86400 * 1.99), "2 days")
        self.assertEqual(ph.of_stations(1, 1), "1 of 1 station")
        self.assertEqual(ph.of_stations(2, 21), "2 of 21 stations")

    def test_templates_worst_case(self):
        ago_from = {"wx_stale": 45 * 60, "air_stale": 3 * 3600, "rad_stale": 18 * 3600, "cam_dead": 20 * 3600}
        checked = 0
        for tu in U.TEMP_UNITS:
            for wu in U.WIND_UNITS:
                for pu in U.PRESS_UNITS:
                    W = worst_params(tu, wu, pu)
                    for key, variants in ph.BANK.items():
                        for tpl, _ in variants:
                            prm = dict(W)
                            if "{ago}" in tpl:
                                prm["ago"] = max([ph.ago(s) for s in range(ago_from[key], 400 * 86400, 600)], key=len)
                            s = wb.fmt_template(tpl, prm)
                            self.assertIsNotNone(s, tpl)
                            self.assertLessEqual(len(s), wb.MAXLEN, (s, tu, wu, pu))
                            checked += 1
                    for key, fbs in ph.FALLBACK.items():
                        for tpl in fbs:
                            s = wb.fmt_template(tpl, W)
                            self.assertIsNotNone(s, tpl)
                            self.assertLessEqual(len(s), wb.MAXLEN, s)
        self.assertGreaterEqual(checked, 18 * 100)

    def test_bank_covers_facts(self):
        # every fact key the brain can produce has wording and every condition flag is spelled right
        produced = set()
        conds = {}
        for raw, src, now, _ in grid(1200, seed=17):
            p = wb.build_panel(raw, src, {}, now, CFG)
            produced.update(s["key"] for s in p["status"])
        produced = {("cam_dead" if k.startswith("cam_") else k) for k in produced}
        self.assertFalse(produced - set(ph.BANK), produced - set(ph.BANK))
        for key, variants in ph.BANK.items():
            conds[key] = {c for _, c in variants if c}
        self.assertEqual(conds["rain_later"], {"rain_hi", "rain_mid", "snow_hi", "snow_mid", "sleet"})
        self.assertEqual(conds["calm"], {"still", "dry_day", "cloudy_dry", "walk", "airing", "clean", "morning",
                                         "clear_eve", "late", "clear_night", "frosty", "warm", "cool", "dry24",
                                         "grey", "sunny"})
        self.assertNotIn(None, [ph.FALLBACK.get(k) or (len(v) and v) for k, v in ph.BANK.items()])

    def test_hysteresis_gusts(self):
        mem = {}
        base = ts_loc(*OCT9, 14, 0)
        got = []
        for k, g in enumerate((15.2, 14.6, 13.0)):
            now = base + k * 900          # Open-Meteo current moves in 15-minute steps
            p = panel(now, mem=mem, fckw=dict(t=16, rh=55, wind=3, gust=g,
                                              hr={j: {"gust": g} for j in range(-24, 25)}))
            check_panel(self, p, mem)
            got.append(wg(p, "wind")["L"])
        self.assertEqual(got, [2, 2, 1])

    def test_hysteresis_needs_new_data(self):
        # rebuilding the panel without new data is not an update
        mem = {}
        now = ts_loc(*OCT9, 14, 0)
        raw1, src = make(now, fckw=dict(gust=15.2, hr={j: {"gust": 15.2} for j in range(-24, 25)}))
        raw2, _ = make(now + 600, fckw=dict(gust=13.0, hr={j: {"gust": 13.0} for j in range(-24, 25)}))
        wb.build_panel(raw1, src, mem, now, CFG)
        Ls = []
        for k in range(3):
            p = wb.build_panel(raw2, src, mem, now + 600 + k * 60, CFG)
            Ls.append(wg(p, "wind")["L"])
        self.assertEqual(Ls, [2, 2, 2])

    def test_stable_with_empty_mem(self):
        for n in (1, 2, 4, 5, 14):
            now, kw = scen(n)
            a = panel(now, mem={}, **kw)
            b = panel(now, mem={}, **kw)
            self.assertEqual(a, b)

    def test_calm_four_different(self):
        mem = {}
        texts = []
        for h in (10, 13, 16, 19):
            now = ts_loc(*OCT9, h, 0)
            _, kw = scen(1)
            p = panel(now, mem=mem, **kw)
            check_panel(self, p, mem)
            self.assertEqual(st0(p)["P"], 5, p["status"])
            texts.append(st0(p)["text"])
        self.assertEqual(len(set(texts)), 4, texts)

    def test_calm_window_keeps_text(self):
        mem = {}
        _, kw = scen(1)
        a = panel(ts_loc(*OCT9, 13, 0), mem=mem, **kw)
        b = panel(ts_loc(*OCT9, 14, 30), mem=mem, **kw)
        self.assertEqual(st0(a)["text"], st0(b)["text"])

    def test_tz_independent(self):
        here = dump_json()
        outs = []
        for tz in ("UTC", "Asia/Tokyo"):
            env = dict(os.environ, TZ=tz)
            code = "import sys; sys.path.insert(0, %r); import test_brain as T; " \
                   "sys.stdout.write(T.dump_json())" % HERE
            r = subprocess.run([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            outs.append(r.stdout.decode("utf-8"))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], here)

    def test_half_hour_time_zone(self):
        # Open-Meteo labels hours at local hour starts; in India they are xx:30 UTC
        cfg = Settings(lat=28.61, lon=77.21, tz_name="Asia/Kolkata", panels=PANELS)
        tz = cfg.tzinfo()
        now = int(datetime(2026, 10, 9, 14, 20, tzinfo=tz).timestamp())
        H = int(datetime(2026, 10, 9, 14, 0, tzinfo=tz).timestamp())
        raw, src = make(now)
        fc = raw["fc"]
        shift = H - (now // 3600 * 3600)
        fc["hourly"]["time"] = [t + shift for t in fc["hourly"]["time"]]
        fc["hourly"]["precipitation_probability"] = [0] * 49
        fc["hourly"]["precipitation_probability"][25] = 77           # label H + 1 h
        p = wb.build_panel(raw, src, {}, now, cfg)
        check_panel(self, p, cfg=cfg)
        self.assertEqual([c["h"] for c in p["rain"]["cols"][:3]], ["14", "15", "16"])
        self.assertEqual(p["rain"]["cols"][0]["pp"], 77)

    def test_unknown_time_zone_uses_reported_offset(self):
        cfg = Settings(tz_name="Mars/Olympus", panels=PANELS)
        p = panel(T14, cfg=cfg)
        check_panel(self, p, cfg=cfg)
        self.assertEqual(p["rain"]["cols"][0]["h"], "14")     # the answer said UTC+1 (BST)

    def test_grid_2000(self):
        ctxs = grid(2200)
        self.assertGreaterEqual(len(ctxs), 2000)
        mem, serie = {}, None
        texts, keyset, scenes = set(), set(), set()
        for raw, src, now, s in ctxs:
            if s != serie:
                mem, serie = {}, s
            p = wb.build_panel(raw, src, mem, now, CFG)
            check_panel(self, p, mem)
            scenes.add(p["now"]["scene"])
            scenes.update(p["now"]["ahead"])
            for st in p["status"]:
                texts.add(st["text"])
                keyset.add(st["key"])
        # the grid really is diverse
        self.assertGreater(len(texts), 300)
        self.assertGreater(len(keyset), 45)
        self.assertGreater(len(scenes), 25, sorted(scenes))

    def test_grid_other_units(self):
        for cfg in (Settings(panels=PANELS, temp="F", wind="mph", press="inHg"),
                    Settings(panels=PANELS, temp="C", wind="km/h", press="mmHg")):
            for raw, src, now, _ in grid(400, seed=11):
                check_panel(self, wb.build_panel(raw, src, {}, now, cfg), cfg=cfg)

    def test_broken_inputs(self):
        now = ts_loc(*OCT9, 14, 0)
        good, src = make(now)
        cases = [None, {}, "junk", [], {"fc": None, "air": None, "rad": None, "cam": None},
                 {"fc": {}, "air": {}, "rad": {}, "cam": {}},
                 {"fc": {"current": None, "hourly": None, "daily": None}},
                 {"fc": {"current": {}, "hourly": {"time": []}, "daily": {"time": []}}},
                 {"rad": {"stations": [None, {}, {"km": "x"}, [1, 2], {"km": 5, "name": None, "usvh": "0.2"},
                                       {"km": 7, "usvh": 0.1, "ts": "yesterday"}, {"km": 8, "usvh": 0.1, "ts": 1e30}]}},
                 {"cam": {"gate": None, "door": {"ts": "x", "was": "yes"}, 5: {}}}]
        nulled = json.loads(json.dumps(good))
        for blk in ("current", "daily"):
            for k in nulled["fc"][blk]:
                nulled["fc"][blk][k] = None
        for k in nulled["fc"]["hourly"]:
            if k != "time":
                nulled["fc"]["hourly"][k] = [None] * len(nulled["fc"]["hourly"][k])
        for k in nulled["air"]["current"]:
            nulled["air"]["current"][k] = None
        cases.append(nulled)
        cut = json.loads(json.dumps(good))
        for k in cut["fc"]["hourly"]:
            if k != "time":
                cut["fc"]["hourly"][k] = cut["fc"]["hourly"][k][:20]
        cases.append(cut)
        strs = json.loads(json.dumps(good))
        strs["fc"]["current"]["temperature_2m"] = "warm"
        strs["fc"]["current"]["weather_code"] = float("nan")
        strs["fc"]["current"]["rain"] = "lots"
        strs["fc"]["current"]["snow_depth"] = [1]
        strs["fc"]["current"]["interval"] = 0
        strs["fc"]["hourly"]["time"] = [str(x) for x in strs["fc"]["hourly"]["time"]]
        strs["fc"]["hourly"]["snowfall"] = "x"
        strs["fc"]["hourly"]["rain"] = [{} for _ in strs["fc"]["hourly"]["rain"]]
        strs["fc"]["daily"]["sunrise"] = ["morning", None]
        strs["fc"]["daily"]["uv_index_max"] = {"a": 1}
        strs["fc"]["elevation"] = "high"
        strs["fc"]["utc_offset_seconds"] = "x"
        cases.append(strs)
        neg = json.loads(json.dumps(good))
        neg["fc"]["current"].update(rain=-5, snowfall=-1, visibility=-10, cloud_cover=500, relative_humidity_2m=-3)
        neg["air"]["current"].update(dust=-1, pm2_5=1e9, pm10=0)
        cases.append(neg)
        cfgs = (CFG, None, "junk", {"units": {"temperature": 5}}, Settings(panels=(), tz_name="Nowhere/Land"))
        for raw in cases:
            for s in (src, None, {}, {"fc": None, "air": "x"}):
                for mem in ({}, None, {"hy": [], "pick": None, "said": {"d": 1, "k": 2},
                                       "radh": {"Hilltop": "x"}, "top": "?"}):
                    p = wb.build_panel(raw, s, mem, now, CFG)
                    check_panel(self, p, mem if isinstance(mem, dict) else None)
        for cfg in cfgs:
            p = wb.build_panel(good, src, {}, now, cfg)
            check_panel(self, p, cfg=wb.read_settings(cfg))
        for bad_now in (None, float("nan"), "now", -1, 0, 1e12):
            p = wb.build_panel(good, src, {}, bad_now, CFG)
            check_panel(self, p)

    def test_fuzz_never_throws(self):
        R = random.Random(99)
        junk = [None, "x", -1, 0, 1e9, float("nan"), [], {}, True]
        for raw, src, now, _ in grid(250, seed=5):
            raw = json.loads(json.dumps(raw))
            for _ in range(R.randint(1, 6)):
                blk = R.choice(["fc", "air", "rad"])
                if not raw.get(blk):
                    continue
                if blk == "rad":
                    sts = raw["rad"].get("stations") or [{}]
                    st = R.choice(sts)
                    st[R.choice(["km", "usvh", "ts", "name", "lat"])] = R.choice(junk)
                    continue
                part = R.choice(["current", "hourly", "daily"]) if blk == "fc" else "current"
                d = raw[blk].get(part)
                if not isinstance(d, dict) or not d:
                    continue
                k = R.choice(sorted(d))
                d[k] = R.choice(junk) if R.random() < 0.6 else [R.choice(junk)] * 49
            p = wb.build_panel(raw, src, {}, now, CFG)
            check_panel(self, p)

    def test_mem_survives_json_roundtrip(self):
        mem = {}
        for n in range(1, 16):
            now, kw = scen(n)
            panel(now, mem=mem, **kw)
            mem = json.loads(json.dumps(mem))
        now, kw = scen(4)
        p = panel(now, mem=mem, **kw)
        check_panel(self, p, mem)

    def test_rad_confirm_by_two_dates(self):
        mem = {}
        d1 = ts_loc(2026, 10, 7, 12)
        d2 = ts_loc(2026, 10, 8, 12)
        now = ts_loc(*OCT9, 9, 0)
        p = panel(now, mem=mem, over={"Quarry": 0.40}, rts=d1)
        self.assertEqual(wv(p, "radiation")[:2], (1, "0.40"))          # one station: not confirmed
        p = panel(now + 600, mem=mem, over={"Quarry": 0.40}, rts=d1 + 3600)
        self.assertEqual(wg(p, "radiation")["L"], 1)                    # the same day confirms nothing
        p = panel(now + 1200, mem=mem, over={"Quarry": 0.38}, rts=d2)
        self.assertEqual(wv(p, "radiation"), (2, "0.38", "µSv/h", "second day running"))
        self.assertEqual(st0(p)["key"], "rad2")
        json.dumps(mem)

    def test_status_hold_10_min(self):
        mem = {}
        base = ts_loc(*OCT9, 14, 0)
        kw = dict(fckw=dict(gust=16, hr={j: {"gust": 16} for j in range(-24, 25)}))
        p = panel(base, mem=mem, **kw)
        self.assertEqual(st0(p)["key"], "wind2")
        kw2 = dict(kw, airkw=dict(aqi=65, pm25=20))       # the same class, higher priority
        p = panel(base + 120, mem=mem, **kw2)
        self.assertEqual(st0(p)["key"], "wind2")
        p = panel(base + 700, mem=mem, **kw2)
        self.assertEqual(st0(p)["key"], "air2")
        p = panel(base + 760, mem=mem, **dict(kw2, over={"Riverside": 0.7, "Hilltop": 0.7}))   # more urgent class
        self.assertEqual(st0(p)["key"], "rad_crit")

    def test_cam_dead_only_after_work(self):
        now = ts_loc(*OCT9, 14, 20)
        p = panel(now, camkw=dict(gate=1200, was=True))
        self.assertIn("cam_gate", keys(p))
        self.assertEqual([s["text"] for s in p["status"] if s["key"] == "cam_gate"],
                         ["Gate camera silent since 14:00"])
        p = panel(now, camkw=dict(gate=None, was=False))
        self.assertNotIn("cam_gate", keys(p))
        self.assertEqual(p["fresh"]["cam"]["gate"]["st"], "none")
        self.assertEqual(set(p["fresh"]["cam"]), {"gate", "door"})


class GreenwichSnapshot(unittest.TestCase):
    """The panel on real Open-Meteo answers for Greenwich (tests/fx)."""

    def setUp(self):
        self.fx = load_fx()
        if not self.fx:
            self.skipTest("no tests/fx/*_greenwich.json")

    def test_snapshot(self):
        # 2026-10-09 18:45 BST: overcast night after a rainy day, 17.3 degC, wind 6 m/s from
        # the west with gusts of 13, pressure 13 hPa lower than a day before, AQI 27
        now, p = fx_panel(self.fx)
        check_panel(self, p)
        n = p["now"]
        self.assertEqual(wb._hm({"tz": TZ}, now), "18:45")
        self.assertEqual((n["scene"], n["day"], n["t"], n["tTxt"], n["stale"]), ("overcast", 0, 17.3, u"17°", False))
        self.assertEqual(n["word"], wb.utci_word(n["utci"], 65, 17.3, 6.2, "overcast"))
        self.assertEqual(wv(p, "wind"), (1, "6", "m/s", "gusts 13, west"))
        self.assertEqual(wv(p, "air"), (0, "27", "AQI", "good"))
        self.assertEqual(wv(p, "pressure"), (3, "1005", "hPa", u"−13 in 24 h"))
        self.assertEqual(wv(p, "sun"), (-1, "07:15", "sunrise", "sunset 18:17"))
        self.assertEqual(wv(p, "uv")[:2], (0, "0"))
        self.assertEqual(wv(p, "radiation"), (0, "0.12", "µSv/h", "normal, 8 stations"))
        h = self.fx["fc"]["hourly"]
        pp = {int(t): v for t, v in zip(h["time"], h["precipitation_probability"])}
        H = now // 3600 * 3600
        self.assertEqual([c["h"] for c in p["rain"]["cols"]], ["18", "19", "20", "21", "22", "23", "00", "01", "02"])
        self.assertEqual([c["pp"] for c in p["rain"]["cols"]], [pp[H + (i + 1) * 3600] for i in range(9)])
        self.assertIn("press_d24", keys(p))
        self.assertEqual(p["fresh"]["rad"]["st"], "ok")

    def test_snapshot_other_units(self):
        cfg = Settings(panels=PANELS, temp="F", wind="mph", press="inHg")
        now, p = fx_panel(self.fx, cfg=cfg)
        check_panel(self, p, cfg=cfg)
        self.assertEqual(p["now"]["tTxt"], u"63°")
        self.assertEqual(wv(p, "wind"), (1, "14", "mph", "gusts 28, west"))
        self.assertEqual(wv(p, "pressure")[:3], (3, "29.68", "inHg"))
        self.assertEqual(st0(p)["text"], "Pressure fell 0.38 inHg in 24 hours")

    def test_aged_snapshot(self):
        # 2.5 h without new downloads: weather stale, chart gray
        now, p = fx_panel(self.fx, 9000)
        check_panel(self, p)
        self.assertEqual(p["fresh"]["wx"]["st"], "stale")
        self.assertTrue(p["rain"]["gray"] and p["now"]["stale"])
        self.assertEqual(st0(p)["key"], "wx_stale")
        self.assertEqual(wv(p, "wind"), (None, u"—", "", "as of 18:45"))
        self.assertEqual(p["rain"]["cols"][0]["h"], "21")
        # 15 h: weather dead -- no scene, the chart tail without forecast
        now, p = fx_panel(self.fx, 15 * 3600)
        check_panel(self, p)
        self.assertEqual((p["now"]["scene"], p["now"]["ahead"]), ("not-available", []))
        self.assertEqual([c["h"] for c in p["rain"]["cols"][:3]], ["09", "10", "11"])
        self.assertEqual(p["fresh"]["wx"]["st"], "dead")
        self.assertIn("15 hours", st0(p)["text"])


def _pp_by_col(raw, now):
    H = int(now) // 3600 * 3600
    h = raw["fc"]["hourly"]
    lab = {int(t): v for t, v in zip(h["time"], h["precipitation_probability"])}
    return {i: lab.get(H + (i + 1) * 3600) for i in range(0, 25)}, H


class Refuted(unittest.TestCase):
    """Situations where the output once was wrong."""

    def test_cam_dead_since_yesterday(self):
        # was: at 10:00 "camera silent since 15:00" -- yesterday's time reads as today
        now = ts_loc(*OCT9, 10, 0)
        p = panel(now, camkw=dict(gate=19 * 3600, was=True))
        self.assertEqual([s["text"] for s in p["status"] if s["key"] == "cam_gate"],
                         ["Gate camera silent for 19 hours"])
        p = panel(ts_loc(*OCT9, 0, 10), camkw=dict(door=20 * 60, was=True))
        self.assertEqual([s["text"] for s in p["status"] if s["key"] == "cam_door"],
                         ["Entrance camera silent for 20 minutes"])

    def test_rad_new_reading_clears_alarm(self):
        # was: the hysteresis counted "two updates", which for daily readings meant a day:
        # the alarm stayed for a day after the readings were back to normal
        mem = {}
        t0 = ts_loc(*OCT9, 14, 0)
        seen = []
        for k in range(0, 48):
            now = t0 + k * 1800
            d = datetime.fromtimestamp(now, TZ)
            spike = (d.day, d.hour) < (10, 6)
            kw = dict(over={"Harbour": 0.45, "Quarry": 0.45}, rts=ts_loc(2026, 10, 9, 12)) if spike \
                else dict(rts=ts_loc(2026, 10, 10, 6))
            p = panel(now, mem=mem, **kw)
            check_panel(self, p, mem)
            rkeys = [s["key"] for s in p["status"] if s["key"] in ("rad1", "rad2", "rad_crit")]
            seen.append((spike, wg(p, "radiation")["L"], rkeys))
        self.assertTrue(all(L == 2 and ks == ["rad2"] for sp, L, ks in seen if sp))
        self.assertTrue(all(L == 0 and ks == [] for sp, L, ks in seen if not sp), seen)
        # L1 held by the margin (0.19 at the 0.20 edge) -- the note is not "0 of 8 stations"
        mem = {}
        for k in range(0, 12):
            panel(t0 + k * 7200, mem=mem, over={"Hilltop": 0.25, "Harbour": 0.24}, rts=ts_loc(*OCT9, 12))
        p = panel(t0 + 86400, mem=mem, over={"Hilltop": 0.19}, rts=ts_loc(2026, 10, 10, 12))
        self.assertEqual(wv(p, "radiation"), (1, "0.19", "µSv/h", "near the limit"))

    def test_rad_same_readings_revised(self):
        # was: the provider revised the values of the same reading time (or a station dropped
        # out) -- not counted as an update, and the alarm held until the next reading
        mem = {}
        now = ts_loc(*OCT9, 14, 0)
        rts = ts_loc(*OCT9, 11)
        p = panel(now, mem=mem, over={"Harbour": 0.45, "Quarry": 0.45}, rts=rts)
        self.assertEqual(wg(p, "radiation")["L"], 2)
        p = panel(now + 1800, mem=mem, rts=rts)
        self.assertEqual(wg(p, "radiation")["L"], 0)
        self.assertFalse([s for s in p["status"] if s["key"].startswith("rad")])

    def test_no_uv_after_dark(self):
        # was: the UV level held by hysteresis while it is dark and UV is 0
        mem = {}
        t17 = ts_loc(2026, 7, 15, 17, 0)
        p = panel(t17, mem=mem, fckw=dict(t=35, rh=35, wind=1, gust=2, cloud=0, uv=7.0, uvmax=7.5,
                                          uv_fn=lambda h: 7.0 if h < 18 else 0.0))
        self.assertTrue(any("UV" in s["text"] for s in p["status"]), p["status"])
        p = panel(t17 + 2400, mem=mem, fckw=dict(t=35, rh=35, wind=1, gust=2, cloud=0, uv=0.0, day=0,
                                                 uv_fn=lambda h: 0.0))
        check_panel(self, p, mem)
        self.assertEqual(mem["hy"]["uv"]["L"], 2)          # the hysteresis holds the level
        self.assertFalse([s["text"] for s in p["status"] if "UV" in s["text"]])
        self.assertIn(st0(p)["key"], ("heat2", "heat3"))
        self.assertEqual(wv(p, "uv")[:2], (0, "0"))        # the widget follows the current index

    def test_icon_wind_only_when_windy_now(self):
        # was: calm now, gusts of 18 expected in 3 h -- and the wind icon already now
        now = ts_loc(*OCT9, 13, 0)
        p = panel(now, fckw=dict(code=0, wind=2, gust=4, hr={3: {"gust": 18, "wind": 9}}))
        self.assertEqual(wv(p, "wind")[::3], (2, "gusts 18 by 16:00"))
        self.assertEqual(st0(p)["key"], "wind_rise")
        self.assertEqual(p["now"]["scene"], "clear-day")

    def test_press_forecast_amount_at_that_time(self):
        # was: by 06:00 the pressure drops 9 mmHg, by the end of the day it recovers to -7 --
        # and the phrase said 7
        def drop(rate, hours, end):
            def f(off):
                if off <= 0:
                    return NORM
                if off <= hours:
                    return NORM - rate * off
                return NORM - rate * hours + (off - hours) * (end - (NORM - rate * hours)) / (24.0 - hours)
            return f
        now = ts_loc(*OCT9, 20, 0)
        p = panel(now, cfg=CFG_MM, fckw=dict(p_fn=drop(0.9, 10, NORM - 7)))
        self.assertEqual(wv(p, "pressure")[::3], (2, u"−9 by 06:00"))
        self.assertEqual(st0(p)["text"], "Pressure to drop 9 mmHg by morning")
        # 16:00 is "by afternoon", not "by noon"
        p = panel(ts_loc(*OCT9, 6, 0), cfg=CFG_MM, fckw=dict(p_fn=drop(0.8, 10, NORM - 7)))
        self.assertEqual(wv(p, "pressure")[3], u"−8 by 16:00")
        self.assertEqual(st0(p)["text"], "Pressure to drop 8 mmHg by afternoon")

    def test_calm_no_dry_promise_against_chart(self):
        # was: "No rain for the rest of the day" while the chart showed 40-49 % bars
        claims = {"No rain for the rest of the day": "day", "No rain expected for 24 hours": "24",
                  "Cloudy but dry until evening": "to18", "Clear evening, no rain": "3",
                  "Clear night, no rain": "3"}
        R = random.Random(11)
        said = set()
        for n in range(700):
            now = ts_loc(2026, R.randint(1, 12), R.randint(1, 28), R.randint(0, 23), R.choice([0, 20, 40]))
            cols = {}
            if n % 2:
                a = R.randint(0, 20)
                cols = {i: {"pp": R.choice([25, 30, 40, 49]), "mm": 0.0} for i in range(a, a + R.randint(1, 4))}
            raw, src = make(now, fckw=dict(t=R.choice([5, 12, 18]), cloud=R.choice([0, 20, 75, 90]), cols=cols))
            p = wb.build_panel(raw, src, {}, now, CFG)
            pp, H = _pp_by_col(raw, now)
            d = datetime.fromtimestamp(now, TZ).date()
            for s in p["status"]:
                kind = claims.get(s["text"])
                if not kind:
                    continue
                said.add(s["text"])
                if kind == "24":
                    idx = range(0, 24)
                elif kind == "3":
                    idx = range(0, 4)
                else:
                    idx = [i for i in range(0, 25) if datetime.fromtimestamp(H + i * 3600, TZ).date() == d and
                           (kind == "day" or datetime.fromtimestamp(H + i * 3600, TZ).hour < 18)]
                bad = [(i, pp[i]) for i in idx if (pp[i] or 0) >= 25]
                self.assertEqual(bad, [], (s["text"], now, cols))
        self.assertGreaterEqual(len(said), 4, said)

    def test_station_count_grammar(self):
        # was: "1 of 1 stations"
        p = panel(ts_loc(*OCT9, 14, 0), stations=[(30.0, "Quarry", 0.25)])
        self.assertEqual(wv(p, "radiation")[3], "1 of 1 station, Quarry")

    def test_numbers_support_level(self):
        # was: "Poor air, AQI 45" (45 is "moderate"; particles made the level)
        for h in range(8, 20, 2):
            p = panel(ts_loc(*OCT9, h, 0), airkw=dict(aqi=45, pm25=30))
            s = [x for x in p["status"] if x["key"] == "air2"]
            self.assertEqual(len(s), 1)
            self.assertNotIn("AQI", s[0]["text"])
        # was: after a sharp drop the hysteresis holds L2 and the text says "up to 9 m/s"
        mem = {}
        t0 = ts_loc(*OCT9, 10, 0)
        for m in range(0, 60, 5):
            g = 17 if m < 15 else 9
            p = panel(t0 + m * 60, mem=mem, fckw=dict(gust=g, wind=g / 2.0,
                                                      hr={j: {"gust": g, "wind": g / 2.0} for j in range(-24, 25)}))
            check_panel(self, p, mem)
            for s in p["status"]:
                if s["key"].startswith("wind"):
                    self.assertNotIn(" 9 m/s", s["text"])
        # the same for air: AQI 68 -> 35, the hysteresis holds L2 until the next hour
        mem = {}
        for m in range(0, 120, 20):
            aq = dict(aqi=68, pm25=33) if m < 60 else dict(aqi=35, pm25=12)
            p = panel(t0 + m * 60, mem=mem, airkw=aq)
            for s in p["status"]:
                self.assertNotIn("AQI 35", s["text"])
            if m >= 60:
                self.assertEqual(wg(p, "air")["L"], 2)


if __name__ == "__main__":
    unittest.main()
