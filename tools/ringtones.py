#!/usr/bin/env python3
"""Synthesize the station ringtones and embed them into the page.

    python3 tools/ringtones.py           # write static/ring/*.wav and update static/index.html
    python3 tools/ringtones.py --check   # exit 1 if the files or the embedded copies are stale

Four short loopable tones, 8 kHz / 16-bit / mono PCM WAV (about 26-32 KB each). They are
designed for different hearing profiles; pick one with ``[ui] ringtone`` in webtab.ini:

``low``    Two-note chime: 330 Hz then 262 Hz (E4 -> C4), soft bell decay, a weak 2nd
           harmonic only (no energy above ~700 Hz). For high-frequency hearing loss,
           where bright tones are the first to disappear.
``mid``    Three-note descending chime 784 / 659 / 523 Hz (G5 E5 C5). Fundamentals stay
           within 500-1000 Hz, where small tablet speakers are most efficient.
``high``   Classic bright electronic trill: 1800 / 2250 Hz alternating at 20 Hz, two
           bursts per cycle. Cuts through room noise for typical hearing.
``sweep``  Three pulses, each an exponential rising sweep 300 Hz -> 3 kHz. Broadband: every
           listener catches the part of each pulse that falls into their audible range.

The page plays the tone through an ``<audio>`` element (the iPad mute switch silences Web
Audio but not media playback), so the WAV is embedded as a data URI and works offline.
Low-level dither (about -84 dBFS) replaces digital silence; it is inaudible and keeps the
base64 text free of long runs of identical characters.

Only the Python standard library is used. Output is deterministic.
"""
import base64
import io
import math
import os
import re
import struct
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "intercom_webtab", "static")
RING_DIR = os.path.join(STATIC, "ring")
PAGE = os.path.join(STATIC, "index.html")

RATE = 8000
PEAK = 0.89                       # about -1 dBFS
NAMES = ("low", "mid", "high", "sweep")
WRAP = 100                        # base64 line length inside the page


def _silence(seconds):
    return [0.0] * int(round(seconds * RATE))


def _bell(buf, start, freq, tau, length, partials=((1, 1.0),), attack=0.006):
    """Add a decaying bell-like note: sum of harmonic partials, exponential decay."""
    i0 = int(round(start * RATE))
    n = int(round(length * RATE))
    a_n = max(1, int(attack * RATE))
    rel = int(0.04 * RATE)
    for i in range(n):
        j = i0 + i
        if j >= len(buf):
            break
        t = i / RATE
        env = math.exp(-t / tau)
        if i < a_n:
            env *= i / a_n
        if n - i < rel:
            env *= (n - i) / rel
        s = 0.0
        for k, g in partials:
            s += g * math.sin(2 * math.pi * freq * k * t)
        buf[j] += s * env


def _trill(buf, start, length, f1, f2, rate_hz):
    """Phase-continuous alternation between two frequencies (no clicks)."""
    i0 = int(round(start * RATE))
    n = int(round(length * RATE))
    ramp = int(0.006 * RATE)
    half = RATE / (2.0 * rate_hz)
    ph = 0.0
    for i in range(n):
        f = f1 if int(i / half) % 2 == 0 else f2
        ph += 2 * math.pi * f / RATE
        env = min(1.0, i / ramp, (n - i) / ramp)
        buf[i0 + i] += math.sin(ph) * env


def _sweep(buf, start, length, f0, f1):
    """Exponential (log-frequency) rising sweep with short fades."""
    i0 = int(round(start * RATE))
    n = int(round(length * RATE))
    ramp = int(0.012 * RATE)
    k = math.log(f1 / f0)
    ph = 0.0
    for i in range(n):
        f = f0 * math.exp(k * i / n)
        ph += 2 * math.pi * f / RATE
        env = min(1.0, i / ramp, (n - i) / ramp)
        buf[i0 + i] += math.sin(ph) * env


def tone_low():
    buf = _silence(2.0)
    p = ((1, 1.0), (2, 0.18))
    _bell(buf, 0.00, 330.0, 0.45, 1.20, p)
    _bell(buf, 0.60, 262.0, 0.50, 1.35, p)
    return buf


def tone_mid():
    buf = _silence(1.6)
    p = ((1, 1.0), (2, 0.12))
    _bell(buf, 0.00, 784.0, 0.30, 0.80, p)
    _bell(buf, 0.30, 659.0, 0.30, 0.80, p)
    _bell(buf, 0.60, 523.0, 0.38, 0.95, p)
    return buf


def tone_high():
    buf = _silence(1.6)
    _trill(buf, 0.00, 0.40, 1800.0, 2250.0, 20.0)
    _trill(buf, 0.60, 0.40, 1800.0, 2250.0, 20.0)
    return buf


def tone_sweep():
    buf = _silence(1.6)
    for s in (0.0, 0.38, 0.76):
        _sweep(buf, s, 0.30, 300.0, 3000.0)
    return buf


TONES = {"low": tone_low, "mid": tone_mid, "high": tone_high, "sweep": tone_sweep}


def _lcg(seed):
    x = seed
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x


def render(name):
    """Return WAV bytes for one tone."""
    buf = TONES[name]()
    peak = max(abs(v) for v in buf) or 1.0
    g = PEAK * 32767.0 / peak
    rnd = _lcg(NAMES.index(name) + 7)
    pcm = bytearray()
    for v in buf:
        d = (next(rnd) % 5) - 2                     # +-2 LSB dither, about -84 dBFS
        s = int(round(v * g)) + d
        s = -32768 if s < -32768 else 32767 if s > 32767 else s
        pcm += struct.pack("<h", s)
    out = io.BytesIO()
    w = wave.open(out, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(bytes(pcm))
    w.close()
    return out.getvalue()


def b64_block(data):
    s = base64.b64encode(data).decode("ascii")
    return "\n".join(s[i:i + WRAP] for i in range(0, len(s), WRAP))


def block_re(name):
    return re.compile(r'(<script type="text/plain" id="ring-%s">\n)(.*?)(\n</script>)' % name, re.S)


def embed(html, name, data):
    rx = block_re(name)
    if not rx.search(html):
        raise SystemExit("index.html has no <script type=\"text/plain\" id=\"ring-%s\"> block" % name)
    return rx.sub(lambda m: m.group(1) + b64_block(data) + m.group(3), html, count=1)


def main(argv):
    check = "--check" in argv
    stale = []
    with open(PAGE, encoding="utf-8") as f:
        html = f.read()
    new_html = html
    for name in NAMES:
        data = render(name)
        path = os.path.join(RING_DIR, name + ".wav")
        old = open(path, "rb").read() if os.path.isfile(path) else None
        if old != data:
            stale.append(path)
            if not check:
                os.makedirs(RING_DIR, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
        new_html = embed(new_html, name, data)
        print("%-6s %6d bytes  %.2f s" % (name, len(data), (len(data) - 44) / 2.0 / RATE))
    if new_html != html:
        stale.append(PAGE)
        if not check:
            with open(PAGE, "w", encoding="utf-8") as f:
                f.write(new_html)
    if check and stale:
        print("stale: " + ", ".join(os.path.relpath(p, ROOT) for p in stale))
        return 1
    if not check:
        print("updated: " + (", ".join(os.path.relpath(p, ROOT) for p in stale) or "nothing"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
