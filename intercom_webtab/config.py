"""Configuration: ``webtab.ini`` plus a separate ``secrets.ini`` and a token file.

``load(path)`` reads and validates everything and returns a ``Config``. Every problem is
reported as a ``ConfigError`` with an English message that names the section and key.
Values are reachable both as attributes and as mapping items::

    cfg.location.latitude, cfg["location"]["latitude"], cfg.latitude
    cfg.units.temperature, cfg.ui.widgets, cfg.panels[0].number, cfg.panel("gate")

Nothing is read at import time.
"""
import configparser
import os
import re
from urllib.parse import quote, urlsplit, urlunsplit

DEFAULT_CONFIG_PATH = "/etc/intercom-webtab/webtab.ini"

WIDGET_KEYS = ("wind", "air", "uv", "pressure", "radiation", "sun", "humidity")
DEFAULT_WIDGETS = ("wind", "air", "uv", "pressure", "radiation", "sun")
WIDGET_SLOTS = 6
RINGTONES = ("low", "mid", "high", "sweep")
TEMPERATURE_UNITS = ("C", "F")
WIND_UNITS = ("m/s", "km/h", "mph")
PRESSURE_UNITS = ("hPa", "mmHg", "inHg")
LOG_LEVELS = ("debug", "info", "warning", "error")
MAX_PANELS = 2

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_NUMBER_RE = re.compile(r"^\+?[A-Za-z0-9._*#-]{1,40}$")
_DTMF_RE = re.compile(r"^[0-9*#A-D]{1,8}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_TRUE = ("1", "yes", "true", "on")
_FALSE = ("0", "no", "false", "off")

# Known keys per section: anything else is reported as a warning (typo protection).
_KNOWN = {
    "station": ("title", "log_level"),
    "http": ("bind", "port"),
    "paths": ("token_file", "secrets_file", "state_dir", "static_dir", "runtime_dir"),
    "location": ("latitude", "longitude", "timezone"),
    "units": ("temperature", "wind", "pressure"),
    "weather": ("enabled", "photos", "attribution"),
    "radiation": ("provider", "radius_km"),
    "ui": ("ringtone", "widgets"),
    "sip": ("uas_host", "uas_port", "rtp_audio_port", "rtp_video_port", "audio_out_port"),
    "asterisk": ("ami_host", "ami_port", "trunk", "own_callerid", "incoming_contexts",
                 "view_context", "view_exten", "demo_context"),
}
_PANEL_KEYS = ("label", "icon", "number", "endpoint", "dtmf", "open_settle", "open_repeat", "hold")
_SECRET_KNOWN = {
    "asterisk": ("ami_user", "ami_secret"),
    "provider": ("host", "port", "username", "auth_username", "password"),
}
_SECRET_PANEL_KEYS = ("rtsp_url", "rtsp_user", "rtsp_password", "sip_password")


class ConfigError(ValueError):
    """The configuration is missing or invalid; the message is meant for the operator."""


class Section(object):
    """A read-only bag of values with attribute and mapping access."""

    def __init__(self, name, **values):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, key):
        try:
            return self._values[key]
        except KeyError:
            raise AttributeError("[%s] has no key %r" % (self._name, key))

    def __setattr__(self, key, value):
        raise AttributeError("configuration is read-only")

    def __getitem__(self, key):
        return self._values[key]

    def __contains__(self, key):
        return key in self._values

    def __iter__(self):
        return iter(self._values)

    def get(self, key, default=None):
        return self._values.get(key, default)

    def keys(self):
        return self._values.keys()

    def items(self):
        return self._values.items()

    def as_dict(self):
        return dict(self._values)

    def __repr__(self):
        return "Section(%s)" % self._name


