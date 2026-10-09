# Security

The station controls a door, so treat its page like a key. This page explains what
protects it and what you have to do yourself. To report a vulnerability, see
[SECURITY.md](../SECURITY.md).

## The token travels in plain HTTP

Every request from the page carries the access token: in the URL (`?k=`) or in an
`X-Token` header. The server compares it in constant time. It is long and random (the
installer generates 32 characters), so it cannot be guessed. But **plain HTTP does not
encrypt it.** Anyone who can watch the traffic between the iPad and the server can copy
it and open your door.

Pick one of these, in order of preference:

1. **Keep it on your LAN.** Serve the page only to your home network: an `[http] bind`
   address on the LAN interface, plus the [firewall](#firewall) allowlist. Use WPA2/WPA3
   Wi-Fi with a strong password.
2. **Use a VPN** when the server is elsewhere, for example a machine in another building
   or a hosted server. An old iPad cannot run current VPN apps. A **site-to-site VPN on
   your router** works for any device and is usually the easiest. iOS also has built-in
   IPsec/IKEv2.
3. **Allow-list addresses** if you cannot use a VPN: put only your home's public address
   or range into `allowlist.txt`. This narrows the exposure, but the token is still
   unencrypted on the path.
4. **Put a TLS reverse proxy in front** (nginx, Caddy and the like) that forwards `/`,
   `/api/`, `/stream`, `/ph/`, `/icons/`, `/ring/` and the `/ws` WebSocket to the station.
   The page switches to `wss://` by itself over HTTPS. **Test this with your iPad:** iOS 9
   may not trust certificates from current public certificate authorities. A private CA
   installed on the iPad as a profile avoids that.

Never forward port 8080 from your router to the Internet without at least one of these.

If the token leaks, replace it ([configuration.md](configuration.md#paths)), restart the
service, and add the station to the Home Screen again.

### What the server adds

- **Security headers** on every response: a Content-Security-Policy that lets the page
  talk only to its own server, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`
  (the token is in the URL), `X-Content-Type-Options: nosniff`, and a Permissions-Policy
  that denies camera, microphone, geolocation and similar features.
- **No caching of the page**, so an old page with an old token is not kept around.
- **No access log**, so tokens in URLs do not end up in the journal.
- Camera URLs are built from `rtsp_user` and `rtsp_password` at run time, and any
  `user:password@` in a log line is masked.
- Only the Home-Screen icons and the ringtones are served without the token.

## Firewall

`sudo ./deploy/install.sh --firewall` installs `intercom-webtab-firewall.service`. It
loads its own nftables table, `inet intercom_webtab`, and leaves the rest of your host
firewall (SSH and so on) alone. The table accepts or drops traffic to the station's
ports only:

| Traffic | Allowed from |
|---|---|
| Page, TCP `[http] port` | addresses and networks in `/etc/intercom-webtab/allowlist.txt` |
| Asterisk SIP, UDP 5060 | `/etc/intercom-webtab/sip_peers.txt`, plus the `[provider] host`. TCP 5060 is always dropped. |
| Asterisk RTP, UDP 10000-10100 | the same SIP peers |
| Internal ports (SIP UAS, ffmpeg RTP, audio relay), AMI | localhost only |

Each list takes one address, network (CIDR) or host name per line, with `#` comments.
See `deploy/firewall/*.example.txt`. Host names are resolved when the rules are loaded.

**Fail-closed:**

- An empty or missing list means **localhost only** for that service. Add your home
  network to `allowlist.txt` before you rely on the firewall, or the iPad cannot load
  the page.
- If rendering the rules fails, `apply.sh` loads emergency rules that close the
  configured ports (read from `webtab.ini`; the defaults 8080, 5038, 5080, 41000-41003 and
  41100 if the file is unreadable), SIP 5060 and RTP 10000-10100 to everything but localhost.

After editing a list:

```sh
sudo systemctl reload intercom-webtab-firewall
sudo nft list table inet intercom_webtab        # check what is loaded
```

The unit is `PartOf=nftables.service`, so the table is reloaded whenever the host
firewall is. To remove the table: `sudo /opt/intercom-webtab/deploy/firewall/apply.sh --remove`.
With another firewall such as `ufw`, open the same ports there too, because both rule
sets apply.

## Least privilege

- **Unprivileged service:** `intercom-webtab` runs as the system user `webtab` with
  `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome` and `PrivateTmp`. It can write
  only its state directory (`/var/lib/intercom-webtab`) and its runtime directory
  (`/run/intercom-webtab`).
- **Secrets:** `/etc/intercom-webtab` is `root:webtab` mode 0750, and `token`,
  `secrets.ini` and `webtab.ini` are mode 0640. The service can read them, but cannot
  change them.
- **AMI:** bound to `127.0.0.1`, with `permit` set to 127.0.0.1 only. Its user may only
  read `system,call,reporting,originate` and write `originate,call,reporting`, which is
  what `Originate`, `PlayDTMF`, `Hangup` and `CoreShowChannels` need. The secret is
  random, generated by the installer.
- **The station's SIP endpoint** listens on 127.0.0.1. In Asterisk it lands in a
  context that cannot dial anything (`intercom-none`).
- **The page** gets no microphone, camera or location. It sends only control requests
  and diagnostics (audio state and script errors) to its own server.

## Door-opening safety

These rules are in `calls.py`, and the end-to-end test checks them:

- The station never answers a call by itself. Only Answer or Open does.
- **A demo call never opens a door.** Calls from the panel simulator are flagged as
  demo, and Open on them sends no DTMF and places no call. Through Asterisk they land in
  a demo context with no route to any panel.
- During a call, the digit goes only into the visitor's own channel. The station never
  calls a panel itself while a call is in progress.
- While one panel rings, Open for the other panel is refused.
- An incoming call cancels a pending open-from-view, so a visitor's channel cannot be
  mistaken for ours.
- A camera-view call is accepted only while our own order is live (20 s) and only if it
  carries our CallerID. A call *from* a panel number is always treated as a visitor.

With a public SIP number (variant A), anyone can call it and make the station ring.
Open would then send the digit into that caller's call, not to a door. To stop unwanted
rings, filter by caller number in the incoming context (see
[asterisk.md](asterisk.md#dialplan-contexts)).

## Data the station sends out

- Open-Meteo: your configured coordinates and time zone.
- Radiation networks: a bounding box around those coordinates (BfS, FMI, Health
  Canada), or nothing location-specific (Safecast, EPA).
- Openverse: requests for photos by id.
- The station sends nothing else, and no telemetry.

## Updates

Install updates with `git pull && sudo ./deploy/install.sh`. Keep the server's packages
updated, especially Asterisk and ffmpeg, which parse data from the network.
