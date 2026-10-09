# Architecture

Intercom WebTab is one Python process (`python3 -m intercom_webtab`) next to an Asterisk
server, plus a single web page that runs on the iPad. This page describes how the parts
fit together. The code is the reference. Each module starts with a docstring that lists
the rules it encodes.

## Components

```
             +--------------------------- server -----------------------------+
             |                                                                |
 door panel  |  Asterisk (PJSIP)          intercom-webtab                     |
  --SIP----->|  incoming context  --SIP--> sip_uas.SipUas (127.0.0.1:5080)    |
  <--RTP---->|  Dial(PJSIP/webtab)         |                                  |
             |                             v                                  |
             |  AMI 127.0.0.1:5038 <--- calls.CallManager --- web.Station ----+--> iPad
             |  (Originate, PlayDTMF,      |    ^              (aiohttp :8080) |    HTTP, MJPEG,
             |   Hangup, CoreShowChannels) v    |                             |    WebSocket
 RTSP camera |                         media.Media (ffmpeg processes)         |
  ---------->|                         warm streams, call audio, priming      |
             |                                                                |
 Internet    |  web.Weather: weather.sources, radiation, weather.photo        |
  <--HTTPS---|  (in executors) -> weather.brain.build_panel -> /api/panel     |
             +----------------------------------------------------------------+
```

| Module | Role |
|---|---|
| `intercom_webtab/__main__.py` | Command line: `--config`, `--check`, `--log-level`, `--version`. |
| `config.py` | Reads and validates `webtab.ini`, `secrets.ini` and the token file into a read-only `Config`. Errors name the section and key. |
| `sip_uas.py` | A minimal SIP user agent server on UDP, localhost only. It handles INVITE, CANCEL, BYE, ACK and OPTIONS. Its pure helpers parse headers and SDP. |
| `ami.py` | Asterisk Manager Interface client. One persistent connection carries actions, and channel listings use their own short connections. Pure functions pick the right channel. |
| `media.py` | All ffmpeg processes: warm camera streams, guest audio, the SIP video fallback, and the H.264 stream plus PCMA silence sent to the panel. |
| `calls.py` | The call, camera-view and door-opening state machine, plus the WebSocket hub. Every public coroutine returns `(http_status, json)`. |
| `web.py` | The aiohttp application, security headers, the weather background loop and the server lifecycle. |
| `weather/` | Open-Meteo fetchers (`sources`), the panel logic (`brain`, `phrases`, `units`, `utci`), and the in-memory photo store (`photo`). |
| `radiation/` | Providers of ambient dose rate (`bfs_odl`, `stuk_fmi`, `epa_radnet`, `hc_fps`, `safecast`) and the automatic choice. |
| `static/` | The page (`index.html`, with `dots.js` embedded), icons, ringtones and mock panels for the preview server. |
| `deploy/` | Installer, Asterisk and nftables templates and their renderer (`render.py`), and systemd units. |
| `tools/` | Panel simulator, fake AMI, end-to-end test, preview server, asset generators and the leak check. |

The process runs one asyncio event loop. Blocking work runs in executors: weather,
radiation and photo downloads, ffmpeg start-up and capability detection. ffmpeg output is
read by threads.

## Call flow

### A visitor rings

1. The panel calls. Asterisk receives the call on the provider trunk (variant A) or from
   a door-station endpoint (variant B). The call lands in the incoming context
   (`intercom-incoming`), which runs `Dial(PJSIP/webtab,180)`.
2. The station's SIP UAS gets the INVITE. It answers `100 Trying` and `180 Ringing`, and
   **never `200 OK` on its own**: a visitor who heard "picked up" would start talking to
   nobody.
3. The caller number is matched against `[panel:<id>] number`. An unknown number is
   treated as the first panel and logged. Calls from the panel simulator (display name
   `panelsim`) are flagged as demo calls.
4. Every connected page gets `{"ev":"call","state":"ringing","panel":"gate"}` over the
   WebSocket. The page shows that panel's camera at once, from the warm stream, and plays
   the ringtone.
5. **Answer** (`POST /api/answer`) sends `200 OK` with an SDP: PCMA audio, plus H.264
   video if the panel offered video. Media starts:
   - an ffmpeg process decodes the visitor's voice to 8 kHz PCM and sends it to
     `audio_out_port`. The server relays it to the pages over the WebSocket, and the page
     plays it with Web Audio;
   - if the RTSP stream is dead, the video is decoded from the SIP call instead;
   - the station sends PCMA silence and a live 320x240 H.264 test picture to the panel.
     Many panels only start their own video once they receive some.
6. **Open** (`POST /api/open`) finds the visitor's channel through AMI and sends the
   digit with `PlayDTMF`. See [door-stations.md](door-stations.md).
7. The call ends with a BYE or CANCEL from the panel, or with the close button
   (`POST /api/hangup {"scope":"call"}`). An unanswered call is declined with
   `486 Busy Here`, and its channel is also hung up through AMI.

BYE and CANCEL act only on the dialog they belong to, matched by `Call-ID`. A late BYE of
an abandoned camera view never ends a visitor's call, and the other way round.

### Camera view without a call

The station can view a panel at any time (`POST /api/view {"panel":"gate"}`):

- **With an RTSP camera:** the server switches the pointer `Media.view` to that panel's
  warm stream, so the picture appears immediately. A second light ffmpeg plays the
  camera's audio.
- **Without RTSP, or when the stream has no frames:** the station places a call
  (`Originate`) to the panel into the view context, with CallerID `own_callerid`. When the
  panel answers, Asterisk dials the station, which accepts this call only while the order
  is live (20 s). It then decodes video and audio from the SIP session. A stale or
  cancelled view call is declined with `603 Decline`, so the station can never end up
  calling itself.

