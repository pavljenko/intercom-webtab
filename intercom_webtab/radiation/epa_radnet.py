"""United States: EPA RadNet near-real-time air monitors (about 140 fixed stations).

EPA publishes one year-to-date CSV per station at
``https://radnet.epa.gov/cdx-radnet-rest/api/rest/csv/<year>/fixed/<state>/<city>`` (linked
from the "RadNet CSV File Downloads" page). Columns used: ``SAMPLE COLLECTION TIME`` (UTC,
``MM/DD/YYYY HH:MM:SS``), ``DOSE EQUIVALENT RATE (nSv/h)`` and ``STATUS``. Only stations
with a gamma exposure-rate detector fill the dose column; gross gamma count rates (CPM) are
filter counts, not ambient dose, and are never converted. Data arrive several times a day.

The CSV has no coordinates, so this module carries the station list with approximate
city-centre coordinates (GeoNames via the Open-Meteo geocoder, rounded to 0.01 degree);
distances are therefore good to a few kilometres. Each file is 0.3-0.8 MB without
compression support, so only the nearest few stations are downloaded per call.

Outlier rules: rows with an empty dose value or a STATUS containing "REJECT"/"INVALID"
are skipped; the newest remaining row of each station is used.

Licence: works of the U.S. Government are in the public domain (17 U.S.C. 105).
"""
import calendar
import concurrent.futures
import csv
import io
import time
import urllib.parse

from . import base

ID = "epa_radnet"
NAME = "US EPA RadNet"
SOURCE_URL = "https://www.epa.gov/radnet"
LICENSE = "Public domain (U.S. Government work, 17 U.S.C. 105)"
LICENSE_URL = "https://www.usa.gov/government-copyright"
ATTRIBUTION = "US EPA RadNet"
KIND = base.KIND_DOSE

CSV_URL = "https://radnet.epa.gov/cdx-radnet-rest/api/rest/csv/{year}/fixed/{state}/{city}"
MAX_DOWNLOADS = 5          # nearest stations tried per call, at most
WANT = 3                   # enough stations with a dose value
DISPLAY = {"NYC (EML)": "New York City", "SAN BERNARDINO COUNTY": "San Bernardino County"}

