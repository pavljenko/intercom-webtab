"""Weather source fetchers: Open-Meteo forecast and Open-Meteo air quality (CAMS).

Both functions are blocking: call them from an executor, never from the event loop. They
return the raw JSON objects; any failure raises an exception with a readable message
(HTTPError / URLError / socket.timeout / ValueError). Parsing happens in ``brain``.

Coordinates and the time zone come from the configuration ([location]). Wind is always
requested in m/s and times as unix seconds (``timeformat=unixtime``); the time zone only
decides where the daily values (min/max, sunrise, sunset) start and end. Unit conversion
for the screen is done later by the brain according to [units].

Field notes:
* ``current`` precipitation, rain, showers and snowfall are sums over the current interval
  (15 minutes in most places, ``current.interval`` seconds); the brain scales them to
  per-hour rates.
* With the default model (best_match) the rain/showers/snowfall fields can disagree with
  ``precipitation`` (phantom 0.1-0.4 mm of rain at 0 % probability and a dry weather code).
  The brain therefore takes the amount from ``precipitation`` and uses the fields only to
  split it into liquid and frozen parts.

Self-test (network):  python3 -m intercom_webtab.weather.sources [--lat 51.4779
                      --lon -0.0015 --tz Europe/London] [--save DIR]
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import read_settings

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
USER_AGENT = "IntercomWebTab/0.1.0"

FORECAST_CURRENT = (
    "temperature_2m", "relative_humidity_2m", "precipitation", "rain", "showers", "snowfall",
    "snow_depth", "weather_code", "is_day", "cloud_cover", "surface_pressure", "wind_speed_10m",
    "wind_direction_10m", "wind_gusts_10m", "shortwave_radiation", "uv_index", "visibility")
FORECAST_HOURLY = (
    "precipitation_probability", "precipitation", "rain", "showers", "snowfall", "weather_code",
    "cloud_cover", "visibility", "relative_humidity_2m", "surface_pressure", "wind_speed_10m",
    "wind_gusts_10m", "uv_index", "temperature_2m")
FORECAST_DAILY = ("temperature_2m_max", "temperature_2m_min", "uv_index_max", "sunrise", "sunset")
AIR_CURRENT = (
    "european_aqi", "european_aqi_pm2_5", "european_aqi_pm10", "european_aqi_nitrogen_dioxide",
    "european_aqi_ozone", "pm2_5", "pm10", "ozone", "nitrogen_dioxide", "dust",
    "aerosol_optical_depth")


def _coord(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def forecast_url(lat, lon, tz):
    q = [("latitude", _coord(lat)), ("longitude", _coord(lon)),
         ("current", ",".join(FORECAST_CURRENT)), ("hourly", ",".join(FORECAST_HOURLY)),
         ("past_hours", "24"), ("forecast_hours", "25"),
         ("daily", ",".join(FORECAST_DAILY)), ("forecast_days", "2"),
         ("wind_speed_unit", "ms"), ("timezone", tz), ("timeformat", "unixtime")]
    return FORECAST_API + "?" + urllib.parse.urlencode(q, safe=",")


def air_url(lat, lon, tz):
    q = [("latitude", _coord(lat)), ("longitude", _coord(lon)),
         ("current", ",".join(AIR_CURRENT)), ("domains", "auto"),
         ("timezone", tz), ("timeformat", "unixtime")]
    return AIR_API + "?" + urllib.parse.urlencode(q, safe=",")


def _read(url, timeout, what, opener=None):
    """Whole response body; errors are re-raised with the source name in the message."""
    op = opener or urllib.request.build_opener()
    rq = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with op.open(rq, timeout=timeout) as r:
            return r.read()          # read to the end: a half-read response blocks the pool
    except urllib.error.HTTPError as e:
        reason = ""
        try:
            body = e.read(600).decode("utf-8", "replace")
            reason = json.loads(body).get("reason", "") if body.lstrip().startswith("{") else ""
        except Exception:
            pass
        raise urllib.error.HTTPError(e.url, e.code, "%s: %s" % (what, str(reason or e.reason)[:160]),
                                     e.hdrs, None)
    except urllib.error.URLError as e:
        raise urllib.error.URLError("%s: no connection (%s)" % (what, e.reason))
    except socket.timeout:
        raise socket.timeout("%s: no response within %s s" % (what, timeout))


def _json(raw, what):
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise ValueError("%s: response is not JSON (%s)" % (what, e))
    if not isinstance(d, dict):
        raise ValueError("%s: response is not a JSON object" % what)
    if d.get("error"):
        raise ValueError("%s: %s" % (what, d.get("reason") or "error without a reason"))
    return d


def fetch_forecast(cfg=None, timeout=15, opener=None):
    """Open-Meteo forecast for the configured location (current + 24 h back + 24 h ahead +
    2 days of daily values)."""
    s = read_settings(cfg)
    what = "Open-Meteo forecast"
    d = _json(_read(forecast_url(s.lat, s.lon, s.tz_name), timeout, what, opener), what)
    if not isinstance(d.get("current"), dict) or not isinstance(d.get("hourly"), dict):
        raise ValueError("%s: no current/hourly block in the response" % what)
    return d


def fetch_air(cfg=None, timeout=15, opener=None):
    """Open-Meteo air quality (CAMS European/global domains) for the configured location."""
    s = read_settings(cfg)
    what = "Open-Meteo air quality"
    d = _json(_read(air_url(s.lat, s.lon, s.tz_name), timeout, what, opener), what)
    if not isinstance(d.get("current"), dict):
        raise ValueError("%s: no current block in the response" % what)
    return d


def _selftest(argv):
    from . import Settings
    a = list(argv)

    def opt(name, default):
        return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default
    s = Settings(lat=float(opt("--lat", 51.4779)), lon=float(opt("--lon", -0.0015)),
                 tz_name=opt("--tz", "Europe/London"))
    save = opt("--save", None)
    ok = True
    for name, fn, brief in (
            ("forecast", fetch_forecast, lambda d: "current %s, %d hours, t %s, code %s, elevation %s" % (
                d["current"].get("time"), len(d["hourly"].get("time") or []),
                d["current"].get("temperature_2m"), d["current"].get("weather_code"), d.get("elevation"))),
            ("air", fetch_air, lambda d: "current %s, AQI %s, PM2.5 %s" % (
                d["current"].get("time"), d["current"].get("european_aqi"), d["current"].get("pm2_5")))):
        t0 = time.time()
        try:
            d = fn(s)
            print("%-8s ok   %.1f s  %s" % (name, time.time() - t0, brief(d)))
            if save:
                with open("%s/%s.json" % (save.rstrip("/"), name), "w", encoding="utf-8") as f:
                    json.dump(d, f, indent=1)
        except Exception as e:
            ok = False
            print("%-8s FAIL %.1f s  %s: %s" % (name, time.time() - t0, type(e).__name__, e))
    return ok


if __name__ == "__main__":
    sys.exit(0 if _selftest(sys.argv[1:]) else 1)
