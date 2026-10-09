# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-09

Initial release.

### Door intercom

- Station page for iPads on iOS 9 or newer, saved to the Home Screen. It has a fixed
  1024x748 landscape layout, one or two door panels, and a third button that changes
  role: Refresh, Answer, Open.
- A tiny SIP UAS that rings and never answers by itself. Answer and Open send `200 OK`,
  and Open on a ringing call answers and opens in one tap.
- Door opening by DTMF through Asterisk AMI, with a per-panel settle delay and repeats
  (`dtmf`, `open_settle`, `open_repeat`), and opening from a camera view through a short
  `Wait` call (`hold`).
- Safety rules:
  - demo calls never open a door;
  - Open is refused for one panel while the other rings;
  - BYE and CANCEL are matched by Call-ID;
  - stale camera-view calls are declined;
  - an incoming call cancels a pending open.
- Warm RTSP camera streams via ffmpeg for instant video, with a SIP video fallback.
  Visitor audio reaches the page over the WebSocket. The station primes panels with
  H.264 video and PCMA silence.
- Four ringtones for different hearing (`low`, `mid`, `high`, `sweep`), plus Web Audio
  fallback beeps.
- Self-update on a new page version or `reload.flag`, pull to refresh, and a watchdog
  that never reloads into an error page.

### Weather panel

- Open-Meteo forecast and CAMS air quality. The "feels like" value is UTCI, with
  condition words, 39 animated dot-matrix scenes and a prioritised status line.
- Six widget slots chosen from seven widgets (wind, air, UV, pressure, radiation, sun,
  humidity) and a
  9-hour precipitation chart.
- Units: °C/°F, m/s, km/h or mph, hPa, mmHg or inHg.
- Weather photos from a catalogue of 91 CC0 / Public Domain Mark images, through
  Openverse, processed in memory only.
- Radiation from BfS ODL-Info, STUK/FMI, US EPA RadNet, Health Canada FPS and Safecast,
  with automatic choice by location and confirmation of raised values.

### Operations

- `deploy/install.sh` for Debian 12, Ubuntu 22.04+ and Raspberry Pi OS. It creates an
  unprivileged systemd service, renders the Asterisk configuration (SIP provider or
  directly registered door stations) and installs an optional fail-closed nftables
  firewall.
- Tools: a panel simulator, a fake AMI, an end-to-end test, a preview server, asset
  generators, and a leak check with a pre-commit hook.
- CI on Python 3.9–3.12 with unit tests, the end-to-end test, the leak check, an ES5
  check of the page, and gitleaks.

[0.1.0]: https://github.com/pavljenko/intercom-webtab/releases/tag/v0.1.0