# state|city as EPA spells it in the CSV URL|lat|lon (approximate city centre)
STATIONS_TXT = """
AK|ANCHORAGE|61.22|-149.90
AK|FAIRBANKS|64.84|-147.72
AK|JUNEAU|58.30|-134.42
AL|BIRMINGHAM|33.52|-86.80
AL|MOBILE|30.69|-88.04
AL|MONTGOMERY|32.37|-86.30
AR|FT SMITH|35.39|-94.40
AR|LITTLE ROCK|34.75|-92.29
AZ|PHOENIX|33.45|-112.07
AZ|TUCSON|32.22|-110.93
AZ|YUMA|32.73|-114.62
CA|ANAHEIM|33.84|-117.92
CA|BAKERSFIELD|35.37|-119.02
CA|EUREKA|40.80|-124.16
CA|FRESNO|36.75|-119.77
CA|LOS ANGELES|34.05|-118.24
CA|RIVERSIDE|33.95|-117.40
CA|SACRAMENTO|38.58|-121.49
CA|SAN BERNARDINO COUNTY|34.11|-117.29
CA|SAN DIEGO|32.72|-117.17
CA|SAN FRANCISCO|37.77|-122.42
CA|SAN JOSE|37.34|-121.89
CO|COLORADO SPRINGS|38.83|-104.82
CO|DENVER|39.74|-104.98
CO|GRAND JUNCTION|39.06|-108.55
CT|HARTFORD|41.76|-72.69
DC|WASHINGTON|38.90|-77.04
DE|DOVER|39.16|-75.52
FL|JACKSONVILLE|30.33|-81.66
FL|MIAMI|25.77|-80.19
FL|ORLANDO|28.54|-81.38
FL|TALLAHASSEE|30.44|-84.28
FL|TAMPA|27.95|-82.46
GA|ATLANTA|33.75|-84.39
GA|AUGUSTA|33.47|-81.97
HI|HONOLULU|21.31|-157.86
IA|DES MOINES|41.60|-93.61
IA|FORT MADISON|40.63|-91.31
IA|MASON CITY|43.15|-93.20
ID|BOISE|43.61|-116.20
ID|IDAHO FALLS|43.47|-112.03
IL|AURORA|41.76|-88.32
IL|CHAMPAIGN|40.12|-88.24
IL|CHICAGO|41.85|-87.65
IN|FORT WAYNE|41.13|-85.13
IN|INDIANAPOLIS|39.77|-86.16
KS|DODGE CITY|37.75|-100.02
KS|KANSAS CITY|39.11|-94.63
KS|WICHITA|37.69|-97.34
KY|LEXINGTON|37.99|-84.48
KY|LOUISVILLE|38.25|-85.76
KY|PADUCAH|37.08|-88.60
LA|BATON ROUGE|30.44|-91.19
LA|SHREVEPORT|32.52|-93.75
MA|BOSTON|42.36|-71.06
MA|WORCESTER|42.26|-71.80
MD|BALTIMORE|39.29|-76.61
ME|ORONO|44.88|-68.67
ME|PORTLAND|43.66|-70.26
MI|BAY CITY|43.59|-83.89
MI|DETROIT|42.33|-83.05
MI|GRAND RAPIDS|42.96|-85.67
MN|DULUTH|46.78|-92.11
MN|ST. PAUL|44.94|-93.09
MO|JEFFERSON CITY|38.58|-92.17
MO|SPRINGFIELD|37.22|-93.30
MO|ST. LOUIS|38.63|-90.20
MS|JACKSON|32.30|-90.19
MT|BILLINGS|45.78|-108.50
MT|KALISPELL|48.20|-114.31
NC|CHARLOTTE|35.23|-80.84
NC|GREENSBORO|36.07|-79.79
NC|RALEIGH|35.77|-78.64
NC|WILMINGTON|34.24|-77.95
ND|BISMARCK|46.81|-100.78
NE|KEARNEY|40.70|-99.08
NE|LINCOLN|40.80|-96.67
NE|OMAHA|41.26|-95.94
NH|CONCORD|43.21|-71.54
NH|PORTSMOUTH|43.08|-70.76
NJ|EDISON|40.52|-74.41
NM|ALBUQUERQUE|35.08|-106.65
NM|CARLSBAD|32.42|-104.23
NM|NAVAJO LAKE|36.80|-107.61
NV|LAS VEGAS|36.17|-115.14
NV|RENO|39.53|-119.81
NY|ALBANY|42.65|-73.76
NY|BUFFALO|42.89|-78.88
NY|NYC (EML)|40.71|-74.01
NY|ROCHESTER|43.16|-77.62
NY|SYRACUSE|43.05|-76.15
NY|YAPHANK|40.84|-72.92
OH|CINCINNATI|39.13|-84.51
OH|CLEVELAND|41.50|-81.69
OH|COLUMBUS|39.96|-83.00
OH|TOLEDO|41.66|-83.56
OK|OKLAHOMA CITY|35.47|-97.52
OK|TULSA|36.15|-95.99
OR|CORVALLIS|44.56|-123.26
OR|PORTLAND|45.52|-122.68
PA|BLOOMSBURG|41.00|-76.45
PA|PHILADELPHIA|39.95|-75.16
PA|PITTSBURGH|40.44|-80.00
PR|SAN JUAN|18.47|-66.11
RI|PROVIDENCE|41.82|-71.41
SC|COLUMBIA|34.00|-81.03
SD|PIERRE|44.37|-100.35
SD|RAPID CITY|44.08|-103.23
TN|KNOXVILLE|35.96|-83.92
TN|MEMPHIS|35.15|-90.05
TN|NASHVILLE|36.17|-86.78
TX|AMARILLO|35.22|-101.83
TX|AUSTIN|30.27|-97.74
TX|CORPUS CHRISTI|27.80|-97.40
TX|DALLAS|32.78|-96.81
TX|EL PASO|31.76|-106.49
TX|FT. WORTH|32.73|-97.32
TX|HARLINGEN|26.19|-97.70
TX|HOUSTON|29.76|-95.36
TX|LAREDO|27.51|-99.51
TX|LUBBOCK|33.58|-101.86
TX|SAN ANGELO|31.46|-100.44
TX|SAN ANTONIO|29.42|-98.49
UT|SALT LAKE CITY|40.76|-111.89
UT|ST. GEORGE|37.10|-113.58
VA|HARRISONBURG|38.45|-78.87
VA|RICHMOND|37.55|-77.46
VA|VIRGINIA BEACH|36.85|-75.98
VT|BURLINGTON|44.48|-73.21
WA|ELLENSBURG|47.00|-120.55
WA|OLYMPIA|47.05|-122.90
WA|RICHLAND|46.29|-119.28
WA|SEATTLE|47.61|-122.33
WA|SPOKANE|47.66|-117.43
WI|LA CROSSE|43.80|-91.24
WI|MADISON|43.07|-89.40
WI|MILWAUKEE|43.04|-87.91
WI|SHAWANO|44.78|-88.61
WV|CHARLESTON|38.35|-81.63
WY|CASPER|42.87|-106.31
"""