The page closes a view after 90 s without touches.

### Opening from a camera view

With no call in progress, Open makes a short call to the panel
(`Originate ... Application=Wait`, `hold` seconds). It waits until that channel is `Up`,
then sends the digit series. A second Open within `hold` reuses the same line. Any
incoming call during the wait cancels the open, so a visitor's channel is never mistaken
for ours.

## Media and ports

| Port (default) | Reachable from | Used by |
|---|---|---|
| TCP 8080 (`[http] port`) | `[http] bind`, 0.0.0.0 by default | The page, `/stream` (MJPEG), `/ws`, `/api/*` |
| UDP 5080 (`[sip] uas_port`) | `[sip] uas_host`, 127.0.0.1 | The station's SIP UAS. Only Asterisk talks to it. |
| UDP 41000/41001 (`rtp_audio_port`) | localhost by design | RTP/RTCP audio of the station's call leg, read by ffmpeg |
| UDP 41002/41003 (`rtp_video_port`) | localhost by design | RTP/RTCP video of the station's call leg |
| UDP 41100 (`audio_out_port`) | 127.0.0.1 | Visitor audio as PCM, from ffmpeg to the server |
| TCP 5038 (`[asterisk] ami_port`) | 127.0.0.1 (`manager.conf`) | Asterisk Manager Interface |
| UDP 5060 | all interfaces | Asterisk SIP, for the provider or door stations |
| UDP 10000-10100 | all interfaces | Asterisk RTP (`deploy/asterisk/rtp.conf`) |

ffmpeg may open the RTP ports on all interfaces. The optional firewall
([security.md](security.md#firewall)) drops every internal port unless the traffic
comes from localhost.

Warm camera streams: one ffmpeg per panel with an `rtsp_url` keeps the camera connected
over RTSP/TCP and writes 360-pixel-high MJPEG frames to a pipe. A supervisor restarts
processes that exit or stop producing frames for 15 s. After four failures in a row it
backs off for 1, 2, 4 and then 5 minutes. The page never sees a frame older than 3 s, so
a dead camera does not look frozen.

ffmpeg differences are detected once at start-up. FFmpeg 5+ takes `-timeout`, 4.x takes
`-stimeout`, and `-fps_mode` replaced `-vsync` in 5.1. Without `libx264` the station
cannot send video to the panel. It logs a warning and carries on.

## Page and server contract

`GET /?k=<token>` returns `static/index.html` with `__TOKEN__` and `__IWT_CONFIG__`
replaced. The config is
`{"title", "panels":[{"id","label","icon"}], "ringtone", "attribution", "widgets"}`.

| Request | Purpose |
|---|---|
| `GET /api/panel` | The weather panel (JSON v3). The page asks every 120 s. |
| `GET /api/ver` | Page version on disk and the reload flag. The page checks every 2 min while idle. |
| `GET /stream` | MJPEG of the panel on screen |
| `WS /ws` | Events: `hello`, `call` (`ringing` / `answered` / `ended`), `view` (`ended`), a pulse `{"ev":"ka","vage":…}` every 5 s. Binary messages carry the visitor's audio. |
| `POST /api/answer`, `/api/open {panel}`, `/api/view {panel, via?}`, `/api/hangup {scope, panel}`, `/api/demo {panel?}`, `/api/diag` | Control. Errors return English text in `err`. |
| `GET /ph/<key>/bg.jpg`, `/ph/<key>/panel.jpg` | Weather photos, from memory |
| `GET /icons/*.png`, `/icon.svg`, `/icon.png`, `/ring/<name>.wav` | Static files, no token. iOS fetches Home-Screen icons without one. |

Every other route requires the token, as `?k=` or in an `X-Token` header, compared in
constant time.

The page is three scripts, so a failure in one does not take the others down:

1. **Guard:** configuration, error reporting to `/api/diag`, pull-to-refresh,
   self-update and the watchdog.
2. **Core:** sound, the WebSocket, the call state machine (`idle`, `ring`, `talk`,
   `view`), video and buttons.
3. **Weather:** photo, left column, widgets, chart and attribution.

The page is strict ES5 for iOS 9. See [development.md](development.md#page-rules).

## Weather loop

`web.Weather` fetches the forecast every 10 minutes, air quality every hour and radiation
every 6 hours. Fetches run in an executor. On failure it retries the forecast after 2, 5
and then 10 minutes, air after 10 minutes and radiation after 30 minutes. A source's
"last success" time changes only on success, so the panel can say honestly that data are
old. The panel is rebuilt after every change and at least once a minute. Photo downloads
run in their own thread, and nothing waits for them.

A watchdog logs once when the station has neither polled the panel nor held a WebSocket
for 10 minutes, and once when it comes back.

## State on disk

| Path | Content |
|---|---|
| `/etc/intercom-webtab/webtab.ini` | Configuration |
| `/etc/intercom-webtab/secrets.ini` | AMI login, provider account, camera and door-station passwords |
| `/etc/intercom-webtab/token` | The access token |
| `/var/lib/intercom-webtab/weather_state.json` | Last raw data, source status and panel memory (hysteresis, chosen phrases, radiation history), so a restart shows a panel at once |
| `/var/lib/intercom-webtab/reload.flag` | Touch it to make idle stations reload |
| `/run/intercom-webtab/` | ffmpeg logs, the SDP file of the current call, the simulator log (runtime directory) |

Photos are never written to disk.
