#!/usr/bin/env python3
"""Contact sheets of the weather photos for review: every photo of the catalogue
(intercom_webtab/weather/photo_ids.json) is downloaded through the Openverse proxy, processed
exactly like on the station (weather.photo.process) and placed as a 512x374 thumbnail with its
group and id.

    python3 tools/photo_sheet.py OUT_DIR [--cache DIR] [--group NAME]

OUT_DIR/sheet-<group>.jpg -- one sheet per group, OUT_DIR/all.jpg -- all groups, one per row.
--cache keeps the downloaded originals so a second run does not spend the Openverse quota.
At the end: processing time per photo and the sizes of bg/panel. Uses the network.
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from intercom_webtab.weather import photo as wp  # noqa: E402

TW, TH = 512, 374          # thumbnail: half of the 1024x748 frame
CAP = 26                   # caption strip
COLS = 5
FONTS = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def font(size):
    for f in FONTS:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def fetch(p, cache):
    """Original picture: from the cache or through the proxy (as the station does), then the
    original URL."""
    f = os.path.join(cache, p["id"] + ".bin") if cache else None
    if f and os.path.exists(f) and os.path.getsize(f):
        with open(f, "rb") as fh:
            return fh.read(), "cache"
    try:
        data, how = wp.http_get(wp.PROXY % p["id"]), "proxy"
    except Exception as e:
        print("  proxy failed for %s: %s -- using the original URL" % (p["id"], e))
        data, how = wp.http_get(p["url"]), "original"
    if f:
        with open(f, "wb") as fh:
            fh.write(data)
    return data, how


def tile(bg, caption, fnt):
    t = Image.new("RGB", (TW, TH + CAP), (18, 18, 18))
    t.paste(Image.open(io.BytesIO(bg)).resize((TW, TH), Image.LANCZOS), (0, 0))
    ImageDraw.Draw(t).text((6, TH + 5), caption, fill=(235, 220, 120), font=fnt)
    return t


def grid(tiles, cols):
    rows = (len(tiles) + cols - 1) // cols
    sh = Image.new("RGB", (cols * TW + (cols - 1) * 4, rows * (TH + CAP) + (rows - 1) * 4), (40, 40, 40))
    for i, t in enumerate(tiles):
        sh.paste(t, ((i % cols) * (TW + 4), (i // cols) * (TH + CAP + 4)))
    return sh


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    out = argv[0]
    cache = argv[argv.index("--cache") + 1] if "--cache" in argv else None
    only = argv[argv.index("--group") + 1] if "--group" in argv else None
    os.makedirs(out, exist_ok=True)
    if cache:
        os.makedirs(cache, exist_ok=True)
    cat = wp.load_catalog()
    fnt = font(15)
    wp.mask()                                   # the mask is computed once, outside the timing
    rows, times, sizes, fails = [], [], [], []
    for g in wp.GROUP_LABELS:
        if only and g != only:
            continue
        tiles = []
        for p in cat.get(g, []):
            try:
                data, how = fetch(p, cache)
                fmt = Image.open(io.BytesIO(data)).format
                w, h = Image.open(io.BytesIO(data)).size
                t0 = time.time()
                item = wp.process(data)
                dt = time.time() - t0
            except Exception as e:
                fails.append((g, p["id"], str(e)))
                print("  FAILED %s %s: %s" % (g, p["id"], e))
                continue
            times.append(dt)
            sizes.append((len(item["bg"]), len(item["panel"])))
            print("%-16s %s %-8s %4s %5dx%-5d %.2f s  bg %3d KB  panel %2d KB  tone %s" % (
                g, p["id"], how, fmt, w, h, dt, len(item["bg"]) // 1024, len(item["panel"]) // 1024,
                item["tone"]))
            tiles.append(tile(item["bg"], "%s / %s / %s" % (wp.GROUP_LABELS[g], g, p["id"][:8]), fnt))
        if tiles:
            grid(tiles, min(COLS, len(tiles))).save(os.path.join(out, "sheet-%s.jpg" % g), quality=85)
            rows.append(tiles)
    if rows and not only:
        allt = []
        for r in rows:                          # every group is a row of its own
            allt += r + [Image.new("RGB", (TW, TH + CAP), (40, 40, 40))] * (COLS - len(r))
        grid(allt, COLS).save(os.path.join(out, "all.jpg"), quality=80)
    if times:
        ts = sorted(times)
        bg = sorted(s[0] for s in sizes)
        pn = sorted(s[1] for s in sizes)
        print("photos %d, processing: median %.2f s, max %.2f s; bg median %d KB (%d-%d), "
              "panel median %d KB (%d-%d)" % (
                  len(ts), ts[len(ts) // 2], ts[-1], bg[len(bg) // 2] // 1024, bg[0] // 1024,
                  bg[-1] // 1024, pn[len(pn) // 2] // 1024, pn[0] // 1024, pn[-1] // 1024))
    if fails:
        print("failures: %d" % len(fails))
        for f in fails:
            print("  ", *f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
