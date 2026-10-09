"""The aiohttp application: page, video, events, control API and the background loops.

Routes (all but the icons need the token as ``?k=`` or an ``X-Token`` header):

    GET  /                  the station page (token and station config injected)
    GET  /stream            MJPEG of the camera on screen
    GET  /ws                WebSocket: call events, a pulse every 5 s, guest audio (PCM)
    POST /api/answer|open|view|hangup|demo|diag
    GET  /api/panel         the weather panel (JSON v3)
    GET  /api/ver           page version + reload flag (the page reloads itself)
    GET  /ph/<key>/<bg|panel>.jpg   weather photos (memory only)
    GET  /icons/<name>.png, /icon.svg, /icon.png    home-screen icons (no token)
    GET  /ring/<name>.wav   ringtones (no token)
"""
import asyncio
import concurrent.futures
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import time

from aiohttp import WSMsgType, web

from . import __version__
from .ami import Ami
from .calls import CallManager, Hub
from .media import Media
from .sip_uas import SipUas

log = logging.getLogger("iwt.web")

PERMISSIONS = ("camera=(), microphone=(), geolocation=(), display-capture=(), "
               "usb=(), midi=(), payment=(), screen-wake-lock=()")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,255}$")

FALLBACK_PAGE = (
    "<!doctype html><html><head><meta charset=utf-8><meta name=viewport "
    "content=\"width=device-width,initial-scale=1\"><title>Intercom WebTab</title>"
    "<style>body{margin:0;background:#000;color:#ddd;font:15px -apple-system,Helvetica,Arial}"
    "img{display:block;width:100%;background:#111}a{display:inline-block;margin:8px;"
    "padding:12px 18px;background:#ddd;color:#000;border-radius:8px;text-decoration:none}"
    "pre{padding:8px}</style></head><body><p style=padding:8px>Intercom WebTab: the page "
    "file is missing, this is a test page.</p><img src=\"/stream?k=__TOKEN__\">"
    "<a href=# onclick=\"api('answer');return false\">Answer</a>"
    "<a href=# onclick=\"api('open');return false\">Open</a>"
    "<a href=# onclick=\"api('hangup');return false\">Hang up</a><pre id=log></pre>"
    "<script>var K='__TOKEN__';function lg(t){var e=document.getElementById('log');"
    "e.textContent=t+'\\n'+e.textContent}function api(a){var x=new XMLHttpRequest();"
    "x.open('POST','/api/'+a+'?k='+K,true);x.setRequestHeader('Content-Type',"
    "'application/json');x.onload=function(){lg(a+': '+x.responseText)};x.send('{}')}"
    "var ws=new WebSocket((location.protocol=='https:'?'wss':'ws')+'://'+location.host+"
    "'/ws?k='+K);ws.onmessage=function(e){if(typeof e.data=='string')lg('ws: '+e.data)};"
    "</script></body></html>")


