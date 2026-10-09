"""ffmpeg-based media: camera streams, call audio, and what we send to the panel.

* **Warm camera streams.** One ffmpeg per panel keeps the RTSP camera connected all the
  time and turns it into MJPEG frames in memory. Switching cameras or showing the caller is
  just a pointer change (``Media.view``), so video appears instantly. A supervisor restarts
  dead or silent processes with a growing back-off.
* **Audio.** A separate light ffmpeg decodes the guest's voice to 8 kHz s16le PCM and sends
  it over UDP to ``audio_out_port``; the server relays it to the page over the WebSocket.
  During a camera view the audio comes from RTSP, during an answered call from SIP.
* **SIP video fallback.** If the RTSP stream is dead, video is decoded from the SIP call.
* **Priming the panel.** Panels only start their own video once they receive ours
  (symmetric RTP), so on answer we send a live H.264 stream and PCMA silence.

ffmpeg option differences are detected once: FFmpeg 5+ uses ``-timeout`` for the RTSP
socket timeout, 4.x needs ``-stimeout``; ``-fps_mode`` replaces ``-vsync`` from 5.1.
"""
import asyncio
import logging
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time

from .config import mask_url

log = logging.getLogger("iwt.media")

MAX_BUF = 4 * 1024 * 1024
WARM_STALE = 15          # s without frames: the warm process is considered hung
WARM_PERIOD = 4          # s between supervisor passes


# ---------------------------------------------------------------------------- ffmpeg flavour

