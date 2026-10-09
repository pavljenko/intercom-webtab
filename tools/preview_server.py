#!/usr/bin/env python3
"""Local preview server for the station page. Standard library only (Pillow is optional and only
draws the placeholder photos and the camera test pattern). It never talks to a real station:
doors, calls and cameras cannot be reached from here.

    python3 tools/preview_server.py [--port 8897] [--host 127.0.0.1]

Open http://127.0.0.1:8897/preview?mock=rain in a desktop browser (window 1024x748).

Page query parameters (read by this server and by the page):
  mock=<name>         weather panel from static/mock/<name>.json: rain, clear-day, clear-night,
                      overcast, snow, fog, thunderstorm, freezing-rain, dust-haze, not-available;
                      mock=down answers 503, mock=offline drops the connection ("no connection")
  panels=1|2          mock config with one panel ("Gate") or two ("Gate", "Entrance"; default)
  ring=low|mid|high|sweep   ringtone in the mock config (default mid)
  attr=0              config with "attribution": false
  demo=ring|answered|ended|view&panel=gate|door
                      the /ws stub plays call events (with demo=view the page starts a view)
  act=open [&sim=err] the page taps the third button itself once a call is answered or a view
                      is running; sim=err makes the simulated request fail
  live=1              on /preview: send requests to this server's stubs instead of simulating

Routes:
  /, /preview         static/index.html with the token and a mock config injected
  /api/panel          static/mock/<mock>.json
  /api/ver            the VER of static/index.html, so "/" never reloads in a loop
  POST /api/*         {"ok": true, "stub": true}; the request body is logged
  /stream?panel=...   a generated test-pattern JPEG (never a real camera frame)
  /ph/<key>/bg.jpg|panel.jpg   generated gradient "photos" (scene = key before the first "-")
  /ws                 WebSocket stub: hello {"stub": true}, demo events, a pulse every 5 s
  anything else       files from static/ (icons/, ring/, mock/, buttons/, dots.js, icon.svg)

Paths other than "/" run the page in preview mode: open, answer, view and hangup are only
simulated (see the page source). "/" behaves like production against these stubs.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import random
import re
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:                       # photos and /stream answer 404 without Pillow
    Image = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(ROOT, "intercom_webtab", "static")
MOCK = os.path.join(STATIC, "mock")
TYPES = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
         ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".png": "image/png", ".wav": "audio/wav",
         ".txt": "text/plain; charset=utf-8", ".js": "application/javascript; charset=utf-8"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TOKEN = "preview"
PANELS = [{"id": "gate", "label": "Gate", "icon": "gate"},
          {"id": "door", "label": "Entrance", "icon": "house"}]
RINGTONES = ("low", "mid", "high", "sweep")


def inside(root, rel):
    """A file inside root, or None (no ../ escapes)."""
    path = os.path.realpath(os.path.join(root, rel))
    base = os.path.realpath(root)
    if path != base and not path.startswith(base + os.sep):
        return None
    return path if os.path.isfile(path) else None


def page_text():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        return f.read()


def page_ver():
    """VER of the page, parsed like the server does: the first line with 'var VER ='."""
    for ln in page_text().split("\n"):
        if "var VER =" in ln:
            return ln.split('"')[1]
    return ""


def mock_config(q):
    n = 1 if q.get("panels") == "1" else 2
    ring = q.get("ring") if q.get("ring") in RINGTONES else "mid"
    return {"title": "Intercom WebTab", "panels": PANELS[:n], "ringtone": ring,
            "attribution": q.get("attr") != "0"}


def config_json(cfg):
    """JSON safe inside <script>: no "<", ">" or "&" characters."""
    s = json.dumps(cfg, ensure_ascii=False)
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def self_check():
    """The embedded copies in index.html must match static/dots.js and static/ring/*.wav."""
    html = page_text()
    bad = []
    m = re.search(r'<script id="dotsJs">\n(.*?)</script>', html, re.S)
    try:
        dots = open(os.path.join(STATIC, "dots.js"), encoding="utf-8").read()
    except OSError:
        dots = None
    if not m or dots is None or m.group(1).rstrip("\n") != dots.rstrip("\n"):
        bad.append("the dots.js copy in index.html differs from static/dots.js")
    for name in RINGTONES:
        m = re.search(r'<script type="text/plain" id="ring-%s">\n(.*?)\n</script>' % name, html, re.S)
        path = os.path.join(STATIC, "ring", name + ".wav")
        try:
            want = base64.b64encode(open(path, "rb").read()).decode("ascii")
        except OSError:
            want = None
        if not m or want is None or re.sub(r"\s+", "", m.group(1)) != want:
            bad.append("ringtone %s: run tools/ringtones.py" % name)
    if "__IWT_CONFIG__" not in html or "__TOKEN__" not in html:
        bad.append("index.html lacks a placeholder")
    return bad


# ---------- generated images (Pillow) ----------
SCENES = {   # top colour, bottom colour of the sky gradient for each photo scene
    "rain": ((132, 142, 142), (52, 60, 60)),
    "clear": ((52, 118, 192), (156, 194, 222)),
    "night": ((6, 10, 26), (26, 36, 66)),
    "overcast": ((156, 162, 166), (92, 98, 102)),
    "snow": ((200, 208, 216), (128, 140, 152)),
    "fog": ((182, 186, 186), (134, 140, 140)),
    "storm": ((64, 70, 84), (26, 30, 36)),
    "dust": ((196, 158, 106), (116, 88, 58)),
}
_img_cache = {}
_img_lock = threading.Lock()


def _jpeg(img, quality=86):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)     # Pillow adds no EXIF by default
    return buf.getvalue()


def _photo(key):
    """bg (1024x748, shade baked in at the bottom left) and panel (384x748, blurred, darkened)."""
    scene = key.split("-")[0]
    top, bot = SCENES.get(scene, SCENES["overcast"])
    W, H = 1024, 748
    col = Image.new("RGB", (1, 2))
    col.putpixel((0, 0), top)
    col.putpixel((0, 1), bot)
    img = col.resize((W, H), Image.BICUBIC)
    # soft large-scale variation, deterministic per key
    rnd = random.Random(hashlib.sha256(key.encode()).digest())
    blob = Image.new("L", (8, 6))
    blob.putdata([rnd.randint(104, 152) for _ in range(48)])
    blob = blob.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(40))
    img = Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), blob.point(lambda v: min(255, v + 120)))
    if scene == "night":
        dr = ImageDraw.Draw(img)
        for _ in range(90):
            x, y = rnd.randint(0, W), rnd.randint(0, int(H * 0.6))
            v = rnd.randint(120, 230)
            dr.point((x, y), fill=(v, v, v))
    # toned shade at the bottom left, under the big number and the lines
    shade = Image.new("L", (64, 47))
    px = []
    for y in range(47):
        for x in range(64):
            sy = max(0.0, min(1.0, (y / 46.0 - 0.42) / 0.58))
            sx = max(0.0, 1.0 - 0.55 * x / 40.0)
            px.append(int(255 * 0.62 * sy * sy * (3 - 2 * sy) * sx))
    shade.putdata(px)
    shade = shade.resize((W, H), Image.BICUBIC)
    bg = Image.composite(Image.new("RGB", (W, H), (10, 12, 12)), img, shade)
    panel = img.crop((640, 0, W, H)).filter(ImageFilter.GaussianBlur(18))
    panel = Image.blend(panel, Image.new("RGB", panel.size, (0, 0, 0)), 0.45)
    return _jpeg(bg), _jpeg(panel)


def photo(key, part):
    if Image is None:
        return None
    with _img_lock:
        if key not in _img_cache:
            _img_cache[key] = _photo(key)
        bg, pn = _img_cache[key]
    return bg if part == "bg" else pn


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                     # Pillow < 10.1: fixed bitmap font
        return ImageFont.load_default()


def test_pattern(label):
    """A neutral 1280x720 test pattern with an on-screen clock, like a camera OSD."""
    if Image is None:
        return None
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (40, 42, 44))
    dr = ImageDraw.Draw(img)
    for i in range(8):                    # grey-scale bars
        v = int(235 - i * 28)
        dr.rectangle([i * W // 8, 0, (i + 1) * W // 8, int(H * 0.62)], fill=(v, v, v))
    for x in range(W):                    # luminance ramp
        v = int(16 + 219 * x / (W - 1))
        dr.line([(x, int(H * 0.62)), (x, int(H * 0.74))], fill=(v, v, v))
    cx, cy, r = W // 2, int(H * 0.40), int(H * 0.30)
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(250, 250, 250), width=4)
    dr.line([(cx - r, cy), (cx + r, cy)], fill=(250, 250, 250), width=3)
    dr.line([(cx, cy - r), (cx, cy + r)], fill=(250, 250, 250), width=3)
    f = _font(30)
    dr.text((24, 18), "2026-01-01 12:00:00", fill=(255, 255, 255), font=f,   # fixed: no local clock
            stroke_width=2, stroke_fill=(0, 0, 0))
    dr.text((24, H - 60), "PREVIEW CAMERA - " + label.upper(), fill=(255, 255, 255), font=f,
            stroke_width=2, stroke_fill=(0, 0, 0))
    return _jpeg(img, 80)


def ws_frame(text):
    """A server-to-client WebSocket text frame (unmasked)."""
    data = text.encode("utf-8")
    n = len(data)
    if n < 126:
        head = struct.pack("!BB", 0x81, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x81, 126, n)
    else:
        head = struct.pack("!BBQ", 0x81, 127, n)
    return head + data


class Handler(BaseHTTPRequestHandler):
    server_version = "iwt-preview"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.command, fmt % a))

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def js(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False))

    def not_found(self):
        self.send(404, "not found", "text/plain; charset=utf-8")

    def file(self, path):
        if not path:
            return self.not_found()
        with open(path, "rb") as f:
            self.send(200, f.read(), TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream"))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlsplit(self.path)
        p = unquote(u.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if p in ("/", "/preview"):
            html = page_text().replace("__TOKEN__", TOKEN).replace("__IWT_CONFIG__", config_json(mock_config(q)))
            return self.send(200, html, TYPES[".html"])
        if p == "/ws":
            return self.ws(q.get("demo", ""), q.get("panel", ""))
        if p == "/api/ver":
            return self.js({"ok": True, "ver": page_ver(), "reload": 0})
        if p == "/api/panel":
            name = q.get("mock", "rain")
            if name == "offline":                 # "no connection": the socket closes, no answer
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return None
            if name == "down":
                return self.js({"ok": False, "err": "weather panel not ready yet"}, 503)
            if not re.match(r"^[a-z0-9-]+$", name):
                return self.not_found()
            return self.file(inside(MOCK, name + ".json"))
        if p == "/stream":
            pid = q.get("panel", "")
            label = next((x["label"] for x in PANELS if x["id"] == pid), PANELS[0]["label"])
            data = test_pattern(label)
            return self.send(200, data, TYPES[".jpg"]) if data else self.not_found()
        m = re.match(r"^/ph/([a-z0-9-]+)/(bg|panel)\.jpg$", p)
        if m:
            data = photo(m.group(1), m.group(2))
            return self.send(200, data, TYPES[".jpg"]) if data else self.not_found()
        if p.startswith("/api/"):
            return self.js({"ok": False, "err": "preview server"}, 404)
        return self.file(inside(STATIC, p.lstrip("/")))

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(n, 65536)) if n else b""
        p = urlsplit(self.path).path
        if p.startswith("/api/"):
            sys.stderr.write("STUB %s %s\n" % (p, body.decode("utf-8", "replace")[:300]))
            return self.js({"ok": True, "stub": True})
        return self.not_found()

    def ws(self, demo, panel):
        """Call channel stub. Order as on the real server: hello, the call state, then the pulse."""
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
            return self.send(400, "WebSocket expected", "text/plain; charset=utf-8")
        acc = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", acc)
        self.end_headers()
        self.close_connection = True
        sock = self.connection
        ids = [x["id"] for x in PANELS]
        panel = panel if panel in ids else ids[0]
        live = demo in ("ring", "answered", "ended", "view")
        steps = [(0, {"ev": "hello", "active": live, "stub": True})]
        if demo in ("ring", "answered", "ended"):
            steps.append((0, {"ev": "call", "state": "ringing", "panel": panel}))
            if demo == "answered":
                steps.append((0, {"ev": "call", "state": "answered", "panel": panel}))
            if demo == "ended":
                steps.append((2.0, {"ev": "call", "state": "ended"}))
        elif demo != "view":
            steps.append((0, {"ev": "call", "state": "ended"}))   # what the server sends outside a call
        try:
            for pause, ev in steps:
                time.sleep(pause)
                sock.sendall(ws_frame(json.dumps(ev)))
            while True:
                time.sleep(5)
                sock.sendall(ws_frame(json.dumps({"ev": "ka", "vage": 0.4 if live else None})))
        except OSError:
            pass
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Intercom WebTab page preview (no real station involved)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8897)
    args = ap.parse_args(argv)
    for msg in self_check():
        sys.stderr.write("WARNING: %s\n" % msg)
    if Image is None:
        sys.stderr.write("Pillow not installed: photos and /stream answer 404\n")
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("preview: http://%s:%d/preview?mock=rain  (VER %s)" % (args.host, args.port, page_ver() or "?"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
