"""Call / view / open logic with a fake Asterisk: the safety rules of the station."""
import asyncio
import json
import logging
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from intercom_webtab import ami as amimod  # noqa: E402
from intercom_webtab import config, sip_uas  # noqa: E402
from intercom_webtab.calls import CallManager, Hub  # noqa: E402

CONF = """
[asterisk]
trunk = provider
own_callerid = 100
[panel:gate]
number = 201
open_settle = 2.0
open_repeat = 0.7, 1.4
hold = 3
[panel:door]
number = 202
dtmf = #1
hold = 3
"""
SECRETS = "[asterisk]\nami_user = webtab\nami_secret = test\n"
SDP = ("v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=x\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
       "m=audio 10010 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\n"
       "m=video 10012 RTP/AVP 99\r\na=rtpmap:99 H264/90000\r\na=fmtp:99 packetization-mode=1\r\n")
ADDR = ("127.0.0.1", 5060)

logging.getLogger("iwt").addHandler(logging.NullHandler())   # expected warnings stay quiet


def cfg():
    return config.parse(CONF, path="/t/webtab.ini", secrets_text=SECRETS, token="t" * 20)


def msg(method="INVITE", frm="<sip:201@127.0.0.1>", cid="c1", body=SDP):
    return ("%s sip:webtab@127.0.0.1:5080 SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK1\r\n"
            "From: %s;tag=a\r\nTo: <sip:webtab@127.0.0.1>\r\nCall-ID: %s\r\nCSeq: 1 %s\r\n"
            "Content-Length: %d\r\n\r\n%s" % (method, frm, cid, method, len(body), body))


def chan(name, state="Up", ctx="intercom-incoming", app="Dial", cid=""):
    return {"chan": name, "state": state, "ctx": ctx, "app": app, "cid": cid}


class FakeAmiClient(object):
    """In-memory stand-in for ``ami.Ami``; Originate can create a channel."""

    def __init__(self):
        self.actions = []
        self.chans = []
        self.connected = True
        self.on_originate = None
        self.n = 0

    async def action(self, fields):
        self.actions.append(dict(fields))
        if fields.get("Action") == "Originate" and self.on_originate:
            self.on_originate(fields)
        if fields.get("Action") == "Hangup":
            self.chans = [c for c in self.chans if c["chan"] != fields["Channel"]]
        return True

    async def channels(self):
        return [dict(c) for c in self.chans]

    def named(self, action):
        return [a for a in self.actions if a.get("Action") == action]

    def new_wait_channel(self, fields):
        self.n += 1
        # Asterisk shows the trunk endpoint's context: the same as a guest's call
        self.chans.append(chan("PJSIP/provider-%08d" % self.n, ctx="intercom-incoming", app="Wait"))


class FakeMedia(object):
    def __init__(self, fresh=()):
        self.view = None
        self.rtsp = {}
        self.fresh = set(fresh)
        self.log = []

    def warm_fresh(self, panel, ttl=6):
        return panel in self.fresh

    def __getattr__(self, name):
        if name.startswith(("start_", "stop_")):
            return lambda *a: self.log.append((name,) + a)
        raise AttributeError(name)


class FakeWs(object):
    def __init__(self):
        self.events = []

    async def send_str(self, s):
        self.events.append(json.loads(s))

    async def send_bytes(self, b):
        pass

    async def close(self):
        pass


async def no_sleep(s):
    await asyncio.sleep(0)


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = cfg()
        self.ami = FakeAmiClient()
        self.media = FakeMedia()
        self.hub = Hub()
        self.ws = FakeWs()
        self.hub.clients.add(self.ws)
        self.cm = CallManager(self.cfg, self.media, self.ami, self.hub, sleep=no_sleep,
                              panelsim="")
        self.replies = []
        self.cm.reply = lambda m, a, code, reason, sdp="": self.replies.append(
            (sip_uas.header(m, "Call-ID"), code, sdp))

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0)

    def codes(self, cid=None):
        return [c for i, c, _ in self.replies if cid is None or i == cid]

    def states(self):
        return [(e.get("ev"), e.get("state"), e.get("panel")) for e in self.ws.events]

    def dtmf(self):
        return [(a["Channel"], a["Digit"]) for a in self.ami.named("PlayDTMF")]


