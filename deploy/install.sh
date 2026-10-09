#!/usr/bin/env bash
# Intercom WebTab installer for Debian 12, Ubuntu 22.04+ and Raspberry Pi OS.
#
#   sudo ./deploy/install.sh [options]
#
#   --no-asterisk   do not install or configure Asterisk (you set it up on this host yourself)
#   --firewall      install and enable the nftables firewall (deploy/firewall/)
#   --no-start      install only; do not enable or (re)start services
#   --skip-apt      do not install packages
#
# Safe to run again: existing configuration, secrets and token are kept; the program
# files are replaced; the Asterisk configuration is rendered again from webtab.ini
# (previous files are kept as *.bak-<timestamp>).
set -euo pipefail

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
APP=/opt/intercom-webtab
ETC=/etc/intercom-webtab
STATE=/var/lib/intercom-webtab
USER_NAME=webtab
WITH_ASTERISK=1
WITH_FIREWALL=0
START=1
APT=1

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-asterisk) WITH_ASTERISK=0 ;;
    --firewall) WITH_FIREWALL=1 ;;
    --no-start) START=0 ;;
    --skip-apt) APT=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
  shift
done

[ "$(id -u)" -eq 0 ] || die "run as root: sudo $0 $*"
[ -f "$SRC/intercom_webtab/__main__.py" ] || die "run from a checkout of the project ($SRC)"

# ---------------------------------------------------------------- system check
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    debian:12*|debian:13*|raspbian:12*|raspbian:13*) ;;
    ubuntu:22.04|ubuntu:24.04|ubuntu:2[4-9].*) ;;
    *) warn "untested system ${PRETTY_NAME:-unknown}; continuing" ;;
  esac
fi

# ---------------------------------------------------------------- packages
if [ "$APT" -eq 1 ]; then
  say "installing packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  pkgs="python3 python3-aiohttp python3-pil ffmpeg ca-certificates"
  [ "$WITH_FIREWALL" -eq 1 ] && pkgs="$pkgs nftables"
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $pkgs
  if [ "$WITH_ASTERISK" -eq 1 ] && ! command -v asterisk >/dev/null 2>&1; then
    cand=$(apt-cache policy asterisk 2>/dev/null | awk '/Candidate:/ {print $2}' || true)
    if [ -n "$cand" ] && [ "$cand" != "(none)" ]; then
      apt-get install -y -q asterisk
    else
      warn "Asterisk is not packaged for this release. Install Asterisk 18 or newer"
      warn "yourself (backports or a source build) and run this script again, or use"
      warn "--no-asterisk (Asterisk must run on this same machine)."
    fi
  fi
fi
python3 -c 'import aiohttp' 2>/dev/null || die "python3-aiohttp is missing"
command -v ffmpeg >/dev/null 2>&1 || warn "ffmpeg is missing: no video or audio"
if [ "$WITH_ASTERISK" -eq 1 ] && ! command -v asterisk >/dev/null 2>&1; then
  warn "asterisk not found: skipping the Asterisk configuration"
  WITH_ASTERISK=0
fi

# ---------------------------------------------------------------- user
if ! getent passwd "$USER_NAME" >/dev/null; then
  say "creating system user $USER_NAME"
  useradd --system --user-group --home-dir "$STATE" --no-create-home \
          --shell /usr/sbin/nologin "$USER_NAME"
fi

