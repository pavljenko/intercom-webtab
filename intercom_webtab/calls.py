"""Call, camera-view and door-opening logic (no HTTP here; ``web`` wraps it).

Every public coroutine returns ``(http_status, json_dict)``. Rules that must be kept:

* The station **rings, it never answers by itself**: INVITE gets 100/180, and 200 OK goes
  out only on Answer or Open. A guest who heard "picked up" would start talking to nobody.
* A call **from a panel number is always a guest call**. Only an INVITE carrying our own
  CallerID can be the camera-view call we ordered, and only while that order is live;
  a stale one (view already closed or switched) is rejected with 603, otherwise the
  station would call itself and Open would open a door nobody is looking at.
* BYE and CANCEL act only on the dialog they belong to (Call-ID): a late BYE of an old
  view must not end a guest's call, and the other way round.
* Opening from a camera view calls the panel (``Originate ... Application=Wait``) and
  waits for that channel. Any incoming call meanwhile (``call_seq``) aborts the wait,
  otherwise the guest's channel could be taken for ours and the door would open itself.
* While one panel rings, Open/hang-up for the other panel is refused (409 / ignored).
* Panels do not hear DTMF in the first seconds after the line comes up: the digit is sent
  after ``open_settle`` seconds and then repeated (``open_repeat``). A spare digit is
  harmless, a missed one is not.
* A demo call (the panel simulator) never opens a door.
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time

from . import ami as amimod
from . import sip_uas

log = logging.getLogger("iwt.calls")

VIEW_ORDER_TTL = 20         # s: a camera-view order older than this is stale
VIEW_TRACK_TIME = 15        # s: how long to look for the channel of a view call
CALL_CHANNEL_TRIES = 8      # x 0.3 s: the guest channel becomes Up in 0.3-1.5 s
OPEN_POLL = 0.15            # s between channel polls while our door call comes up
DTMF_DURATION = 250         # ms per digit
DEMO_SECONDS = 30


class Hub(object):
    """Connected pages (WebSockets). Works with any object that has ``send_str``,
    ``send_bytes`` and ``close`` coroutines."""

    def __init__(self):
        self.clients = set()
        self._tasks = set()

    def spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def notify(self, ev):
        """Send to all pages in parallel, each with a timeout: one stuck client (a
        half-open connection with a full buffer) must not delay the call signal."""
        txt = json.dumps(ev)
        clients = list(self.clients)
        if not clients:
            return

        async def one(ws):
            try:
                await asyncio.wait_for(ws.send_str(txt), 3)
                return None
            except Exception:
                return ws

        for dead in await asyncio.gather(*[one(w) for w in clients]):
            if dead is not None:
                self.clients.discard(dead)
                self.spawn(dead.close())            # close() waits for the peer: background

    def notify_soon(self, ev):
        self.spawn(self.notify(ev))

    def broadcast_bytes(self, data):
        for ws in list(self.clients):
            try:
                self.spawn(ws.send_bytes(data))
            except Exception:
                pass


class CallManager(object):
    def __init__(self, cfg, media, ami, hub, runtime_dir=None, sleep=asyncio.sleep,
                 panelsim=None):
        self.cfg = cfg
        self.media = media
        self.ami = ami
        self.hub = hub
        self.runtime_dir = runtime_dir
        self.sleep = sleep
        self.panelsim = panelsim if panelsim is not None else _find_panelsim()
        self.reply = None                 # set by the SIP UAS: reply(msg, addr, code, reason, sdp)
        self.prefixes = amimod.channel_prefixes(cfg)
        self.default_panel = cfg.panels[0].id
        # incoming call
        self.ring = None                  # unanswered INVITE: {"msg", "addr"}
        self.answered = False
        self.answered_at = 0.0
        self.call_cid = None              # Call-ID of the guest's call
        self.call_seq = 0                 # +1 per incoming call; aborts a pending door call
        self.last_invite_cid = None       # to recognise INVITE retransmissions
        self.demo = False                 # current call comes from the simulator
        self.demo_proc = None
        # what is going on
        self.active = False
        self.mode = None                  # "incoming" | "view" | None
        self.panel = None                 # panel of the call or view (for Open)
        self.src = None                   # "sip" | "rtsp": where the view media comes from
        # camera view through SIP: {panel, t, chan, cancelled, consumed, up_at}
        self.view_req = None
        self.view_cid = None
        # our own door call from a camera view: {chan, panel, up_at}; while it is alive a
        # second Open sends the digit straight into it (a new call to a busy panel fails)
        self.open_chan = None

    # ------------------------------------------------------------------ small helpers
    def _panel(self, pid):
        return self.cfg.panel(pid or self.default_panel) or self.cfg.panels[0]

    def _reply(self, msg, addr, code, reason, sdp=""):
        if self.reply:
            self.reply(msg, addr, code, reason, sdp)

    def _wanted(self, body):
        pid = body.get("panel") if isinstance(body, dict) else None
        return pid if pid in self.cfg.panel_ids else None

    @property
    def in_call(self):
        return bool(self.ring or self.answered)

    async def _channels(self):
        return await self.ami.channels()

    async def _trunk(self):
        return amimod.on_trunk(await self._channels(), self.prefixes)

    def _find_call_channel(self, chans, up_only=True):
        a = self.cfg.asterisk
        num = self._panel(self.panel).number
        return amimod.find_call_channel(chans, self.prefixes, a.incoming_contexts,
                                        a.view_context, number=num, up_only=up_only, logger=log)

    async def log_channels(self, tag):
        """One log line about trunk channels: shows the real contexts on a live call."""
        try:
            info = await self._trunk()
            log.info("%s: channels %s", tag, ", ".join(
                "%s[%s/%s/%s]" % (c["chan"][-8:], c["state"], c["ctx"], c["app"])
                for c in info) or "none")
        except Exception:
            pass

    def hello_events(self):
        """Events for a page that just connected: show a call in progress at once, or
        clear a call that ended while the page was offline."""
        out = [{"ev": "hello", "active": self.active}]
        if self.in_call:
            out.append({"ev": "call", "state": "ringing", "panel": self.panel})
            if self.answered:
                out.append({"ev": "call", "state": "answered", "panel": self.panel})
        else:
            out.append({"ev": "call", "state": "ended"})
        return out

    # ------------------------------------------------------------------ view media
    def cancel_view_req(self):
        """Drop the camera-view order; hang up its call if the channel is already known
        (otherwise ``track_view_chan`` does it once it finds the channel)."""
        vr, self.view_req = self.view_req, None
        if vr:
            vr["cancelled"] = True
            if vr.get("chan"):
                self.hub.spawn(self.ami.action({"Action": "Hangup", "Channel": vr["chan"]}))

    def end_view_media(self):
        """Stop the view: audio / SIP decoder and the view call. The mode is kept."""
        if self.src == "sip":
            self.media.stop_call_media()
        else:
            self.media.stop_audio()
        self.cancel_view_req()
        self.src = None
        self.active = False
        self.view_cid = None

    async def track_view_chan(self, vr, before):
        """Find the channel of our view Originate (view context, new since ``before``) and
        remember when it came up: Open and cancellation need it."""
        t_end = time.time() + VIEW_TRACK_TIME
        while time.time() < t_end:
            await self.sleep(0.5)
            chan, up = amimod.find_view_channel(await self._channels(), self.prefixes, before,
                                                self.cfg.asterisk.view_context)
            if chan:
                vr["chan"] = chan
                if up and not vr.get("up_at"):
                    vr["up_at"] = time.time()
            if vr["cancelled"] and vr.get("chan"):
                await self.ami.action({"Action": "Hangup", "Channel": vr["chan"]})
                log.info("view call of a cancelled view hung up")
                return
            if vr.get("up_at"):
                return

    # ------------------------------------------------------------------ SIP events
    def sip_invite(self, msg, addr):
        cid = sip_uas.header(msg, "Call-ID")
        frm = sip_uas.header(msg, "From")
        body = sip_uas.split_message(msg)[1]
        # Our own camera-view call (we ordered it with Originate): answer at once and
        # quietly. A call FROM a panel number is never this, whatever else is going on.
        if (sip_uas.is_own_call(msg, self.cfg.asterisk.own_callerid)
                and not sip_uas.panel_from_callerid(msg, self.cfg.panels)):
            self._own_invite(msg, addr, cid, body)
            return
        # A guest. Retransmission of the INVITE we already ring for:
        if cid and cid == self.last_invite_cid and self.in_call:
            self._reply(msg, addr, "100", "Trying")
            self._reply(msg, addr, "180", "Ringing")
            return
        # A newer call replaces an unanswered older one: give the old one a final answer.
        if self.ring and self.ring.get("cid") != cid:
            old = self.ring
            self._reply(old["msg"], old["addr"], "486", "Busy Here")
        self.last_invite_cid = cid
        self._reply(msg, addr, "100", "Trying")
        # A guest calls during a camera view: stop the view (its audio must not play over
        # the call) and hang up the view call.
        if self.mode == "view":
            self.end_view_media()
        self.demo = sip_uas.is_demo(msg)
        self.call_cid = cid
        self.call_seq += 1                   # aborts a pending "open from view"
        self.ring = {"msg": msg, "addr": addr, "cid": cid}
        self.answered = False
        self.mode = "incoming"
        pid = sip_uas.panel_from_callerid(msg, self.cfg.panels)
        if not pid:
            log.warning("caller %r matches no panel number, treating it as %s",
                        sip_uas.caller(msg)[1], self.default_panel)
        self.panel = pid or self.default_panel
        self.media.view = self.panel         # instant video from the warm stream
        log.info("incoming call: panel=%s%s, ringing (not answering), pages connected: %d%s",
                 self.panel, " (demo)" if self.demo else "", len(self.hub.clients),
                 "" if self.hub.clients else " - WARNING: nobody to ring")
        log.debug("INVITE from %s", frm[:80])
        self._reply(msg, addr, "180", "Ringing")
        self.hub.notify_soon({"ev": "call", "state": "ringing", "panel": self.panel})
        self.hub.spawn(self.log_channels("incoming"))

    def _own_invite(self, msg, addr, cid, body):
        if cid and cid == self.view_cid:
            return                                       # retransmission of an accepted view
        vr = self.view_req
        live = (vr is not None and not vr["cancelled"] and not vr["consumed"]
                and time.time() - vr["t"] < VIEW_ORDER_TTL and self.mode == "view"
                and self.media.view == vr["panel"])
        if not live:
            self._reply(msg, addr, "603", "Decline")
            log.info("camera-view call declined: %s",
                     "order cancelled" if vr and vr["cancelled"] else "no live order")
            return
        vr["consumed"] = True
        self.view_cid = cid
        sip = self.cfg.sip
        has_v, vpt = sip_uas.sdp_video_pt(body)
        a_ip, a_port = sip_uas.sdp_audio_dest(body)
        if has_v:
            fmtp = sip_uas.sdp_video_fmtp(body, vpt)
            sdp = sip_uas.answer_sdp(sip.rtp_audio_port, sip.rtp_video_port, vpt, fmtp)
            self.media.start_sip_decoder(vpt, fmtp, vr["panel"])
            v_ip, v_port = sip_uas.sdp_media_dest(body, "video")
            self.media.start_dummy_video(v_ip, v_port, vpt)
        else:
            sdp = sip_uas.answer_sdp(sip.rtp_audio_port)
            self.media.start_audio_sip()
        self.media.start_silence(a_ip, a_port)
        self.active = True
        self.src = "sip"
        self._reply(msg, addr, "100", "Trying")
        self._reply(msg, addr, "200", "OK", sdp)
        log.info("camera view through SIP answered: %s", vr["panel"])

    def sip_cancel(self, msg, addr):
        """The caller gave up (or the PBX timed out) while we were ringing."""
        cid = sip_uas.header(msg, "Call-ID")
        self._reply(msg, addr, "200", "OK")
        if self.ring and cid == self.ring.get("cid"):
            self._reply(self.ring["msg"], self.ring["addr"], "487", "Request Terminated")
            self.ring = None
            self.mode = None
            self.media.view = None
            self.answered = False
            self.demo = False
            self.call_cid = None
            log.info("CANCEL: the caller hung up before an answer")
            self.hub.notify_soon({"ev": "call", "state": "ended"})

    def sip_bye(self, msg, addr):
        """What ends is decided by the Call-ID: a late BYE of an abandoned view must not
        end the guest's call, and the other way round."""
        cid = sip_uas.header(msg, "Call-ID")
        self._reply(msg, addr, "200", "OK")
        if cid and cid == self.call_cid and self.in_call:
            log.info("BYE: the call with the guest is over")
            self.media.stop_call_media()
            self.active = False
            self.mode = None
            self.media.view = None
            self.answered = False
            self.ring = None
            self.src = None
            self.demo = False
            self.call_cid = None
            self.hub.notify_soon({"ev": "call", "state": "ended"})
        elif cid and cid == self.view_cid and self.mode == "view":
            log.info("BYE: the panel ended the camera view")
            self.end_view_media()
            self.mode = None
            self.media.view = None
            self.hub.notify_soon({"ev": "view", "state": "ended"})
        else:
            log.info("BYE of an old dialog ignored")

    # ------------------------------------------------------------------ answer
    def do_answer(self):
        """Answer the ringing call: 200 OK + media. Used by Answer and by Open."""
        if not self.ring:
            return False
        msg, addr = self.ring["msg"], self.ring["addr"]
        body = sip_uas.split_message(msg)[1]
        sip = self.cfg.sip
        has_v, vpt = sip_uas.sdp_video_pt(body)
        a_ip, a_port = sip_uas.sdp_audio_dest(body)
        fmtp = sip_uas.sdp_video_fmtp(body, vpt) if has_v else "packetization-mode=1"
        if has_v:
            # The panel needs two-way video or it sends none: answer with our stream.
            # The owner sees the guest from the warm RTSP stream, so SIP video is only
            # decoded when RTSP is dead.
            sdp = sip_uas.answer_sdp(sip.rtp_audio_port, sip.rtp_video_port, vpt, fmtp)
            v_ip, v_port = sip_uas.sdp_media_dest(body, "video")
            self.media.start_dummy_video(v_ip, v_port, vpt)
        else:
            sdp = sip_uas.answer_sdp(sip.rtp_audio_port)
        if self.media.warm_fresh(self.panel):
            self.media.start_audio_sip()             # video is already there: audio only
        elif has_v:
            self.media.start_sip_decoder(vpt, fmtp, self.panel)
        else:
            self.media.start_audio_sip()
        self.media.start_silence(a_ip, a_port)
        self._reply(msg, addr, "200", "OK", sdp)
        self.ring = None
        self.answered = True
        self.answered_at = time.time()
        self.active = True
        self.src = "sip"
        log.info("ANSWERED: panel=%s", self.panel)
        return True

    async def answer(self, body=None):
        if self.answered:
            return 200, {"ok": True, "already": True}
        if self.do_answer():
            await self.hub.notify({"ev": "call", "state": "answered", "panel": self.panel})
            return 200, {"ok": True}
        return 200, {"ok": False, "err": "No incoming call"}

    # ------------------------------------------------------------------ open the door
    async def open(self, body):
        want = self._wanted(body)
        in_call = self.in_call
        # The Open button of one panel's screen while the other panel rings: do nothing,
        # or a finger resting on Open in the gate view would let in the entrance guest.
        if in_call and want and want != self.panel:
            log.info("open %s refused: %s is calling", want, self.panel)
            return 409, {"ok": False, "err": "Another panel is ringing"}
        if not in_call and want:
            self.panel = want
        # Open on a ringing call answers it first (no separate Answer needed).
        if self.ring and not self.answered:
            if self.do_answer():
                await self.hub.notify({"ev": "call", "state": "answered", "panel": self.panel})
                await self.sleep(0.7)                     # let the channel come Up
        if self.demo and self.answered:
            log.info("demo: Open pressed, no door is opened")
            return 200, {"ok": True, "demo": True}
        panel = self._panel(self.panel)
        since = self.answered_at if self.answered else 0.0
        chan = None
        if self.answered:
            # in a call: only the guest's own channel, never a view or our own call
            for _ in range(CALL_CHANNEL_TRIES):
                chan = self._find_call_channel(await self._channels())
                if chan:
                    break
                await self.sleep(0.3)
            if not chan:
                # A call without a channel. Calling the panel ourselves is NOT allowed
                # here: a demo or a foreign call would open a real door.
                return 200, {"ok": False, "err": "No call channel — try again"}
        else:
            # camera view through SIP of this panel: the digit goes into its channel
            vr = self.view_req
            if (vr and vr["consumed"] and vr.get("chan") and vr["panel"] == panel.id
                    and self.mode == "view" and self.src == "sip"):
                if amimod.is_up(await self._trunk(), vr["chan"]):
                    chan = vr["chan"]
                    since = vr.get("up_at") or 0.0
        if not chan and self.open_chan and self.open_chan["panel"] == panel.id:
            # repeated press: our previous door call to this panel is still up
            oc = self.open_chan
            if amimod.is_up(await self._trunk(), oc["chan"]):
                chan = oc["chan"]
                since = oc["up_at"]
        if not chan:
            if self.in_call:
                return 200, {"ok": False, "err": "No active call"}
            chan, err = await self._door_call(panel)
            if not chan:
                return 200, {"ok": False, "err": err}
            since = time.time()
            self.open_chan = {"chan": chan, "panel": panel.id, "up_at": since}
        wait = panel.open_settle - (time.time() - since)
        if wait > 0:
            await self.sleep(wait)
        await self.dtmf_open(chan, since, 1, panel.dtmf)

        async def tail():
            t0 = time.time()
            for i, at in enumerate(panel.open_repeat, 2):
                await self.sleep(max(0, at - (time.time() - t0)))
                await self.dtmf_open(chan, since, i, panel.dtmf)

        if panel.open_repeat:
            self.hub.spawn(tail())                        # answer the button right away
        return 200, {"ok": True, "chan": chan}

    async def _door_call(self, panel):
        """Call the panel only to send DTMF (camera view over RTSP: no SIP channel).
        Returns (channel, None) or (None, error)."""
        before = set(c["chan"] for c in await self._trunk())   # other calls are not ours
        seq0 = self.call_seq
        # Wait <hold>: the panel answers in ~1 s, the digit series ends ~5 s later, the
        # rest is the window for a second press. Not longer: while the line is ours the
        # panel is busy for guests too.
        sent = await self.ami.action({
            "Action": "Originate", "Channel": amimod.dial_string(self.cfg, panel),
            "Application": "Wait", "Data": str(panel.hold),
            "CallerID": self.cfg.asterisk.own_callerid, "Async": "true"})
        if not sent:
            return None, "Asterisk unreachable"
        for _ in range(int(panel.hold / OPEN_POLL) + 1):
            await self.sleep(OPEN_POLL)
            # SAFETY: an incoming call during the wait aborts at once, otherwise the
            # guest's new channel could be taken for ours and the door would open itself.
            if self.call_seq != seq0:
                log.info("open cancelled: an incoming call arrived")
                return None, "Cancelled: incoming call"
            chan = amimod.find_new_wait_channel(await self._channels(), self.prefixes, before,
                                                self.cfg.asterisk.view_context)
            if chan:
                return chan, None
        # A panel in a conversation (a guest calling a neighbour) does not take a second
        # call; from here "busy" and "no answer" look the same.
        log.info("open %s: the panel line did not come up within %d s", panel.id, panel.hold)
        await self.log_channels("open")
        return None, "Panel busy"

    async def dtmf_open(self, chan, since, n, digits):
        for i, d in enumerate(digits):
            if i:
                await self.sleep(0.1)
            try:
                await self.ami.action({"Action": "PlayDTMF", "Channel": chan, "Digit": d,
                                       "Duration": str(DTMF_DURATION)})
            except Exception:
                return
        log.info("open door: DTMF #%d %.1f s after connect -> %s", n, time.time() - since, chan)

    # ------------------------------------------------------------------ camera view
    async def view(self, body):
        pid = body.get("panel") if isinstance(body, dict) else None
        pid = pid or self.default_panel
        if pid not in self.cfg.panel_ids:
            return 400, {"ok": False, "err": "Unknown panel"}
        # During a call only the picture switches; the call keeps its panel.
        if self.in_call:
            if not self.media.warm_fresh(pid):
                return 200, {"ok": False, "err": "Camera unavailable"}
            self.media.view = pid
            log.info("during the call showing %s (%s is calling)", pid, self.panel)
            return 200, {"ok": True, "panel": pid, "src": "rtsp", "incall": True}
        # switching cameras: stop only the previous audio / SIP fallback and its own call
        if self.mode == "view":
            self.end_view_media()
        rtsp = self.media.rtsp.get(pid)
        if body.get("via") == "sip":                      # forced fallback (diagnostics)
            rtsp = None
        if rtsp and not self.media.warm_fresh(pid):
            rtsp = None
            log.info("camera stream %s has no frames, viewing through SIP", pid)
        if rtsp:
            self.cancel_view_req()
            self.mode = "view"
            self.panel = pid
            self.src = "rtsp"
            self.media.view = pid                         # the actual switch: instant
            await asyncio.get_running_loop().run_in_executor(None, self.media.start_audio_rtsp,
                                                             rtsp)
            log.info("camera view (stream): %s", pid)
            return 200, {"ok": True, "panel": pid, "src": "rtsp"}
        panel = self._panel(pid)
        self.mode = "view"
        self.panel = pid
        self.media.view = pid
        self.src = None
        vr = {"panel": pid, "t": time.time(), "chan": None, "cancelled": False,
              "consumed": False, "up_at": 0.0}
        self.view_req = vr
        before = set(c["chan"] for c in await self._trunk())
        a = self.cfg.asterisk
        sent = await self.ami.action({
            "Action": "Originate", "Channel": amimod.dial_string(self.cfg, panel),
            "Context": a.view_context, "Exten": a.view_exten, "Priority": "1",
            "CallerID": a.own_callerid, "Codecs": "alaw,h264", "Async": "true"})
        if not sent:
            self.view_req = None
            return 200, {"ok": False, "err": "Asterisk unreachable"}
        self.hub.spawn(self.track_view_chan(vr, before))
        log.info("camera view (SIP call): %s", pid)
        return 200, {"ok": True, "panel": pid, "src": "sip"}

    # ------------------------------------------------------------------ hang up
    async def hangup(self, body):
        body = body if isinstance(body, dict) else {}
        scope = body.get("scope")                 # "view" | "call"; older pages send {}
        want = self._wanted(body)
        in_call = self.in_call
        # Closing a VIEW (button, return timer) never touches a call: if a guest rang a
        # second earlier and the page has not heard yet, the call would be dropped.
        if scope == "view":
            if in_call:
                log.info("close view ignored: a call is in progress (%s)", self.panel)
                return 200, {"ok": True, "ignored": True}
            log.info("close: end of view")
            if self.mode == "view":
                self.end_view_media()
            self.mode = None
            self.media.view = None
            return 200, {"ok": True}
        if scope == "call" and in_call and want and want != self.panel:
            log.info("close %s ignored: %s is calling", want, self.panel)
            return 200, {"ok": True, "ignored": True}
        ring, demo = self.ring, self.demo
        log.info("close: %s", "call declined" if ring else "call ended" if self.answered
                 else "end of view" if self.mode == "view" else "nothing to close")
        if self.mode == "view":
            self.end_view_media()
        self.media.stop_audio()
        if self.src == "sip" or self.answered:
            self.media.stop_call_media()
        self.active = False
        self.mode = None
        self.media.view = None
        self.src = None
        self.answered = False
        self.ring = None
        self.demo = False
        self.call_cid = None
        await self.hub.notify({"ev": "call", "state": "ended"})    # pages go quiet at once
        # An unanswered call is declined right away (the guest hears busy, not endless
        # ringing); the channel is also hung up through AMI (belt and braces).
        if ring:
            self._reply(ring["msg"], ring["addr"], "486", "Busy Here")
        if in_call:
            self.hub.spawn(self._hangup_channels(demo))
        if demo:
            self._stop_demo()
        return 200, {"ok": True}

    async def _hangup_channels(self, demo):
        try:
            chans = await self._channels()
            if demo:
                names = [c["chan"] for c in chans if c["ctx"] == self.cfg.asterisk.demo_context]
            else:
                ch = self._find_call_channel(chans, up_only=False)
                names = [ch] if ch else []
            for ch in names:
                await self.ami.action({"Action": "Hangup", "Channel": ch})
        except Exception:
            pass

    # ------------------------------------------------------------------ demo call
    def _stop_demo(self):
        p, self.demo_proc = self.demo_proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    async def start_demo(self, body):
        """Ring the station with the panel simulator (through Asterisk when it is there)."""
        if self.in_call:
            return 409, {"ok": False, "err": "A call is in progress"}
        if not self.panelsim:
            return 200, {"ok": False, "err": "Panel simulator is not installed"}
        pid = self._wanted(body if isinstance(body, dict) else {}) or self.default_panel
        panel = self._panel(pid)
        self._stop_demo()
        a, sip = self.cfg.asterisk, self.cfg.sip
        cmd = [sys.executable, "-u", self.panelsim, "--number", panel.number,
               "--seconds", str(DEMO_SECONDS)]
        if getattr(self.ami, "connected", False):
            cmd += ["--to", "asterisk", "--exten", a.view_exten]
        else:
            cmd += ["--to", "web", "--uas-host", sip.uas_host, "--uas-port", str(sip.uas_port)]
        logpath = os.path.join(self.runtime_dir or ".", "panelsim.log")
        try:
            with open(logpath, "wb") as lf:
                self.demo_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=lf,
                                                  stderr=lf, close_fds=True)
        except OSError as e:
            return 200, {"ok": False, "err": "Simulator failed to start: %s" % e.__class__.__name__}
        log.info("demo call started for %s (%s)", panel.id, cmd[cmd.index("--to") + 1])
        return 200, {"ok": True, "panel": panel.id}

    def stop(self):
        self._stop_demo()


def _find_panelsim():
    """tools/panelsim.py next to the package (source checkout or /opt install)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "tools", "panelsim.py")
    return path if os.path.isfile(path) else ""