class Panel(Section):
    """One door panel: ``id``, ``label``, ``icon``, ``number``, ``endpoint``, ``dtmf``,
    ``open_settle``, ``open_repeat``, ``hold``, ``rtsp_url`` (credentials included, never log
    it) and ``sip_password`` (only used to render the Asterisk configuration)."""

    def public(self):
        """What the page may know about the panel."""
        return {"id": self.id, "label": self.label, "icon": self.icon}

    def __repr__(self):
        return "Panel(%s)" % self.id


class Config(object):
    """Validated configuration. Sections are attributes; see the module docstring."""

    def __init__(self, path, sections, panels, token, warnings):
        self.path = path
        self._sections = sections
        self.panels = panels
        self.token = token
        self.warnings = warnings
        for name, sec in sections.items():
            setattr(self, name, sec)

    # mapping access to sections: cfg["location"]["latitude"]
    def __getitem__(self, name):
        return self._sections[name]

    def __contains__(self, name):
        return name in self._sections

    def get(self, name, default=None):
        return self._sections.get(name, default)

    # convenient flat aliases used by the weather and radiation code
    @property
    def title(self):
        return self.station.title

    @property
    def latitude(self):
        return self.location.latitude

    @property
    def longitude(self):
        return self.location.longitude

    @property
    def timezone(self):
        return self.location.timezone

    def tzinfo(self):
        """``zoneinfo.ZoneInfo`` of [location] timezone (UTC if the zone database is missing)."""
        return _zone(self.location.timezone, strict=False)

    @property
    def widgets(self):
        return list(self.ui.widgets)

    @property
    def panel_ids(self):
        return [p.id for p in self.panels]

    def panel(self, pid):
        for p in self.panels:
            if p.id == pid:
                return p
        return None

    def page_config(self):
        """The JSON object injected into the page as ``__IWT_CONFIG__``."""
        return {
            "title": self.station.title,
            "panels": [p.public() for p in self.panels],
            "ringtone": self.ui.ringtone,
            "attribution": bool(self.weather.attribution),
            "widgets": list(self.ui.widgets),
        }

    def __repr__(self):
        return "Config(%s)" % self.path


# ---------------------------------------------------------------------------- helpers

def _zone(name, strict=True):
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:                        # pragma: no cover - Python < 3.9
        if strict:
            return None
        from datetime import timezone
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if strict:
            raise ConfigError("[location] timezone: unknown time zone %r "
                              "(use an IANA name such as Europe/London)" % name)
        from datetime import timezone
        return timezone.utc


class _Reader(object):
    """Typed getters that produce uniform error messages."""

    def __init__(self, parser, section):
        self.p = parser
        self.s = section

    def has(self, key):
        return self.p.has_option(self.s, key)

    def raw(self, key, default=None):
        if self.p.has_option(self.s, key):
            return self.p.get(self.s, key).strip()
        if default is None:
            raise ConfigError("[%s] %s: missing value" % (self.s, key))
        return default

    def err(self, key, msg):
        return ConfigError("[%s] %s: %s" % (self.s, key, msg))

    def text(self, key, default=None, allow_empty=False):
        v = self.raw(key, default)
        if not v and not allow_empty:
            raise self.err(key, "must not be empty")
        return v

    def integer(self, key, default, lo, hi):
        v = self.raw(key, str(default))
        try:
            n = int(v)
        except ValueError:
            raise self.err(key, "expected a whole number, got %r" % v)
        if not lo <= n <= hi:
            raise self.err(key, "must be between %d and %d, got %d" % (lo, hi, n))
        return n

    def number(self, key, default, lo, hi):
        v = self.raw(key, str(default))
        try:
            n = float(v)
        except ValueError:
            raise self.err(key, "expected a number, got %r" % v)
        if n != n or not lo <= n <= hi:
            raise self.err(key, "must be between %g and %g, got %s" % (lo, hi, v))
        return n

    def flag(self, key, default):
        v = self.raw(key, "on" if default else "off").lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
        raise self.err(key, "expected on or off, got %r" % v)

    def choice(self, key, default, choices, fold=False):
        v = self.raw(key, default)
        for c in choices:
            if v == c or (fold and v.lower() == c.lower()):
                return c
        raise self.err(key, "must be one of %s, got %r" % (", ".join(choices), v))

    def items(self, key, default):
        v = self.raw(key, default)
        return [x.strip() for x in v.split(",") if x.strip()]


