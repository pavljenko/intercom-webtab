"""Canada: Health Canada Fixed Point Surveillance (FPS) network (about 80 probes).

Health Canada publishes the latest reading of every FPS station through an ArcGIS REST map
service listed in the open.canada.ca catalogue ("Real-time Environmental Radioactivity
Monitoring in Canada"). Layer 1 ("Stations Readings (nSv/hr)") carries ``location_name``,
``latest_value_nSvHr`` (integer nSv/h), ``time`` (epoch milliseconds, UTC) and
``dose_rate_type``. The network uses spectroscopic NaI detectors and reports the
*terrestrial* gamma dose rate: the cosmic component (roughly 0.03 uSv/h at sea level) is
not included, so values read lower than total ambient dose rate elsewhere. Updated about
every 15 minutes; no key.

Outlier rules: features without geometry, value or time are skipped.

Licence: Open Government Licence - Canada (attribution statement required).
"""
from . import base

ID = "hc_fps"
NAME = "Health Canada FPS network"
SOURCE_URL = "https://open.canada.ca/data/en/dataset/f0d1c3a9-cf78-4b07-af55-9e8d4303449e"
LICENSE = "Open Government Licence - Canada"
LICENSE_URL = "https://open.canada.ca/en/open-government-licence-canada"
ATTRIBUTION = "Health Canada FPS (OGL-Canada)"
KIND = base.KIND_TERRESTRIAL

QUERY_URL = ("https://maps-cartes.services.geo.ca/server_serveur/rest/services/HC/fpsn_en/"
             "MapServer/1/query")
FIELDS = "location_name,latest_value_nSvHr,time,dose_rate_type"

# Natural Earth (public domain) outlines south of 60.5 N, simplified to ~30 km, plus one
# box for the territories and the Arctic islands (west edge = the Alaska border).
COVERAGE_RINGS = (
    ('-94.8,60.5 -95,59.05 -94.2,58.81 -94.36,58.22 -94.18,58.78 -93.16,58.74 -92.42,57.35 '
     '-92.86,56.91 -90.59,57.23 -85.13,55.35 -85.42,55 -82.25,55.12 -82.3,52.97 -81.48,52.3 '
     '-81.88,52.19 -80.59,51.71 -80.43,51.36 -80.95,51.01 -80.14,51.3 -79.33,50.73 -79.75,51.2 '
     '-79.32,51.67 -78.85,51.16 -79.04,51.77 -78.41,52.25 -78.98,53.03 -79.05,54.18 '
     '-79.77,54.66 -77.78,55.27 -76.54,56.32 -76.81,57.68 -78.57,58.64 -77.19,60.06 '
     '-77.63,60.07 -69.63,60.08 -71.04,60.07 -69.56,59.86 -69.45,58.9 -70.25,58.77 '
     '-68.77,58.92 -68.19,58.55 -69.37,57.77 -68.01,58.58 -68.13,58.08 -67.74,58.47 '
     '-67.71,57.93 -66.38,58.85 -66.05,58.33 -65.98,58.92 -65.32,59.05 -65.56,59.49 '
     '-64.98,59.38 -65.55,59.73 -64.98,59.76 -64.85,60.37 -63.73,59.52 -64.06,59.39 '
     '-63.38,59.28 -64.04,59.02 -62.91,58.81 -63.59,58.31 -62.58,58.5 -63.34,57.98 '
     '-62.93,58.13 -61.89,57.67 -62.52,57.48 -61.37,57.11 -61.69,56.62 -62.59,56.8 '
     '-61.65,56.53 -62.07,56.29 -60.68,55.56 -60.33,55.78 -60.69,55 -59.78,55.33 -59.43,55.14 '
     '-59.92,54.74 -59.16,55.24 -59.38,54.98 -57.35,54.59 -60.14,53.53 -60.88,53.83 '
     '-60.05,53.5 -60.42,53.27 -57.47,54.2 -57.08,53.82 -57.38,53.43 -55.98,53.55 -55.75,53.15 '
     '-56.17,53.03 -55.8,52.84 -56.16,52.82 -55.75,52.62 -56.5,52.6 -55.65,52.44 -56.2,52.45 '
     '-55.68,52.11 -56.97,51.43 -58.62,51.28 -60.13,50.21 -66.47,50.27 -69.68,48.15 '
     '-71.07,48.44 -69.74,48.12 -70.75,47.09 -74.38,45.56 -73.96,45.35 -76.8,43.65 '
     '-78.75,43.62 -79.17,43.46 -79.02,42.8 -82.67,41.67 -83.16,42.05 -82.15,43.58 '
     '-82.55,45.33 -84.11,46.53 -88.38,48.31 -91.57,48.05 -94.59,48.73 -95.16,49.37 '
     '-95.18,48.99 -123.09,48.99 -123.17,49.71 -124.06,49.63 -123.53,49.71 -123.83,50.16 '
     '-124.28,49.75 -124.83,50.05 -124.36,50.51 -125.08,50.32 -124.81,50.93 -125.7,50.44 '
     '-126.28,50.63 -125.64,51.1 -126.19,50.67 -127.78,51.17 -126.6,51.71 -127.87,51.67 '
     '-127.17,52.32 -126.67,51.99 -127.22,52.46 -126.97,52.83 -128.39,52.3 -128.03,52.92 '
     '-128.44,52.83 -128.97,53.56 -127.87,53.23 -128.81,53.62 -128.6,54.03 -129.29,53.39 '
     '-130.1,53.95 -129.49,54.25 -130.48,54.37 -129.96,54.32 -130.37,54.66 -129.88,54.62 '
     '-130.17,54.84 -129.48,55.48 -129.82,55.62 -130.12,55.01 -130.07,56.08 -131.83,56.59 '
     '-135.03,59.57 -136.26,59.62 -137.49,58.9 -139.08,60.34 -141,60.32'),
    ('-74.71,45 -70.53,47.01 -68.82,48.37 -65.4,49.26 -64.3,48.96 -64.19,48.53 -66.85,48 '
     '-64.8,47.81 -65.37,47.09 -64.8,47.08 -64.56,46.22 -62.75,45.61 -61.91,45.89 -60.97,45.31 '
     '-63.85,44.5 -65.73,43.51 -66.17,43.86 -66,44.58 -63.36,45.36 -64.93,45.33 -64.26,45.77 '
     '-64.75,46.09 -64.77,45.61 -65.75,45.24 -67.36,45.17 -67.81,45.71 -67.81,47.08 '
     '-69.22,47.46 -70.89,45.23'),
    ('-52.63,47.53 -53.07,46.67 -53.55,46.62 -53.56,47.2 -54.19,46.83 -53.81,47.42 '
     '-54.08,47.88 -55.38,46.87 -55.99,46.95 -54.7,47.67 -59.31,47.62 -58.27,48.52 '
     '-59.23,48.54 -58.37,49.15 -57.88,48.97 -58.23,49.37 -57.7,49.46 -57.4,50.71 -55.68,51.53 '
     '-56.86,49.55 -56.12,50.16 -55.47,49.96 -56.13,49.43 -55.14,49.54 -55.38,49.05 '
     '-54.47,49.55 -53.49,49.29 -54.16,48.39 -52.98,48.55 -53.93,48.24 -53.61,48.05 '
     '-53.93,47.85 -53.55,47.54 -52.92,48.17 -53.26,47.56'),
    ('-123.32,48.5 -125.09,48.73 -124.81,49.25 -125.5,48.92 -126.57,49.41 -126.11,49.68 '
     '-127.61,50.12 -127.98,50.36 -127.42,50.6 -128.42,50.77 -125.48,50.34'),
    ('-60.08,45.79 -61.28,45.55 -61.55,46.02 -60.4,47.03 -60.43,46.28 -61.13,45.93 -60.8,45.97 '
     '-61.15,45.71 -60.28,46.33 -59.81,46.17'),
    ('-132.58,53.21 -133.07,54.17 -132.2,54.02 -132.67,53.68 -131.66,54.17 -131.96,53.28'),
    ('-62,46.46 -62.75,45.95 -63.77,46.4 -64.4,46.72 -64,47.07 -63.94,46.48'),
    ('-79.55,56.04 -80.03,55.9 -79.26,56.68 -79.01,56.43 -79.2,55.86 -78.98,56.39 -79.79,55.79'),
    ('-131.11,52.17 -132.57,53.15 -131.65,53.12 -131.98,52.88'),
    ('-64.32,49.79 -62.13,49.4 -61.66,49.15'),
    ('-128.8,52.7 -129.06,53.23 -128.54,53.02'),
    ('-130.25,53.89 -129.33,53.26 -129.64,53.32'),
    ('-141,60 -52,60 -52,83.5 -141,83.5'),
)
COVERAGE = base.parse_rings(COVERAGE_RINGS)


