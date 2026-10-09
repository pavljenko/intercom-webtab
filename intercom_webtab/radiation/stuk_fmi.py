"""Finland: STUK automatic dose rate network (about 255 probes), served by FMI open data.

The Radiation and Nuclear Safety Authority (STUK) publishes its external dose rate through
the Finnish Meteorological Institute open data WFS. Stored query
``stuk::observations::external-radiation::latest::multipointcoverage`` returns the latest
10-minute mean per station (``DR_PT10M_avg``, uSv/h, ambient dose equivalent rate) as
GML multipoint coverage. No key; FMI asks for moderate request rates.

Outlier rules: NaN values (station not reporting) are skipped.

Licence: Creative Commons Attribution 4.0 (FMI open data licence, which covers STUK sets).
"""
import xml.etree.ElementTree as ET

from . import base

ID = "stuk_fmi"
NAME = "STUK dose rate network (Finland)"
SOURCE_URL = "https://en.ilmatieteenlaitos.fi/open-data"
LICENSE = "Creative Commons Attribution 4.0 International (CC BY 4.0)"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
ATTRIBUTION = "STUK via FMI open data (CC BY 4.0)"
KIND = base.KIND_DOSE

WFS_URL = "https://opendata.fmi.fi/wfs"
QUERY = "stuk::observations::external-radiation::latest::multipointcoverage"
PARAM = "DR_PT10M_avg"

NS = {"gml": "http://www.opengis.net/gml/3.2",
      "gmlcov": "http://www.opengis.net/gmlcov/1.0",
      "swe": "http://www.opengis.net/swe/2.0"}

# Natural Earth (public domain) outline incl. the Aland islands, simplified to ~25 km.
COVERAGE_RINGS = (
    ('28.95,69.03 28.41,68.9 28.8,68.84 28.45,68.53 28.66,68.2 30.01,67.69 29.05,66.91 '
     '30.11,65.71 29.7,65.63 29.63,64.91 30.56,64.22 29.98,63.74 31.57,62.91 31.24,62.5 '
     '27.72,60.49 26.48,60.47 26.66,60.65 23.07,59.82 23.33,60.03 22.87,60.15 23.08,60.36 '
     '21.41,60.58 21.49,61.81 21.07,62.6 21.69,63.03 21.5,63.22 22.33,63.28 24.54,64.81 '
     '25.44,64.97 25.32,65.51 23.69,66.26 24,66.81 23.43,67.49 23.66,67.95 20.62,69.04 '
     '21.64,69.27 22.41,68.71 24.91,68.56 25.8,68.99 26.01,69.69 27.87,70.08 29.15,69.66'),
    ('22.84,60.24 22.61,59.98 22.45,60.22'),
    ('20.21,60.22 20.02,60.31 19.95,60.05 19.71,60.15 19.65,60.27 19.93,60.28 19.86,60.41'),
)
COVERAGE = base.parse_rings(COVERAGE_RINGS)


def request_url(lat, lon, radius_km):
    w, s, e, n = base.bbox(lat, lon, radius_km)
    return base.url_with(WFS_URL, [
        ("service", "WFS"), ("version", "2.0.0"), ("request", "getFeature"),
        ("storedquery_id", QUERY), ("bbox", "%.4f,%.4f,%.4f,%.4f" % (w, s, e, n))])


def _key(la, lo):
    return "%.4f %.4f" % (la, lo)


def parse(body, lat, lon, radius_km):
    head = body[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in body.lower():
        raise base.RadiationError("%s: unexpected document type" % NAME)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise base.RadiationError("%s: not XML (%s)" % (NAME, e))
    if root.tag.endswith("ExceptionReport"):
        raise base.RadiationError("%s: service exception" % NAME)
    names = {}
    for pt in root.iter("{%s}Point" % NS["gml"]):
        nm = pt.find("gml:name", NS)
        pos = pt.find("gml:pos", NS)
        if nm is None or pos is None or not pos.text:
            continue
        xy = pos.text.split()
        if len(xy) >= 2 and base.num(xy[0]) is not None and base.num(xy[1]) is not None:
            names[_key(float(xy[0]), float(xy[1]))] = (nm.text or "").strip()
    out = []
    for cov in root.iter("{%s}MultiPointCoverage" % NS["gmlcov"]):
        fields = [f.get("name") for f in cov.iter("{%s}field" % NS["swe"])]
        if PARAM not in fields:
            continue
        col, width = fields.index(PARAM), len(fields)
        pos_el = cov.find(".//gmlcov:positions", NS)
        val_el = cov.find(".//gml:doubleOrNilReasonTupleList", NS)
        if pos_el is None or val_el is None:
            continue
        pos = (pos_el.text or "").split()
        vals = (val_el.text or "").split()
        rows = min(len(pos) // 3, len(vals) // width)
        for i in range(rows):
            la, lo, ts = base.num(pos[3 * i]), base.num(pos[3 * i + 1]), base.num(pos[3 * i + 2])
            v = base.num(vals[i * width + col])
            if v is None or ts is None or not base.valid_latlon(la, lo):
                continue
            km = base.haversine_km(lat, lon, la, lo)
            if km <= radius_km:
                name = names.get(_key(la, lo)) or "STUK %.2f, %.2f" % (la, lo)
                out.append(base.station(name, la, lo, v, ts, KIND, km))
    return out


def fetch(lat, lon, radius_km, timeout):
    body = base.http_get(request_url(lat, lon, radius_km), timeout)
    return parse(body, lat, lon, radius_km)
