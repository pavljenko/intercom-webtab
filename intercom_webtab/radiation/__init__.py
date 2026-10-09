"""Ambient gamma dose rate near the station's address, from open monitoring networks.

Usage::

    from intercom_webtab import radiation
    data = radiation.fetch(51.4779, -0.0015, 100)            # provider="auto"
    # {"source": "...", "source_url": "...", "license": "...", "license_url": "...",
    #  "attribution": "...", "provider": "<id>",
    #  "stations": [{"name", "lat", "lon", "km", "usvh", "ts", "kind"}, ...]}

Stations are sorted by distance (``km``, great-circle), values are uSv/h, ``ts`` is unix
seconds of the measurement. ``kind`` is one of ``base.KINDS``:

* ``"dose"`` - official probe, ambient dose equivalent rate including the cosmic part;
* ``"terrestrial"`` - official spectrometric probe without the cosmic part (Canada);
* ``"community"`` - volunteer Geiger counter, counts converted with a tube factor.

Readings older than three days (or dated in the future) are dropped. ``fetch`` raises
:class:`RadiationError` (or its subclass :class:`NoStationsError`) when nothing usable
came back, and ``ValueError`` for bad arguments.

Automatic choice (``provider="auto"``): national networks whose (simplified) country
outline contains the point come first, then networks whose outline lies within
``MARGIN_KM``, then Safecast worldwide. The first network that returns at least one fresh
station inside the radius wins; network errors and empty answers fall through to the next.
Each attempt has its own ``timeout`` budget.

All network access is blocking stdlib urllib; call it from an executor.
"""
from collections import OrderedDict
import math
import time

from . import base, bfs_odl, epa_radnet, hc_fps, safecast, stuk_fmi
from .base import KINDS, NoStationsError, RadiationError

__all__ = ["PROVIDERS", "FALLBACK", "RadiationError", "NoStationsError", "KINDS",
           "candidates", "pick", "describe", "fetch"]

PROVIDERS = OrderedDict((m.ID, m) for m in (bfs_odl, stuk_fmi, epa_radnet, hc_fps, safecast))
FALLBACK = safecast.ID
MARGIN_KM = 50.0                     # border zone in which a neighbour's network is tried
MAX_STATIONS = 25
MAX_USVH = 100000.0                  # 0.1 Sv/h: above this a value is garbage, not news


def _check_point(lat, lon):
    la, lo = base.num(lat), base.num(lon)
    if la is None or lo is None or not -90 <= la <= 90 or not -180 <= lo <= 180:
        raise ValueError("bad coordinates: %r, %r" % (lat, lon))
    return la, lo


def candidates(lat, lon, margin_km=MARGIN_KM):
    """Provider ids to try for a point, best first; always ends with the fallback."""
    la, lo = _check_point(lat, lon)
    near = []
    for pid, mod in PROVIDERS.items():
        if mod.COVERAGE is None:
            continue
        d = base.distance_to_rings_km(la, lo, mod.COVERAGE)
        if d <= margin_km:
            near.append((d, pid))
    near.sort()
    return [pid for _, pid in near] + [FALLBACK]


def pick(lat, lon):
    """The provider id ``fetch(..., provider="auto")`` tries first for this point."""
    return candidates(lat, lon)[0]


def describe(provider_id):
    """Static facts about a provider, e.g. for an about/attribution page."""
    mod = PROVIDERS[provider_id]
    return {"provider": mod.ID, "source": mod.NAME, "source_url": mod.SOURCE_URL,
            "license": mod.LICENSE, "license_url": mod.LICENSE_URL,
            "attribution": mod.ATTRIBUTION}


def _clean(stations, radius_km, now):
    out, seen = [], set()
    for s in stations:
        try:
            v, ts, km = float(s["usvh"]), int(s["ts"]), float(s["km"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(v) and 0 < v < MAX_USVH) or km > radius_km:
            continue
        if ts < now - base.STALE_S or ts > now + base.FUTURE_S:
            continue
        key = (s.get("name"), s.get("lat"), s.get("lon"))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    out.sort(key=lambda s: (s["km"], s["name"]))
    return out[:MAX_STATIONS]


def _run(mod, lat, lon, radius_km, timeout, now):
    try:
        raw = mod.fetch(lat, lon, radius_km, base.Deadline(timeout))
    except RadiationError:
        raise
    except Exception as e:                       # contract: callers only see RadiationError
        raise RadiationError("%s: unexpected data (%s: %s)" % (mod.NAME, type(e).__name__, e))
    stations = _clean(raw or [], radius_km, time.time() if now is None else now)
    if not stations:
        raise NoStationsError("%s: no fresh stations within %g km" % (mod.NAME, radius_km))
    res = describe(mod.ID)
    res["stations"] = stations
    return res


def fetch(lat, lon, radius_km, provider="auto", timeout=20, now=None):
    """Nearest stations of one network (see the module docstring for the result).

    ``provider`` is "auto", "none" or a key of ``PROVIDERS``. ``now`` (unix seconds) only
    exists for tests.
    """
    la, lo = _check_point(lat, lon)
    radius = base.num(radius_km)
    if radius is None or radius <= 0:
        raise ValueError("bad radius_km: %r" % (radius_km,))
    pid = str(provider or "auto").strip().lower()
    if pid == "none":
        raise RadiationError("radiation data are switched off ([radiation] provider = none)")
    if pid != "auto":
        if pid not in PROVIDERS:
            raise ValueError("unknown radiation provider %r (known: auto, none, %s)"
                             % (provider, ", ".join(PROVIDERS)))
        return _run(PROVIDERS[pid], la, lo, radius, timeout, now)
    errors, empty = [], True
    for cand in candidates(la, lo):
        try:
            return _run(PROVIDERS[cand], la, lo, radius, timeout, now)
        except NoStationsError as e:
            errors.append(str(e))
        except RadiationError as e:
            empty = False
            errors.append(str(e))
    cls = NoStationsError if empty else RadiationError
    raise cls("; ".join(errors))
