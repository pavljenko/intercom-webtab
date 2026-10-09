"""Weather, air and radiation panel for the station page.

Modules:
    sources  -- Open-Meteo forecast and air-quality fetchers (blocking, run them in an executor)
    brain    -- build_panel(raw, src, mem, now, cfg): raw data -> GET /api/panel JSON (pure logic)
    phrases  -- the English phrase bank and word lists used by the brain
    units    -- unit conversion and number formatting (degC/F, m/s|km/h|mph, hPa|mmHg|inHg)
    photo    -- PhotoStore: weather photos processed in memory for the page background
    utci     -- Universal Thermal Climate Index ("feels like") polynomial

``read_settings(cfg)`` turns whatever configuration object the application passes into a small
``Settings`` value, so the weather code does not depend on the exact shape of the config class.
"""
import re

from . import units

__all__ = ["Settings", "read_settings", "ALL_WIDGETS", "DEFAULT_WIDGETS", "DEFAULT_LOCATION"]

ALL_WIDGETS = ("wind", "air", "uv", "pressure", "radiation", "sun", "humidity")
DEFAULT_WIDGETS = ("wind", "air", "uv", "pressure", "radiation", "sun")
WIDGET_SLOTS = 6
DEFAULT_LOCATION = (51.4779, -0.0015, "Europe/London")     # Greenwich, the documented example

_WIDGET_ALIASES = {"pres": "pressure", "press": "pressure", "rad": "radiation",
                   "hum": "humidity", "rh": "humidity"}
_TRUE = ("1", "on", "yes", "true", "enabled")
_FALSE = ("0", "off", "no", "false", "disabled", "none")


class Settings(object):
    """Everything the weather code needs from the configuration."""

    __slots__ = ("lat", "lon", "tz_name", "elevation", "temp", "wind", "press", "widgets",
                 "attribution", "photos", "rad_provider", "rad_radius_km", "panels")

    def __init__(self, lat=None, lon=None, tz_name=None, elevation=None, temp="C", wind="m/s",
                 press="hPa", widgets=DEFAULT_WIDGETS, attribution=True, photos=True,
                 rad_provider="auto", rad_radius_km=100.0, panels=()):
        self.lat = DEFAULT_LOCATION[0] if lat is None else float(lat)
        self.lon = DEFAULT_LOCATION[1] if lon is None else float(lon)
        self.tz_name = tz_name or DEFAULT_LOCATION[2]
        self.elevation = elevation
        self.temp = units.temp_unit(temp)
        self.wind = units.wind_unit(wind)
        self.press = units.press_unit(press)
        self.widgets = normalize_widgets(widgets)
        self.attribution = bool(attribution)
        self.photos = bool(photos)
        self.rad_provider = rad_provider or "auto"
        self.rad_radius_km = rad_radius_km
        self.panels = tuple(panels)          # ((id, label), ...)

    def tzinfo(self):
        """tzinfo for tz_name, or None when the zone database does not know it."""
        return load_tz(self.tz_name)

    def __repr__(self):
        return "Settings(%s)" % ", ".join("%s=%r" % (k, getattr(self, k)) for k in self.__slots__)


def load_tz(name):
    try:
        from zoneinfo import ZoneInfo        # Python 3.9+
        return ZoneInfo(str(name))
    except Exception:
        return None


def normalize_widgets(value):
    """Exactly WIDGET_SLOTS widget keys: known keys from ``value`` in order, without
    duplicates, padded from the default order (then humidity)."""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    out = []
    for it in items:
        k = str(it).strip().lower()
        k = _WIDGET_ALIASES.get(k, k)
        if k in ALL_WIDGETS and k not in out:
            out.append(k)
    for k in DEFAULT_WIDGETS + ("humidity",):
        if len(out) >= WIDGET_SLOTS:
            break
        if k not in out:
            out.append(k)
    return tuple(out[:WIDGET_SLOTS])


def _bool(v, default):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return default


def _float(v, default=None):
    f = units.num(v.strip() if isinstance(v, str) else v)
    return default if f is None else f


