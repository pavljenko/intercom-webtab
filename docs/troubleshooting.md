# Troubleshooting

Start with the log. Most problems name themselves there:

```sh
journalctl -u intercom-webtab -f              # the station
journalctl -u intercom-webtab -b | grep -i -E "warn|error"
sudo asterisk -rvvv                           # Asterisk console
```

The iPad has no console, so the page reports its audio state and script errors to the
server. They appear in the log as `page diagnostics: {...}`.

## The service does not start

| Log message | Fix |
|---|---|
| `configuration error: ...` | The message names the section and key. Check with `--check` ([configuration.md](configuration.md)). |
| `secrets file not found` / `token file not found` | Create them, or run `deploy/install.sh` again. |
| `access token ... must be at least 16 characters` | Generate a new token ([configuration.md](configuration.md#paths)). |
| `cannot open the SIP/audio UDP ports` | Another program uses `[sip] uas_port` or `audio_out_port`. Change the port, and render Asterisk again for `uas_port`. |
| `cannot start: [Errno 98] ... address already in use` | Another program uses `[http] port`. |
| `missing dependency: ... (install python3-aiohttp)` | `sudo apt install python3-aiohttp` |

## The iPad cannot open the page

- **"forbidden":** the token in the URL is wrong or old. Open the URL the installer
  printed, then remove the Home-Screen icon and add it again.
- **No answer at all:**
  - Is the iPad on the same network or VPN?
  - Does `curl -I http://<server>:8080/icon.svg` work from another device?
  - With the firewall on, is the iPad's network in `/etc/intercom-webtab/allowlist.txt`?
    An empty list means localhost only. After editing it, run
    `sudo systemctl reload intercom-webtab-firewall`.
  - Is another firewall (such as `ufw`) blocking the port?
- **"The page file is missing, this is a test page":** `static_dir` points to the wrong
  place.

## No sound

- The status line says **"Sound is off — tap the screen"**: iOS needs one touch after
  every page load. Tap once. See [ipad-setup.md](ipad-setup.md#sound).
- The volume is too low: raise it with the side buttons *while something plays*, and
  check **Settings > Sounds > Change with Buttons**.
- You hear the ringtone but not the visitor: the side switch may be set to mute, which
  silences Web Audio on some iOS versions. Also check for ffmpeg warnings in the log.
- `ffmpeg not found: video and audio are disabled`: `sudo apt install ffmpeg`.

## The station does not ring

1. Does the call reach Asterisk? In `asterisk -rvvv`, watch for the incoming call. In
   variant A, check `pjsip show registrations` (it should say `Registered`). In variant
   B, check `pjsip show contacts` (the door station should be `Avail`).
2. Does Asterisk reach the station? `pjsip show contacts` should list
   `webtab/sip:webtab@127.0.0.1:5080` as `Avail`. The station answers OPTIONS.
3. Does the station see the call? The log shows `incoming call: panel=gate, ringing`. If
   it ends with `WARNING: nobody to ring`, no page is connected: open the station on the
   iPad.
4. `caller '...' matches no panel number`: set `[panel:<id>] number` to the number shown.
5. `station silent for N min: no polls, no event channel`: the iPad is asleep, locked,
   off the network, or showing another app. Check Auto-Lock and Guided Access.

## The door does not open

| Message on the iPad or in the log | Meaning and fix |
|---|---|
| "No call channel — try again" | During a call, the station could not find the visitor's channel. Check that `incoming_contexts` lists the context where panel calls really arrive. The `incoming: channels ...` log line shows it. |
| `call channel found by the fallback rule` (log) | The channel was found, but not in an incoming context. Fix `incoming_contexts`. |
| The DTMF line is logged but the door stays shut | Wrong digit (`dtmf`), wrong transport (`dtmf_mode`, [asterisk.md](asterisk.md#dtmf-modes)), or the digit comes too early (`open_settle`). See [door-stations.md](door-stations.md#tuning). |
| "Panel busy" | Opening from a view: the panel did not answer our call within `hold` seconds. It is in another call, or it does not auto-answer calls from the server or your number. |
| "Another panel is ringing" | Expected: Open is refused for one panel while the other panel rings. |
| "Asterisk unreachable" | AMI is down. Check `systemctl status asterisk` and look for `AMI login refused` in the log (credentials in `secrets.ini` and `manager.conf` must match; run the installer again). |
| "Demo: nothing was opened" | Expected for demo calls. |
| "Cancelled: incoming call" | A visitor called while an open-from-view was waiting. Answer the visitor's call and open from it. |

## No video

- **"Camera unavailable"** when you tap a panel during a call: that panel's RTSP stream
  has no fresh frames.
- `camera stream gate: 4 failures in a row (...), next try in 60 s`: ffmpeg cannot read
  the camera. The reason in brackets is ffmpeg's last error, with credentials masked.
  Check `rtsp_url`, `rtsp_user` and `rtsp_password`, and test the stream with
  `ffplay -rtsp_transport tcp <url>` from the server.
- Without RTSP, video comes from the SIP call. A panel that offers no H.264 shows no
  picture. If the panel waits for video from us, install ffmpeg with `libx264`. The log
  warns `ffmpeg has no libx264 encoder`.
- The status line says "Gate camera silent since 14:05": the warm stream delivered
  frames in the last 24 hours, but has been silent for more than a minute.

## Weather or radiation problems

- **"Weather data has not loaded yet" / "Weather not updated for ...":** the server
  cannot reach `api.open-meteo.com`. Test it with
  `cd /opt/intercom-webtab && python3 -m intercom_webtab.weather.sources --lat 51.4779 --lon -0.0015`.
  The log says `forecast unavailable: ...` once, then again every 12 failures.
- **Radiation "no stations nearby":** no open network has a fresh station within
  `radius_km`. See [radiation.md](radiation.md#limitations). Raise the radius, or set
  `provider = none`.
- The service does not start and `--check` reports
  `[radiation] provider: unknown radiation provider 'xyz'`: a typo in `[radiation] provider`.
- **No background photo:** the photos come from `api.openverse.org`. Look for
  `weather photo:` lines. A photo that failed is retried after 30 minutes, and until
  then the previous one or a plain tone stays.
- **Wrong hours on the chart:** set `[location] timezone`.

## The page keeps reloading or says "Station keeps failing"

The watchdog reloads the page when its scripts stop, or when the event channel is
silent while HTTP works. After three reloads in a row, it pauses for 10 minutes. Look
for `page diagnostics` lines with `watchdog` in the log. Common causes:

- a weak Wi-Fi signal;
- a proxy that does not pass WebSockets (see [security.md](security.md));
- a server that is overloaded by ffmpeg.

## Reset

```sh
sudo systemctl stop intercom-webtab
sudo rm /var/lib/intercom-webtab/weather_state.json   # forget weather memory and cached data
sudo systemctl start intercom-webtab
```

Then pull to refresh on the iPad, and tap once for sound.
