"""A deliberately tiny SIP user agent server (UDP, localhost only).

Asterisk rings this UAS (PJSIP endpoint ``webtab``) for every door-panel call. The UAS
never answers on its own: it answers ``100 Trying`` + ``180 Ringing`` and keeps the
INVITE until the owner presses Answer or Open on the page. Only then ``200 OK`` with an
SDP goes out (see ``calls.CallManager``).

Requests handled: INVITE, ACK (ignored), CANCEL, BYE, OPTIONS. Everything else gets 405.
The helpers below are pure functions and are unit-tested.
"""
import asyncio
import logging
import zlib

log = logging.getLogger("iwt.sip")

DEMO_NAME = "panelsim"


# ---------------------------------------------------------------------------- parsing

def split_message(msg):
    """Return (head, body) of a SIP message (CRLF or bare LF line endings)."""
    if "\r\n\r\n" in msg:
        head, body = msg.split("\r\n\r\n", 1)
    elif "\n\n" in msg:
        head, body = msg.split("\n\n", 1)
    else:
        head, body = msg, ""
    return head, body


_COMPACT = {"f": "from", "t": "to", "v": "via", "i": "call-id", "m": "contact",
            "l": "content-length", "c": "content-type"}


def header_all(msg, name):
    """All values of header ``name`` (case-insensitive, compact forms understood)."""
    name = name.lower()
    head = split_message(msg)[0]
    out = []
    for ln in head.replace("\r\n", "\n").split("\n")[1:]:
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        k = k.strip().lower()
        if _COMPACT.get(k, k) == name:
            out.append(v.strip())
    return out


def header(msg, name):
    """First value of header ``name`` or an empty string."""
    vals = header_all(msg, name)
    return vals[0] if vals else ""


def first_line(msg):
    return msg.split("\n", 1)[0].strip()


def method(msg):
    """Request method (``INVITE``...) or an empty string for responses."""
    fl = first_line(msg)
    if not fl or fl.startswith("SIP/2.0"):
        return ""
    return fl.split(" ", 1)[0].upper()


def parse_name_addr(value):
    """``"Name" <sip:user@host>;tag=x`` -> (display_name, user). Works with bare URIs."""
    value = (value or "").strip()
    display = ""
    if "<" in value:
        display = value.split("<", 1)[0].strip().strip('"').strip()
        uri = value.split("<", 1)[1].split(">", 1)[0]
    else:
        uri = value.split(";", 1)[0]
    user = ""
    low = uri.lower()
    for scheme in ("sips:", "sip:", "tel:"):
        if low.startswith(scheme):
            rest = uri[len(scheme):]
            user = rest.split("@", 1)[0] if "@" in rest or scheme == "tel:" else ""
            user = user.split(";", 1)[0]
            break
    return display, user


def caller(msg):
    """(display_name, user) of the From header."""
    return parse_name_addr(header(msg, "From"))


def is_demo(msg):
    """A call from the panel simulator (display name or user ``panelsim``)."""
    display, user = caller(msg)
    return display.lower() == DEMO_NAME or user.lower() == DEMO_NAME


def _num(x):
    return (x or "").strip().lstrip("+")


def panel_from_callerid(msg, panels):
    """Panel id whose ``number`` equals the caller's number, else an empty string.

    ``panels`` is a list of objects (or dicts) with ``id`` and ``number``."""
    _, user = caller(msg)
    user = _num(user)
    if not user:
        return ""
    for p in panels:
        pid = p["id"] if isinstance(p, dict) else p.id
        num = p["number"] if isinstance(p, dict) else p.number
        if num and _num(num) == user:
            return pid
    return ""


def is_own_call(msg, own_callerid):
    """The INVITE carries our own CallerID: it is the camera-view call we ordered with
    Originate. Matched exactly on the user part or the display name (never by substring)."""
    display, user = caller(msg)
    own = _num(own_callerid)
    return bool(own) and (_num(user) == own or _num(display) == own)


def sdp_media_dest(body, media):
    """(ip, port) where the remote side wants ``media`` (``audio``/``video``) sent."""
    port, sess_ip, media_ip, in_media = None, None, None, None
    for ln in body.replace("\r", "").split("\n"):
        ln = ln.strip()
        if ln.startswith("m="):
            in_media = ln[2:].split(" ", 1)[0]
            if in_media == media and port is None:
                try:
                    port = int(ln.split()[1])
                except (IndexError, ValueError):
                    port = None
        elif ln.startswith("c=IN IP4"):
            addr = ln.split()[-1].split("/", 1)[0]
            if in_media is None:
                sess_ip = addr                       # session level
            elif in_media == media and media_ip is None:
                media_ip = addr                      # media level wins
    return media_ip or sess_ip or "127.0.0.1", port