class Ringing(Base):
    async def test_rings_without_answering(self):
        self.cm.sip_invite(msg(), ADDR)
        await self.settle()
        self.assertEqual(self.codes(), ["100", "180"])
        self.assertEqual(self.cm.panel, "gate")
        self.assertEqual(self.media.view, "gate")
        self.assertIn(("call", "ringing", "gate"), self.states())
        self.assertFalse(self.cm.answered)

    async def test_retransmission(self):
        self.cm.sip_invite(msg(), ADDR)
        self.cm.sip_invite(msg(), ADDR)
        await self.settle()
        self.assertEqual(self.codes(), ["100", "180", "100", "180"])
        self.assertEqual(self.cm.call_seq, 1)

    async def test_unknown_caller_uses_first_panel(self):
        self.cm.sip_invite(msg(frm="<sip:555@127.0.0.1>"), ADDR)
        self.assertEqual(self.cm.panel, "gate")

    async def test_second_panel_by_number(self):
        self.cm.sip_invite(msg(frm='"Entrance" <sip:+202@127.0.0.1>'), ADDR)
        self.assertEqual(self.cm.panel, "door")

    async def test_newer_call_declines_the_older(self):
        self.cm.sip_invite(msg(cid="old"), ADDR)
        self.cm.sip_invite(msg(frm="<sip:202@h>", cid="new"), ADDR)
        self.assertEqual(self.codes("old"), ["100", "180", "486"])
        self.assertEqual(self.cm.panel, "door")

    async def test_answer(self):
        self.cm.sip_invite(msg(), ADDR)
        status, data = await self.cm.answer({})
        self.assertEqual((status, data), (200, {"ok": True}))
        cid, code, sdp = self.replies[-1]
        self.assertEqual(code, "200")
        self.assertIn("m=audio 41000 RTP/AVP 8", sdp)
        self.assertIn("m=video 41002 RTP/AVP 99", sdp)
        names = [x[0] for x in self.media.log]
        self.assertIn("start_dummy_video", names)
        self.assertIn("start_silence", names)
        self.assertIn("start_sip_decoder", names)        # no warm stream: SIP fallback
        self.assertIn(("call", "answered", "gate"), self.states())
        self.assertEqual(await self.cm.answer({}), (200, {"ok": True, "already": True}))

    async def test_answer_with_warm_stream_takes_audio_only(self):
        self.media.fresh.add("gate")
        self.cm.sip_invite(msg(), ADDR)
        self.cm.do_answer()
        names = [x[0] for x in self.media.log]
        self.assertIn("start_audio_sip", names)
        self.assertNotIn("start_sip_decoder", names)

    async def test_answer_without_call(self):
        self.assertEqual(await self.cm.answer({}), (200, {"ok": False, "err": "No incoming call"}))

    async def test_cancel(self):
        self.cm.sip_invite(msg(), ADDR)
        self.cm.sip_cancel(msg("CANCEL", body=""), ADDR)
        await self.settle()
        self.assertEqual(self.codes(), ["100", "180", "200", "487"])
        self.assertIsNone(self.cm.ring)
        self.assertIn(("call", "ended", None), self.states())

    async def test_bye_matches_call_id(self):
        self.cm.sip_invite(msg(cid="guest"), ADDR)
        self.cm.do_answer()
        self.cm.sip_bye(msg("BYE", cid="old-view", body=""), ADDR)
        self.assertTrue(self.cm.answered)                 # a stale BYE ends nothing
        self.cm.sip_bye(msg("BYE", cid="guest", body=""), ADDR)
        await self.settle()
        self.assertFalse(self.cm.answered)
        self.assertIn(("call", "ended", None), self.states())

    async def test_hello_events(self):
        self.assertEqual(self.cm.hello_events()[1], {"ev": "call", "state": "ended"})
        self.cm.sip_invite(msg(), ADDR)
        self.cm.do_answer()
        ev = self.cm.hello_events()
        self.assertEqual(ev[0]["ev"], "hello")
        self.assertNotIn("stub", ev[0])
        self.assertEqual(ev[1:], [{"ev": "call", "state": "ringing", "panel": "gate"},
                                  {"ev": "call", "state": "answered", "panel": "gate"}])