# Natural Earth (public domain) outlines of the 50 states and Puerto Rico, simplified to
# ~30 km; one box stands in for the main Hawaiian islands.
COVERAGE_RINGS = (
    ('-95.16,49.37 -94.59,48.73 -91.57,48.05 -88.35,48.3 -84.11,46.53 -82.53,45.29 '
     '-82.15,43.58 -83.16,42.05 -82.67,41.67 -79.02,42.8 -79.17,43.46 -78.75,43.62 '
     '-76.84,43.63 -74.9,45.01 -70.89,45.23 -69.24,47.46 -67.81,47.08 -67.81,45.71 -66.98,44.8 '
     '-68.54,44.24 -68.8,44.56 -69.19,43.95 -70.29,43.65 -71.05,42.32 -69.94,41.67 '
     '-71.19,41.47 -71.39,41.82 -71.5,41.37 -73.89,40.8 -73.98,41.29 -74.41,39.36 -74.95,38.93 '
     '-75.56,39.61 -75.02,40.02 -75.59,39.65 -75.04,38.43 -75.95,37.12 -75.63,37.96 '
     '-76.37,38.84 -75.83,39.58 -76.62,39.26 -76.32,38.04 -77.26,38.39 -77.07,38.9 '
     '-77.34,38.35 -76.25,37.9 -76.5,37.66 -77.13,38.17 -76.28,37.57 -76.8,37.5 -76.27,37.05 '
     '-77.27,37.32 -76,36.93 -75.52,35.79 -75.95,36.72 -75.79,36.08 -76.73,36.32 -76.73,35.95 '
     '-75.72,35.83 -76.15,35.35 -77.05,35.53 -76.47,35.27 -76.94,34.98 -76.35,34.88 '
     '-77.44,34.75 -77.94,33.93 -78.76,33.78 -80.55,32.28 -80.85,32.54 -81.53,30.85 '
     '-80.04,26.81 -80.4,25.19 -81.15,25.17 -82.69,27.97 -82.72,27.66 -82.63,28.89 '
     '-83.79,29.99 -85.35,29.68 -85.59,30.33 -87.03,30.61 -88.03,30.22 -88.03,30.74 '
     '-88.14,30.32 -90.41,30.22 -89.37,30.05 -89.76,29.63 -89.01,29.17 -89.41,28.93 '
     '-89.82,29.47 -90.88,29.13 -91.85,29.83 -92.31,29.53 -93.85,29.99 -94.76,29.37 '
     '-94.49,29.57 -95.05,29.75 -95.16,29.05 -96.22,28.49 -96.65,28.73 -96.4,28.43 '
     '-97.52,27.87 -97.41,27.34 -97.77,27.47 -97.14,25.97 -98.22,26.08 -99.11,26.42 '
     '-99.52,27.59 -101.41,29.78 -102.32,29.89 -103.39,29.03 -106.39,31.75 -111.01,31.33 '
     '-114.72,32.71 -117.13,32.53 -118.51,34.02 -120.64,34.56 -120.64,35.14 -122.52,37.53 '
     '-122.03,37.46 -122.37,38.01 -121.45,38 -123.03,37.99 -124.36,40.26 -124.07,41.54 '
     '-124.57,42.83 -124.07,43.71 -124.02,46.23 -123.18,46.18 -124.08,46.27 -123.81,46.96 '
     '-124.18,46.93 -124.73,48.39 -122.77,48.14 -123.16,47.36 -122.47,47.76 -122.89,47.05 '
     '-122.19,48.03 -122.75,48.99 -95.18,48.99'),
    ('-141.01,69.65 -140.99,60.3 -139.08,60.34 -137.49,58.9 -135.26,59.7 -131.83,56.59 '
     '-130.07,56.08 -130.37,54.91 -130.93,54.82 -130.46,55.33 -131.06,55.13 -130.61,55.3 '
     '-131.01,56.11 -132.17,55.59 -131.49,56.22 -133.55,57.18 -133.04,57.37 -133.65,57.7 '
     '-133,57.52 -133.55,57.92 -133.12,57.86 -134.06,58.07 -133.77,58.52 -134.76,58.38 '
     '-135.35,59.47 -135.09,58.24 -135.91,58.38 -135.77,58.9 -137.06,59.06 -136.03,58.39 '
     '-136.66,58.22 -139.86,59.55 -139.47,59.99 -139.29,59.58 -138.9,59.81 -139.52,60.05 '
     '-140.32,59.7 -141.38,60.14 -144.25,60.03 -144.94,60.31 -144.62,60.72 -145.94,60.46 '
     '-145.63,60.68 -146.7,60.74 -146.09,60.84 -146.75,60.96 -146.28,61.12 -147.87,60.84 '
     '-147.73,61.28 -148.7,60.79 -148.2,60.63 -148.69,60.46 -147.94,60.46 -148.44,59.96 '
     '-149.41,60.12 -150.96,59.2 -151.98,59.28 -150.99,59.78 -151.87,59.75 -151.4,60.74 '
     '-149.03,60.85 -150.07,61.16 -149.43,61.51 -153.08,60.3 -152.58,60.07 -154.26,59.15 '
     '-153.26,58.86 -158.66,56.13 -163.39,54.86 -161.8,55.89 -160.24,55.78 -160.58,55.99 '
     '-160.19,56.39 -157.41,57.49 -157.61,58.11 -157.14,58.17 -157.56,58.36 -156.78,59.16 '
     '-158.2,58.61 -158.5,58.99 -158,58.91 -158.54,59.18 -158.83,58.4 -160.35,59.07 '
     '-161.76,58.56 -162.18,58.64 -161.66,58.81 -162.05,59.27 -161.71,59.5 -162.46,60.3 '
     '-161.88,60.7 -162.7,60.27 -162.53,60 -163.61,59.8 -165.43,60.55 -164.94,60.94 '
     '-164.42,60.55 -163.41,60.75 -165.13,60.92 -164.77,61.11 -165.36,61.21 -164.69,61.6 '
     '-165.51,61.09 -166.1,61.82 -164.58,62.43 -164.88,62.84 -164.37,63.23 -163.09,63.06 '
     '-161.06,63.57 -160.97,64.25 -161.54,64.4 -160.79,64.73 -162.78,64.34 -166.19,64.59 '
     '-166.96,65.18 -166.03,65.24 -168.13,65.67 -164.27,66.6 -163.63,66.57 -164.19,66.19 '
     '-163.68,66.08 -161.02,66.19 -161.9,66.27 -162.48,66.96 -161.63,66.46 -160.22,66.53 '
     '-163.72,67.11 -164.15,67.62 -166.83,68.35 -166.22,68.89 -163.96,69 -161.96,70.3 '
     '-159.95,70.6 -159.77,70.2 -159.29,70.54 -160.13,70.63 -156.47,71.41 -155.57,71.15 '
     '-155.98,70.76 -155.09,71.15 -152.33,70.86 -152.08,70.58 -152.63,70.56 -151.98,70.45 '
     '-143.24,70.12'),
    ('-152.16,57.63 -153.96,56.75 -153.74,57.13 -154.49,57.11 -154.27,56.86 -154.81,57.35 '
     '-154.22,57.68 -153.63,57.27 -153.84,57.87'),
    ('-131.98,54.83 -133.21,55.28 -132.91,55.63 -133.38,55.63 -133.14,55.89 -133.61,56.25 '
     '-132.56,55.87 -132.14,55.47 -132.68,55.46 -131.99,55.27'),
    ('-160.6,18.8 -154.6,18.8 -154.6,22.4 -160.6,22.4'),
    ('-168.74,63.28 -169.67,62.95 -171.8,63.61'),
    ('-134.35,57.09 -134.96,58.4 -133.89,57.79 -134.31,58.1 -133.84,57.46'),
    ('-135.85,57.39 -136.42,58.12 -135.39,58.14 -134.93,57.81 -135.89,57.99 -134.82,57.49 '
     '-135.81,57.76'),
    ('-134.62,56.74 -134.67,56.17 -135.31,56.98 -135.44,57.55'),
    ('-163.07,54.69 -164.94,54.6 -163.76,55.06'),
    ('-133.91,57.08 -132.98,56.94 -132.97,56.61 -133.35,56.83 -133.12,56.51 -133.66,56.46'),
    ('-165.58,59.95 -167.25,60.23 -166.16,60.44'),
    ('-130.96,55.59 -131.19,55.19 -131.44,55.53 -131.82,55.46 -131.28,55.97'),
    ('-166.29,53.73 -167.85,53.31 -167.04,53.63 -166.75,54.02'),
    ('-71.92,41.06 -74.04,40.62 -73.57,40.91'),
    ('-133.88,56.49 -134.11,56 -134.04,56.5 -134.4,56.86 -133.72,56.77'),
    ('-167.86,53.39 -169.11,52.82 -168.85,53.02'),
    ('-174.05,52.24 -175.34,52.01 -175,52.07'),
    ('-65.63,18.28 -65.94,17.97 -67.18,17.94 -67.27,18.36 -67.1,18.52 -65.9,18.46'),
)
COVERAGE = base.parse_rings(COVERAGE_RINGS)


