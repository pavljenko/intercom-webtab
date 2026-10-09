#!/usr/bin/env python3
"""Leak check for Intercom WebTab.

Scans repository files (or the staged index) for things that must never be published:

* any Cyrillic text (the project is English-only, so leftovers are a red flag);
* public IPv4 addresses (loopback, unspecified, private RFC 1918 and documentation
  RFC 5737 ranges are allowed);
* 32-character hex strings (typical access tokens);
* URLs with embedded credentials (``rtsp://user:pass@host``);
* e-mail addresses other than no-reply / example domains;
* EXIF blocks in JPEG files;
* optional private denylist: a file with one case-insensitive substring per line,
  passed via ``IWT_DENYLIST`` (kept outside the repository). ``IWT_ALLOWLIST`` may
  name a file of ``<glob>\\t<substring>`` pairs that are allowed in specific files.

Denylist hits are reported by line number only, never by value.

Usage::

    python3 tools/leakcheck.py            # all tracked and untracked, non-ignored files
    python3 tools/leakcheck.py --staged   # what is about to be committed
    python3 tools/leakcheck.py PATH...    # specific files
"""
import fnmatch
import ipaddress
import os
import re
import subprocess
import sys

CYRILLIC = re.compile("[\u0400-\u04ff]")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
HEX32 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])")
CRED_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(noreply|no-reply)@|@(example\.(com|org|net)|[a-z0-9.-]*\.invalid)$|"
                      r"@users\.noreply\.github\.com$", re.I)
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wav", ".pdf", ".woff", ".woff2"}
SELF = "tools/leakcheck.py"


def _ip_ok(text):
    try:
        ip = ipaddress.IPv4Address(text)
    except ValueError:
        return True                     # not an address (e.g. a version number)
    if ip.is_loopback or ip.is_private or ip.is_unspecified or ip.is_multicast:
        return True
    for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "255.255.255.255/32"):
        if ip in ipaddress.IPv4Network(net):
            return True
    return False


def _load_lines(env):
    path = os.environ.get(env)
    if not path:
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\n")
            if ln.strip() and not ln.lstrip().startswith("#"):
                out.append(ln)
    return out


def _files(args):
    if args and args[0] == "--staged":
        names = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                               capture_output=True, text=True, check=True).stdout.split("\n")
        return [(n, lambda n=n: subprocess.run(["git", "show", ":" + n], capture_output=True,
                                                check=True).stdout) for n in names if n]
    if args:
        return [(n, lambda n=n: open(n, "rb").read()) for n in args]
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split("\n")
    return [(n, lambda n=n: open(n, "rb").read()) for n in names if n and os.path.isfile(n)]


def check(name, data, deny, allow):
    problems = []
    low_name = name.lower()
    allowed = {s.lower() for g, s in allow if fnmatch.fnmatch(name, g)}
    if CYRILLIC.search(name):
        problems.append((name, 0, "cyrillic in file name"))
    for i, word in enumerate(deny, 1):
        if word.lower() in low_name and word.lower() not in allowed:
            problems.append((name, 0, "denylist #%d in file name" % i))
    ext = os.path.splitext(name)[1].lower()
    if ext in BINARY_EXT:
        blob = data.decode("latin-1").lower()
        for i, word in enumerate(deny, 1):
            if word.lower() in blob and word.lower() not in allowed:
                problems.append((name, 0, "denylist #%d inside binary" % i))
        if ext in (".jpg", ".jpeg") and b"Exif" in data[:4096]:
            problems.append((name, 0, "JPEG carries EXIF metadata"))
        return problems
    text = data.decode("utf-8", errors="replace")
    for n, line in enumerate(text.split("\n"), 1):
        if CYRILLIC.search(line):
            problems.append((name, n, "cyrillic text"))
        if name != SELF:
            for m in IPV4.finditer(line):
                if not _ip_ok(m.group(1)):
                    problems.append((name, n, "public IPv4 address"))
            if HEX32.search(line):
                problems.append((name, n, "32-hex string (token?)"))
            if CRED_URL.search(line):
                problems.append((name, n, "URL with credentials"))
            for m in EMAIL.finditer(line):
                if not EMAIL_OK.search(m.group(0)):
                    problems.append((name, n, "e-mail address"))
        low = line.lower()
        for i, word in enumerate(deny, 1):
            if word.lower() in low and word.lower() not in allowed:
                problems.append((name, n, "denylist #%d" % i))
    return problems


def main(argv):
    deny = _load_lines("IWT_DENYLIST")
    allow = [tuple(ln.split("\t", 1)) for ln in _load_lines("IWT_ALLOWLIST") if "\t" in ln]
    problems = []
    for name, read in _files(argv):
        try:
            problems.extend(check(name, read(), deny, allow))
        except (OSError, subprocess.CalledProcessError) as e:
            problems.append((name, 0, "unreadable: %s" % e.__class__.__name__))
    for name, n, what in problems:
        print("%s:%s: %s" % (name, n, what))
    note = "" if deny else " (no private denylist: set IWT_DENYLIST)"
    print("leakcheck: %d problem(s)%s" % (len(problems), note), file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