class OpenInCall(Base):
    async def test_open_answers_and_uses_the_guest_channel(self):
        # the guest's channel and an older own Wait channel live side by side
        self.ami.chans = [chan("PJSIP/provider-00000001", app="Dial", cid="201"),
                          chan("PJSIP/provider-00000002", app="Wait")]
        self.cm.sip_invite(msg(), ADDR)
        status, data = await self.cm.open({"panel": "gate"})
        self.assertEqual(status, 200)
        self.assertEqual(data, {"ok": True, "chan": "PJSIP/provider-00000001"})
        self.assertIn("200", self.codes())                # Open answered the ringing call
        await self.settle()
        self.assertEqual(self.dtmf(), [("PJSIP/provider-00000001", "1")] * 3)   # 1 + 2 repeats
        self.assertEqual(self.ami.named("Originate"), [])

    async def test_multi_digit_dtmf(self):
        self.ami.chans = [chan("PJSIP/provider-00000001", cid="202")]
        self.cm.sip_invite(msg(frm="<sip:202@h>"), ADDR)
        await self.cm.open({"panel": "door"})
        await self.settle()
        self.assertEqual([d for _, d in self.dtmf()][:2], ["#", "1"])

    async def test_other_panel_is_refused(self):
        self.cm.sip_invite(msg(), ADDR)                   # gate rings
        status, data = await self.cm.open({"panel": "door"})
        self.assertEqual(status, 409)
        self.assertFalse(data["ok"])
        self.assertEqual(self.dtmf(), [])
        self.assertNotIn("200", self.codes())             # and nothing was answered

    async def test_no_channel_never_calls_the_panel(self):
        self.cm.sip_invite(msg(), ADDR)
        status, data = await self.cm.open({"panel": "gate"})
        self.assertFalse(data["ok"])
        self.assertEqual(self.ami.named("Originate"), [])
        self.assertEqual(self.dtmf(), [])

    async def test_demo_never_opens(self):
        self.ami.chans = [chan("PJSIP/provider-00000001", cid="201")]
        self.ami.on_originate = self.ami.new_wait_channel
        self.cm.sip_invite(msg(frm='"panelsim" <sip:201@127.0.0.1>'), ADDR)
        self.assertTrue(self.cm.demo)
        status, data = await self.cm.open({"panel": "gate"})
        self.assertEqual((status, data), (200, {"ok": True, "demo": True}))
        await self.cm.open({})
        await self.settle()
        self.assertEqual(self.dtmf(), [])
        self.assertEqual(self.ami.named("Originate"), [])

    async def test_demo_hangup_uses_demo_context(self):
        self.ami.chans = [chan("PJSIP/local-sim-00000001", ctx="intercom-demo"),
                          chan("PJSIP/provider-00000002", cid="201")]
        self.cm.sip_invite(msg(frm='"panelsim" <sip:201@127.0.0.1>'), ADDR)
        await self.cm.hangup({"scope": "call", "panel": "gate"})
        await self.settle()
        self.assertEqual([a["Channel"] for a in self.ami.named("Hangup")],
                         ["PJSIP/local-sim-00000001"])


