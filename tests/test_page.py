"""Static checks of the station page (intercom_webtab/static).

    python3 -m unittest tests/test_page.py

The page runs on iOS 9 Safari, so every executable <script> must be strict ES5 and must not
reference undefined globals. Those two checks need Node.js (npx downloads acorn and eslint on
first use); they are skipped when node is not installed or the packages cannot be fetched.
"""
import base64
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "intercom_webtab", "static")
PAGE = os.path.join(STATIC, "index.html")
TOOLS = os.path.join(ROOT, "tools")
PAGE_BUDGET = 250 * 1024
CYRILLIC = re.compile("[%s-%s]" % (chr(0x400), chr(0x4FF)))
RINGTONES = ("low", "mid", "high", "sweep")
SCRIPT = re.compile(r"<script([^>]*)>(.*?)</script>", re.S)
NPM_FAIL = ("npm ERR", "npm error", "ENOTFOUND", "EAI_AGAIN", "ECONNREFUSED", "ETIMEDOUT")
# APIs newer than iOS 9 Safari that acorn --ecma5 cannot see (they are plain calls)
LATE_API = re.compile(r"\.includes\(|Object\.values\(|Object\.entries\(|\.padStart\(|\.padEnd\(|"
                      r"\bfetch\(|\basync\s+function\b|\bawait\b|\.classList\b")


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def page():
    return read(PAGE)


def scripts(html):
    """(attributes, body) of every <script> in the page."""
    return SCRIPT.findall(html)


def executable(html):
    out = []
    for attrs, body in scripts(html):
        m = re.search(r'type="([^"]+)"', attrs)
        if not m or m.group(1) in ("text/javascript", "application/javascript"):
            out.append(body)
    return out


