#!/usr/bin/env python3
"""End-to-end test of the station server without Asterisk, a door or a browser.

It starts the real server (``python3 -m intercom_webtab``) with a temporary configuration,
a fake AMI (tools/fake_ami.py) on a localhost port, and rings the station's SIP UAS with
the panel simulator (tools/panelsim.py). The HTTP API and the WebSocket are driven like
the page drives them:

* real call: ringing -> answer -> (video and audio, if ffmpeg with libx264 is present)
  -> open (DTMF into the guest's channel, repeated) -> hang up;
* demo call: open answers but never sends DTMF;
* another panel ringing: 409, view close and other-panel close are ignored, decline -> 486;
* caller gives up: CANCEL -> 487 and "ended";
* open from a camera view: Originate Wait, our own channel found by application, second
  press reuses it;
* camera view through SIP: the ordered call is accepted, Open uses its channel, a stale
  view call is declined with 603.

An optional last step renders the page in headless Chrome if one is installed.
Needs aiohttp in the Python that runs this script. Exit code 0 when everything passed.
"""
import asyncio
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

try:
    import aiohttp
except ImportError:
    print("e2e: aiohttp is required (apt install python3-aiohttp or pip install aiohttp)")
    sys.exit(2)

from fake_ami import FakeAmi  # noqa: E402

TOKEN = "e2e-" + "".join(random.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(24))
RESULTS = []


def check(ok, name, detail=""):
    RESULTS.append((bool(ok), name))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" - %s" % detail) if detail and not ok else ""),
          flush=True)
    return bool(ok)


def free_tcp():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def free_udp(count=1, even=False):
    for _ in range(200):
        base = random.randrange(46000, 60000, 2 if even else 1)
        socks = []
        try:
            for i in range(count):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                socks.append(s)
                s.bind(("127.0.0.1", base + i))
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free UDP ports")


def has_media():
    ff = shutil.which("ffmpeg")
    if not ff:
        return False
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "libx264" in out


def find_chrome():
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if os.path.exists(c):
            return c
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(n)
        if p:
            return p
    return None