class OpenFromView(Base):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ami.on_originate = self.ami.new_wait_channel

    async def test_door_call_found_by_wait_application(self):
        # an unrelated older call on the trunk is not ours
        self.ami.chans = [chan("PJSIP/provider-00000077", app="Dial")]
        status, data = await self.cm.open({"panel": "gate"})
        self.assertTrue(data["ok"], data)
        orig = self.ami.named("Originate")
        self.assertEqual(len(orig), 1)
        self.assertEqual(orig[0]["Channel"], "PJSIP/201@provider")
        self.assertEqual(orig[0]["Application"], "Wait")
        self.assertEqual(orig[0]["Data"], "3")
        self.assertEqual(orig[0]["CallerID"], "100")
        self.assertEqual(data["chan"], "PJSIP/provider-00000001")
        await self.settle()
        self.assertEqual(len(self.dtmf()), 3)
        # second press while our call is still up: no new call, digit straight in
        status, data2 = await self.cm.open({"panel": "gate"})
        self.assertEqual(data2["chan"], data["chan"])
        self.assertEqual(len(self.ami.named("Originate")), 1)

    async def test_reuse_only_for_the_same_panel(self):
        await self.cm.open({"panel": "gate"})
        await self.cm.open({"panel": "door"})
        orig = self.ami.named("Originate")
        self.assertEqual([o["Channel"] for o in orig], ["PJSIP/201@provider", "PJSIP/202@provider"])

    async def test_busy_panel(self):
        self.ami.on_originate = None                      # the panel never answers
        status, data = await self.cm.open({"panel": "gate"})
        self.assertEqual(data, {"ok": False, "err": "Panel busy"})
        self.assertEqual(self.dtmf(), [])

    async def test_incoming_call_aborts_the_wait(self):
        def guest_calls(fields):
            # while we wait for our line, a guest's call arrives (its channel is Up and
            # new, in the same context): the door must not open by itself
            self.ami.chans.append(chan("PJSIP/provider-00000009", app="Dial", cid="202"))
            self.cm.sip_invite(msg(frm="<sip:202@h>", cid="guest"), ADDR)
        self.ami.on_originate = guest_calls
        status, data = await self.cm.open({"panel": "gate"})
        self.assertEqual(data, {"ok": False, "err": "Cancelled: incoming call"})
        self.assertEqual(self.dtmf(), [])

    async def test_view_call_channel_is_used(self):
        def view_channel(fields):
            self.ami.chans.append(chan("PJSIP/provider-00000005", ctx="intercom-view", app="Dial"))
        self.ami.on_originate = view_channel
        status, data = await self.cm.view({"panel": "gate"})
        self.assertEqual(data["src"], "sip")
        orig = self.ami.named("Originate")[0]
        self.assertEqual((orig["Context"], orig["Exten"], orig["CallerID"]),
                         ("intercom-view", "webtab", "100"))
        await self.cm.track_view_chan(self.cm.view_req, set())
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="view1"), ADDR)
        self.assertEqual(self.codes("view1"), ["100", "200"])
        self.assertEqual(self.cm.mode, "view")
        self.assertIsNone(self.cm.ring)                    # not a guest call
        status, data = await self.cm.open({"panel": "gate"})
        self.assertEqual(data["chan"], "PJSIP/provider-00000005")
        self.assertEqual(len(self.ami.named("Originate")), 1)


