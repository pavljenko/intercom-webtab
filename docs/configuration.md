# Configuration

The station reads three files, named in `[paths]`:

| File | Default path | Content |
|---|---|---|
| `webtab.ini` | `/etc/intercom-webtab/webtab.ini` | Everything that is not secret |
| `secrets.ini` | `/etc/intercom-webtab/secrets.ini` | Logins, passwords, camera URLs |
| token | `/etc/intercom-webtab/token` | The access token for the page |

Annotated templates: [`config/webtab.example.ini`](../config/webtab.example.ini) and
[`config/secrets.example.ini`](../config/secrets.example.ini).

After a change:

```sh
sudo -u webtab env PYTHONPATH=/opt/intercom-webtab \
  python3 -m intercom_webtab --config /etc/intercom-webtab/webtab.ini --check
sudo systemctl restart intercom-webtab
# changed panels, numbers, contexts or provider data? render Asterisk again:
sudo ./deploy/install.sh
```

The page reads its part of the configuration (title, panels, ringtone, widgets,
attribution) when it loads. After a restart, pull to refresh on the iPad, or touch
`/var/lib/intercom-webtab/reload.flag` to make idle stations reload.

## Syntax and validation

- This is the INI format of Python's `configparser`, without interpolation.
- In `webtab.ini`, `;` starts a comment, also after a value when a space comes before it.
- In `secrets.ini`, values are taken literally, so passwords may contain `;` or `#`.
  Only whole lines that start with `;` or `#` are comments there.
- Switches accept `on`/`off`, `yes`/`no`, `true`/`false` or `1`/`0`.
- Every missing key takes the default listed below.
- Every error names the section and key, for example
  `[panel:gate] dtmf: expected 1 to 8 DTMF digits (0-9, *, #, A-D), got 'x'`.
- Unknown sections and keys are not errors. They are logged as warnings, which catches
  typos.
- Relative paths are resolved against the directory of `webtab.ini`. `~` is expanded.

## webtab.ini

### [station]

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `title` | `Intercom WebTab` | text, cut to 60 characters | Page title. iOS also uses it as the Home-Screen name. |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` | Logging level. `--log-level` on the command line overrides it. |

### [http]

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `bind` | `0.0.0.0` | an address | Where the page is served. See [security.md](security.md) before exposing it. |
| `port` | `8080` | 1-65535 | HTTP port. Must differ from `[sip] uas_port`. |

### [paths]

| Key | Default | Meaning |
|---|---|---|
| `token_file` | `/etc/intercom-webtab/token` | The token file (one line). |
| `secrets_file` | `/etc/intercom-webtab/secrets.ini` | The secrets file. |
| `state_dir` | `/var/lib/intercom-webtab` | Weather state and `reload.flag`. Created if missing. |
| `static_dir` | the `static/` folder of the package | The page and its assets. Change it only to serve a modified page. |
| `runtime_dir` | `$RUNTIME_DIRECTORY` (systemd), else a private temporary directory | ffmpeg logs and the call SDP file. |

**The token** must be at least 16 characters from `A-Z a-z 0-9 . _ ~ -`, because it
travels in URLs. The installer generates one. To make one yourself:

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(24))' | sudo tee /etc/intercom-webtab/token
```

After changing the token, open the new URL on the iPad and add it to the Home Screen
again.

### [location]

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `latitude` | `51.4779` | -90 to 90 | Point for weather, air quality and radiation. |
| `longitude` | `-0.0015` | -180 to 180 | |
| `timezone` | `Europe/London` | an IANA zone name | Local time of the panel: hours on the chart, "as of" times, sunrise and sunset. Checked against the system zone database. |

Two decimals (about 1 km) are enough for the forecast. You do not have to give the exact
coordinates of your door. The weather APIs receive what you configure.

### [units]

| Key | Default | Allowed |
|---|---|---|
| `temperature` | `C` | `C`, `F` |
| `wind` | `m/s` | `m/s`, `km/h`, `mph` |
| `pressure` | `hPa` | `hPa`, `mmHg`, `inHg` |