def load_tool(name):
    spec = importlib.util.spec_from_file_location("iwt_" + name, os.path.join(TOOLS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def npx(args, cwd):
    """Run npx; returns (returncode, output) or raises SkipTest when npm cannot fetch."""
    if not shutil.which("node") or not shutil.which("npx"):
        raise unittest.SkipTest("node is not installed")
    try:
        r = subprocess.run(["npx", "-y"] + args, cwd=cwd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise unittest.SkipTest("npx timed out")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode and any(s in out for s in NPM_FAIL):
        raise unittest.SkipTest("npx could not fetch %s" % args[0])
    return r.returncode, out


class PageText(unittest.TestCase):
    def test_size_budget(self):
        self.assertLess(os.path.getsize(PAGE), PAGE_BUDGET, "index.html is over the 250 KB budget")

    def test_no_cyrillic(self):
        names = [os.path.join(TOOLS, n) for n in ("preview_server.py", "make_icons.py", "ringtones.py")]
        names.append(os.path.abspath(__file__))
        for base, _dirs, files in os.walk(STATIC):
            for n in files:
                if os.path.splitext(n)[1] in (".html", ".js", ".json", ".svg", ".txt"):
                    names.append(os.path.join(base, n))
        for path in names:
            for i, line in enumerate(read(path).split("\n"), 1):
                self.assertIsNone(CYRILLIC.search(line), "%s:%d has Cyrillic text" % (os.path.relpath(path, ROOT), i))
            self.assertIsNone(CYRILLIC.search(path), path)

    def test_language_and_placeholders(self):
        html = page()
        self.assertIn('<html lang="en">', html)
        self.assertEqual(html.count("__TOKEN__"), 1)
        self.assertEqual(html.count("__IWT_CONFIG__"), 1)
        self.assertIn('<script type="application/json" id="iwtConfig">__IWT_CONFIG__</script>', html)
        vers = [ln for ln in html.split("\n") if "var VER =" in ln]
        self.assertTrue(vers and re.match(r'^var VER = "[^"]+";$', vers[0]), "first VER line must be var VER = \"...\";")

    def test_dots_copy(self):
        m = re.search(r'<script id="dotsJs">\n(.*?)</script>', page(), re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).rstrip("\n"), read(os.path.join(STATIC, "dots.js")).rstrip("\n"),
                         "copy static/dots.js into <script id=\"dotsJs\"> in full")

    def test_metric_tables(self):
        """Every side-bearing table lists exactly one value per glyph."""
        body = page()
        tables = re.findall(r'\["([^"]+)",\s*\[([^\]]+)\]\]', body)
        self.assertGreaterEqual(len(tables), 6)
        for chars, vals in tables:
            self.assertEqual(len(chars), len(vals.split(",")), chars)

    def test_no_late_apis(self):
        for body in executable(page()) + [read(os.path.join(STATIC, "dots.js"))]:
            m = LATE_API.search(body)
            self.assertIsNone(m, "API not available on iOS 9: %s" % (m.group(0) if m else ""))


class Assets(unittest.TestCase):
    def test_ringtones_embedded_and_fresh(self):
        html = page()
        rt = load_tool("ringtones")
        for name in RINGTONES:
            path = os.path.join(STATIC, "ring", name + ".wav")
            data = read(path, "rb")
            self.assertEqual(data, rt.render(name), "static/ring/%s.wav is stale: run tools/ringtones.py" % name)
            m = re.search(r'<script type="text/plain" id="ring-%s">\n(.*?)\n</script>' % name, html, re.S)
            self.assertIsNotNone(m, name)
            self.assertEqual(re.sub(r"\s+", "", m.group(1)), base64.b64encode(data).decode("ascii"),
                             "embedded ring-%s is stale: run tools/ringtones.py" % name)
            w = wave.open(path)
            self.assertEqual((w.getnchannels(), w.getsampwidth()), (1, 2))
            self.assertIn(w.getframerate(), (8000, 16000))
            self.assertLessEqual(w.getnframes() / w.getframerate(), 2.5)

    def test_touch_icons(self):
        for size in (180, 152, 120):
            path = os.path.join(STATIC, "icons", "apple-touch-icon-%d.png" % size)
            data = read(path, "rb")
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            pos, kinds = 8, []
            while pos < len(data):
                n, kind = struct.unpack(">I4s", data[pos:pos + 8])
                kinds.append(kind)
                if kind == b"IHDR":
                    self.assertEqual(struct.unpack(">II", data[pos + 8:pos + 16]), (size, size))
                pos += 12 + n
            for bad in (b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME"):
                self.assertNotIn(bad, kinds, "%s carries a %s chunk" % (path, bad.decode()))
            self.assertIn('href="/icons/apple-touch-icon-%d.png"' % size, page())

    def test_icon_svg_license(self):
        svg = read(os.path.join(STATIC, "icon.svg"))
        self.assertNotIn("data-name", svg)
        self.assertIn("<dc:rights>CC BY-SA 4.0</dc:rights>", svg)
        self.assertIn("https://creativecommons.org/licenses/by-sa/4.0/", svg)

    def test_mocks(self):
        dots = read(os.path.join(STATIC, "dots.js"))
        scenes = set(re.findall(r'^\s+"([a-z-]+)": function', dots, re.M))
        names = sorted(n for n in os.listdir(os.path.join(STATIC, "mock")) if n.endswith(".json"))
        self.assertGreaterEqual(len(names), 10)
        for n in names:
            d = json.loads(read(os.path.join(STATIC, "mock", n)))
            self.assertEqual(d["v"], 3, n)
            self.assertIn(d["now"]["scene"], scenes, n)
            self.assertEqual(len(d["widgets"]), 6, n)
            self.assertEqual(len(d["rain"]["cols"]), 9, n)
            self.assertTrue(d.get("attribution"), n)
            for w in d["widgets"]:
                self.assertIn(w["k"], ("wind", "air", "uv", "pressure", "radiation", "sun", "humidity"), n)
            for s in (d["now"]["utciTxt"], d["now"]["tTxt"]):
                self.assertNotIn("+", s, n)
                self.assertNotIn("-", s, n)                 # minus is U+2212


class Es5(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="iwt-es5-")
        cls.files = []
        for i, body in enumerate(executable(page())):
            path = os.path.join(cls.tmp, "page_%d.js" % i)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            cls.files.append(path)
        cls.all = os.path.join(cls.tmp, "page_all.js")
        with open(cls.all, "w", encoding="utf-8") as f:            # one global scope, as in the browser
            f.write("\n;\n".join(executable(page())))
        shutil.copy(os.path.join(STATIC, "dots.js"), os.path.join(cls.tmp, "dots.js"))
        cls.files.append(os.path.join(cls.tmp, "dots.js"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_es5_syntax(self):
        self.assertGreaterEqual(len(self.files), 4)
        for path in self.files:
            rc, out = npx(["acorn@8.11.3", "--ecma5", "--silent", path], self.tmp)
            self.assertEqual(rc, 0, "%s is not ES5: %s" % (os.path.basename(path), out.strip()[:500]))

    def test_no_undefined_names(self):
        rc, out = npx(["eslint@8.57.0", "--no-eslintrc", "--env", "browser", "--parser-options=ecmaVersion:5",
                       "--rule", "no-undef:error", self.all], self.tmp)
        self.assertEqual(rc, 0, out.strip()[:2000])


if __name__ == "__main__":
    unittest.main()
