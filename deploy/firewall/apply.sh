#!/bin/sh
# Load the Intercom WebTab nftables table (idempotent, fail-closed).
#
#   sudo /opt/intercom-webtab/deploy/firewall/apply.sh            # (re)load
#   sudo /opt/intercom-webtab/deploy/firewall/apply.sh --remove   # remove the table
#
# Lists (one address, network or host name per line, '#' comments):
#   /etc/intercom-webtab/allowlist.txt   networks that may open the page
#   /etc/intercom-webtab/sip_peers.txt   SIP provider / door station addresses
# An empty or missing list means "localhost only" for that service.
# Who is knocking:  sudo tcpdump -ni any "tcp dst port 8080"
set -eu

APP=${IWT_APP_DIR:-/opt/intercom-webtab}
ETC=${IWT_ETC_DIR:-/etc/intercom-webtab}
RUN=${RUNTIME_DIRECTORY:-/run}
OUT="$RUN/intercom-webtab-firewall.nft"

command -v nft >/dev/null 2>&1 || { echo "apply.sh: nft not found (apt install nftables)" >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
  nft delete table inet intercom_webtab 2>/dev/null || true
  echo "intercom-webtab firewall table removed"
  exit 0
fi

if python3 "$APP/deploy/render.py" --config "$ETC/webtab.ini" firewall \
     --allowlist "$ETC/allowlist.txt" --sip-peers "$ETC/sip_peers.txt" --out "$OUT.tmp" \
   && nft -c -f "$OUT.tmp"; then
  mv "$OUT.tmp" "$OUT"
  nft -f "$OUT"
  echo "intercom-webtab firewall loaded from $OUT"
  exit 0
fi

# Rendering failed: close the default ports to everyone but localhost rather than
# leaving them open.
rm -f "$OUT.tmp"
logger -t intercom-webtab-fw "rendering failed: emergency rules, page/SIP/RTP for localhost only" 2>/dev/null || true
echo "apply.sh: rendering failed, loading emergency rules (localhost only)" >&2
# Ports come from webtab.ini when it is readable (defaults otherwise), so a station on
# non-default ports is closed too.
PORTS=$(python3 - "$ETC/webtab.ini" <<'PY' 2>/dev/null || echo "8080 5080 41000 41002 41100 5038"
import configparser, sys
c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
c.read(sys.argv[1])
g = lambda s, k, d: c.get(s, k, fallback=str(d)).strip() or str(d)
print(g("http", "port", 8080), g("sip", "uas_port", 5080), g("sip", "rtp_audio_port", 41000),
      g("sip", "rtp_video_port", 41002), g("sip", "audio_out_port", 41100), g("asterisk", "ami_port", 5038))
PY
)
set -- $PORTS
HTTP=$1 UAS=$2 RTPA=$3 RTPV=$4 AOUT=$5 AMI=$6
nft -f - <<EOF
table inet intercom_webtab
delete table inet intercom_webtab
table inet intercom_webtab {
	chain input {
		type filter hook input priority -10; policy accept;
		iif "lo" accept
		ct state established,related accept
		udp dport 5060 drop
		tcp dport 5060 drop
		udp dport 10000-10100 drop
		udp dport { $UAS, $RTPA, $((RTPA + 1)), $RTPV, $((RTPV + 1)), $AOUT } drop
		tcp dport { $AMI, $HTTP } drop
	}
}
EOF
exit 1
