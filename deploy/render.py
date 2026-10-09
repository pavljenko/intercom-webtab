#!/usr/bin/env python3
"""Render the Asterisk configuration and the nftables firewall from webtab.ini.

    python3 deploy/render.py --config /etc/intercom-webtab/webtab.ini asterisk --out /etc/asterisk
    python3 deploy/render.py --config /etc/intercom-webtab/webtab.ini asterisk --stdout
    python3 deploy/render.py --config /etc/intercom-webtab/webtab.ini firewall \\
        --allowlist /etc/intercom-webtab/allowlist.txt \\
        --sip-peers /etc/intercom-webtab/sip_peers.txt --out /run/intercom-webtab-firewall.nft

Templates live in deploy/asterisk/*.tmpl and deploy/firewall/*.tmpl. Placeholders look like
``{NAME}`` (``${...}`` dialplan variables are left alone). Blocks between
``;; BEGIN <name>`` and ``;; END <name>`` are kept, dropped or repeated:

* ``provider``: kept when [asterisk] trunk is set (variant A, SIP provider);
* ``station``: repeated for every panel with an ``endpoint`` (variant B, an IP door
  station registering to this Asterisk).

Existing files in --out are backed up as ``<name>.bak-<timestamp>`` before being replaced.
"""
import argparse
import ipaddress
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from intercom_webtab import config as configmod  # noqa: E402

PLACEHOLDER = re.compile(r"(?<!\$)\{([A-Z][A-Z0-9_]*)\}")
BLOCK = re.compile(r"^[;#]{2} BEGIN (\w+)\n(.*?)^[;#]{2} END \1\n", re.S | re.M)
RTP_START, RTP_END = 10000, 10100          # keep in sync with deploy/asterisk/rtp.conf
ASTERISK_FILES = ("pjsip.conf", "extensions.conf", "manager.conf")


class RenderError(Exception):
    pass


def fill(text, values, where):
    def rep(m):
        key = m.group(1)
        if key not in values:
            raise RenderError("%s: no value for {%s}" % (where, key))
        return str(values[key])
    return PLACEHOLDER.sub(rep, text)


def render(text, values, blocks, where):
    """``blocks`` maps a block name to a list of extra value dicts (one copy per dict)."""
    def rep(m):
        out = []
        for extra in blocks.get(m.group(1), []):
            v = dict(values)
            v.update(extra)
            out.append(fill(m.group(2), v, where))
        return "".join(out)
    return fill(BLOCK.sub(rep, text), values, where)


def _clean(value, what, pattern=r"^[A-Za-z0-9_.+@:-]*$"):
    if "\n" in str(value) or "\r" in str(value):
        raise RenderError("%s must be a single line" % what)
    if pattern and not re.match(pattern, str(value)):
        raise RenderError("%s contains characters Asterisk configuration cannot take: %r"
                          % (what, value))
    return value


def _secret(value, what):
    """A password for an Asterisk .conf file: one line, ';' escaped (it starts a comment)."""
    value = _clean(value, what, None)
    if value != value.strip():
        raise RenderError("%s must not start or end with spaces" % what)
    return value.replace(";", "\\;")


def asterisk_values(cfg):
    a, s, prov = cfg.asterisk, cfg.sip, cfg.provider
    if a.ami_secret == "CHANGE_ME":
        raise RenderError("secrets file [asterisk] ami_secret is still CHANGE_ME "
                          "(deploy/install.sh generates a random one)")
    v = {
        "UAS_HOST": s.uas_host, "UAS_PORT": s.uas_port,
        "AMI_PORT": a.ami_port,
        "AMI_USER": _clean(a.ami_user, "[asterisk] ami_user", r"^[A-Za-z0-9_.-]+$"),
        "AMI_SECRET": _secret(a.ami_secret, "[asterisk] ami_secret"),
        "TRUNK": a.trunk, "OWN_NUMBER": a.own_callerid,
        "INCOMING_CONTEXT": a.incoming_contexts[0],
        "VIEW_CONTEXT": a.view_context, "VIEW_EXTEN": a.view_exten,
        "DEMO_CONTEXT": a.demo_context,
        "RTP_START": RTP_START, "RTP_END": RTP_END,
        "PROVIDER_HOST": _clean(prov.host, "[provider] host"),
        "PROVIDER_PORT": prov.port,
        "PROVIDER_USER": _clean(prov.username, "[provider] username"),
        "PROVIDER_AUTH_USER": _clean(prov.auth_username, "[provider] auth_username"),
        "PROVIDER_PASSWORD": _secret(prov.password, "[provider] password"),
    }
    blocks = {"provider": [], "station": []}
    if a.trunk:
        missing = [k for k in ("host", "username", "password") if not prov[k]
                   or prov[k] == "CHANGE_ME"]
        if missing:
            raise RenderError("secrets file [provider]: set %s (variant A, SIP provider) or "
                              "leave [asterisk] trunk empty" % ", ".join(missing))
        blocks["provider"].append({})
    for p in cfg.panels:
        if p.endpoint:
            if not p.sip_password or p.sip_password == "CHANGE_ME":
                raise RenderError("secrets file [panel:%s] sip_password is needed for the door "
                                  "station endpoint %s" % (p.id, p.endpoint))
            blocks["station"].append({
                "PANEL_ID": p.id, "PANEL_ENDPOINT": p.endpoint,
                "PANEL_NUMBER": _clean(p.number, "[panel:%s] number" % p.id),
                "PANEL_LABEL": _clean(p.label, "[panel:%s] label" % p.id, r'^[^"<>;]*$'),
                "PANEL_PASSWORD": _secret(p.sip_password, "[panel:%s] sip_password" % p.id)})
    return v, blocks


