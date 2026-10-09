# Installation

`deploy/install.sh` installs Intercom WebTab as a systemd service on Debian 12,
Ubuntu 22.04/24.04 (and newer) and Raspberry Pi OS. You can run it again safely: it keeps
your configuration, secrets and token, replaces the program files, and renders the
Asterisk configuration again.

## Before you start

- An always-on Linux machine with a fixed address on your network, or one reachable over
  a VPN. The iPad connects to it over HTTP.
- `sudo` rights on that machine, and Internet access for packages, weather data and
  photos.
- Details of your door panel: how it reaches Asterisk (through a SIP provider, or by
  registering directly), the number it calls from, and the DTMF digit that opens it. See
  [asterisk.md](asterisk.md) and [door-stations.md](door-stations.md).

## Install

```sh
git clone https://github.com/pavljenko/intercom-webtab.git
cd intercom-webtab
sudo ./deploy/install.sh
```

The first run installs a working skeleton: example configuration for Greenwich with two
example panels, a random AMI secret and a random access token. Asterisk is not configured
yet, because the example secrets still say `CHANGE_ME`. Then:

1. Edit `/etc/intercom-webtab/webtab.ini`: location, units, panels. See
   [configuration.md](configuration.md).
2. Edit `/etc/intercom-webtab/secrets.ini`: the provider account (variant A), the
   door-station passwords (variant B), and the camera URLs.
3. Run `sudo ./deploy/install.sh` again. This time it renders `/etc/asterisk/*.conf` and
   restarts Asterisk and the station.
4. Open the printed URL on the iPad ([ipad-setup.md](ipad-setup.md)).

To check the configuration without restarting anything:

```sh
sudo -u webtab env PYTHONPATH=/opt/intercom-webtab \
  python3 -m intercom_webtab --config /etc/intercom-webtab/webtab.ini --check
```

## Options

| Option | Effect |
|---|---|
| `--no-asterisk` | Do not install or configure Asterisk. Use this when you maintain the Asterisk configuration yourself. Asterisk still has to run on the same machine (see [Asterisk](#asterisk)). |
| `--firewall` | Also install `nftables` and enable `intercom-webtab-firewall.service`. See [security.md](security.md#firewall). |
| `--no-start` | Install only, without enabling or restarting any service. |
| `--skip-apt` | Do not install packages. Use this when they are already present or come from elsewhere. |
| `-h`, `--help` | Show the option summary. |

## What the installer changes

| Item | Details |
|---|---|
| Packages | `python3 python3-aiohttp python3-pil ffmpeg ca-certificates`. `nftables` is added with `--firewall`, and `asterisk` if it is packaged and not yet installed. |
| User | A system user and group `webtab`, home `/var/lib/intercom-webtab`, no login shell. |
| `/opt/intercom-webtab/` | Program files: the `intercom_webtab` package, `deploy/`, `config/` and `tools/panelsim.py`. Owned by root and replaced as a whole on every run. |
| `/etc/intercom-webtab/` | `root:webtab`, mode 0750. It holds `webtab.ini`, `secrets.ini` and `token` (mode 0640, created only if missing), plus `allowlist.txt` and `sip_peers.txt` for the firewall (created empty, meaning localhost only). |
| `secrets.ini` | If `ami_secret` is still `CHANGE_ME`, it is replaced with a random value. |
| `/var/lib/intercom-webtab/` | State directory, owned by `webtab`. |
| `/etc/asterisk/` | `pjsip.conf`, `extensions.conf`, `manager.conf` and `rtp.conf` are rendered from `deploy/asterisk/`, owned by `asterisk`, mode 0640. Files that change are first kept as `<name>.bak-<timestamp>`. This is skipped with `--no-asterisk`, when Asterisk is missing, or while the secrets are incomplete. |
| systemd | `intercom-webtab.service`, plus `intercom-webtab-firewall.service` with `--firewall`, in `/etc/systemd/system/`. They are enabled and restarted unless you pass `--no-start`. |

At the end it prints the page address with the token, and how to follow the log:

```sh
journalctl -u intercom-webtab -f
```

The service runs `--check` before every start (`ExecStartPre`), so a broken configuration
shows up in the journal right away instead of as a crash loop.

## Supported systems

The installer recognises Debian 12 and 13, Raspberry Pi OS based on them, Ubuntu 22.04,
and Ubuntu 24.04 and later. On other systems it prints a warning and continues. Any
system with systemd, Python 3.9+, aiohttp, Pillow and ffmpeg should work.

Python 3.9 is the minimum because the code uses `zoneinfo`. The system time-zone database
(`tzdata`) must be installed, which it is on all the systems above.

### Asterisk

The station needs **Asterisk 18 or newer with `res_pjsip`**. Distributions differ here:

- **Ubuntu** ships Asterisk in the `universe` component, so the installer can install it.
- **Debian 12 and Raspberry Pi OS** do not ship an `asterisk` package, at least at the
  time of writing. Check with `apt-cache policy asterisk`.

If no package is available, the installer warns and continues without Asterisk. Install
Asterisk 18+ yourself, for example from source following the official Asterisk
documentation, or from a package source you trust. Then run the installer again, and it
renders the configuration.

**Run Asterisk on the same machine as the station.** The station's SIP answer and its
media pipeline use 127.0.0.1, so an Asterisk on another host would get no audio or video
from the call leg to the station.

**If you already use Asterisk for other things:** the installer **replaces**
`pjsip.conf`, `extensions.conf`, `manager.conf` and `rtp.conf`. It keeps backups, but
your own configuration stops working until you merge it back. Use `--no-asterisk` and
merge by hand instead:

```sh
python3 /opt/intercom-webtab/deploy/render.py \
  --config /etc/intercom-webtab/webtab.ini asterisk --stdout
```

## Upgrading

```sh
cd intercom-webtab
git pull
sudo ./deploy/install.sh
```

The station page notices the new page version within about two minutes, while idle, and
reloads itself. iOS needs one tap after a reload before it plays sound again (see
[ipad-setup.md](ipad-setup.md#sound)).

## Running without the installer

For development, or on other systems:

```sh
pip install aiohttp pillow            # or the distribution packages
cp config/webtab.example.ini webtab.ini
cp config/secrets.example.ini secrets.ini
python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > token
# in webtab.ini: token_file = token, secrets_file = secrets.ini, state_dir = state
python3 -m intercom_webtab --config webtab.ini
```

Relative paths in `[paths]` are resolved against the directory of `webtab.ini`. The
environment variable `IWT_CONFIG` can replace `--config`.

## Removing

```sh
sudo systemctl disable --now intercom-webtab intercom-webtab-firewall
sudo /opt/intercom-webtab/deploy/firewall/apply.sh --remove     # if the firewall was used
sudo rm /etc/systemd/system/intercom-webtab.service \
        /etc/systemd/system/intercom-webtab-firewall.service
sudo systemctl daemon-reload
sudo rm -rf /opt/intercom-webtab /var/lib/intercom-webtab
sudo rm -rf /etc/intercom-webtab      # configuration, secrets and token
sudo userdel webtab
```

Restore your earlier Asterisk files from the `/etc/asterisk/*.bak-*` copies, or remove
the generated ones.