def _sub(obj, name):
    """Section ``name`` of a config object, mapping or ConfigParser, or None."""
    if obj is None:
        return None
    if hasattr(obj, "has_section") and hasattr(obj, "items"):          # ConfigParser
        try:
            return dict(obj.items(name)) if obj.has_section(name) else None
        except Exception:
            return None
    if isinstance(obj, dict):
        v = obj.get(name)
    else:
        v = getattr(obj, name, None)
    return v if v is not None and not isinstance(v, (str, int, float, bool)) else None


def _val(obj, names):
    if obj is None:
        return None
    for n in names:
        if isinstance(obj, dict):
            if obj.get(n) is not None:
                return obj[n]
        else:
            v = getattr(obj, n, None)
            if v is not None and not callable(v):
                return v
    return None


def _get(cfg, section, names):
    """Look a value up as cfg.<section>.<name>, cfg[section][name], cfg.<name> or
    cfg.<section>_<name>, trying each alias in ``names``."""
    v = _val(_sub(cfg, section), names)
    if v is None and not hasattr(cfg, "has_section"):
        v = _val(cfg, list(names) + ["%s_%s" % (section, n) for n in names])
    if isinstance(v, str):                   # INI inline comment: "C   ; C | F"
        v = re.split(r"\s[;#]", " " + v)[0].strip()
    return v


def _panels(cfg):
    out = []

    def add(pid, obj):
        pid = str(pid).strip()
        if not pid:
            return
        label = _val(obj, ("label", "name", "title")) if obj is not None else None
        label = str(label).strip() if label else pid[:1].upper() + pid[1:]
        if pid not in [p for p, _ in out]:
            out.append((pid, label))

    if hasattr(cfg, "has_section") and hasattr(cfg, "sections"):
        for s in cfg.sections():
            if s.lower().startswith("panel:"):
                add(s.split(":", 1)[1], dict(cfg.items(s)))
        return out
    raw = cfg.get("panels") if isinstance(cfg, dict) else getattr(cfg, "panels", None)
    if isinstance(raw, dict):
        for pid, obj in raw.items():
            add(pid, obj)
    elif isinstance(raw, (list, tuple)):
        for obj in raw:
            if isinstance(obj, (list, tuple)) and len(obj) >= 2:
                add(obj[0], {"label": obj[1]})
            elif isinstance(obj, str):
                add(obj, None)
            else:
                pid = _val(obj, ("id", "key", "name"))
                if pid is not None:
                    add(pid, obj)
    elif isinstance(cfg, dict):
        for k, v in cfg.items():
            if isinstance(k, str) and k.lower().startswith("panel:"):
                add(k.split(":", 1)[1], v)
    return out


def read_settings(cfg=None):
    """Settings from a config object of any reasonable shape (attribute sections such as
    ``cfg.location.latitude``, nested dicts, a ConfigParser with the INI layout, flat
    attributes like ``cfg.latitude``) or None for the documented defaults. Never raises."""
    if isinstance(cfg, Settings):
        return cfg
    try:
        lat = _float(_get(cfg, "location", ("latitude", "lat")))
        lon = _float(_get(cfg, "location", ("longitude", "lon", "lng")))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            lat = lon = None
        tz = _get(cfg, "location", ("timezone", "tz", "time_zone"))
        if tz is not None and not isinstance(tz, str):
            tz = getattr(tz, "key", None) or str(tz)
        elev = _float(_get(cfg, "location", ("elevation", "altitude")))
        return Settings(
            lat=lat, lon=lon, tz_name=(tz or "").strip() or None, elevation=elev,
            temp=_get(cfg, "units", ("temperature", "temp", "temperature_unit")) or "C",
            wind=_get(cfg, "units", ("wind", "wind_unit", "wind_speed")) or "m/s",
            press=_get(cfg, "units", ("pressure", "pressure_unit", "press")) or "hPa",
            widgets=_get(cfg, "ui", ("widgets",)) or DEFAULT_WIDGETS,
            attribution=_bool(_get(cfg, "weather", ("attribution",)), True),
            photos=_bool(_get(cfg, "weather", ("photos",)), True),
            rad_provider=str(_get(cfg, "radiation", ("provider",)) or "auto").strip().lower(),
            rad_radius_km=_float(_get(cfg, "radiation", ("radius_km", "radius")), 100.0),
            panels=_panels(cfg))
    except Exception:
        return Settings()
