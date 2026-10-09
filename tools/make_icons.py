#!/usr/bin/env python3
"""Render the Home-Screen icons from static/icon.svg with Pillow (no SVG library needed).

    python3 tools/make_icons.py            # write static/icons/apple-touch-icon-{180,152,120}.png
    python3 tools/make_icons.py --check    # exit 1 if the PNGs differ from a fresh render

The icon is simple geometry, so this script draws it with Pillow primitives straight from the
SVG: <rect>, <circle> (with translate/rotate transforms) and <path> with the commands
M L H V C S Z (absolute and relative). Curves are flattened, the image is drawn at 8x and scaled
down for anti-aliasing. The square is full bleed: iOS applies its own rounded mask.
The PNGs carry no text chunks (no author, software or date metadata).

Icon artwork: static/icon.svg, CC BY-SA 4.0 (see the <metadata> block in the file).
"""
import io
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "intercom_webtab", "static")
SVG = os.path.join(STATIC, "icon.svg")
OUT = os.path.join(STATIC, "icons")
SIZES = (180, 152, 120)
SS = 8                                    # supersampling factor
NS = "{http://www.w3.org/2000/svg}"
NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _color(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _matrix(transform):
    """2x3 affine matrix [a, b, c, d, e, f] from translate()/rotate()/scale()/matrix()."""
    m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]

    def mul(p, q):
        a, b, c, d, e, f = p
        A, B, C, D, E, F = q
        return [a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D,
                a * E + c * F + e, b * E + d * F + f]

    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", transform or ""):
        v = [float(x) for x in NUM.findall(args)]
        if name == "translate":
            q = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0]
        elif name == "rotate":
            r = math.radians(v[0])
            q = [math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0]
            if len(v) == 3:
                q = mul(mul([1, 0, 0, 1, v[1], v[2]], q), [1, 0, 0, 1, -v[1], -v[2]])
        elif name == "scale":
            q = [v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0]
        elif name == "matrix":
            q = v[:6]
        else:
            raise ValueError("unsupported transform: " + name)
        m = mul(m, q)
    return m


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _cubic(p0, p1, p2, p3, steps=24):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def path_polygons(d):
    """Flatten an SVG path (M L H V C S Z, both cases) into a list of point lists."""
    tokens = re.findall(r"[MLHVCSZmlhvcsz]|" + NUM.pattern, d)
    polys, cur, i = [], [], 0
    x = y = sx = sy = 0.0
    last_c2 = None
    cmd = None
    while i < len(tokens):
        if re.match(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    polys.append(cur)
                cur = []
                x, y = sx, sy
                last_c2 = None
                continue
        if cmd is None:
            raise ValueError("path data must start with a command")
        rel = cmd.islower()
        c = cmd.upper()
        need = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4}[c]
        v = [float(t) for t in tokens[i:i + need]]
        i += need
        if c == "M":
            if cur:
                polys.append(cur)
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            sx, sy = x, y
            cur = [(x, y)]
            cmd = "l" if rel else "L"            # implicit line-to after a move-to
            last_c2 = None
        elif c == "L":
            x, y = (x + v[0], y + v[1]) if rel else (v[0], v[1])
            cur.append((x, y))
            last_c2 = None
        elif c == "H":
            x = x + v[0] if rel else v[0]
            cur.append((x, y))
            last_c2 = None
        elif c == "V":
            y = y + v[0] if rel else v[0]
            cur.append((x, y))
            last_c2 = None
        elif c in "CS":
            if c == "C":
                pts = [(v[k] + (x if rel else 0), v[k + 1] + (y if rel else 0)) for k in (0, 2, 4)]
                c1, c2, end = pts
            else:
                c1 = (2 * x - last_c2[0], 2 * y - last_c2[1]) if last_c2 else (x, y)
                c2 = (v[0] + (x if rel else 0), v[1] + (y if rel else 0))
                end = (v[2] + (x if rel else 0), v[3] + (y if rel else 0))
            cur.extend(_cubic((x, y), c1, c2, end))
            x, y = end
            last_c2 = c2
    if cur:
        polys.append(cur)
    return polys


def render(size):
    root = ET.parse(SVG).getroot()
    vb = [float(v) for v in NUM.findall(root.get("viewBox"))]
    big = size * SS
    k = big / vb[2]
    img = Image.new("RGB", (big, big), (0, 0, 0))
    dr = ImageDraw.Draw(img)

    def walk(node, m):
        for ch in node:
            tag = ch.tag.replace(NS, "")
            mm = m
            if ch.get("transform"):
                t = _matrix(ch.get("transform"))
                mm = [m[0] * t[0] + m[2] * t[1], m[1] * t[0] + m[3] * t[1],
                      m[0] * t[2] + m[2] * t[3], m[1] * t[2] + m[3] * t[3],
                      m[0] * t[4] + m[2] * t[5] + m[4], m[1] * t[4] + m[3] * t[5] + m[5]]
            fill = ch.get("fill")
            if tag == "g":
                walk(ch, mm)
            elif tag == "rect" and fill:
                # the background square: full bleed, the stroke has the same colour
                dr.rectangle([0, 0, big, big], fill=_color(fill))
            elif tag == "circle" and fill:
                cx, cy = _apply(mm, float(ch.get("cx")), float(ch.get("cy")))
                r = float(ch.get("r")) * math.hypot(mm[0], mm[1])
                cx, cy, r = (cx - vb[0]) * k, (cy - vb[1]) * k, r * k
                dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_color(fill))
            elif tag == "path" and fill:
                for poly in path_polygons(ch.get("d")):
                    pts = [_apply(mm, px, py) for px, py in poly]
                    dr.polygon([((px - vb[0]) * k, (py - vb[1]) * k) for px, py in pts], fill=_color(fill))

    walk(root, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    return img.resize((size, size), Image.LANCZOS)


def png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)          # no pnginfo: no tEXt/iTXt/zTXt chunks
    return buf.getvalue()


def main(argv):
    check = "--check" in argv
    stale = []
    os.makedirs(OUT, exist_ok=True)
    for s in SIZES:
        path = os.path.join(OUT, "apple-touch-icon-%d.png" % s)
        data = png_bytes(render(s))
        old = open(path, "rb").read() if os.path.isfile(path) else None
        if old != data:
            stale.append(path)
            if not check:
                with open(path, "wb") as f:
                    f.write(data)
        print("%s %d bytes" % (os.path.relpath(path, ROOT), len(data)))
    if check and stale:
        print("stale: " + ", ".join(os.path.relpath(p, ROOT) for p in stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