def request_url(lat, lon, radius_km):
    w, s, e, n = base.bbox(lat, lon, radius_km)
    return base.url_with(QUERY_URL, [
        ("where", "1=1"), ("geometry", "%.4f,%.4f,%.4f,%.4f" % (w, s, e, n)),
        ("geometryType", "esriGeometryEnvelope"), ("inSR", "4326"),
        ("spatialRel", "esriSpatialRelIntersects"), ("outFields", FIELDS),
        ("outSR", "4326"), ("returnGeometry", "true"), ("f", "json")])


def display_name(code):
    """``7000_8_45.5049_-73.5749`` -> ``FPS 7000-8`` (the rest are coordinates)."""
    parts = str(code or "").split("_")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return "FPS %s-%s" % (parts[0], parts[1])
    return "FPS %s" % (code or "?")


def parse(doc, lat, lon, radius_km):
    if not isinstance(doc, dict):
        raise base.RadiationError("%s: unexpected response" % NAME)
    if isinstance(doc.get("error"), dict):
        raise base.RadiationError("%s: service error %s" % (NAME, doc["error"].get("code")))
    feats = doc.get("features")
    if not isinstance(feats, list):
        raise base.RadiationError("%s: no feature list in the response" % NAME)
    out = []
    for f in feats:
        a = f.get("attributes") if isinstance(f, dict) else None
        g = f.get("geometry") if isinstance(f, dict) else None
        if not isinstance(a, dict) or not isinstance(g, dict):
            continue
        la, lo = base.num(g.get("y")), base.num(g.get("x"))
        v = base.num(a.get("latest_value_nSvHr"))
        ms = base.num(a.get("time"))
        if v is None or v <= 0 or ms is None or not base.valid_latlon(la, lo):
            continue
        kind = KIND
        typ = str(a.get("dose_rate_type") or "").lower()
        if typ and "terrestrial" not in typ:
            kind = base.KIND_DOSE
        km = base.haversine_km(lat, lon, la, lo)
        if km <= radius_km:
            out.append(base.station(display_name(a.get("location_name")), la, lo, v / 1000.0,
                                    ms / 1000.0, kind, km))
    return out


def fetch(lat, lon, radius_km, timeout):
    doc = base.get_json(request_url(lat, lon, radius_km), timeout, NAME)
    return parse(doc, lat, lon, radius_km)
