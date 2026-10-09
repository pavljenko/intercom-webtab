"""Worldwide fallback: Safecast real-time fixed sensors (volunteer network, CC0 data).

Safecast's real-time service ``https://tt.safecast.org/devices`` lists the latest
reading of every fixed device (bGeigie Zen/geigiecast, Pointcast, Solarcast, Radnote...).
The public Safecast tools query it the same way; ``class`` (a regular expression on the
device class) and ``template`` (the fields wanted) keep the answer near 100 kB. Radiation
fields are Geiger tube count rates in counts per minute:

=================  ===============================================  =============
field              tube                                             CPM per uSv/h
=================  ===============================================  =============
``lnd_7318u``      LND 7317/7318 pancake, unshielded                334
``lnd_7318c``      LND 7317/7318 pancake, energy-compensated cover  334
``lnd_7128ec``     LND 7128 energy-compensated                      108
=================  ===============================================  =============

The factors are the ones Safecast's own ingest server (TTServe) uses to derive uSv/h
(Cs-137 calibration). ``lnd_712u`` is ignored: in the current feed it holds zeros or values
that already look like uSv/h, so its unit is ambiguous. ``lnd_78017w`` has no published
factor and is ignored. When a device carries several tubes the pancake tube is preferred.

Outlier rules: readings below 1 CPM (dead tube), devices at 0,0, unparseable or future
capture times (some devices report years like 2044 or 2080) are dropped; duplicate entries
of one device keep the newest. High values are kept: deciding what is unusual is the
panel's job, and these are flagged as kind "community".

Licence: Safecast data are released under CC0 1.0; attribution is requested, not required.
"""
import json
import time

from . import base

ID = "safecast"
NAME = "Safecast"
SOURCE_URL = "https://safecast.org/"
LICENSE = "CC0 1.0 Universal (public domain dedication)"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
ATTRIBUTION = "Safecast (CC0)"
KIND = base.KIND_COMMUNITY

DEVICES_URL = "https://tt.safecast.org/devices"
CLASS_RE = "rad|geigie|pointcast|safecast"
CPM_PER_USVH = (("lnd_7318u", 334.0), ("lnd_7318c", 334.0), ("lnd_7128ec", 108.0))
TEMPLATE = {"device_urn": "", "device_class": "", "device": "", "when_captured": "",
            "loc_lat": None, "loc_lon": None, "loc_name": "",
            "lnd_7318u": None, "lnd_7318c": None, "lnd_7128ec": None}
MIN_CPM = 1.0

COVERAGE = None                      # worldwide


def request_url():
    return base.url_with(DEVICES_URL, [
        ("class", CLASS_RE), ("template", json.dumps(TEMPLATE, separators=(",", ":")))])


def usvh_of(dev):
    """(uSv/h, field) from the preferred tube of a device record, or None."""
    for field, factor in CPM_PER_USVH:
        cpm = base.num(dev.get(field))
        if cpm is not None and cpm >= MIN_CPM:
            return cpm / factor, field
    return None


def parse(doc, lat, lon, radius_km, now=None):
    if not isinstance(doc, list):
        raise base.RadiationError("%s: no device list in the response" % NAME)
    now = time.time() if now is None else now
    best = {}
    for dev in doc:
        if not isinstance(dev, dict):
            continue
        la, lo = base.num(dev.get("loc_lat")), base.num(dev.get("loc_lon"))
        ts = base.parse_iso(dev.get("when_captured"))
        got = usvh_of(dev)
        if got is None or ts is None or ts > now + base.FUTURE_S or not base.valid_latlon(la, lo):
            continue
        km = base.haversine_km(lat, lon, la, lo)
        if km > radius_km:
            continue
        key = str(dev.get("device_urn") or dev.get("device") or "%.5f,%.5f" % (la, lo))
        if key in best and best[key]["ts"] >= ts:
            continue
        name = (dev.get("loc_name") or "").strip() or "Safecast sensor %s" % (
            dev.get("device") or key)
        best[key] = base.station(name, la, lo, got[0], ts, KIND, km)
    return list(best.values())


def fetch(lat, lon, radius_km, timeout):
    doc = base.get_json(request_url(), timeout, NAME)
    return parse(doc, lat, lon, radius_km)
