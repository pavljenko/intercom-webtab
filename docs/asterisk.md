# Asterisk

Asterisk does the telephony. It receives the door panel's call and rings the station's
own SIP endpoint, `webtab`, on `127.0.0.1:5080`. When asked over the Asterisk Manager
Interface (AMI), it calls a panel for a camera view or a door opening, and it sends the
DTMF digit. The station never talks SIP to the outside world itself.

`deploy/render.py` generates the whole configuration from `webtab.ini` and `secrets.ini`.
`deploy/install.sh` runs it. Requirements: **Asterisk 18 or newer with PJSIP, on the
same machine as the station** (see [install.md](install.md#asterisk)).

```sh
# preview what would be written
python3 /opt/intercom-webtab/deploy/render.py --config /etc/intercom-webtab/webtab.ini asterisk --stdout
# write it (changed files are first kept as <name>.bak-<timestamp>) and restart Asterisk
sudo python3 /opt/intercom-webtab/deploy/render.py --config /etc/intercom-webtab/webtab.ini \
  asterisk --out /etc/asterisk
sudo systemctl restart asterisk
```

To change the templates, edit `deploy/asterisk/*.tmpl` in your checkout and run the
installer again. Hand edits to `/etc/asterisk/*.conf` are replaced on the next install.

## Two ways panels reach Asterisk

Both can be mixed: one panel through a provider, another registered directly.

### Variant A: a SIP provider

Many building intercoms and IP door panels call a phone number at a SIP provider.
Asterisk registers with the provider as that number, so the calls arrive at Asterisk.

`webtab.ini`:

```ini
[asterisk]
trunk = provider          ; name of the generated PJSIP endpoint
own_callerid = 100        ; your number at the provider

[panel:gate]
number = 201              ; the number the gate panel calls from
```

`secrets.ini`:

```ini
[provider]
host = sip.provider.example
port = 5060
username = 100
password = your-sip-password
```

The renderer generates a `registration`, an `auth`, an `aor` and an `endpoint` named
after `trunk`, plus an `identify` that matches the provider's host:

- calls from the provider land in the incoming context;
- calls to a panel are dialled as `PJSIP/<number>@provider`;
- the endpoint has `rtp_symmetric`, `force_rport` and `rewrite_contact` for NAT.

If your provider signals or sends media from more addresses than `host` resolves to, add
them to the `match =` line of the `-identify` section in the template. Add them to the
firewall's `sip_peers.txt` as well.

### Variant B: a door station registered to Asterisk

An IP door station can register to your Asterisk directly, like a SIP phone.

`webtab.ini`:

```ini
[asterisk]
trunk =                   ; empty if no panel uses a provider
own_callerid = 100

[panel:gate]
number = 201              ; an identifier: Asterisk presents calls from the station as 201
endpoint = gate-station   ; the SIP user the door station registers with
```

`secrets.ini`:

```ini
[panel:gate]
sip_password = a-long-random-password
```

For every panel with an `endpoint`, the renderer generates an endpoint, an auth and an
aor (`max_contacts = 1`). The endpoint has `callerid = "<label>" <number>`, so the
station recognises the panel by `number` whatever the door station puts in `From`.

In the door station's own settings:

- SIP server: the address of this machine, port 5060 (UDP);
- user and authentication name: the `endpoint` value (`gate-station`);
- password: the `sip_password`;
- number to call when the button is pressed: `own_callerid` (`100`).

### Calls *to* the panel

A camera view without RTSP, and opening the door from a camera view, both need Asterisk
to **call the panel**. The panel must accept such calls, usually by answering them
automatically. In variant A that means calls from your provider number; in variant B,
calls from the server. Many door stations have an "auto answer" or "accept calls from"
setting for this. With an RTSP camera, only opening from a view needs the call. Opening
during a visitor's call always works, because the digit goes into the visitor's call.

## Dialplan contexts

`deploy/asterisk/extensions.conf.tmpl`:

| Context (default name) | What happens |
|---|---|
| `intercom-incoming` (`incoming_contexts`) | Any extension → `Dial(PJSIP/webtab,180)`. The station rings until the panel gives up or someone answers. The trunk and the door-station endpoints deliver calls here. |
| `intercom-view` (`view_context`) | `webtab` (`view_exten`) → `Dial(PJSIP/webtab,30)`. This is where the station's camera-view call goes once the panel answers. |
| `intercom-demo` (`demo_context`) | Calls from the panel simulator on localhost, through the `local-sim` endpoint matched by `127.0.0.1`. It has no way to reach a panel. |
| `intercom-none` | The station's own endpoint cannot place calls. |

The station finds the visitor's channel by its context, so `incoming_contexts` must name
the context where panel calls arrive. If you route panel calls through your own
dialplan, list your contexts there. The generated endpoints use the first one.

The incoming context accepts **any** caller on the trunk. If your provider number is
public, unwanted calls would ring the station too. They ring as the first panel, and
**Open** sends the digit into that caller's call, not to a door. To filter, check
`${CALLERID(num)}` in the incoming context before the `Dial`.

## AMI

`deploy/asterisk/manager.conf.tmpl` enables AMI on `127.0.0.1` only, for the user from
`secrets.ini`:

```
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
read = system,call,reporting,originate
write = originate,call,reporting
```

The station uses only `Originate`, `PlayDTMF`, `Hangup` and `CoreShowChannels`, logging
in with `Events: off`. If the login fails, the log says
`AMI login refused: check [asterisk] ami_user / ami_secret ...`.

## DTMF modes

The door-opening digit is sent with `PlayDTMF` on the panel's channel. How it travels is
set by `dtmf_mode` on the trunk or door-station endpoint, `rfc4733` in the templates:

| `dtmf_mode` | How the digit travels | When to use |
|---|---|---|
| `rfc4733` | RTP telephone-event packets | The default. Most providers and panels. |
| `info` | SIP INFO messages | Panels or providers that ignore RTP events |
| `inband` | Audible tones in the audio (needs G.711) | Old analogue gateways |
| `auto` | RFC 4733 if negotiated, else inband | When unsure |

A good sign that the mode is wrong: the panel opens when you press the digit on a normal
phone during a call, but not from the station. Change `dtmf_mode` in
`deploy/asterisk/pjsip.conf.tmpl`, in the `provider` or `station` block, and run the
installer again.

## Codecs

The station answers with **G.711 A-law** (`alaw`, PCMA) audio and, if offered, **H.264**
video. The trunk and door-station endpoints also allow `ulaw`, and Asterisk converts
between the two. A panel that offers only other video codecs gives no SIP video. Use its
RTSP stream instead.

## NAT and ports

- Asterisk listens on UDP 5060 for SIP and UDP 10000-10100 for RTP
  (`deploy/asterisk/rtp.conf`, `strictrtp = yes`).
- **Behind a home router or another NAT:** uncomment and fill in the `transport-udp`
  lines in the template:

  ```ini
  external_media_address = 203.0.113.10
  external_signaling_address = 203.0.113.10
  local_net = 192.168.0.0/16
  ```

  With a provider (variant A), the registration keeps the NAT mapping open in most
  setups. If calls arrive without audio, forward UDP 10000-10100 to the server and
  restrict it to the provider's addresses (the firewall does this on the host).
- Door stations on the same LAN (variant B) need no NAT settings.
- Do not expose port 5060 to the whole Internet. Scanners probe it constantly. The
  firewall ([security.md](security.md#firewall)) accepts SIP and RTP only from the
  listed peers.

## Testing with the panel simulator

`tools/panelsim.py` is a minimal SIP caller. It rings like a real panel and, once
answered, streams an H.264 test picture and a 440 Hz tone (if ffmpeg is present). It
uses only localhost and never dials out. Unless you change the display name, its calls
are **demo calls**, and Open never opens a door on them.

```sh
# straight into the station, without Asterisk
python3 tools/panelsim.py

# through Asterisk: localhost calls land in the demo context
python3 /opt/intercom-webtab/tools/panelsim.py --to asterisk --number 201

# look exactly like real panel 201: NOT a demo call. Straight into the station there is
# no Asterisk channel, so Open answers "No call channel" and sends nothing
python3 tools/panelsim.py --number 201 --display ""
```

Exit codes: 0 answered, 3 declined or cancelled, 1 no answer. Other options: `--seconds`,
`--ring-timeout`, `--no-video`, `--no-media`, `--uas-host/--uas-port`,
`--asterisk-host/--asterisk-port`, `--exten`.

The station can start a demo call itself:

```sh
curl -s -X POST -H "X-Token: $(sudo cat /etc/intercom-webtab/token)" \
     -H 'Content-Type: application/json' -d '{"panel":"gate"}' \
     http://127.0.0.1:8080/api/demo
```

It uses Asterisk when AMI is connected, and rings the station directly otherwise.

Useful Asterisk commands (`sudo asterisk -rvvv`):

| Command | Shows |
|---|---|
| `pjsip show registrations` | Is the provider registration up? (variant A) |
| `pjsip show contacts` | Are the door stations registered, and does `webtab` answer OPTIONS? |
| `pjsip show endpoints` | All endpoints and their state |
| `core show channels` | Live calls, with context and application |
| `pjsip set logger on` | Every SIP message, for one session |
| `manager show connected` | Is the station logged in to AMI? |

The station logs a line about the trunk channels on every incoming call, showing the
real context and application of each channel. That line is the quickest way to check
`incoming_contexts`.