def render_asterisk(cfg):
    values, blocks = asterisk_values(cfg)
    out = {}
    for name in ASTERISK_FILES:
        path = os.path.join(HERE, "asterisk", name + ".tmpl")
        with open(path, encoding="utf-8") as f:
            out[name] = render(f.read(), values, blocks, name + ".tmpl")
    with open(os.path.join(HERE, "asterisk", "rtp.conf"), encoding="utf-8") as f:
        out["rtp.conf"] = f.read()
    return out


def write_files(files, outdir, mode=0o640, owner=None):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(outdir, exist_ok=True)
    for name, text in files.items():
        path = os.path.join(outdir, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                if f.read() == text:
                    print("unchanged %s" % path)
                    continue
            os.replace(path, "%s.bak-%s" % (path, stamp))
            print("backed up %s" % path)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if owner is not None:
            try:
                import grp
                import pwd
                os.chown(tmp, pwd.getpwnam(owner).pw_uid, grp.getgrnam(owner).gr_gid)
            except (KeyError, PermissionError, ImportError):
                pass
        os.replace(tmp, path)
        print("wrote %s" % path)


# ---------------------------------------------------------------------------- firewall

def read_list(path):
    """Addresses / networks / host names, one per line, '#' comments."""
    out = []
    if not path:
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.split("#", 1)[0].strip()
                if ln:
                    out.append(ln)
    except FileNotFoundError:
        pass
    return out


def to_networks(items, warn):
    v4, v6 = [], []
    for it in items:
        try:
            net = ipaddress.ip_network(it, strict=False)
            (v4 if net.version == 4 else v6).append(net)
            continue
        except ValueError:
            pass
        if not re.match(r"^[A-Za-z0-9.-]+$", it):
            warn("ignored %r: not an address, network or host name" % it)
            continue
        try:
            infos = socket.getaddrinfo(it, None)
        except socket.gaierror:
            warn("ignored %r: the name does not resolve" % it)
            continue
        for info in infos:
            net = ipaddress.ip_network(info[4][0])
            (v4 if net.version == 4 else v6).append(net)
    return (sorted(set(ipaddress.collapse_addresses(v4)), key=str),
            sorted(set(ipaddress.collapse_addresses(v6)), key=str))


def elements(nets):
    return ("elements = { %s }" % ", ".join(str(n) for n in nets)) if nets else ""


def render_firewall(cfg, allowlist, sip_peers, warn):
    peers = read_list(sip_peers)
    if cfg is not None and cfg.asterisk.trunk and cfg.provider.host:
        peers.append(cfg.provider.host)               # the provider is always a SIP peer
    p4, p6 = to_networks(peers, warn)
    h4, h6 = to_networks(read_list(allowlist), warn)
    if not (p4 or p6):
        warn("no SIP peers: SIP and RTP are open to localhost only")
    if not (h4 or h6):
        warn("empty HTTP allowlist: the page is reachable from localhost only")
    if cfg is not None:
        s = cfg.sip
        http, ami = cfg.http.port, cfg.asterisk.ami_port
        internal = [s.uas_port, s.rtp_audio_port, s.rtp_audio_port + 1, s.rtp_video_port,
                    s.rtp_video_port + 1, s.audio_out_port]
    else:
        http, ami, internal = 8080, 5038, [5080, 41000, 41001, 41002, 41003, 41100]
    values = {"SIP_PORT": 5060, "RTP_START": RTP_START, "RTP_END": RTP_END, "HTTP_PORT": http,
              "AMI_PORT": ami, "INTERNAL_UDP": ", ".join(str(p) for p in sorted(set(internal))),
              "SIP_PEERS_V4": elements(p4), "SIP_PEERS_V6": elements(p6),
              "HTTP_ALLOW_V4": elements(h4), "HTTP_ALLOW_V6": elements(h6),
              "RENDERED": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(os.path.join(HERE, "firewall", "intercom-webtab.nft.tmpl"), encoding="utf-8") as f:
        return render(f.read(), values, {}, "intercom-webtab.nft.tmpl")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render Asterisk and firewall configuration")
    ap.add_argument("--config", default=configmod.DEFAULT_CONFIG_PATH)
    sub = ap.add_subparsers(dest="what")
    a = sub.add_parser("asterisk", help="pjsip.conf, extensions.conf, manager.conf, rtp.conf")
    a.add_argument("--out", help="directory to write into (existing files are backed up)")
    a.add_argument("--stdout", action="store_true", help="print instead of writing")
    a.add_argument("--owner", default="asterisk", help="owner of the written files")
    f = sub.add_parser("firewall", help="nftables ruleset")
    f.add_argument("--allowlist", default="/etc/intercom-webtab/allowlist.txt")
    f.add_argument("--sip-peers", default="/etc/intercom-webtab/sip_peers.txt")
    f.add_argument("--out", help="file to write (default: standard output)")
    args = ap.parse_args(argv)

    def warn(msg):
        print("render: %s" % msg, file=sys.stderr)

    if args.what == "asterisk":
        try:
            cfg = configmod.load(args.config, require_token=False)
            files = render_asterisk(cfg)
        except (configmod.ConfigError, RenderError) as e:
            print("render: %s" % e, file=sys.stderr)
            return 2
        if args.stdout or not args.out:
            for name, text in files.items():
                print(";;;;;;;;;; %s\n%s" % (name, text))
        else:
            write_files(files, args.out, owner=args.owner)
        return 0
    if args.what == "firewall":
        try:
            cfg = configmod.load(args.config, require_token=False)
        except configmod.ConfigError as e:
            warn("%s; using default ports" % e)
            cfg = None
        try:
            text = render_firewall(cfg, args.allowlist, args.sip_peers, warn)
        except RenderError as e:
            print("render: %s" % e, file=sys.stderr)
            return 2
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        else:
            sys.stdout.write(text)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
