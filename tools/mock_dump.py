#!/usr/bin/env python3
"""Example GET /api/panel answers (panel v3) for page work and the preview server.

    python3 tools/mock_dump.py              # write the files and print a summary
    python3 tools/mock_dump.py --out DIR    # write into DIR instead
    python3 tools/mock_dump.py --dry        # summary only

Default output folder: intercom_webtab/static/mock/ when that folder exists, otherwise
tests/fx/panels/. Files:

    live.json            build_panel on the Greenwich Open-Meteo snapshot (tests/fx)
    live-imperial.json   the same in degF, mph and inHg
    scen-01.json ...     the 15 reference situations (builders from tests/test_brain.py)
    <scene>.json         one panel for each of the 39 scene icons (rain.json, fog.json, ...)

Every file is one panel (the page tests read them all); each also carries "_title", a short
description ignored by the page. ``photo`` is filled the way the
server does it -- keys picked by weather.photo.PhotoStore from the real catalogue -- but
nothing is downloaded: the tone is a neutral placeholder and the preview server draws the
pictures itself. No network is used.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from datetime import datetime  # noqa: E402

from intercom_webtab.weather import Settings, brain as wb, phrases as ph  # noqa: E402
from intercom_webtab.weather import photo as wp  # noqa: E402
import test_brain as T  # noqa: E402  synthetic answer builders

PLACEHOLDER_TONE = "#16191c"


def with_photo(p):
    """Fill ``photo`` like the server: keys from the catalogue, URLs under /ph/."""
    store = wp.PhotoStore(loader=lambda url: b"", tz=T.TZ)
    day = datetime.fromtimestamp(p["ts"], T.TZ).date()
    key, ahead = store.choose(p["now"], day)
    if key:
        p["photo"] = {"key": key, "bg": "/ph/%s/bg.jpg" % key, "panel": "/ph/%s/panel.jpg" % key,
                      "tone": PLACEHOLDER_TONE,
                      "next": [{"bg": "/ph/%s/bg.jpg" % k, "panel": "/ph/%s/panel.jpg" % k}
                               for k in ahead if k != key]}
    return p


def out_dir(argv):
    if "--out" in argv:
        return argv[argv.index("--out") + 1]
    static_mock = os.path.join(ROOT, "intercom_webtab", "static", "mock")
    if os.path.isdir(static_mock):
        return static_mock
    return os.path.join(ROOT, "tests", "fx", "panels")


def panels():
    """[(file name, title, panel)] in a stable order."""
    out = []
    fx = T.load_fx()
    if fx:
        now, p = T.fx_panel(fx)
        out.append(("live", "Greenwich snapshot, 2026-10-09 18:45", p))
        imperial = Settings(panels=T.PANELS, temp="F", wind="mph", press="inHg")
        now, p = T.fx_panel(fx, cfg=imperial)
        out.append(("live-imperial", "Greenwich snapshot in degF, mph, inHg", p))
    for n in range(1, 16):
        now, kw = T.scen(n)
        out.append(("scen-%02d" % n, "%d: %s" % (n, T.SCEN_TITLES[n]), T.panel(now, **kw)))
    for name in wb.SCENE_NAMES:
        kw = dict(T.Scenes.CASES[name])
        now = kw.pop("now", T.T14)
        nofc = kw.pop("nofc", False)
        raw, src = T.make(now, **kw)
        if nofc:
            raw["fc"] = None
        p = wb.build_panel(raw, src, {}, now, T.CFG)
        out.append((name, "scene %s: %s" % (name, ph.SCENE_TITLES[name]), p))
    return out


def main(argv):
    dry = "--dry" in argv
    target = out_dir(argv)
    if not dry:
        os.makedirs(target, exist_ok=True)
    count = 0
    for name, title, p in panels():
        p = with_photo(dict(p, _title=title))
        count += 1
        if not dry:
            with open(os.path.join(target, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=1)
                f.write("\n")
        n = p["now"]
        line = " | ".join(s["text"] for s in p["status"])
        print("%-30s %-20s %-14s %5s/%-5s %s" % (name + ".json", n["scene"], n["word"], n["utciTxt"],
                                                n["tTxt"], line))
    if not dry:
        print("%d panels written to %s" % (count, os.path.relpath(target, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
