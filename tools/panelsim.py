#!/usr/bin/env python3
"""Door-panel simulator: a minimal SIP caller that rings the station like a real panel.

It sends an INVITE offering PCMA audio and H.264 video, waits (the station rings and does
not answer by itself), and once answered streams an H.264 test picture and a 440 Hz tone
with ffmpeg, then hangs up with BYE.

    python3 tools/panelsim.py                         # demo call straight into the station
    python3 tools/panelsim.py --to asterisk           # through Asterisk (demo context)
    python3 tools/panelsim.py --number 201 --display ""   # looks like a real panel 201

With the default display name "panelsim" the station treats the call as a demo: Open
never opens a door. Exit codes: 0 answered, 3 declined/cancelled (486/487/603/...),
1 no answer or error.
"""
import argparse
import random
import shutil
import signal
import socket
import subprocess
import sys
import time


def build_sdp(ip, a_port, v_port, video=True):
    sdp = ("v=0\r\no=panelsim 0 0 IN IP4 %s\r\ns=panelsim\r\nc=IN IP4 %s\r\nt=0 0\r\n"
           "m=audio %d RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\na=sendrecv\r\n" % (ip, ip, a_port))
    if video:
        sdp += ("m=video %d RTP/AVP 99\r\na=rtpmap:99 H264/90000\r\n"
                "a=fmtp:99 packetization-mode=1;profile-level-id=4D001E\r\na=sendrecv\r\n" % v_port)
    return sdp