Values are case-insensitive. With `wind = mph`, visibility in fog messages is shown in
feet. See [weather.md](weather.md#units).

### [weather]

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `on` | `off`: no network requests at all. The panel shows "No data". |
| `photos` | `on` | Background photos (see [weather.md](weather.md#photos)). `off`: a plain tone. |
| `attribution` | `on` | The small credit line for the data sources on the page. The data licences require credit, so if you turn it off, give credit elsewhere. |

### [radiation]

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `provider` | `auto` | `auto`, `none`, `bfs_odl`, `stuk_fmi`, `epa_radnet`, `hc_fps`, `safecast` | Which network supplies the radiation widget. `none` switches it off, and the widget slot then goes to the first widget not on the list. |
| `radius_km` | `100` | 1-2000 | Only stations within this distance are used. |

An unknown id is a configuration error: `--check` (and therefore the service start)
fails with `[radiation] provider: unknown radiation provider 'xyz' (known: auto, none, ...)`.
See [radiation.md](radiation.md).

### [ui]

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `ringtone` | `mid` | `low`, `mid`, `high`, `sweep` | See [ringtones-and-hearing.md](ringtones-and-hearing.md). |
| `widgets` | `wind, air, uv, pressure, radiation, sun` | exactly six different keys from `wind`, `air`, `uv`, `pressure`, `radiation`, `sun`, `humidity` | Widget order: left to right, top to bottom. |

### [sip]

The station's own SIP endpoint and media ports. Asterisk rings it on localhost. Keep the
defaults unless they collide with something else.

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `uas_host` | `127.0.0.1` | an address | Where the station's SIP UAS listens. Keep it local. |
| `uas_port` | `5080` | 1-65535 | SIP UAS port. Must differ from `[http] port`. |
| `rtp_audio_port` | `41000` | even, 1024-65534 | RTP audio of the station's call leg. The next odd port carries RTCP. |
| `rtp_video_port` | `41002` | even, 1024-65534 | RTP video. The next odd port carries RTCP. |
| `audio_out_port` | `41100` | 1024-65535 | Visitor audio from ffmpeg to the server, on localhost. |

The six ports (`uas_port`, both RTP pairs and `audio_out_port`) must not overlap.

### [asterisk]

| Key | Default | Meaning |
|---|---|---|
| `ami_host` | `127.0.0.1` | Asterisk Manager Interface host. |
| `ami_port` | `5038` | AMI port. |
| `trunk` | `provider` | The PJSIP endpoint of the SIP provider (variant A). Leave it empty if every panel has its own `endpoint` (variant B). |
| `own_callerid` | `100` | Our number: the CallerID of our own camera-view and door-opening calls. It must differ from every panel number. |
| `incoming_contexts` | `intercom-incoming` | Dialplan contexts where door-panel calls arrive, comma separated. The station uses them to find the visitor's channel. Generated endpoints use the first one. |
| `view_context` | `intercom-view` | Context of the camera-view call. |
| `view_exten` | `webtab` | Extension in the view and demo contexts. |
| `demo_context` | `intercom-demo` | Context of the panel simulator's calls. |

Context and extension names are letters, digits, `_`, `.` and `-`. The view, demo and
incoming contexts must all be different. See [asterisk.md](asterisk.md).

### [panel:&lt;id&gt;]

One or two sections. The id after `panel:` is lowercase letters, digits, `-` or `_`, up
to 32 characters. It is used in the API and in `secrets.ini`. The first panel is the
default for calls from unknown numbers.

| Key | Default | Allowed | Meaning |
|---|---|---|---|
| `label` | the id, capitalised | text, cut to 40 characters | Name on the page ("Opening the gate…", "Gate open"). |
| `icon` | `door` | `gate`, `house`, `door` | Button icon. The page replaces unknown names with `gate` for the first panel and `house` for the second. |
| `number` | (required) | a SIP number, `+` allowed | The caller number this panel calls from. It identifies the panel and is the number dialled through the trunk. Numbers must be unique. |
| `endpoint` | empty | a PJSIP endpoint name | Variant B: the door station registers to this Asterisk under this name. Empty: the panel is reached as `number@trunk`. |
| `dtmf` | `1` | 1-8 of `0-9 * # A-D` | Digit(s) that open the door. |
| `open_settle` | `2.0` | 0-10 s | Delay after the line comes up before the first digit. |
| `open_repeat` | `0.7, 1.4, 2.1, 3.0` | up to 10 ascending values, 0-30 s | When to repeat the digit, counted from the first one. An empty value means no repeats. |
| `hold` | `12` | 3-120 s | How long our own door-opening call stays up. It is also the window for a second press. |

Every panel needs either `endpoint` or `[asterisk] trunk`. To tune `dtmf`, `open_settle`,
`open_repeat` and `hold`, see [door-stations.md](door-stations.md#tuning).

## secrets.ini

Owned by `root:webtab`, mode 0640. Never commit it. The repository's `.gitignore`
already excludes `secrets*.ini`.

### [asterisk] (required)

| Key | Meaning |
|---|---|
| `ami_user` | AMI login. The installer writes the same login into `/etc/asterisk/manager.conf`. |
| `ami_secret` | AMI password, one line. If the value is `CHANGE_ME`, the installer replaces it with a random one. |

### [provider] (variant A)

| Key | Default | Meaning |
|---|---|---|
| `host` | empty | SIP server of the provider. It is also added to the firewall's SIP peers. |
| `port` | `5060` | SIP port of the provider. |
| `username` | empty | Your SIP account or number at the provider. |
| `auth_username` | `username` | The authentication user, if the provider uses a different one. |
| `password` | empty | SIP password. |

The Asterisk renderer refuses to run while `[asterisk] trunk` is set and `host`,
`username` or `password` is empty or still `CHANGE_ME`.

### [panel:&lt;id&gt;] (optional)

| Key | Meaning |
|---|---|
| `rtsp_url` | The panel's camera, for example `rtsp://camera-gate.local:554/stream1`. It must start with `rtsp://` or `rtsps://`. Without it, video comes through the SIP call. |
| `rtsp_user`, `rtsp_password` | Camera login. Keep the credentials out of the URL: the code inserts them URL-encoded, and log lines mask them. |
| `sip_password` | Variant B: the SIP password of the door station that registers as `endpoint`. |

A `[panel:<id>]` section in the secrets without a matching panel in `webtab.ini` is
logged as a warning.

## Command line

```
python3 -m intercom_webtab [--config FILE] [--check] [--log-level LEVEL] [--version]
```

| Option | Meaning |
|---|---|
| `--config FILE` | Path to `webtab.ini`. The default comes from `$IWT_CONFIG`, else `/etc/intercom-webtab/webtab.ini`. |
| `--check` | Validate the configuration, secrets and token, print a summary, then exit. Exit code 0 means OK, 2 means a configuration error. |
| `--log-level` | `debug`, `info`, `warning` or `error`. Overrides `[station] log_level`. |
| `--version` | Print the version. |

Exit codes when running: 1 if the server cannot start (port in use, unwritable state
directory), 2 for a configuration error, 3 if aiohttp is missing.