class E2E(object):
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="iwt-e2e-")
        self.fake = FakeAmi(user="webtab", secret="e2e-secret", up_delay=0.3)
        self.events = []
        self.audio_bytes = 0
        self.app = None
        self.sims = []

    # ---- setup
    def write_config(self, ami_port):
        self.http = free_tcp()
        self.uas = free_udp()
        rtp = free_udp(4, even=True)
        aout = free_udp()
        conf = """
[station]
title = E2E Station
log_level = debug
[http]
bind = 127.0.0.1
port = %(http)d
[paths]
token_file = token
secrets_file = secrets.ini
state_dir = state
runtime_dir = run
[weather]
enabled = off
photos = off
[radiation]
provider = none
[sip]
uas_host = 127.0.0.1
uas_port = %(uas)d
rtp_audio_port = %(a)d
rtp_video_port = %(v)d
audio_out_port = %(o)d
[asterisk]
ami_port = %(ami)d
trunk = provider
own_callerid = 100
[panel:gate]
label = Gate
icon = gate
number = 201
open_settle = 0.3
open_repeat = 0.2, 0.4
hold = 4
[panel:door]
label = Entrance
icon = house
number = 202
open_settle = 0.3
open_repeat = 0.2
hold = 4
""" % {"http": self.http, "uas": self.uas, "a": rtp, "v": rtp + 2, "o": aout, "ami": ami_port}
        with open(os.path.join(self.tmp, "webtab.ini"), "w") as f:
            f.write(conf)
        with open(os.path.join(self.tmp, "secrets.ini"), "w") as f:
            f.write("[asterisk]\nami_user = webtab\nami_secret = e2e-secret\n")
        with open(os.path.join(self.tmp, "token"), "w") as f:
            f.write(TOKEN + "\n")
        self.base = "http://127.0.0.1:%d" % self.http

    def start_app(self):
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
        env.pop("RUNTIME_DIRECTORY", None)
        self.app_log = os.path.join(self.tmp, "server.log")
        self.app = subprocess.Popen(
            [sys.executable, "-m", "intercom_webtab", "--config", os.path.join(self.tmp, "webtab.ini")],
            cwd=ROOT, env=env, stdout=open(self.app_log, "wb"), stderr=subprocess.STDOUT)

    def sim(self, number, display="", seconds=20, extra=()):
        cmd = [sys.executable, os.path.join(HERE, "panelsim.py"), "--to", "web",
               "--uas-port", str(self.uas), "--number", number, "--display", display,
               "--seconds", str(seconds)] + list(extra)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.sims.append(p)
        return p

    @staticmethod
    def finish(p, timeout=15, term=False):
        if term and p.poll() is None:
            p.send_signal(signal.SIGTERM)
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
        return p.returncode, out or ""

    # ---- HTTP / WS helpers
    async def api(self, path, body=None, token=True, method="POST"):
        url = self.base + path + (("?k=" + TOKEN) if token else "")
        async with self.session.request(method, url, json=body if method == "POST" else None) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = None
            return r.status, data

    async def ws_reader(self):
        async with self.session.ws_connect(self.base.replace("http", "ws") + "/ws?k=" + TOKEN) as ws:
            self.ws = ws
            async for m in ws:
                if m.type == aiohttp.WSMsgType.TEXT:
                    self.events.append(json.loads(m.data))
                elif m.type == aiohttp.WSMsgType.BINARY:
                    self.audio_bytes += len(m.data)

    async def wait_event(self, ev, state=None, panel=None, timeout=8.0, since=0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.events[since:]:
                if e.get("ev") == ev and (state is None or e.get("state") == state) and \
                        (panel is None or e.get("panel") == panel):
                    return e
            await asyncio.sleep(0.05)
        return None

    async def wait_for(self, fn, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v:
                return v
            await asyncio.sleep(0.05)
        return fn()

    async def first_jpeg(self, timeout=15.0):
        try:
            async with self.session.get(self.base + "/stream?k=" + TOKEN,
                                        timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                buf = b""
                while len(buf) < 4 * 1024 * 1024:
                    chunk = await r.content.read(65536)
                    if not chunk:
                        return False
                    buf += chunk
                    s = buf.find(b"\xff\xd8")
                    if s != -1 and buf.find(b"\xff\xd9", s) != -1:
                        return True
        except (asyncio.TimeoutError, aiohttp.ClientError):
            return False
        return False

    # ---- scenarios
    async def basics(self):
        async with self.session.get(self.base + "/?k=" + TOKEN) as r:
            html = await r.text()
            check(r.status == 200, "page served with the token")
            check("__TOKEN__" not in html and "__IWT_CONFIG__" not in html, "placeholders replaced")
            check(TOKEN in html and "E2E Station" in html, "token and config injected")
            m = re.search(r'id="iwtConfig">(.*?)</script>', html, re.S)
            if m:
                cfg = json.loads(m.group(1))
                check([p["id"] for p in cfg["panels"]] == ["gate", "door"]
                      and "number" not in json.dumps(cfg), "page config: panel ids, no numbers")
            csp = r.headers.get("Content-Security-Policy", "")
            check("frame-ancestors 'none'" in csp and r.headers.get("X-Frame-Options") == "DENY"
                  and r.headers.get("Referrer-Policy") == "no-referrer", "security headers")
        async with self.session.get(self.base + "/") as r:
            check(r.status == 403, "page refused without the token")
        async with self.session.get(self.base + "/", headers={"X-Token": TOKEN}) as r:
            check(r.status == 200, "X-Token header accepted")
        st, d = await self.api("/api/open", {"panel": "gate"}, token=False)
        check(st == 403, "API refused without the token")
        st, d = await self.api("/api/ver", method="GET")
        page = os.path.join(ROOT, "intercom_webtab", "static", "index.html")
        want = ""
        if os.path.exists(page):
            for ln in open(page, encoding="utf-8"):
                if "var VER =" in ln:
                    want = ln.split('"')[1]
                    break
        check(st == 200 and d.get("ok") and d.get("ver") == want and d.get("reload") == 0,
              "/api/ver reports the page version", repr(d))
        for path in ("/icons/apple-touch-icon-180.png", "/icon.svg", "/ring/mid.wav"):
            if os.path.exists(os.path.join(ROOT, "intercom_webtab", "static", path.lstrip("/"))):
                async with self.session.get(self.base + path) as r:
                    check(r.status == 200, "%s served without the token" % path)
        st, d = await self.api("/api/panel", method="GET")
        check(st in (200, 503), "/api/panel answers (%d)" % st)
        hello = await self.wait_event("hello", timeout=5)
        check(hello is not None and "stub" not in hello, "WebSocket hello")
        check(await self.wait_event("call", "ended", timeout=2) is not None,
              "WebSocket clears a stale call on connect")
        ka = await self.wait_event("ka", timeout=8)
        check(ka is not None and "vage" in ka, "WebSocket pulse {ev: ka, vage}")

    async def real_call(self, media):
        self.fake.clear()
        n0 = len(self.events)
        sim = self.sim("201", seconds=25)
        e = await self.wait_event("call", "ringing", "gate", since=n0)
        check(e is not None, "panel 201 rings as 'gate'")
        await asyncio.sleep(0.3)
        check(sim.poll() is None, "station does not answer by itself")
        self.fake.add_channel("PJSIP/provider-00000101", "Up", "intercom-incoming", "Dial", "201")
        st, d = await self.api("/api/answer", {})
        check(st == 200 and d == {"ok": True}, "answer", repr(d))
        check(await self.wait_event("call", "answered", "gate", since=n0) is not None,
              "answered event")
        if media:
            check(await self.first_jpeg(), "video: MJPEG frame from the SIP call on /stream")
            check(await self.wait_for(lambda: self.audio_bytes > 1000, 8),
                  "audio: guest PCM over the WebSocket (%d bytes)" % self.audio_bytes)
        st, d = await self.api("/api/open", {"panel": "gate"})
        check(st == 200 and d.get("ok") and d.get("chan") == "PJSIP/provider-00000101",
              "open sends DTMF into the guest's channel", repr(d))
        n = await self.wait_for(lambda: len(self.fake.recorded("PlayDTMF")) >= 3, 3)
        dt = self.fake.recorded("PlayDTMF")
        check(n and len(dt) == 3 and all(a["Channel"] == "PJSIP/provider-00000101"
                                         and a["Digit"] == "1" for a in dt),
              "DTMF '1' sent 1 + 2 times", repr(dt))
        check(not self.fake.recorded("Originate"), "no extra call placed during a call")
        st, d = await self.api("/api/hangup", {"scope": "call", "panel": "gate"})
        check(st == 200 and d.get("ok"), "hang up")
        check(await self.wait_event("call", "ended", since=n0) is not None, "ended event")
        check(await self.wait_for(lambda: self.fake.recorded("Hangup",
                                                             Channel="PJSIP/provider-00000101"), 3),
              "guest channel hung up through AMI")
        code, out = self.finish(sim, term=True)
        check("200 OK" in out, "simulator saw 200 OK only after answer", out[-300:])

    async def demo_call(self):
        self.fake.clear()
        n0 = len(self.events)
        sim = self.sim("202", display="panelsim", seconds=20, extra=["--no-media"])
        check(await self.wait_event("call", "ringing", "door", since=n0) is not None,
              "demo call rings as 'door'")
        self.fake.add_channel("PJSIP/provider-00000202", "Up", "intercom-incoming", "Dial", "202")
        st, d = await self.api("/api/open", {"panel": "door"})
        check(st == 200 and d == {"ok": True, "demo": True}, "demo open answers but opens nothing",
              repr(d))
        await asyncio.sleep(1.0)
        check(not self.fake.recorded("PlayDTMF") and not self.fake.recorded("Originate"),
              "demo: no PlayDTMF, no Originate")
        await self.api("/api/hangup", {"scope": "call", "panel": "door"})
        check(await self.wait_event("call", "ended", since=n0) is not None, "demo ended")
        self.finish(sim, term=True)

    async def other_panel(self):
        self.fake.clear()
        n0 = len(self.events)
        sim = self.sim("201", seconds=20, extra=["--no-media"])
        check(await self.wait_event("call", "ringing", "gate", since=n0) is not None, "gate rings")
        st, d = await self.api("/api/open", {"panel": "door"})
        check(st == 409 and not d.get("ok"), "open of the other panel refused (409)", repr(d))
        st, d = await self.api("/api/hangup", {"scope": "view", "panel": "gate"})
        check(d == {"ok": True, "ignored": True}, "view close ignored during a call")
        st, d = await self.api("/api/hangup", {"scope": "call", "panel": "door"})
        check(d == {"ok": True, "ignored": True}, "other panel close ignored")
        st, d = await self.api("/api/hangup", {"scope": "call", "panel": "gate"})
        code, out = self.finish(sim)
        check(code == 3 and "486" in out, "declined call gets 486", out[-200:])
        check(not self.fake.recorded("PlayDTMF"), "no DTMF")

    async def cancel(self):
        n0 = len(self.events)
        sim = self.sim("202", seconds=5, extra=["--no-media", "--ring-timeout", "1.5"])
        check(await self.wait_event("call", "ringing", "door", since=n0) is not None, "door rings")
        check(await self.wait_event("call", "ended", since=n0, timeout=6) is not None,
              "caller gave up: CANCEL ends the call")
        self.finish(sim)
        st, d = await self.api("/api/answer", {})
        check(d == {"ok": False, "err": "No incoming call"}, "nothing left to answer")

    async def open_from_view(self):
        self.fake.clear()
        st, d = await self.api("/api/open", {"panel": "door"})
        ok = st == 200 and d.get("ok") and d.get("chan", "").startswith("PJSIP/provider-")
        check(ok, "open from a camera view", repr(d))
        orig = self.fake.recorded("Originate")
        check(len(orig) == 1 and orig[0].get("Channel") == "PJSIP/202@provider"
              and orig[0].get("Application") == "Wait" and orig[0].get("Data") == "4"
              and orig[0].get("CallerID") == "100", "Originate Wait to the panel", repr(orig))
        await asyncio.sleep(0.6)
        dt = self.fake.recorded("PlayDTMF")
        check(len(dt) == 2 and all(a["Channel"] == d.get("chan") for a in dt),
              "DTMF into our own Wait channel (1 + 1)", repr(dt))
        st, d2 = await self.api("/api/open", {"panel": "door"})
        check(d2.get("ok") and d2.get("chan") == d.get("chan")
              and len(self.fake.recorded("Originate")) == 1,
              "second press reuses the live channel", repr(d2))

    async def view_via_sip(self):
        self.fake.clear()
        n0 = len(self.events)
        st, d = await self.api("/api/view", {"panel": "gate"})
        check(st == 200 and d.get("src") == "sip", "view without a camera stream goes through SIP",
              repr(d))
        orig = self.fake.recorded("Originate")
        check(len(orig) == 1 and orig[0].get("Context") == "intercom-view"
              and orig[0].get("Exten") == "webtab" and orig[0].get("CallerID") == "100",
              "view Originate into the view context", repr(orig))
        await asyncio.sleep(1.2)                       # the view channel is found and Up
        sim = self.sim("100", seconds=8, extra=["--no-media"])
        ok = await self.wait_for(lambda: self.fake.recorded("Originate") and sim.poll() is None, 1)
        await asyncio.sleep(1.0)
        check(ok and sim.poll() is None, "ordered view call accepted")
        check(await self.wait_event("call", "ringing", since=n0, timeout=0.5) is None,
              "the view call does not ring")
        st, d = await self.api("/api/open", {"panel": "gate"})
        vchan = [c["Channel"] for c in self.fake.channels if c["Context"] == "intercom-view"]
        check(d.get("ok") and vchan and d.get("chan") == vchan[0]
              and len(self.fake.recorded("Originate")) == 1,
              "open during a SIP view uses the view channel", repr(d))
        st, d = await self.api("/api/hangup", {"scope": "view", "panel": "gate"})
        check(d == {"ok": True}, "view closed")
        check(await self.wait_for(lambda: self.fake.recorded("Hangup"), 3), "view call hung up")
        self.finish(sim, term=True)
        late = self.sim("100", seconds=2, extra=["--no-media"])
        code, out = self.finish(late)
        check(code == 3 and "603" in out, "stale view call declined with 603", out[-200:])

    async def demo_route(self):
        st, d = await self.api("/api/demo", {"panel": "gate"})
        check(st == 200 and d == {"ok": True, "panel": "gate"}, "/api/demo starts the simulator",
              repr(d))

    def chrome(self):
        chrome = None if os.environ.get("IWT_E2E_NO_CHROME") else find_chrome()
        if not chrome:
            print("SKIP headless Chrome (not installed or IWT_E2E_NO_CHROME set)")
            return
        prof = os.path.join(self.tmp, "chrome")
        try:
            out = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                                  "--no-default-browser-check", "--user-data-dir=" + prof,
                                  "--virtual-time-budget=4000", "--dump-dom",
                                  self.base + "/?k=" + TOKEN],
                                 capture_output=True, text=True, timeout=60).stdout
        except (OSError, subprocess.SubprocessError) as e:
            print("SKIP headless Chrome (%s)" % e.__class__.__name__)
            return
        check("<body" in out and "__TOKEN__" not in out, "headless Chrome renders the page")

    async def run(self):
        ami_port = self.fake.start_in_thread()
        self.write_config(ami_port)
        self.start_app()
        self.session = aiohttp.ClientSession()
        try:
            up = False
            for _ in range(100):
                if self.app.poll() is not None:
                    break
                try:
                    st, _ = await self.api("/api/ver", method="GET")
                    up = st == 200
                    if up:
                        break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.15)
            if not check(up, "server starts"):
                return
            reader = asyncio.ensure_future(self.ws_reader())
            check(await self.wait_for(lambda: self.fake.recorded("Login"), 5),
                  "server logs in to AMI")
            media = has_media()
            if not media:
                print("SKIP media checks (ffmpeg with libx264 not found)")
            await self.basics()
            await self.real_call(media)
            await self.demo_call()
            await self.other_panel()
            await self.cancel()
            await self.open_from_view()
            await self.view_via_sip()
            await self.demo_route()
            self.chrome()
            reader.cancel()
        finally:
            await self.session.close()
            for p in self.sims:
                if p.poll() is None:
                    p.kill()
            if self.app and self.app.poll() is None:
                self.app.send_signal(signal.SIGTERM)
                try:
                    self.app.wait(timeout=15)
                    check(True, "server stops on SIGTERM")
                except subprocess.TimeoutExpired:
                    self.app.kill()
                    check(False, "server stops on SIGTERM")
            self.fake.stop()
            log = open(self.app_log, encoding="utf-8", errors="replace").read()
            check("Traceback" not in log, "no tracebacks in the server log",
                  log[log.find("Traceback"):][:800])
            if not all(ok for ok, _ in RESULTS) or os.environ.get("IWT_E2E_LOG"):
                print("---- server log (tail) ----\n" + log[-4000:])


def main():
    t = E2E()
    try:
        asyncio.run(t.run())
    finally:
        shutil.rmtree(t.tmp, ignore_errors=True)
    failed = [n for ok, n in RESULTS if not ok]
    print("e2e: %d checks, %d failed%s" % (len(RESULTS), len(failed),
                                            (": " + "; ".join(failed)) if failed else ""))
    return 1 if failed or not RESULTS else 0


if __name__ == "__main__":
    sys.exit(main())