# ---------------------------------------------------------------- program files
say "installing program files to $APP"
rm -rf "$APP.new"
install -d -m 0755 "$APP.new" "$APP.new/tools"
cp -R "$SRC/intercom_webtab" "$APP.new/"
cp -R "$SRC/deploy" "$SRC/config" "$APP.new/"
cp "$SRC/tools/panelsim.py" "$APP.new/tools/"
find "$APP.new" -name '__pycache__' -prune -exec rm -rf {} +
chown -R root:root "$APP.new"
chmod -R u=rwX,go=rX "$APP.new"
chmod 0755 "$APP.new/deploy/install.sh" "$APP.new/deploy/firewall/apply.sh" "$APP.new/tools/panelsim.py"
rm -rf "$APP.old"
[ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"
rm -rf "$APP.old"

# ---------------------------------------------------------------- configuration
say "configuration in $ETC"
install -d -o root -g "$USER_NAME" -m 0750 "$ETC"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE"
if [ ! -f "$ETC/webtab.ini" ]; then
  install -o root -g "$USER_NAME" -m 0640 "$APP/config/webtab.example.ini" "$ETC/webtab.ini"
  NEW_CONFIG=1
else
  NEW_CONFIG=0
fi
if [ ! -f "$ETC/secrets.ini" ]; then
  install -o root -g "$USER_NAME" -m 0640 "$APP/config/secrets.example.ini" "$ETC/secrets.ini"
fi
# a random AMI secret instead of the placeholder (the same value goes to manager.conf)
python3 - "$ETC/secrets.ini" <<'PY'
import re, secrets, sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
new = re.sub(r"(?m)^(ami_secret\s*=\s*)CHANGE_ME\s*$",
             lambda m: m.group(1) + secrets.token_urlsafe(24), text, count=1)
if new != text:
    open(path, "w", encoding="utf-8").write(new)
    print("generated a random AMI secret")
PY
if [ ! -s "$ETC/token" ]; then
  say "generating the access token"
  ( umask 027; python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$ETC/token" )
fi
chown root:"$USER_NAME" "$ETC/token" "$ETC/secrets.ini" "$ETC/webtab.ini"
chmod 0640 "$ETC/token" "$ETC/secrets.ini" "$ETC/webtab.ini"
for f in allowlist sip_peers; do
  if [ ! -f "$ETC/$f.txt" ]; then
    { echo "# See $APP/deploy/firewall/$f.example.txt for the format."
      echo "# Empty: localhost only."; } > "$ETC/$f.txt"
    chmod 0644 "$ETC/$f.txt"
  fi
done

say "checking the configuration"
if ! runuser -u "$USER_NAME" -- env PYTHONPATH="$APP" python3 -m intercom_webtab \
       --config "$ETC/webtab.ini" --check; then
  die "fix $ETC/webtab.ini or $ETC/secrets.ini and run the installer again"
fi

# ---------------------------------------------------------------- Asterisk
ASTERISK_OK=0
if [ "$WITH_ASTERISK" -eq 1 ]; then
  say "rendering the Asterisk configuration into /etc/asterisk"
  if python3 "$APP/deploy/render.py" --config "$ETC/webtab.ini" asterisk \
       --out /etc/asterisk --owner asterisk; then
    ASTERISK_OK=1
    if [ "$START" -eq 1 ]; then
      systemctl enable asterisk >/dev/null 2>&1 || true
      systemctl restart asterisk
    fi
  else
    warn "Asterisk not configured yet: fill in $ETC/secrets.ini ([provider] for a SIP"
    warn "provider, or [panel:<id>] sip_password for door stations), then run again."
  fi
fi

# ---------------------------------------------------------------- services
say "installing systemd units"
install -m 0644 "$APP/deploy/systemd/intercom-webtab.service" /etc/systemd/system/
if [ "$WITH_FIREWALL" -eq 1 ]; then
  install -m 0644 "$APP/deploy/systemd/intercom-webtab-firewall.service" /etc/systemd/system/
fi
systemctl daemon-reload
if [ "$START" -eq 1 ]; then
  if [ "$WITH_FIREWALL" -eq 1 ]; then
    systemctl enable intercom-webtab-firewall.service >/dev/null
    systemctl restart intercom-webtab-firewall.service || warn "firewall failed: see journalctl -u intercom-webtab-firewall"
  fi
  systemctl enable intercom-webtab.service >/dev/null
  systemctl restart intercom-webtab.service
fi

# ---------------------------------------------------------------- summary
PORT=$(PYTHONPATH="$APP" python3 -c "from intercom_webtab.config import load; print(load('$ETC/webtab.ini').http.port)" 2>/dev/null || echo 8080)
HOST=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
echo
say "done"
[ "$NEW_CONFIG" -eq 1 ] && echo "  Edit $ETC/webtab.ini (location, panels) and $ETC/secrets.ini, then run this again."
[ "$WITH_ASTERISK" -eq 1 ] && [ "$ASTERISK_OK" -eq 0 ] && echo "  Asterisk still needs its settings (see the warning above)."
[ "$WITH_FIREWALL" -eq 1 ] && echo "  Firewall: add your home network to $ETC/allowlist.txt and the SIP peers to $ETC/sip_peers.txt,"
[ "$WITH_FIREWALL" -eq 1 ] && echo "  then: sudo systemctl reload intercom-webtab-firewall"
echo "  Open on the iPad (Safari, then Share > Add to Home Screen):"
echo "    http://${HOST:-<this-machine>}:$PORT/?k=$(cat "$ETC/token")"
echo "  Logs: journalctl -u intercom-webtab -f"