def _parser(inline_comments):
    return configparser.ConfigParser(
        interpolation=None, strict=True,
        inline_comment_prefixes=(";",) if inline_comments else None)


def _resolve(base, path):
    if not path:
        return path
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))


def build_rtsp_url(url, user="", password=""):
    """Insert URL-encoded credentials into an RTSP URL (kept separate in secrets.ini)."""
    if not url or not (user or password):
        return url
    parts = urlsplit(url)
    if "@" in parts.netloc:
        return url                               # credentials already inside the URL
    cred = quote(user, safe="")
    if password:
        cred += ":" + quote(password, safe="")
    return urlunsplit((parts.scheme, cred + "@" + parts.netloc, parts.path, parts.query,
                       parts.fragment))


def mask_url(text):
    """Hide ``user:password@`` inside any URL in ``text`` (for logs)."""
    return re.sub(r"//[^/@\s]+@", "//***@", text or "")


def same_number(a, b):
    """Compare SIP numbers / user parts, ignoring a leading ``+``."""
    a = (a or "").strip().lstrip("+")
    b = (b or "").strip().lstrip("+")
    return bool(a) and a == b


# ---------------------------------------------------------------------------- loading

def load(path=DEFAULT_CONFIG_PATH, require_token=True):
    """Read ``path`` (and the secrets and token files it names) and validate them."""
    path = os.path.abspath(os.path.expanduser(path))
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise ConfigError("config file not found: %s" % path)
    except OSError as e:
        raise ConfigError("cannot read config file %s: %s" % (path, e.strerror or e))
    return parse(text, path=path, require_token=require_token)


