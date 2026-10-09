"""Germany: BfS ODL-Info, the federal ambient dose rate network (about 1,700 probes).

Open data WFS of the Federal Office for Radiation Protection (Bundesamt fuer Strahlenschutz).
Layer ``opendata:odlinfo_odl_1h_latest`` holds the latest validated 1-hour mean per probe as
GeoJSON, value in uSv/h (gross gamma ambient dose rate, cosmic part included). No key, no
documented rate limit; the data change once an hour.

Outlier rules: probes whose ``site_status`` is not 1 ("in operation") are skipped (2 means
defective, 3 test operation), as are features without a value.

Licence: Datenlizenz Deutschland - Namensnennung - Version 2.0 (attribution required).
"""
from . import base

ID = "bfs_odl"
NAME = "BfS ODL-Info (Germany)"
SOURCE_URL = "https://odlinfo.bfs.de/"
LICENSE = "Datenlizenz Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0)"
LICENSE_URL = "https://www.govdata.de/dl-de/by-2-0"
ATTRIBUTION = "BfS ODL-Info (dl-de/by-2-0)"
KIND = base.KIND_DOSE

WFS_URL = "https://www.imis.bfs.de/ogc/opendata/ows"
LAYER = "opendata:odlinfo_odl_1h_latest"
UNITS = {"µsv/h": 1.0, "μsv/h": 1.0, "usv/h": 1.0, "nsv/h": 0.001}

# Natural Earth (public domain) outline, simplified to ~20 km. Rings: "lon,lat ...".
COVERAGE_RINGS = (
    ('13.82,48.77 12.74,48.11 13,47.47 12.24,47.73 11.24,47.39 10.43,47.58 10.16,47.27 '
     '8.58,47.8 8.56,47.59 7.59,47.58 7.59,48.13 8.2,48.96 6.35,49.46 6.5,49.8 6.1,50.05 '
     '6.37,50.32 5.86,51.02 6.21,51.39 5.93,51.81 7.03,52.23 6.67,52.54 7.37,53.3 7.04,53.35 '
     '7.1,53.6 8.43,53.56 8.5,53.36 8.65,53.89 9.83,53.54 8.6,54.31 9.01,54.48 8.82,54.91 '
     '9.95,54.78 9.84,54.48 10.14,54.32 11.14,54.39 10.89,53.96 12.12,54.1 12.53,54.49 '
     '12.92,54.43 12.37,54.27 13.03,54.43 14.26,53.75 14.44,53.25 14.12,52.85 14.64,52.58 '
     '14.91,50.99 14.29,51.04 12.3,50.16 12.08,50.32 12.64,49.43'),
)
COVERAGE = base.parse_rings(COVERAGE_RINGS)


def request_url(lat, lon, radius_km):
    w, s, e, n = base.bbox(lat, lon, radius_km)
    # WFS 1.0.0 keeps the classic lon,lat axis order for the bbox.
    return base.url_with(WFS_URL, [
        ("service", "WFS"), ("version", "1.0.0"), ("request", "GetFeature"),
        ("typeName", LAYER), ("outputFormat", "application/json"), ("maxFeatures", "3000"),
        ("bbox", "%.4f,%.4f,%.4f,%.4f" % (w, s, e, n))])


def parse(doc, lat, lon, radius_km):
    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        raise base.RadiationError("%s: no feature list in the response" % NAME)
    out = []
    for f in doc["features"]:
        try:
            p = f["properties"]
            x, y = f["geometry"]["coordinates"][:2]
        except (KeyError, TypeError, ValueError):
            continue
        if p.get("site_status") != 1:
            continue
        v = base.num(p.get("value"))
        k = UNITS.get(str(p.get("unit") or "µSv/h").strip().lower())
        ts = base.parse_iso(p.get("end_measure"))
        la, lo = base.num(y), base.num(x)
        if v is None or k is None or ts is None or not base.valid_latlon(la, lo):
            continue
        km = base.haversine_km(lat, lon, la, lo)
        if km <= radius_km:
            out.append(base.station(p.get("name") or p.get("id") or "?", la, lo, v * k, ts,
                                    KIND, km))
    return out


def fetch(lat, lon, radius_km, timeout):
    doc = base.get_json(request_url(lat, lon, radius_km), timeout, NAME)
    return parse(doc, lat, lon, radius_km)