class Call(object):
    def __init__(self, args):
        self.a = args
        self.ip = "127.0.0.1"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.ip, 0))
        self.port = self.sock.getsockname()[1]
        self.a_port = random.randrange(42000, 45998, 2)
        self.v_port = self.a_port + 2
        if args.to == "asterisk":
            self.dst = (args.asterisk_host, args.asterisk_port)
            self.ruri = "sip:%s@%s" % (args.exten, args.asterisk_host)
        else:
            self.dst = (args.uas_host, args.uas_port)
            self.ruri = "sip:webtab@%s:%d" % (args.uas_host, args.uas_port)
        self.cid = "sim-%d@%s" % (random.randint(1, 10 ** 9), self.ip)
        self.tag = "ps%d" % random.randint(1, 10 ** 9)
        self.branch = "z9hG4bK%d" % random.randint(1, 10 ** 9)
        user = args.number or "panelsim"
        disp = ('"%s" ' % args.display) if args.display else ""
        self.from_hdr = "%s<sip:%s@%s>;tag=%s" % (disp, user, self.ip, self.tag)
        self.to_hdr = "<%s>" % self.ruri
        self.cseq = 1
        self.answered = False
        self.procs = []

    def send(self, text):
        self.sock.sendto(text.encode("utf-8"), self.dst)

    def request(self, method, cseq, branch, body="", to=None, extra=""):
        msg = ("%s %s SIP/2.0\r\nVia: SIP/2.0/UDP %s:%d;branch=%s;rport\r\nMax-Forwards: 70\r\n"
               "From: %s\r\nTo: %s\r\nCall-ID: %s\r\nCSeq: %d %s\r\n"
               "Contact: <sip:panelsim@%s:%d>\r\nUser-Agent: panelsim\r\n%s"
               % (method, self.ruri, self.ip, self.port, branch, self.from_hdr, to or self.to_hdr,
                  self.cid, cseq, method, self.ip, self.port, extra))
        if body:
            msg += "Content-Type: application/sdp\r\nContent-Length: %d\r\n\r\n%s" % (
                len(body.encode("utf-8")), body)
        else:
            msg += "Content-Length: 0\r\n\r\n"
        self.send(msg)

    def recv(self, timeout):
        self.sock.settimeout(max(0.01, timeout))
        try:
            data, _ = self.sock.recvfrom(65535)
        except socket.timeout:
            return None
        return data.decode("utf-8", "ignore")

    def reply_to(self, msg, code, reason):
        """Answer a request from the other side (BYE / OPTIONS)."""
        lines = [ln for ln in msg.split("\r\n") if ln]
        keep = [ln for ln in lines[1:] if ln.split(":", 1)[0].lower() in
                ("via", "v", "from", "f", "to", "t", "call-id", "i", "cseq")]
        self.sock.sendto(("SIP/2.0 %s %s\r\n%s\r\nContent-Length: 0\r\n\r\n"
                          % (code, reason, "\r\n".join(keep))).encode("utf-8"), self.dst)

    def invite(self):
        sdp = build_sdp(self.ip, self.a_port, self.v_port, video=not self.a.no_video)
        self.request("INVITE", self.cseq, self.branch, body=sdp)
        print("INVITE -> %s:%d (%s)" % (self.dst[0], self.dst[1], self.from_hdr.split(";")[0]))
        deadline = time.time() + self.a.ring_timeout
        while time.time() < deadline:
            msg = self.recv(deadline - time.time())
            if msg is None:
                break
            first = msg.split("\r\n", 1)[0]
            if not first.startswith("SIP/2.0"):
                if first.startswith("BYE") or first.startswith("OPTIONS"):
                    self.reply_to(msg, "200", "OK")
                continue
            code = int(first.split()[1])
            print("response: %s" % first)
            if code < 200:
                continue
            to = ""
            for ln in msg.split("\r\n"):
                if ln.lower().startswith("to:"):
                    to = ln.split(":", 1)[1].strip()
            # ACK of an error response belongs to the INVITE transaction (same branch)
            self.request("ACK", self.cseq, self.branch if code >= 300 else self.branch + "a",
                         to=to)
            if code >= 300:
                print("declined: %d" % code)
                return code, None
            self.to_hdr = to
            self.answered = True
            body = msg.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in msg else ""
            ports = {}
            for ln in body.replace("\r", "").split("\n"):
                p = ln.split()
                if ln.startswith("m=") and len(p) >= 2:
                    ports[p[0][2:]] = int(p[1])
            return code, ports
        # no final answer: give up like a real panel would
        self.request("CANCEL", self.cseq, self.branch)
        print("no answer within %d s, CANCEL sent" % self.a.ring_timeout)
        msg = self.recv(2.0)
        while msg is not None and " 487 " not in msg.split("\r\n", 1)[0]:
            msg = self.recv(2.0)
        return None, None

    def stream(self, ports):
        ff = shutil.which("ffmpeg")
        if self.a.no_media or not ff:
            if not ff:
                print("ffmpeg not found: no media sent")
            return
        host = self.dst[0]
        if ports.get("video") and not self.a.no_video:
            self.procs.append(subprocess.Popen(
                [ff, "-nostdin", "-loglevel", "error", "-re", "-f", "lavfi",
                 "-i", "testsrc2=size=320x240:rate=15", "-c:v", "libx264", "-profile:v", "main",
                 "-level", "3.0", "-pix_fmt", "yuv420p", "-g", "15", "-an", "-f", "rtp",
                 "-payload_type", "99", "rtp://%s:%d" % (host, ports["video"])],
                stdin=subprocess.DEVNULL))
            print("H.264 test video -> %s:%d" % (host, ports["video"]))
        if ports.get("audio"):
            self.procs.append(subprocess.Popen(
                [ff, "-nostdin", "-loglevel", "error", "-re", "-f", "lavfi",
                 "-i", "sine=frequency=440:sample_rate=8000", "-ac", "1", "-c:a", "pcm_alaw",
                 "-f", "rtp", "-payload_type", "8", "rtp://%s:%d" % (host, ports["audio"])],
                stdin=subprocess.DEVNULL))
            print("440 Hz tone -> %s:%d" % (host, ports["audio"]))

    def talk(self, seconds):
        """Stay in the call; a BYE from the other side ends it early."""
        end = time.time() + seconds
        while time.time() < end:
            msg = self.recv(end - time.time())
            if msg is None:
                continue
            first = msg.split("\r\n", 1)[0]
            if first.startswith("BYE"):
                self.reply_to(msg, "200", "OK")
                self.answered = False
                print("BYE received, call over")
                return
            if first.startswith("OPTIONS"):
                self.reply_to(msg, "200", "OK")

    def stop(self):
        for p in self.procs:
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass
        self.procs = []
        if self.answered:
            self.cseq += 1
            self.request("BYE", self.cseq, self.branch + "b")
            self.answered = False
            print("BYE sent")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Door-panel SIP simulator for Intercom WebTab")
    ap.add_argument("--to", choices=("web", "asterisk"), default="web",
                    help="ring the station directly (web) or through Asterisk")
    ap.add_argument("--number", default="", help="caller number (From user); default panelsim")
    ap.add_argument("--display", default="panelsim",
                    help='display name; "panelsim" marks a demo call, "" for none')
    ap.add_argument("--seconds", type=float, default=30, help="talk time after the answer")
    ap.add_argument("--ring-timeout", type=float, default=60, help="give up (CANCEL) after this")
    ap.add_argument("--uas-host", default="127.0.0.1")
    ap.add_argument("--uas-port", type=int, default=5080)
    ap.add_argument("--asterisk-host", default="127.0.0.1")
    ap.add_argument("--asterisk-port", type=int, default=5060)
    ap.add_argument("--exten", default="webtab", help="extension dialled on Asterisk")
    ap.add_argument("--no-video", action="store_true", help="offer audio only")
    ap.add_argument("--no-media", action="store_true", help="do not start ffmpeg")
    args = ap.parse_args(argv)

    call = Call(args)

    def on_term(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    code = None
    try:
        code, ports = call.invite()
        if code is None:
            return 1
        if code >= 300:
            return 3
        call.stream(ports)
        call.talk(args.seconds)
        return 0
    except KeyboardInterrupt:
        if not call.answered and code is None:
            call.request("CANCEL", call.cseq, call.branch)
            print("interrupted while ringing, CANCEL sent")
        return 0 if call.answered else 1
    finally:
        call.stop()
        sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