class ViewOrders(Base):
    async def test_own_call_without_order_is_declined(self):
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="v"), ADDR)
        self.assertEqual(self.codes("v"), ["603"])
        self.assertIsNone(self.cm.ring)

    async def test_stale_order_is_declined(self):
        await self.cm.view({"panel": "gate"})
        await self.cm.hangup({"scope": "view", "panel": "gate"})
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="late"), ADDR)
        self.assertEqual(self.codes("late"), ["603"])

    async def test_switched_view_declines_the_old_order(self):
        await self.cm.view({"panel": "gate"})
        await self.cm.view({"panel": "door"})
        self.cm.view_req["panel"] = "door"
        self.cm.view_req["consumed"] = True                # door's call already arrived
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="gate-late"), ADDR)
        self.assertEqual(self.codes("gate-late"), ["603"])

    async def test_order_is_used_once_and_retransmission_ignored(self):
        await self.cm.view({"panel": "gate"})
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="v1"), ADDR)
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="v1"), ADDR)
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="v2"), ADDR)
        self.assertEqual(self.codes("v1"), ["100", "200"])
        self.assertEqual(self.codes("v2"), ["603"])

    async def test_panel_call_during_view_is_a_guest(self):
        await self.cm.view({"panel": "gate"})              # order live
        self.cm.sip_invite(msg(frm="<sip:201@127.0.0.1>", cid="guest"), ADDR)
        await self.settle()
        self.assertEqual(self.codes("guest"), ["100", "180"])   # rings, never auto-answered
        self.assertEqual(self.cm.mode, "incoming")
        self.assertIsNone(self.cm.view_req)                     # the view was cancelled
        self.assertIn(("call", "ringing", "gate"), self.states())

    async def test_view_bye_ends_view_only(self):
        await self.cm.view({"panel": "gate"})
        self.cm.sip_invite(msg(frm="<sip:100@127.0.0.1>", cid="v1"), ADDR)
        self.cm.sip_bye(msg("BYE", cid="v1", body=""), ADDR)
        await self.settle()
        self.assertIsNone(self.cm.mode)
        self.assertIn(("view", "ended", None), self.states())

    async def test_view_during_call_switches_picture_only(self):
        self.cm.sip_invite(msg(), ADDR)
        self.media.fresh.add("door")
        status, data = await self.cm.view({"panel": "door"})
        self.assertTrue(data["incall"])
        self.assertEqual(self.media.view, "door")
        self.assertEqual(self.cm.panel, "gate")
        status, data = await self.cm.view({"panel": "nope"})
        self.assertEqual(status, 400)

    async def test_rtsp_view(self):
        self.media.rtsp["gate"] = "rtsp://camera-gate.local/s"
        self.media.fresh.add("gate")
        status, data = await self.cm.view({"panel": "gate"})
        self.assertEqual(data, {"ok": True, "panel": "gate", "src": "rtsp"})
        self.assertEqual(self.ami.named("Originate"), [])
        self.assertIn(("start_audio_rtsp", "rtsp://camera-gate.local/s"), self.media.log)


class Hangup(Base):
    async def test_view_close_never_drops_a_call(self):
        self.cm.sip_invite(msg(), ADDR)
        self.assertEqual(await self.cm.hangup({"scope": "view", "panel": "gate"}),
                         (200, {"ok": True, "ignored": True}))
        self.assertIsNotNone(self.cm.ring)

    async def test_other_panel_close_ignored(self):
        self.cm.sip_invite(msg(), ADDR)
        self.assertEqual(await self.cm.hangup({"scope": "call", "panel": "door"}),
                         (200, {"ok": True, "ignored": True}))
        self.assertIsNotNone(self.cm.ring)

    async def test_decline_ringing_call(self):
        self.ami.chans = [chan("PJSIP/provider-00000001", state="Ring", cid="201")]
        self.cm.sip_invite(msg(), ADDR)
        self.assertEqual(await self.cm.hangup({"scope": "call", "panel": "gate"}),
                         (200, {"ok": True}))
        await self.settle()
        self.assertEqual(self.codes(), ["100", "180", "486"])
        self.assertEqual([a["Channel"] for a in self.ami.named("Hangup")],
                         ["PJSIP/provider-00000001"])
        self.assertIn(("call", "ended", None), self.states())