def json_for_script(obj):
    """JSON that is safe inside a <script> element."""
    return (json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def page_version(html):
    """VER of the page: the first line containing 'var VER =' (the page parses it the same
    way; a mismatch makes every station reload itself)."""
    for ln in (html or "").splitlines():
        if "var VER =" in ln:
            parts = ln.split('"')
            return parts[1] if len(parts) > 2 else ""
    return ""


def csp_for(host):
    """Content-Security-Policy of the page: it may only talk to itself."""
    host = host if host and _HOST_RE.match(host) else ""
    ws = " ws://%s wss://%s" % (host, host) if host else ""
    return ("default-src 'self'; img-src 'self' data:; media-src 'self' data:; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'%s; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'" % ws)


@web.middleware
async def security_headers(request, handler):
    """No camera, microphone or location for the page, no framing, no referrer (the token
    is in the URL), no MIME sniffing."""
    try:
        resp = await handler(request)
    except web.HTTPException as e:                    # 404 and friends get them too
        _harden(e)
        raise
    _harden(resp)
    return resp


def _harden(resp):
    if getattr(resp, "prepared", False):              # streams have sent their headers
        return
    resp.headers["Permissions-Policy"] = PERMISSIONS
    resp.headers["Feature-Policy"] = "camera 'none'; microphone 'none'; geolocation 'none'"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Content-Type-Options"] = "nosniff"


class _AudioIn(asyncio.DatagramProtocol):
    """Guest audio (PCM from ffmpeg) -> every connected page."""

    def __init__(self, hub):
        self.hub = hub

    def datagram_received(self, data, addr):
        self.hub.broadcast_bytes(data)


# ---------------------------------------------------------------------------- weather

class Weather(object):
    """Fetches forecast, air quality and radiation in the background (network only in an
    executor) and keeps the finished panel in memory. A source's "ok" time is set only on
    success, so the page can honestly say that a source has not updated for a while."""

    EVERY = {"fc": 600, "air": 3600, "rad": 21600}
    RETRY = {"fc": (120, 300, 600), "air": (600,), "rad": (1800,)}
    NAME = {"fc": "forecast", "air": "air quality", "rad": "radiation"}

    def __init__(self, cfg, media, hub, state_path):
        self.cfg = cfg
        self.media = media
        self.hub = hub
        self.state_path = state_path
        self.raw = {"fc": None, "air": None, "rad": None}
        self.src = {k: {"ok": 0, "fail": 0, "err": ""} for k in self.raw}
        self.mem = {}
        self.panel = None
        self.brain = None
        self.fetch = {}
        self.photos = None
        self._ph_pool = None
        self._ph_last = {"key": None, "ahead": []}
        self.poll = {"t": time.time(), "v": "", "quiet": False}
        self._load_modules()

    def _load_modules(self):
        cfg = self.cfg
        try:
            from .weather import brain
            self.brain = brain
        except Exception as e:                       # calls must work without the panel
            log.warning("weather panel disabled: %s", e)
        if not cfg.weather.enabled:
            log.info("weather fetching is off ([weather] enabled = off)")
            return
        try:
            from .weather import sources
            self.fetch["fc"] = lambda: sources.fetch_forecast(cfg)
            self.fetch["air"] = lambda: sources.fetch_air(cfg)
        except Exception as e:
            log.warning("weather sources unavailable: %s", e)
        if cfg.radiation.provider != "none":
            try:
                from . import radiation
                self.fetch["rad"] = lambda: self._fetch_rad(radiation)
            except Exception as e:
                log.warning("radiation data unavailable: %s", e)
        if cfg.weather.photos:
            try:
                from .weather import photo
                try:            # local date of the photo of the day, hemisphere for seasons
                    self.photos = photo.PhotoStore(tz=cfg.tzinfo(), lat=cfg.latitude)
                except TypeError:
                    self.photos = photo.PhotoStore()
            except Exception as e:                   # without photos the page uses a tone
                log.warning("weather photos disabled: %s", e)

    def _fetch_rad(self, radiation):
        """radiation.fetch; "no station within the radius" is a valid answer, not a failure
        (otherwise the panel would keep saying that radiation data did not load)."""
        cfg = self.cfg
        try:
            return radiation.fetch(cfg.latitude, cfg.longitude, cfg.radiation.radius_km,
                                   provider=cfg.radiation.provider, timeout=20)
        except Exception as e:
            no_stations = getattr(radiation, "NoStationsError", None)
            if no_stations is None or not isinstance(e, no_stations):
                raise
            try:
                pid = cfg.radiation.provider
                if pid == "auto":
                    pid = radiation.pick(cfg.latitude, cfg.longitude)
                out = dict(radiation.describe(pid))
            except Exception:
                out = {"source": str(e).split(":", 1)[0]}
            out["stations"] = []
            return out

    # ---- state file
    def load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        for k in self.src:
            if isinstance((d.get("src") or {}).get(k), dict):
                self.src[k].update(d["src"][k])
            self.raw[k] = (d.get("raw") or {}).get(k)
        if isinstance(d.get("mem"), dict):
            self.mem.update(d["mem"])

    def save_state(self):
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"src": self.src, "mem": self.mem, "raw": self.raw}, f,
                          ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except (OSError, TypeError, ValueError) as e:
            log.warning("weather state not saved: %s", e)

    # ---- panel
    def build(self):
        if not self.brain:
            return
        now = time.time()
        raw = dict(self.raw)
        raw["cam"] = {}
        for p in self.cfg.panels:
            ts = self.media.warm_ts.get(p.id, 0)
            raw["cam"][p.id] = {"ts": ts, "was": bool(ts) and now - ts < 86400}
        src = {k: dict(v) for k, v in self.src.items()}
        try:
            self.panel = self.brain.build_panel(raw, src, self.mem, now, self.cfg)
        except Exception as e:                       # contract: never raises; calls matter more
            log.warning("weather panel not built: %s", e)
        self._photo()
        self.save_state()

    def _photo(self):
        """Photo keys for the scene now and the next hours; downloads run in their own
        thread without waiting. Until the wanted photo is there, the previous one stays."""
        d = self.panel
        if self.photos is None or not isinstance(d, dict) or d.get("v") != 3:
            return
        try:
            n = d.get("now") or {}
            if hasattr(self.photos, "choose"):
                key, ahead = self.photos.choose(n)
            else:
                key = self.photos.pick(n.get("scene"), n.get("day"), n.get("t"), None)
                ahead = [self.photos.pick(s, n.get("day"), n.get("t"), None)
                         for s in (n.get("ahead") or [])]
            self._ph_last.update(key=key, ahead=ahead)
            self.photos.want([key] + ahead)
            if self._ph_pool is None:
                self._ph_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = asyncio.get_running_loop().run_in_executor(self._ph_pool,
                                                             self.photos.fetch_missing)
            fut.add_done_callback(self._photo_done)
            d["photo"] = self.photos.block(key, ahead)
        except Exception as e:
            log.warning("weather photo: %s", e)

    def _photo_done(self, fut):
        try:
            if fut.result() and self.panel:
                self.panel["photo"] = self.photos.block(self._ph_last["key"],
                                                        self._ph_last["ahead"])
                log.info("weather photo: %d downloaded, showing %s", fut.result(),
                         (self.panel["photo"] or {}).get("key"))
        except Exception as e:
            log.warning("weather photo: %s", e)

    def photo(self, key, kind):
        return self.photos.get(key, kind) if self.photos else None

    def _log_rad(self, data):
        st = (data or {}).get("stations") or []
        if st:
            s = st[0]
            log.info("radiation: %d stations, nearest %s %.1f km %s uSv/h (%s)", len(st),
                     s.get("name"), s.get("km") or 0, s.get("usvh"), s.get("ts"))
        else:
            log.info("radiation: no stations within %g km", self.cfg.radiation.radius_km)

    async def loop(self):
        loop = asyncio.get_running_loop()
        self.load_state()
        for k in self.raw:
            if k not in self.fetch:                  # a source switched off: no old data
                self.raw[k] = None
        self.build()                                 # a panel at once, before any download
        due = {k: 0.0 for k in self.fetch}
        last_build = time.time()
        while True:
            changed = False
            for k, fn in self.fetch.items():
                if time.time() < due[k]:
                    continue
                s = self.src[k]
                try:
                    self.raw[k] = await loop.run_in_executor(None, fn)
                    if s["fail"]:
                        log.info("%s is back after %d failures", self.NAME[k], s["fail"])
                    s.update(ok=time.time(), fail=0, err="")
                    due[k] = time.time() + self.EVERY[k]
                    changed = True
                    if k == "rad":
                        self._log_rad(self.raw[k])
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    s["fail"] += 1
                    s["err"] = str(e)[:160]
                    r = self.RETRY[k]
                    due[k] = time.time() + r[min(s["fail"] - 1, len(r) - 1)]
                    # log on change and then rarely, not on every retry
                    if s["fail"] == 1 or s["fail"] % 12 == 0:
                        log.warning("%s unavailable: %s (failures in a row: %d)",
                                    self.NAME[k], s["err"], s["fail"])
            if changed or time.time() - last_build >= 60:
                self.build()
                last_build = time.time()
            # watchdog: no panel polls and no event channel for 10 minutes means the
            # iPad froze or dropped off the network; one log line per episode
            quiet = not self.hub.clients and time.time() - self.poll["t"] > 600
            if quiet and not self.poll["quiet"]:
                log.warning("station silent for %d min: no polls, no event channel",
                            (time.time() - self.poll["t"]) // 60)
            elif not quiet and self.poll["quiet"]:
                log.info("station is back online")
            self.poll["quiet"] = quiet
            await asyncio.sleep(15)


# ---------------------------------------------------------------------------- application

class Station(object):
    """Everything one running server needs; the aiohttp handlers are its methods."""

    def __init__(self, cfg, runtime_dir):
        self.cfg = cfg
        self.runtime_dir = runtime_dir
        self.token = cfg.token.encode("utf-8")
        self.hub = Hub()
        self.media = Media(cfg, runtime_dir)
        a = cfg.asterisk
        self.ami = Ami(a.ami_host, a.ami_port, a.ami_user, a.ami_secret)
        self.calls = CallManager(cfg, self.media, self.ami, self.hub, runtime_dir=runtime_dir)
        self.weather = Weather(cfg, self.media, self.hub,
                               os.path.join(cfg.paths.state_dir, "weather_state.json"))
        self.static = cfg.paths.static_dir
        self.tasks = []
        self.transports = []

    # ---- helpers
    def ok_token(self, request):
        """Constant-time comparison: the token cannot be guessed from response timing."""
        for v in (request.query.get("k"), request.headers.get("X-Token")):
            if v and hmac.compare_digest(v.encode("utf-8"), self.token):
                return True
        return False

    def _read_static(self, name, mode="r"):
        path = os.path.join(self.static, name)
        with open(path, mode, **({"encoding": "utf-8"} if mode == "r" else {})) as f:
            return f.read()

    @staticmethod
    async def _body(request):
        try:
            if request.can_read_body:
                b = await request.json()
                return b if isinstance(b, dict) else {}
        except Exception:
            pass
        return {}

    @staticmethod
    def _deny_json():
        return web.json_response({"ok": False, "err": "Forbidden"}, status=403)

    # ---- page and assets
    async def h_index(self, request):
        if not self.ok_token(request):
            return web.Response(status=403, text="forbidden")
        try:
            html = self._read_static("index.html")
        except OSError:
            html = FALLBACK_PAGE
        html = (html.replace("__IWT_CONFIG__", json_for_script(self.cfg.page_config()))
                .replace("__TOKEN__", self.cfg.token))
        r = web.Response(text=html, content_type="text/html")
        # iOS home-screen apps otherwise keep running a cached old page after a reload
        r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        r.headers["Pragma"] = "no-cache"
        r.headers["Content-Security-Policy"] = csp_for(request.host)
        return r

    def _file(self, rel, ctype):
        try:
            data = self._read_static(rel, "rb")
        except OSError:
            return web.Response(status=404, text="not found")
        return web.Response(body=data, content_type=ctype,
                            headers={"Cache-Control": "public, max-age=86400"})

    async def h_icon_png(self, request):
        # iOS fetches home-screen icons without the token
        name = request.match_info.get("name")
        if name is None:
            return self._file(os.path.join("icons", "apple-touch-icon-180.png"), "image/png")
        if not re.match(r"^[A-Za-z0-9_.-]+$", name) or ".." in name:
            return web.Response(status=404, text="not found")
        return self._file(os.path.join("icons", name + ".png"), "image/png")

    async def h_icon_svg(self, request):
        return self._file("icon.svg", "image/svg+xml")

    async def h_ring(self, request):
        name = request.match_info["name"]
        if not re.match(r"^[a-z0-9_-]{1,32}$", name):
            return web.Response(status=404, text="not found")
        return self._file(os.path.join("ring", name + ".wav"), "audio/wav")

    async def h_ver(self, request):
        """Version of the page on disk: the station compares it with its own and reloads
        when a new one is deployed. Touching ``<state_dir>/reload.flag`` reloads idle
        stations without a new version."""
        if not self.ok_token(request):
            return self._deny_json()
        try:
            v = page_version(self._read_static("index.html"))
        except OSError:
            v = ""
        try:
            rl = int(os.path.getmtime(os.path.join(self.cfg.paths.state_dir, "reload.flag")))
        except OSError:
            rl = 0
        return web.json_response({"ok": True, "ver": v, "reload": rl, "server": __version__})

    async def h_diag(self, request):
        """The page reports its audio state: an iPad has no console to look at."""
        if not self.ok_token(request):
            return self._deny_json()
        b = await self._body(request)
        log.info("page diagnostics: %s", json.dumps(b, ensure_ascii=False)[:300])
        return web.json_response({"ok": True})

    # ---- weather
    async def h_panel(self, request):
        if not self.ok_token(request):
            return self._deny_json()
        w = self.weather
        w.poll["t"] = time.time()
        v = request.query.get("v", "")[:40]
        if v and v != w.poll["v"]:
            log.info("page version %s", v)
            w.poll["v"] = v
        if w.panel is None:
            return web.json_response({"ok": False, "err": "Panel is not ready yet"}, status=503,
                                     headers={"Cache-Control": "no-store"})
        return web.json_response(w.panel, headers={"Cache-Control": "no-store"},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))

    async def h_photo(self, request):
        if not self.ok_token(request):
            return web.Response(status=403, text="forbidden")
        data = self.weather.photo(request.match_info["key"], request.match_info["kind"])
        if data is None:
            return web.Response(status=404, text="no photo")
        return web.Response(body=data, content_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=86400"})

    # ---- video and events
    async def h_stream(self, request):
        if not self.ok_token(request):
            return web.Response(status=403, text="forbidden")
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "multipart/x-mixed-replace; boundary=fr"
        resp.headers["Cache-Control"] = "no-cache"
        await resp.prepare(request)
        media = self.media
        try:
            # every new frame goes out at once; the timeout only guards against a hang
            last = 0
            while True:
                f, fid = media.current_frame()
                if f and fid != last:
                    last = fid
                    await resp.write(("--fr\r\nContent-Type: image/jpeg\r\nContent-Length: %d"
                                      "\r\n\r\n" % len(f)).encode("ascii"))
                    await resp.write(f)
                    await resp.write(b"\r\n")
                ev = media.frame_ev
                if ev:
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.06)
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            pass
        return resp

    async def h_ws(self, request):
        if not self.ok_token(request):
            return web.Response(status=403, text="forbidden")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.hub.clients.add(ws)
        ua = request.headers.get("User-Agent", "")
        who = "%s %s" % (request.remote, (re.findall(r"iPad|iPhone|Macintosh|Android|Windows|Linux",
                                                      ua) or ["?"])[0])
        log.info("page connected: %s (total %d)", who, len(self.hub.clients))
        try:
            for ev in self.calls.hello_events():
                await ws.send_str(json.dumps(ev))
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        except Exception:
            pass
        finally:
            self.hub.clients.discard(ws)
            log.info("page disconnected: %s (left %d)", who, len(self.hub.clients))
        return ws

    async def pulse_loop(self):
        """A pulse the page can see (protocol pings are invisible to JavaScript, and iOS does
        not tell a page that its connection silently died). ``vage`` is the age of the
        frame on screen, so the page can tell "connecting" from "no video"."""
        while True:
            await asyncio.sleep(5)
            if self.hub.clients:
                try:
                    await self.hub.notify({"ev": "ka", "vage": self.media.frame_age()})
                except Exception:
                    pass

    # ---- control API
    async def _api(self, request, fn):
        if not self.ok_token(request):
            return self._deny_json()
        status, data = await fn(await self._body(request))
        return web.json_response(data, status=status)

    async def h_answer(self, request):
        return await self._api(request, self.calls.answer)

    async def h_open(self, request):
        return await self._api(request, self.calls.open)

    async def h_view(self, request):
        return await self._api(request, self.calls.view)

    async def h_hangup(self, request):
        return await self._api(request, self.calls.hangup)

    async def h_demo(self, request):
        return await self._api(request, self.calls.start_demo)

    # ---- lifecycle
    async def on_startup(self, app):
        loop = asyncio.get_running_loop()
        self.media.attach(loop)
        sip = self.cfg.sip
        uas = SipUas(self.calls, sip.uas_host, sip.uas_port)
        self.calls.reply = uas.reply
        try:
            t, _ = await loop.create_datagram_endpoint(lambda: uas,
                                                       local_addr=(sip.uas_host, sip.uas_port))
            self.transports.append(t)
            t, _ = await loop.create_datagram_endpoint(lambda: _AudioIn(self.hub),
                                                       local_addr=("127.0.0.1", sip.audio_out_port))
            self.transports.append(t)
        except OSError as e:
            raise RuntimeError("cannot open the SIP/audio UDP ports ([sip] uas_port %d, "
                               "audio_out_port %d): %s" % (sip.uas_port, sip.audio_out_port,
                                                           e.strerror or e))
        await loop.run_in_executor(None, self.media.caps.detect)
        for coro in (self.ami.run(), self.media.warm_loop(), self.pulse_loop(),
                     self.weather.loop()):
            self.tasks.append(asyncio.ensure_future(coro))
        log.info("Intercom WebTab %s ready on http://%s:%d/ (panels: %s)", __version__,
                 self.cfg.http.bind, self.cfg.http.port, ", ".join(self.cfg.panel_ids))

    async def on_shutdown(self, app):
        for ws in list(self.hub.clients):
            try:
                await ws.close()
            except Exception:
                pass

    async def on_cleanup(self, app):
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except BaseException:
                pass
        for t in self.transports:
            t.close()
        self.calls.stop()
        await asyncio.get_running_loop().run_in_executor(None, self.media.stop_all)
        self.weather.save_state()

    def make_app(self):
        app = web.Application(middlewares=[security_headers])
        app.on_startup.append(self.on_startup)
        app.on_shutdown.append(self.on_shutdown)
        app.on_cleanup.append(self.on_cleanup)
        app.add_routes([
            web.get("/", self.h_index),
            web.get("/icon.png", self.h_icon_png),
            web.get("/icon.svg", self.h_icon_svg),
            web.get("/icons/{name}.png", self.h_icon_png),
            web.get("/ring/{name}.wav", self.h_ring),
            web.get("/stream", self.h_stream),
            web.get("/ws", self.h_ws),
            web.post("/api/open", self.h_open),
            web.post("/api/answer", self.h_answer),
            web.post("/api/view", self.h_view),
            web.post("/api/hangup", self.h_hangup),
            web.post("/api/demo", self.h_demo),
            web.post("/api/diag", self.h_diag),
            web.get("/api/panel", self.h_panel),
            web.get("/api/ver", self.h_ver),
            web.get("/ph/{key}/{kind:bg|panel}.jpg", self.h_photo),
        ])
        return app


def runtime_directory(cfg):
    """(path, created_by_us): [paths] runtime_dir, systemd's $RUNTIME_DIRECTORY, or a
    private temporary directory."""
    if cfg.paths.runtime_dir:
        os.makedirs(cfg.paths.runtime_dir, mode=0o700, exist_ok=True)
        return cfg.paths.runtime_dir, False
    env = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    if env and os.path.isdir(env):
        return env, False
    return tempfile.mkdtemp(prefix="intercom-webtab-"), True


def run(cfg):
    """Run the server until SIGINT/SIGTERM."""
    os.makedirs(cfg.paths.state_dir, exist_ok=True)
    rdir, created = runtime_directory(cfg)
    try:
        station = Station(cfg, rdir)
        web.run_app(station.make_app(), host=cfg.http.bind, port=cfg.http.port,
                    print=None, access_log=None, shutdown_timeout=5)
    finally:
        if created:
            shutil.rmtree(rdir, ignore_errors=True)