def sdp_audio_dest(body):
    return sdp_media_dest(body, "audio")


def sdp_video_pt(body):
    """(has_video, payload_type) of the first ``m=video`` line."""
    for ln in body.replace("\r", "").split("\n"):
        p = ln.split()
        if ln.startswith("m=video") and len(p) >= 4:
            try:
                if int(p[1]) > 0:
                    return True, p[3]
            except ValueError:
                pass
    return False, "96"


def sdp_video_fmtp(body, pt):
    for ln in body.replace("\r", "").split("\n"):
        ln = ln.strip()
        if ln.startswith("a=fmtp:%s " % pt):
            return ln.split(" ", 1)[1]
    return "packetization-mode=1"


def answer_sdp(audio_port, video_port=None, video_pt=None, video_fmtp=None, ip="127.0.0.1"):
    """Our SDP answer: PCMA audio, plus H.264 video when the offer had video."""
    lines = ["v=0", "o=- 0 0 IN IP4 %s" % ip, "s=webtab", "c=IN IP4 %s" % ip, "t=0 0",
             "m=audio %d RTP/AVP 8" % audio_port, "a=rtpmap:8 PCMA/8000", "a=sendrecv"]
    if video_port and video_pt is not None:
        lines += ["m=video %d RTP/AVP %s" % (video_port, video_pt),
                  "a=rtpmap:%s H264/90000" % video_pt,
                  "a=fmtp:%s %s" % (video_pt, video_fmtp or "packetization-mode=1"),
                  "a=sendrecv"]
    return "\r\n".join(lines) + "\r\n"


def to_tag(call_id):
    """Stable To-tag per Call-ID: every response of one dialog carries the same tag."""
    return "iwt%08x" % (zlib.crc32((call_id or "").encode("utf-8")) & 0xffffffff)


def build_response(msg, code, reason, contact, sdp=""):
    """A response to request ``msg`` (Via/From/To/Call-ID/CSeq copied)."""
    to = header(msg, "To")
    cid = header(msg, "Call-ID")
    if ";tag=" not in to.lower() and int(code) > 100:
        to = to + ";tag=" + to_tag(cid)
    lines = ["SIP/2.0 %s %s" % (code, reason)]
    for v in header_all(msg, "Via"):
        lines.append("Via: " + v)
    lines += ["From: " + header(msg, "From"), "To: " + to, "Call-ID: " + cid,
              "CSeq: " + header(msg, "CSeq"), "Contact: " + contact,
              "Server: intercom-webtab"]
    if sdp:
        body = sdp.encode("utf-8")
        lines += ["Content-Type: application/sdp", "Content-Length: %d" % len(body), "", sdp]
    else:
        lines += ["Content-Length: 0", "", ""]
    return "\r\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------- protocol

class SipUas(asyncio.DatagramProtocol):
    """Dispatches requests to ``handler`` (a ``calls.CallManager``):
    ``sip_invite(msg, addr)``, ``sip_cancel(msg, addr)``, ``sip_bye(msg, addr)``.
    The handler answers through ``reply(msg, addr, code, reason, sdp)``."""

    def __init__(self, handler, host, port):
        self.handler = handler
        self.contact = "<sip:webtab@%s:%d>" % (host, port)
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        log.info("SIP UAS listening on %s", self.contact[5:-1])

    def reply(self, msg, addr, code, reason, sdp=""):
        if self.transport is None:
            return
        try:
            self.transport.sendto(build_response(msg, code, reason, self.contact, sdp), addr)
        except Exception as e:                       # socket closed during shutdown
            log.warning("SIP reply %s failed: %s", code, e)

    def datagram_received(self, data, addr):
        msg = data.decode("utf-8", "ignore")
        if not msg.strip():
            return                                   # keep-alive (CRLF)
        m = method(msg)
        if not m:
            return                                   # a response: we never send requests
        try:
            if m == "INVITE":
                self.handler.sip_invite(msg, addr)
            elif m == "CANCEL":
                self.handler.sip_cancel(msg, addr)
            elif m == "BYE":
                self.handler.sip_bye(msg, addr)
            elif m == "ACK":
                pass
            elif m == "OPTIONS":
                self.reply(msg, addr, "200", "OK")
            else:
                self.reply(msg, addr, "405", "Method Not Allowed")
        except Exception:
            log.exception("SIP %s handling failed", m)
            if m in ("INVITE", "BYE", "CANCEL"):
                self.reply(msg, addr, "500", "Server Internal Error")

    def error_received(self, exc):
        log.debug("SIP socket error: %s", exc)
