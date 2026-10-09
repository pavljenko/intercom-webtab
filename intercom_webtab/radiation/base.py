"""Shared helpers for the radiation providers.

Everything here is blocking and stdlib-only: the web server calls ``radiation.fetch`` in an
executor. Network access goes through :func:`http_get`, which enforces a total deadline, a
response size limit and a fixed User-Agent, and turns every network or HTTP problem into
:class:`RadiationError`. Tests replace ``base.http_get`` to run offline, so providers must
call it as ``base.http_get(...)``.
"""
import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

USER_AGENT = "IntercomWebTab/0.1.0"
MAX_BYTES = 4 * 1024 * 1024          # per response, after decompression
CHUNK = 64 * 1024
STALE_S = 3 * 86400                  # older readings are dropped
FUTURE_S = 3600                      # newer than now + this: a device clock error

# Station "kind": what the number means.
KIND_DOSE = "dose"                   # official probe, ambient dose equivalent rate incl. cosmic
KIND_TERRESTRIAL = "terrestrial"     # official spectrometric probe, cosmic component excluded
KIND_COMMUNITY = "community"         # volunteer Geiger counter, counts converted to dose rate
KINDS = (KIND_DOSE, KIND_TERRESTRIAL, KIND_COMMUNITY)

EARTH_KM = 6371.0088


class RadiationError(Exception):
    """A provider could not deliver usable data (network, HTTP, format, timeout)."""


class NoStationsError(RadiationError):
    """The provider answered, but no fresh station lies within the radius."""


# --------------------------------------------------------------------------- time budget

class Deadline(object):
    """A total time budget shared by all requests of one provider call."""

    def __init__(self, seconds):
        self.end = time.monotonic() + max(0.1, float(seconds))

    def left(self):
        rest = self.end - time.monotonic()
        if rest <= 0:
            raise RadiationError("timed out")
        return rest


def as_deadline(timeout):
    """Accept either a number of seconds or an existing :class:`Deadline`."""
    return timeout if isinstance(timeout, Deadline) else Deadline(timeout)


# --------------------------------------------------------------------------- HTTP

def url_with(base_url, params):
    return base_url + "?" + urllib.parse.urlencode(params)


def http_get(url, timeout, max_bytes=MAX_BYTES):
    """GET ``url`` and return the body as bytes (gzip is decoded).

    ``timeout`` is seconds or a :class:`Deadline` for the whole transfer. Raises
    :class:`RadiationError` on any network/HTTP failure, on timeout and when the body is
    larger than ``max_bytes``.
    """
    deadline = as_deadline(timeout)
    host = urllib.parse.urlsplit(url).hostname or "?"
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept": "application/json, text/csv, application/xml;q=0.9, */*;q=0.5",
    })
    try:
        with urllib.request.urlopen(req, timeout=min(deadline.left(), 60)) as r:
            length = r.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise RadiationError("%s: response too large (%s bytes)" % (host, length))
            gz = (r.headers.get("Content-Encoding") or "").lower() == "gzip"
            dec = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
            out = bytearray()
            raw = 0
            while True:
                deadline.left()
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                raw += len(chunk)
                if dec is not None:
                    chunk = dec.decompress(chunk, max_bytes + 1 - len(out))
                    if dec.unconsumed_tail:
                        raise RadiationError("%s: response too large" % host)
                out += chunk
                if len(out) > max_bytes or raw > max_bytes:
                    raise RadiationError("%s: response too large" % host)
            if dec is not None:
                out += dec.flush()
            return bytes(out)
    except RadiationError:
        raise
    except urllib.error.HTTPError as e:
        raise RadiationError("%s: HTTP %s" % (host, e.code))
    except urllib.error.URLError as e:
        raise RadiationError("%s: %s" % (host, e.reason))
    except (socket.timeout, TimeoutError):
        raise RadiationError("%s: timed out" % host)
    except (OSError, zlib.error, ValueError) as e:
        raise RadiationError("%s: %s" % (host, e))


def decode_json(body, what):
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise RadiationError("%s: not JSON (%s)" % (what, e))


def get_json(url, timeout, what, max_bytes=MAX_BYTES):
    return decode_json(http_get(url, timeout, max_bytes), what)


# --------------------------------------------------------------------------- values

def num(x):
    """A finite float or None (accepts numbers and numeric strings)."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_iso(text):
    """ISO-8601 time to unix seconds (int); naive times are UTC. None if unusable."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-3] != ":" and "T" in s:
        s = s[:-2] + ":" + s[-2:]                    # +0000 -> +00:00
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def station(name, lat, lon, usvh, ts, kind, km):
    """One station record in the shape the panel expects."""
    return {"name": str(name), "lat": round(lat, 4), "lon": round(lon, 4),
            "km": round(km, 1), "usvh": round(usvh, 4), "ts": int(ts), "kind": kind}


# --------------------------------------------------------------------------- geometry

def valid_latlon(lat, lon):
    return (lat is not None and lon is not None and -90.0 <= lat <= 90.0
            and -180.0 <= lon <= 180.0 and not (abs(lat) < 1e-6 and abs(lon) < 1e-6))


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def bbox(lat, lon, radius_km):
    """(west, south, east, north) in degrees around a point; clamped, no dateline wrap."""
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.05, math.cos(math.radians(lat))))
    return (max(-180.0, lon - dlon), max(-90.0, lat - dlat),
            min(180.0, lon + dlon), min(90.0, lat + dlat))


def parse_rings(rings):
    """Compact ring strings ``"lon,lat lon,lat ..."`` to tuples of (lon, lat) floats."""
    out = []
    for text in rings:
        pts = tuple(tuple(float(v) for v in p.split(",")) for p in text.split())
        if len(pts) >= 3:
            out.append(pts)
    return tuple(out)


def _inside(lon, lat, ring):
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def distance_to_rings_km(lat, lon, rings):
    """0 when the point is inside any ring, else the distance to the nearest ring edge
    (local equirectangular approximation, good to a few percent at these scales)."""
    if any(_inside(lon, lat, r) for r in rings):
        return 0.0
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    best = float("inf")
    for ring in rings:
        n = len(ring)
        for i in range(n):
            ax, ay = (ring[i][0] - lon) * kx, (ring[i][1] - lat) * ky
            bx, by = (ring[(i + 1) % n][0] - lon) * kx, (ring[(i + 1) % n][1] - lat) * ky
            dx, dy = bx - ax, by - ay
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best