def parse(text, path="<string>", secrets_text=None, token=None, require_token=True):
    """Validate configuration text. ``secrets_text`` / ``token`` override the files named
    in [paths] (useful for tests)."""
    base = os.path.dirname(path) if os.path.isabs(path) else os.getcwd()
    p = _parser(inline_comments=True)
    try:
        p.read_string(text, source=path)
    except configparser.Error as e:
        raise ConfigError("cannot parse %s: %s" % (path, str(e).splitlines()[0]))
    warnings = []
    for sec in p.sections():
        known = _KNOWN.get(sec)
        if sec.startswith("panel:"):
            known = _PANEL_KEYS
        if known is None:
            warnings.append("unknown section [%s] ignored" % sec)
            continue
        for key in p.options(sec):
            if key not in known:
                warnings.append("[%s] unknown key %r ignored" % (sec, key))

    sections = {}

    r = _Reader(p, "station")
    sections["station"] = Section(
        "station",
        title=r.text("title", "Intercom WebTab")[:60],
        log_level=r.choice("log_level", "info", LOG_LEVELS, fold=True))

    r = _Reader(p, "http")
    sections["http"] = Section("http", bind=r.text("bind", "0.0.0.0"),
                               port=r.integer("port", 8080, 1, 65535))

    r = _Reader(p, "paths")
    paths = Section(
        "paths",
        token_file=_resolve(base, r.text("token_file", "/etc/intercom-webtab/token")),
        secrets_file=_resolve(base, r.text("secrets_file", "/etc/intercom-webtab/secrets.ini")),
        state_dir=_resolve(base, r.text("state_dir", "/var/lib/intercom-webtab")),
        static_dir=_resolve(base, r.raw("static_dir", "")) or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static"),
        runtime_dir=_resolve(base, r.raw("runtime_dir", "")))
    sections["paths"] = paths

    r = _Reader(p, "location")
    tz = r.text("timezone", "Europe/London")
    _zone(tz, strict=True)
    sections["location"] = Section(
        "location",
        latitude=r.number("latitude", 51.4779, -90.0, 90.0),
        longitude=r.number("longitude", -0.0015, -180.0, 180.0),
        timezone=tz)

    r = _Reader(p, "units")
    sections["units"] = Section(
        "units",
        temperature=r.choice("temperature", "C", TEMPERATURE_UNITS, fold=True),
        wind=r.choice("wind", "m/s", WIND_UNITS, fold=True),
        pressure=r.choice("pressure", "hPa", PRESSURE_UNITS, fold=True))

    r = _Reader(p, "weather")
    sections["weather"] = Section(
        "weather", enabled=r.flag("enabled", True), photos=r.flag("photos", True),
        attribution=r.flag("attribution", True))

    r = _Reader(p, "radiation")
    provider = r.text("provider", "auto").lower()
    if provider not in ("auto", "none") and not _ID_RE.match(provider):
        raise r.err("provider", "expected auto, none or a provider id, got %r" % provider)
    if provider not in ("auto", "none"):
        try:
            from . import radiation
            known = list(radiation.PROVIDERS)
        except Exception:           # the radiation package is optional at config time
            known = None
        if known is not None and provider not in known:
            raise r.err("provider", "unknown radiation provider %r (known: auto, none, %s)"
                        % (provider, ", ".join(known)))
    sections["radiation"] = Section("radiation", provider=provider,
                                    radius_km=r.number("radius_km", 100, 1, 2000))

    r = _Reader(p, "ui")
    widgets = [w.lower() for w in r.items("widgets", ", ".join(DEFAULT_WIDGETS))]
    for w in widgets:
        if w not in WIDGET_KEYS:
            raise r.err("widgets", "unknown widget %r (allowed: %s)" % (w, ", ".join(WIDGET_KEYS)))
    if len(set(widgets)) != len(widgets):
        raise r.err("widgets", "each widget may appear only once")
    if len(widgets) != WIDGET_SLOTS:
        raise r.err("widgets", "exactly %d widgets are needed, got %d" % (WIDGET_SLOTS, len(widgets)))
    sections["ui"] = Section("ui", ringtone=r.choice("ringtone", "mid", RINGTONES, fold=True),
                             widgets=tuple(widgets))

    r = _Reader(p, "sip")
    sip = Section(
        "sip",
        uas_host=r.text("uas_host", "127.0.0.1"),
        uas_port=r.integer("uas_port", 5080, 1, 65535),
        rtp_audio_port=r.integer("rtp_audio_port", 41000, 1024, 65534),
        rtp_video_port=r.integer("rtp_video_port", 41002, 1024, 65534),
        audio_out_port=r.integer("audio_out_port", 41100, 1024, 65535))
    for key in ("rtp_audio_port", "rtp_video_port"):
        if sip[key] % 2:
            raise r.err(key, "RTP ports must be even (the next odd port carries RTCP)")
    used = {}
    for key, ports in (("rtp_audio_port", (sip.rtp_audio_port, sip.rtp_audio_port + 1)),
                       ("rtp_video_port", (sip.rtp_video_port, sip.rtp_video_port + 1)),
                       ("audio_out_port", (sip.audio_out_port,)),
                       ("uas_port", (sip.uas_port,))):
        for port in ports:
            if port in used:
                raise r.err(key, "port %d is already used by %s" % (port, used[port]))
            used[port] = key
    if sip.uas_port == sections["http"].port:
        raise r.err("uas_port", "must differ from [http] port")
    sections["sip"] = sip

    r = _Reader(p, "asterisk")
    ast = dict(
        ami_host=r.text("ami_host", "127.0.0.1"),
        ami_port=r.integer("ami_port", 5038, 1, 65535),
        trunk=r.raw("trunk", "provider"),
        own_callerid=r.text("own_callerid", "100"),
        incoming_contexts=tuple(r.items("incoming_contexts", "intercom-incoming")),
        view_context=r.text("view_context", "intercom-view"),
        view_exten=r.text("view_exten", "webtab"),
        demo_context=r.text("demo_context", "intercom-demo"))
    if ast["trunk"] and not _NAME_RE.match(ast["trunk"]):
        raise r.err("trunk", "expected a PJSIP endpoint name, got %r" % ast["trunk"])
    if not _NUMBER_RE.match(ast["own_callerid"]):
        raise r.err("own_callerid", "expected a SIP number such as 100, got %r" % ast["own_callerid"])
    if not ast["incoming_contexts"]:
        raise r.err("incoming_contexts", "name at least one dialplan context")
    for key in ("view_context", "view_exten", "demo_context"):
        if not _NAME_RE.match(ast[key]):
            raise r.err(key, "expected a dialplan name, got %r" % ast[key])
    for ctx in ast["incoming_contexts"]:
        if not _NAME_RE.match(ctx):
            raise r.err("incoming_contexts", "expected dialplan names, got %r" % ctx)
        if ctx in (ast["view_context"], ast["demo_context"]):
            raise r.err("incoming_contexts", "%r is also the view or demo context" % ctx)
    if ast["view_context"] == ast["demo_context"]:
        raise r.err("demo_context", "must differ from view_context")

    panels = []
    for sec in p.sections():
        if not sec.startswith("panel:"):
            continue
        pid = sec.split(":", 1)[1].strip()
        if not _ID_RE.match(pid):
            raise ConfigError("[%s]: panel id must be lowercase letters, digits, '-' or '_' "
                              "(up to 32 characters)" % sec)
        r = _Reader(p, sec)
        number = r.text("number")
        if not _NUMBER_RE.match(number):
            raise r.err("number", "expected a SIP number such as 201, got %r" % number)
        endpoint = r.raw("endpoint", "")
        if endpoint and not _NAME_RE.match(endpoint):
            raise r.err("endpoint", "expected a PJSIP endpoint name, got %r" % endpoint)
        dtmf = r.text("dtmf", "1").upper()
        if not _DTMF_RE.match(dtmf):
            raise r.err("dtmf", "expected 1 to 8 DTMF digits (0-9, *, #, A-D), got %r" % dtmf)
        repeat = []
        for x in r.items("open_repeat", "0.7, 1.4, 2.1, 3.0"):
            try:
                repeat.append(float(x))
            except ValueError:
                raise r.err("open_repeat", "expected numbers separated by commas, got %r" % x)
        if len(repeat) > 10 or any(not 0 <= x <= 30 for x in repeat) or repeat != sorted(repeat):
            raise r.err("open_repeat", "expected up to 10 ascending delays between 0 and 30 s")
        icon = r.text("icon", "door").lower()
        if not re.match(r"^[a-z0-9_-]{1,32}$", icon):
            raise r.err("icon", "expected an icon name such as gate or house, got %r" % icon)
        panels.append(dict(
            id=pid, label=r.text("label", pid.capitalize())[:40], icon=icon, number=number,
            endpoint=endpoint, dtmf=dtmf,
            open_settle=r.number("open_settle", 2.0, 0.0, 10.0),
            open_repeat=tuple(repeat),
            hold=r.integer("hold", 12, 3, 120)))
    if not panels:
        raise ConfigError("no door panels configured: add a [panel:<id>] section "
                          "(for example [panel:gate])")
    if len(panels) > MAX_PANELS:
        raise ConfigError("at most %d panels are supported, found %d" % (MAX_PANELS, len(panels)))
    seen = {}
    for pn in panels:
        key = pn["number"].lstrip("+")
        if key in seen:
            raise ConfigError("[panel:%s] number: %s is already used by [panel:%s]"
                              % (pn["id"], pn["number"], seen[key]))
        seen[key] = pn["id"]
        if same_number(pn["number"], ast["own_callerid"]):
            raise ConfigError("[panel:%s] number: must differ from [asterisk] own_callerid "
                              "(a panel call would look like our own camera-view call)" % pn["id"])
        if not pn["endpoint"] and not ast["trunk"]:
            raise ConfigError("[panel:%s]: set either 'endpoint' (door station registered to "
                              "Asterisk) or [asterisk] trunk (SIP provider)" % pn["id"])

    # ---- secrets
    secrets = _parser(inline_comments=False)
    if secrets_text is None:
        sf = paths.secrets_file
        try:
            with open(sf, encoding="utf-8") as f:
                secrets_text = f.read()
        except FileNotFoundError:
            raise ConfigError("secrets file not found: %s ([paths] secrets_file); copy "
                              "config/secrets.example.ini there" % sf)
        except OSError as e:
            raise ConfigError("cannot read secrets file %s: %s" % (sf, e.strerror or e))
    try:
        secrets.read_string(secrets_text, source=paths.secrets_file)
    except configparser.Error as e:
        raise ConfigError("cannot parse secrets file: %s" % str(e).splitlines()[0])
    for sec in secrets.sections():
        known = _SECRET_PANEL_KEYS if sec.startswith("panel:") else _SECRET_KNOWN.get(sec)
        if known is None:
            warnings.append("secrets: unknown section [%s] ignored" % sec)
            continue
        if sec.startswith("panel:") and sec.split(":", 1)[1].strip() not in [x["id"] for x in panels]:
            warnings.append("secrets: [%s] has no matching panel in the config" % sec)
        for key in secrets.options(sec):
            if key not in known:
                warnings.append("secrets: [%s] unknown key %r ignored" % (sec, key))
    sr = _Reader(secrets, "asterisk")
    if not secrets.has_section("asterisk"):
        raise ConfigError("secrets file: section [asterisk] with ami_user and ami_secret is missing")
    ast["ami_user"] = sr.text("ami_user")
    ast["ami_secret"] = sr.text("ami_secret")
    if "\n" in ast["ami_secret"] or "\r" in ast["ami_secret"]:
        raise sr.err("ami_secret", "must be a single line")
    sections["asterisk"] = Section("asterisk", **ast)

    prov = {"host": "", "port": 5060, "username": "", "auth_username": "", "password": ""}
    if secrets.has_section("provider"):
        pr = _Reader(secrets, "provider")
        prov.update(host=pr.raw("host", ""), port=pr.integer("port", 5060, 1, 65535),
                    username=pr.raw("username", ""), password=pr.raw("password", ""))
        prov["auth_username"] = pr.raw("auth_username", "") or prov["username"]
    sections["provider"] = Section("provider", **prov)

    out = []
    for pn in panels:
        sec = "panel:" + pn["id"]
        url = user = pwd = sip_pwd = ""
        if secrets.has_section(sec):
            pr = _Reader(secrets, sec)
            url, user = pr.raw("rtsp_url", ""), pr.raw("rtsp_user", "")
            pwd, sip_pwd = pr.raw("rtsp_password", ""), pr.raw("sip_password", "")
            if url and not re.match(r"^rtsps?://", url, re.I):
                raise pr.err("rtsp_url", "expected an rtsp:// URL")
        pn["rtsp_url"] = build_rtsp_url(url, user, pwd)
        pn["sip_password"] = sip_pwd
        out.append(Panel(sec, **pn))

    if token is None:
        token = ""
        if require_token:
            tf = paths.token_file
            try:
                with open(tf, encoding="utf-8") as f:
                    token = f.read().strip()
            except FileNotFoundError:
                raise ConfigError(
                    "token file not found: %s ([paths] token_file); create one with: "
                    "python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > %s" % (tf, tf))
            except OSError as e:
                raise ConfigError("cannot read token file %s: %s" % (tf, e.strerror or e))
    if require_token:
        if len(token) < 16 or not re.match(r"^[A-Za-z0-9._~-]+$", token):
            raise ConfigError("access token in %s must be at least 16 characters of A-Z, a-z, "
                              "0-9, '.', '_', '~' or '-' (it travels in URLs)" % paths.token_file)
    return Config(path, sections, out, token, warnings)