def parse_ffmpeg_version(text):
    """``ffmpeg -version`` output -> (major, minor) or None. Git snapshots count as new."""
    m = re.search(r"ffmpeg version [nN]?(\d+)\.(\d+)", text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.search(r"ffmpeg version N-\d+", text or ""):
        return (99, 0)
    return None


class FfmpegCaps(object):
    """What the installed ffmpeg understands (detected once, on first use)."""

    def __init__(self, binary="ffmpeg"):
        self.binary = binary
        self.version = None
        self.timeout_opt = "-timeout"
        self.passthrough = ["-fps_mode", "passthrough"]
        self.has_x264 = True
        self.present = True
        self._done = False

    def detect(self):
        if self._done:
            return self
        self._done = True
        try:
            out = subprocess.run([self.binary, "-hide_banner", "-version"], capture_output=True,
                                 text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            self.present = False
            log.error("ffmpeg not found: video and audio are disabled (install the ffmpeg package)")
            return self
        self.version = parse_ffmpeg_version(out)
        if self.version is None:
            try:
                helptext = subprocess.run([self.binary, "-hide_banner", "-h", "demuxer=rtsp"],
                                          capture_output=True, text=True, timeout=10).stdout
            except (OSError, subprocess.SubprocessError):
                helptext = ""
            old = "stimeout" in helptext
            self.version = (4, 0) if old else (99, 0)
        self.set_version(self.version)
        try:
            enc = subprocess.run([self.binary, "-hide_banner", "-encoders"], capture_output=True,
                                 text=True, timeout=10).stdout
            self.has_x264 = "libx264" in enc
        except (OSError, subprocess.SubprocessError):
            self.has_x264 = False
        if not self.has_x264:
            log.warning("ffmpeg has no libx264 encoder: panels that wait for our video "
                        "may not start theirs")
        log.info("ffmpeg %s.%s detected (RTSP timeout option %s)", self.version[0],
                 self.version[1], self.timeout_opt)
        return self

    def set_version(self, version):
        self.version = version
        self.timeout_opt = "-timeout" if version >= (5, 0) else "-stimeout"
        self.passthrough = (["-fps_mode", "passthrough"] if version >= (5, 1)
                            else ["-vsync", "0"])


# ---------------------------------------------------------------------------- helpers

def split_jpegs(buf):
    """Cut complete JPEG frames out of ``buf`` -> (frames, rest)."""
    frames = []
    while True:
        s = buf.find(b"\xff\xd8")
        e = buf.find(b"\xff\xd9", s + 2) if s != -1 else -1
        if s == -1 or e == -1:
            if s > 0:
                buf = buf[s:]
            elif s == -1 and len(buf) > 1:
                buf = buf[-1:]                     # keep a possible half marker
            return frames, buf
        frames.append(buf[s:e + 2])
        buf = buf[e + 2:]


def rtp_silence_sender(dst_ip, dst_port, stop):
    """PCMA silence (20 ms packets) to the panel until ``stop[0]`` becomes true. Thread body."""
    if not dst_port:
        return
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = random.randint(0, 65000)
    ts = random.randint(0, 2 ** 30)
    ssrc = random.randint(0, 2 ** 30)
    payload = b"\xd5" * 160
    log.debug("RTP silence -> %s:%d", dst_ip, dst_port)
    try:
        while not stop[0]:
            hdr = struct.pack("!BBHII", 0x80, 8, seq & 0xffff, ts & 0xffffffff, ssrc)
            try:
                s.sendto(hdr + payload, (dst_ip, dst_port))
            except OSError:
                break
            seq += 1
            ts += 160
            time.sleep(0.02)
    finally:
        s.close()


def _kill(procs):
    """Kill processes now, reap them in a thread (``wait`` must not block the event loop)."""
    procs = [p for p in procs if p]
    for p in procs:
        try:
            p.kill()
        except Exception:
            pass
    if procs:
        def reap():
            for p in procs:
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass
        threading.Thread(target=reap, daemon=True).start()


# ---------------------------------------------------------------------------- media

class Media(object):
    def __init__(self, cfg, runtime_dir, caps=None):
        self.cfg = cfg
        self.runtime_dir = runtime_dir
        self.caps = caps or FfmpegCaps()
        self.loop = None
        self.rtsp = {p.id: p.rtsp_url for p in cfg.panels if p.rtsp_url}
        # frames
        self.frames = {}          # panel -> latest JPEG (warm stream or SIP fallback)
        self.frames_ts = {}       # panel -> time of the latest frame from any source
        self.frame_id = 0         # increases with every frame of the panel on screen
        self.frame_ev = None      # "rotating" asyncio.Event, replaced on every frame
        self.view = None          # panel on screen (None = idle)
        # warm streams
        self.warm = {}            # panel -> ffmpeg process
        self.warm_ts = {}         # panel -> time of the latest warm (RTSP) frame
        self.warm_fail = {}       # panel -> consecutive failed restarts
        self.warm_next = {}       # panel -> do not restart before this time
        self.warm_start = {}      # panel -> start time of the current process
        # call media
        self.ffa = None           # audio ffmpeg (RTSP audio, SIP audio or SIP fallback decoder)
        self.dummy = None         # our live H.264 stream to the panel
        self.rtp_stop = [True]    # stop flag of the current PCMA silence thread

    def attach(self, loop):
        self.loop = loop
        self.frame_ev = asyncio.Event()

    def path(self, name):
        return os.path.join(self.runtime_dir, name)

    # ---- frames
    def _frame_notify(self):
        ev, self.frame_ev = self.frame_ev, asyncio.Event()
        if ev:
            ev.set()

    def _store(self, panel, frame, warm):
        now = time.time()
        self.frames[panel] = frame
        self.frames_ts[panel] = now
        if warm:
            self.warm_ts[panel] = now
            self.warm_fail[panel] = 0
        if self.view == panel:
            self.frame_id += 1
            if self.loop:
                try:
                    self.loop.call_soon_threadsafe(self._frame_notify)
                except RuntimeError:
                    pass

    def _reader(self, proc, panel, warm):
        buf = b""
        while True:
            try:
                chunk = proc.stdout.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk
            frames, buf = split_jpegs(buf)
            for f in frames:
                self._store(panel, f, warm)
            if len(buf) > MAX_BUF:
                buf = b""

    def current_frame(self, max_age=3.0):
        """(jpeg, frame_id) of the panel on screen, or (None, id) if there is no fresh frame.
        Frames older than ``max_age`` are never shown (a dead camera would look frozen)."""
        v = self.view
        f = self.frames.get(v) if v else None
        if f and time.time() - self.frames_ts.get(v, 0) < max_age:
            return f, self.frame_id
        return None, self.frame_id

    def frame_age(self):
        """Age of the latest frame of the panel on screen (for the WS pulse) or None."""
        if not self.view:
            return None
        return round(time.time() - self.frames_ts.get(self.view, 0), 1)

    def warm_fresh(self, panel, ttl=6):
        """True if the warm RTSP stream of ``panel`` delivered a frame within ``ttl`` s."""
        return time.time() - self.warm_ts.get(panel, 0) < ttl

    # ---- warm streams
    def _input_opts(self, analyze, probe):
        return ["-fflags", "nobuffer", "-flags", "low_delay",
                "-analyzeduration", str(analyze), "-probesize", str(probe)]

    def start_warm(self, panel):
        url = self.rtsp.get(panel)
        if not url or not self.caps.detect().present:
            return None
        cmd = ([self.caps.binary, "-nostdin", "-loglevel", "warning", "-rtsp_transport", "tcp"]
               + self._input_opts(1000000, 500000)
               # socket timeout: a silent camera makes ffmpeg exit, and the supervisor
               # restarts it; without it a dead TCP connection hangs the reader forever
               + [self.caps.timeout_opt, "10000000", "-max_delay", "500000", "-i", url,
                  # 360 px high, q 16, every frame as it comes: sized for the iPad decoder
                  "-map", "0:v", "-c:v", "mjpeg", "-q:v", "16"] + self.caps.passthrough
               + ["-vf", "scale=-2:360", "-an", "-f", "image2pipe", "pipe:1"])
        with open(self.path("ffmpeg-%s.log" % panel), "wb") as errf:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=errf, bufsize=0)
        threading.Thread(target=self._reader, args=(p, panel, True), daemon=True).start()
        log.info("camera stream %s started", panel)
        return p

    def warm_reason(self, panel):
        """Last ffmpeg error line of the warm stream, credentials masked."""
        try:
            with open(self.path("ffmpeg-%s.log" % panel), "rb") as f:
                tail = f.read()[-400:].decode("utf-8", "replace").strip().splitlines()
            return mask_url(tail[-1])[:160] if tail else ""
        except OSError:
            return ""

    async def warm_loop(self):
        """Supervisor of the warm camera streams (runs forever)."""
        loop = asyncio.get_running_loop()
        while True:
            for panel in list(self.rtsp):
                try:
                    await self._warm_check(loop, panel)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("camera stream %s: %s", panel, e.__class__.__name__)
            await asyncio.sleep(WARM_PERIOD)

    async def _warm_check(self, loop, panel):
        p = self.warm.get(panel)
        now = time.time()
        dead = p is None or p.poll() is not None
        # a live process without frames (hung TCP, camera rebooting) is as bad as a dead
        # one; a new process gets WARM_STALE seconds of grace from its start
        last = max(self.warm_ts.get(panel, 0), self.warm_start.get(panel, 0))
        stale = (not dead) and (now - last > WARM_STALE)
        if not (dead or stale):
            return
        if now < self.warm_next.get(panel, 0):
            return                                   # back-off: the camera is down
        # failure = not a single frame since the previous start
        if self.warm_ts.get(panel, 0) >= self.warm_start.get(panel, 0):
            self.warm_fail[panel] = 0
        else:
            self.warm_fail[panel] = self.warm_fail.get(panel, 0) + 1
        n = self.warm_fail[panel]
        if p is not None:
            _kill([p])
            if n < 4:
                log.info("camera stream %s %s, restarting", panel,
                         "exited" if dead else "hung (no frames for %d s)" % WARM_STALE)
        if n >= 4:
            pause = min(300, 60 * 2 ** (n - 4))      # 1, 2, 4, then 5 minutes
            self.warm_next[panel] = now + pause
            log.warning("camera stream %s: %d failures in a row (%s), next try in %d s",
                        panel, n, self.warm_reason(panel) or "no details", pause)
        self.warm[panel] = await loop.run_in_executor(None, self.start_warm, panel)
        self.warm_start[panel] = time.time()

    # ---- call / view audio
    def stop_audio(self):
        p, self.ffa = self.ffa, None
        _kill([p])

    def _sdp_file(self, video_pt=None, video_fmtp=None):
        sip = self.cfg.sip
        sdp = ("v=0\no=- 0 0 IN IP4 127.0.0.1\ns=webtab\nc=IN IP4 127.0.0.1\nt=0 0\n"
               "m=audio %d RTP/AVP 8\na=rtpmap:8 PCMA/8000\n" % sip.rtp_audio_port)
        if video_pt is not None:
            sdp += ("m=video %d RTP/AVP %s\na=rtpmap:%s H264/90000\na=fmtp:%s %s\n"
                    % (sip.rtp_video_port, video_pt, video_pt, video_pt, video_fmtp))
        path = self.path("call.sdp")
        with open(path, "w") as f:
            f.write(sdp)
        return path

    def _audio_out(self):
        return ["-c:a", "pcm_s16le", "-ar", "8000", "-ac", "1", "-f", "s16le",
                "udp://127.0.0.1:%d" % self.cfg.sip.audio_out_port]

    def _spawn(self, cmd, logname, stdout=subprocess.DEVNULL):
        with open(self.path(logname), "wb") as errf:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=errf,
                                    bufsize=0)

    def start_audio_rtsp(self, url):
        """Camera audio during a view (first audio track only; some cameras have two)."""
        self.stop_audio()
        if not self.caps.detect().present:
            return
        cmd = ([self.caps.binary, "-nostdin", "-loglevel", "warning", "-rtsp_transport", "tcp"]
               + self._input_opts(500000, 200000)
               + [self.caps.timeout_opt, "10000000", "-i", url, "-vn", "-map", "0:a:0?"]
               + self._audio_out())
        self.ffa = self._spawn(cmd, "ffmpeg-audio.log")

    def start_audio_sip(self):
        """Guest audio from the SIP call (video comes from the warm stream)."""
        self.stop_audio()
        if not self.caps.detect().present:
            return
        cmd = ([self.caps.binary, "-nostdin", "-loglevel", "warning",
                "-protocol_whitelist", "file,udp,rtp"] + self._input_opts(300000, 100000)
               + ["-i", self._sdp_file(), "-map", "0:a"] + self._audio_out())
        self.ffa = self._spawn(cmd, "ffmpeg-audio.log")

    def start_sip_decoder(self, video_pt, video_fmtp, panel):
        """Fallback when RTSP is dead: video and audio both from the SIP session."""
        self.stop_audio()
        if not self.caps.detect().present:
            return
        cmd = ([self.caps.binary, "-nostdin", "-loglevel", "warning",
                "-protocol_whitelist", "file,udp,rtp"] + self._input_opts(300000, 100000)
               + ["-i", self._sdp_file(video_pt, video_fmtp),
                  # 15 fps and 360 px high: what the iPad decoder handles comfortably
                  "-map", "0:v", "-c:v", "mjpeg", "-q:v", "16"] + self.caps.passthrough
               + ["-vf", "fps=15,scale=-2:360", "-f", "image2pipe", "pipe:1", "-map", "0:a"]
               + self._audio_out())
        self.frames.pop(panel, None)           # an old frame must not pass for live video
        p = self._spawn(cmd, "ffmpeg-sip.log", stdout=subprocess.PIPE)
        self.ffa = p
        threading.Thread(target=self._reader, args=(p, panel, False), daemon=True).start()
        log.info("SIP video fallback for %s", panel)

    # ---- what we send to the panel
    def start_dummy_video(self, ip, port, pt):
        """A live 15 fps H.264 Main stream to the panel: panels only send their own video
        once they receive ours."""
        if not port or not self.caps.detect().present or not self.caps.has_x264:
            return
        _kill([self.dummy])
        cmd = [self.caps.binary, "-nostdin", "-loglevel", "error", "-re", "-t", "900",
               "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
               "-c:v", "libx264", "-profile:v", "main", "-level", "3.0", "-pix_fmt", "yuv420p",
               "-tune", "zerolatency", "-g", "30", "-an",
               "-f", "rtp", "-payload_type", str(pt), "rtp://%s:%d" % (ip, port)]
        log.debug("our video -> %s:%d pt %s", ip, port, pt)
        self.dummy = self._spawn(cmd, "ffmpeg-dummy.log")

    def start_silence(self, ip, port):
        """Restart the PCMA silence sender towards (ip, port)."""
        self.rtp_stop[0] = True
        self.rtp_stop = [False]
        threading.Thread(target=rtp_silence_sender, args=(ip, port, self.rtp_stop),
                         daemon=True).start()

    def stop_call_media(self):
        """Stop call media (audio, SIP decoder, our video, silence). Warm streams stay."""
        self.rtp_stop[0] = True
        procs = [self.ffa, self.dummy]
        self.ffa = None
        self.dummy = None
        _kill(procs)

    def stop_all(self):
        self.stop_call_media()
        procs = list(self.warm.values())
        self.warm = {}
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=3)
            except Exception:
                pass