def _stations():
    out = []
    for line in STATIONS_TXT.strip().splitlines():
        st, city, la, lo = line.split("|")
        out.append((st, city, float(la), float(lo)))
    return tuple(out)


STATIONS = _stations()


def display_name(state, city):
    name = DISPLAY.get(city) or " ".join(w.capitalize() for w in city.split())
    return "%s, %s" % (name, state)


def csv_url(year, state, city):
    return CSV_URL.format(year=year, state=urllib.parse.quote(state),
                          city=urllib.parse.quote(city))


def _parse_time(text):
    """CSV time (UTC, ``MM/DD/YYYY HH:MM:SS``) to unix seconds, or None."""
    try:
        return calendar.timegm(time.strptime(text.strip(), "%m/%d/%Y %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def latest_dose(body):
    """(nSv/h, unix ts) of the newest usable row, or None. Raises on a foreign format."""
    text = body.decode("utf-8-sig", "replace")
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as e:
        raise base.RadiationError("%s: bad CSV (%s)" % (NAME, e))
    if not rows:
        return None
    head = [h.strip().upper() for h in rows[0]]
    try:
        t_col = head.index("SAMPLE COLLECTION TIME")
    except ValueError:
        raise base.RadiationError("%s: unexpected CSV header" % NAME)
    d_col = next((i for i, h in enumerate(head)
                  if h.startswith("DOSE EQUIVALENT RATE") and "NSV/H" in h), None)
    s_col = head.index("STATUS") if "STATUS" in head else None
    if d_col is None:
        return None
    for row in reversed(rows[1:]):
        if len(row) <= max(t_col, d_col):
            continue
        if s_col is not None and len(row) > s_col:
            status = row[s_col].upper()
            if "REJECT" in status or "INVALID" in status:
                continue
        v = base.num(row[d_col])
        ts = _parse_time(row[t_col])
        if v is not None and v > 0 and ts is not None:
            return v, ts
    return None


def _station_dose(state, city, years, deadline):
    """((nSv/h, ts) or None, error text or None) for one station."""
    errors, downloaded = [], False
    for year in years:                           # early January: last year's file too
        try:
            got = latest_dose(base.http_get(csv_url(year, state, city), deadline))
        except base.RadiationError as e:
            errors.append(str(e))
            continue
        downloaded = True
        if got:
            return got, None
    return None, None if downloaded else "; ".join(errors)


def fetch(lat, lon, radius_km, timeout):
    deadline = base.as_deadline(timeout)
    near = sorted((base.haversine_km(lat, lon, la, lo), st, city, la, lo)
                  for st, city, la, lo in STATIONS)
    near = [n for n in near if n[0] <= radius_km][:MAX_DOWNLOADS]
    now = time.gmtime()
    years = [now.tm_year] + ([now.tm_year - 1] if now.tm_yday <= 4 else [])
    out, errors = [], []
    # The server needs a few seconds per file: fetch the nearest WANT files in parallel,
    # then the next ones only if some of them had no dose-rate detector.
    with concurrent.futures.ThreadPoolExecutor(max_workers=WANT) as pool:
        for batch in (near[:WANT], near[WANT:]):
            if not batch or len(out) >= WANT:
                break
            jobs = [(n, pool.submit(_station_dose, n[1], n[2], years, deadline)) for n in batch]
            for (km, st, city, la, lo), job in jobs:
                got, err = job.result()
                if err and not got:
                    errors.append("%s: %s" % (display_name(st, city), err))
                if got and len(out) < WANT:
                    out.append(base.station(display_name(st, city), la, lo, got[0] / 1000.0,
                                            got[1], KIND, km))
    if not out and near and len(errors) == len(near):
        raise base.RadiationError("; ".join(errors[:3]))
    return out
