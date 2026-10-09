# Development

## Ground rules

- **English only**, everywhere: code, comments, docs, UI strings, test data. The leak
  check rejects any Cyrillic text.
- **No personal or provider data.** Use neutral examples:
  - location: Greenwich, `51.4779, -0.0015`, `Europe/London`;
  - IP addresses from `192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24`;
  - SIP numbers such as `100` (own) and `201` / `202` (panels);
  - host names such as `camera-gate.local` or `sip.provider.example`.
- **Python 3.9+**, using only the standard library, aiohttp and Pillow.
- **The page is strict ES5** for iOS 9 Safari (see [Page rules](#page-rules)).

## Layout

```
intercom_webtab/        the package (see architecture.md)
  static/               index.html, dots.js, icons, ringtones, mock panels
  weather/              sources, brain, phrases, units, utci, photo (+ photo_ids.json)
  radiation/            providers and automatic choice
config/                 example webtab.ini and secrets.ini
deploy/                 install.sh, render.py, Asterisk / nftables templates, systemd units
tools/                  simulators, tests, generators, leak check, git hook
tests/                  unit tests; tests/fx/ holds recorded API answers
docs/                   this documentation
```

## Tests

```sh
pip install aiohttp pillow
python3 -m unittest discover -s tests       # unit tests, about 280, under a minute
python3 tools/e2e.py                        # end-to-end test of the real server
```

The unit tests need no network. Recorded answers in `tests/fx/` stand in for Open-Meteo
and the radiation networks. `tests/test_page.py` checks the page:

- the 250 KB size budget;
- no Cyrillic;
- the placeholders;
- the embedded `dots.js` and ringtones match their sources;
- icons without metadata;
- the mock panels;
- if Node.js is installed, the ES5 syntax check with `acorn --ecma5` and undefined
  globals with `eslint no-undef`. `npx` fetches both on first use. Without Node, these
  tests are skipped.

### End-to-end test

`tools/e2e.py` starts the real server (`python3 -m intercom_webtab`) with a temporary
configuration on free localhost ports, a **fake AMI** (`tools/fake_ami.py`), and the
**panel simulator** (`tools/panelsim.py`). It then drives the HTTP API and the WebSocket
the way the page does:

- a real call: ringing, answer, video and audio (if ffmpeg with libx264 is present),
  open (DTMF into the visitor's channel, repeated), hang up;
- a demo call: Open answers but never sends DTMF;
- another panel ringing: 409, closing the view or the other panel is ignored, decline
  gives 486;
- the caller gives up: CANCEL gives 487 and "ended";
- open from a camera view: `Originate ... Wait`, our channel is found by its
  application, and a second press reuses it;
- a camera view through SIP: the ordered call is accepted, Open uses its channel, and a
  stale view call is declined with 603;
- optionally, the page renders in headless Chrome, if Chrome is installed.

The test prints `PASS`/`FAIL` per check and exits with 0 when everything passed. Set
`IWT_E2E_LOG=1` to print the server log tail even on success.

The fake AMI can also run on its own:

```sh
python3 tools/fake_ami.py --port 5038 --user webtab --secret test   # --busy: calls never come up
```

It records every action. An `Originate` with `Application=Wait` creates an `Up` channel
in the incoming context, as Asterisk would. Nothing real is ever dialled.

## Preview server

```sh
python3 tools/preview_server.py            # http://127.0.0.1:8897/preview?mock=rain
```

This server uses only the standard library. Pillow is optional: it draws placeholder
photos and the camera test pattern. It never talks to a real station. Open the page in a
desktop browser at 1024x748.

| Parameter | Effect |
|---|---|
| `mock=<name>` | A panel from `static/mock/<name>.json`: `rain`, `snow`, `fog`, `clear-night`, `thunderstorm`, `not-available`, `live`, `live-imperial`, `scen-01` … `scen-15`, one per scene, and more. `mock=down` answers 503, `mock=offline` drops the connection. |
| `panels=1` | One panel instead of two |
| `ring=low\|mid\|high\|sweep` | The ringtone in the mock config |
| `attr=0` | Hide the attribution |
| `demo=ring\|answered\|ended\|view&panel=gate\|door` | The WebSocket stub plays call events |
| `act=open[&sim=err]` | The page taps Open by itself once a call or view is up. `sim=err` makes it fail. |
| `live=1` | Send requests to the stubs instead of simulating them |

On any path other than `/`, the page runs in **preview mode**: open, answer, view and
hang-up are simulated and never sent. `/` behaves like production against the stubs.

`tools/mock_dump.py` regenerates `static/mock/*.json` from the recorded Greenwich data
and the reference scenarios in `tests/test_brain.py`.

## Leak check and pre-commit hook

`tools/leakcheck.py` scans every tracked and untracked, non-ignored file. With
`--staged`, it scans what is about to be committed. It flags:

- Cyrillic text;
- public IPv4 addresses (private, loopback and documentation ranges are fine);
- 32-character hex strings;
- URLs with embedded credentials;
- e-mail addresses other than no-reply and example domains;
- JPEG EXIF blocks.

```sh
python3 tools/leakcheck.py              # whole tree
python3 tools/leakcheck.py --staged     # the index
git config core.hooksPath tools/git-hooks   # run it before every commit
```

**Private denylist.** You can keep a list of your own sensitive strings outside the
repository: your street, names, numbers, host names. Put one case-insensitive substring
per line and point `IWT_DENYLIST` at the file. `IWT_ALLOWLIST` may name a file of
`<glob><TAB><substring>` exceptions. Hits are reported by line number only, never by
value. The pre-commit hook picks up `~/.config/intercom-webtab-guard/denylist.txt` and
`allow.txt` automatically when they exist. CI runs without a denylist.

## Page rules

`intercom_webtab/static/index.html` runs on iOS 9 Safari, a 2015 browser:

- **ES5 only:**
  - `var` and `function`, no `let`, `const`, arrow functions, classes, template
    literals, destructuring, default parameters or `for…of`;
  - no `Promise`-based APIs (`fetch`), no `async`/`await`;
  - no `classList`, `Array.prototype.includes`, `Object.values`/`entries` or
    `padStart`.

  Use `XMLHttpRequest` and the class helpers in the page. `tests/test_page.py` enforces
  this with acorn, eslint and a list of late APIs.
- **CSS:** absolute positioning in a fixed 1024x748 layout. No flexbox, grid or CSS
  variables. Add `-webkit-` prefixes for transforms and transitions.
- **No new globals** (eslint `no-undef`). The page is three scripts: guard, core and
  weather. A failure in one must not stop the others.
- **Budget:** under 250 KB in total. Icons are embedded as data URIs, and the ringtones
  as base64 blocks written by `tools/ringtones.py`.
- **`static/dots.js`** is the source of the dot-matrix icons. Copy it in full into
  `<script id="dotsJs">`, and the test compares the two.
- **Bump `var VER`** (the first line of its kind in the file) whenever the page changes.
  Stations compare it with the server's `/api/ver` and reload themselves.
- **Every user-visible string is English.** Errors from the server are shown as they
  come, with a capitalised first letter.

## Generated assets

| Command | Writes |
|---|---|
| `python3 tools/ringtones.py [--check]` | `static/ring/*.wav` and the copies in `index.html` |
| `python3 tools/make_icons.py [--check]` | `static/icons/apple-touch-icon-{180,152,120}.png` from `static/icon.svg`, with no metadata chunks |
| `python3 tools/mock_dump.py [--out DIR] [--dry]` | `static/mock/*.json` |
| `python3 tools/photo_sheet.py OUT_DIR [--cache DIR] [--group NAME]` | Contact sheets of the photo catalogue (network) |
| `python3 -m intercom_webtab.weather.sources [--lat … --lon … --tz …] [--save DIR]` | Live self-test of the Open-Meteo fetchers (network) |

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

- unit tests and the end-to-end test on Python 3.9–3.12;
- the leak check, without a private denylist;
- the ES5 check of the page with acorn;
- gitleaks over the full history.