class ChannelRules(unittest.TestCase):
    P = ("PJSIP/provider-",)
    INC = ("intercom-incoming",)

    def test_call_channel(self):
        chans = [chan("PJSIP/provider-1", cid="202"), chan("PJSIP/provider-2", app="Wait"),
                 chan("PJSIP/webtab-3", cid="201"), chan("PJSIP/provider-4", cid="201")]
        f = amimod.find_call_channel
        self.assertEqual(f(chans, self.P, self.INC, "intercom-view", number="201"), "PJSIP/provider-4")
        self.assertEqual(f(chans, self.P, self.INC, "intercom-view", number="202"), "PJSIP/provider-1")
        self.assertEqual(f(chans, self.P, self.INC, "intercom-view"), "PJSIP/provider-4")
        only_wait = [chan("PJSIP/provider-2", app="Wait")]
        self.assertIsNone(f(only_wait, self.P, self.INC, "intercom-view"))
        ringing = [chan("PJSIP/provider-5", state="Ring")]
        self.assertIsNone(f(ringing, self.P, self.INC, "intercom-view"))
        self.assertEqual(f(ringing, self.P, self.INC, "intercom-view", up_only=False),
                         "PJSIP/provider-5")
        other = [chan("PJSIP/provider-6", ctx="from-elsewhere"),
                 chan("PJSIP/provider-7", ctx="intercom-view")]
        self.assertEqual(f(other, self.P, self.INC, "intercom-view"), "PJSIP/provider-6")
        self.assertIsNone(f(other[1:], self.P, self.INC, "intercom-view"))

    def test_wait_channel(self):
        before = {"PJSIP/provider-1"}
        chans = [chan("PJSIP/provider-1", app="Wait"), chan("PJSIP/provider-2", app="Dial"),
                 chan("PJSIP/provider-3", app="Wait", state="Ringing"),
                 chan("PJSIP/provider-4", app="Wait")]
        self.assertEqual(amimod.find_new_wait_channel(chans, self.P, before, "intercom-view"),
                         "PJSIP/provider-4")
        self.assertIsNone(amimod.find_new_wait_channel(chans[:3], self.P, before, "intercom-view"))

    def test_view_channel(self):
        chans = [chan("PJSIP/provider-1"), chan("PJSIP/provider-2", ctx="intercom-view", state="Ring")]
        self.assertEqual(amimod.find_view_channel(chans, self.P, set(), "intercom-view"),
                         ("PJSIP/provider-2", False))

    def test_dial_strings_and_prefixes(self):
        c = config.parse("[asterisk]\ntrunk = provider\n[panel:gate]\nnumber = 201\n"
                         "[panel:door]\nnumber = 202\nendpoint = door-station\n",
                         secrets_text=SECRETS, token="t" * 20)
        self.assertEqual(amimod.dial_string(c, c.panel("gate")), "PJSIP/201@provider")
        self.assertEqual(amimod.dial_string(c, c.panel("door")), "PJSIP/door-station")
        self.assertEqual(amimod.channel_prefixes(c), ("PJSIP/provider-", "PJSIP/door-station-"))


class AmiOverTcp(unittest.IsolatedAsyncioTestCase):
    """The real AMI client against tools/fake_ami.py."""

    async def test_client(self):
        from fake_ami import FakeAmi
        fake = FakeAmi(user="webtab", secret="test", up_delay=0.01)
        port = await fake.start()
        fake.add_channel("PJSIP/provider-00000001", "Up", "intercom-incoming", "Dial", "201")
        client = amimod.Ami("127.0.0.1", port, "webtab", "test")
        chans = await client.channels()
        self.assertEqual(chans, [chan("PJSIP/provider-00000001", cid="201")])
        task = asyncio.ensure_future(client.run())
        for _ in range(100):
            if client.connected:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(client.connected)
        self.assertTrue(await client.action({"Action": "PlayDTMF", "Channel": "PJSIP/provider-00000001",
                                             "Digit": "1"}))
        self.assertTrue(await client.action({"Action": "Originate", "Channel": "PJSIP/201@provider",
                                             "Application": "Wait", "Data": "5"}))
        for _ in range(100):
            if len(fake.channels) == 2:
                break
            await asyncio.sleep(0.01)
        chans = await client.channels()
        self.assertEqual(chans[1]["app"], "Wait")
        self.assertEqual(chans[1]["ctx"], "intercom-incoming")
        self.assertEqual(len(fake.recorded("PlayDTMF", Digit="1")), 1)
        self.assertEqual(fake.recorded("Login")[0]["Secret"], "***")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        bad = amimod.Ami("127.0.0.1", port, "webtab", "wrong")
        self.assertEqual(await bad.channels(), [])
        fake.server.close()


if __name__ == "__main__":
    unittest.main()
