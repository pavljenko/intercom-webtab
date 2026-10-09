"""SIP parsing, caller identification and the UAS dispatcher."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intercom_webtab import sip_uas  # noqa: E402

PANELS = [{"id": "gate", "number": "201"}, {"id": "door", "number": "202"}]

SDP = ("v=0\r\no=- 1 1 IN IP4 192.0.2.10\r\ns=Asterisk\r\nc=IN IP4 192.0.2.10\r\nt=0 0\r\n"
       "m=audio 10012 RTP/AVP 8 101\r\na=rtpmap:8 PCMA/8000\r\na=sendrecv\r\n"
       "m=video 10014 RTP/AVP 99\r\nc=IN IP4 192.0.2.11\r\na=rtpmap:99 H264/90000\r\n"
       "a=fmtp:99 packetization-mode=1;profile-level-id=4D001E\r\na=sendrecv\r\n")


def invite(frm='"Gate" <sip:201@127.0.0.1>', cid="abc@127.0.0.1", body=SDP):
    return ("INVITE sip:webtab@127.0.0.1:5080 SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1:5060;rport;branch=z9hG4bKPj1\r\n"
            "Via: SIP/2.0/UDP 127.0.0.1:5070;branch=z9hG4bKPj0\r\n"
            "From: %s;tag=f1\r\nTo: <sip:webtab@127.0.0.1>\r\nCall-ID: %s\r\n"
            "CSeq: 7 INVITE\r\nContent-Type: application/sdp\r\nContent-Length: %d\r\n\r\n%s"
            % (frm, cid, len(body), body))


class Parsing(unittest.TestCase):
    def test_headers(self):
        m = invite()
        self.assertEqual(sip_uas.method(m), "INVITE")
        self.assertEqual(sip_uas.header(m, "call-id"), "abc@127.0.0.1")
        self.assertEqual(len(sip_uas.header_all(m, "Via")), 2)
        self.assertEqual(sip_uas.header(m, "X-Missing"), "")
        self.assertEqual(sip_uas.method("SIP/2.0 200 OK\r\n\r\n"), "")
        self.assertEqual(sip_uas.split_message(m)[1], SDP)

    def test_compact_headers(self):
        m = "BYE sip:x SIP/2.0\r\nf: <sip:201@h>;tag=1\r\ni: cid9\r\nv: SIP/2.0/UDP h\r\n\r\n"
        self.assertEqual(sip_uas.header(m, "Call-ID"), "cid9")
        self.assertEqual(sip_uas.caller(m), ("", "201"))

    def test_name_addr(self):
        self.assertEqual(sip_uas.parse_name_addr('"Front Gate" <sip:+201@h:5060;user=phone>;tag=x'),
                         ("Front Gate", "+201"))
        self.assertEqual(sip_uas.parse_name_addr("sip:202@h;tag=1"), ("", "202"))
        self.assertEqual(sip_uas.parse_name_addr("<sip:h>"), ("", ""))
        self.assertEqual(sip_uas.parse_name_addr("panelsim <sips:panelsim@h>"), ("panelsim", "panelsim"))

    def test_panel_from_callerid(self):
        self.assertEqual(sip_uas.panel_from_callerid(invite(), PANELS), "gate")
        self.assertEqual(sip_uas.panel_from_callerid(invite("<sip:+202@h>"), PANELS), "door")
        self.assertEqual(sip_uas.panel_from_callerid(invite("<sip:2020@h>"), PANELS), "")
        self.assertEqual(sip_uas.panel_from_callerid(invite('"201" <sip:h>'), PANELS), "")

        class P(object):
            def __init__(self, id, number):
                self.id, self.number = id, number

        self.assertEqual(sip_uas.panel_from_callerid(invite(), [P("x", "201")]), "x")

    def test_demo_detection(self):
        self.assertTrue(sip_uas.is_demo(invite('"panelsim" <sip:201@127.0.0.1>')))
        self.assertTrue(sip_uas.is_demo(invite("<sip:panelsim@127.0.0.1>")))
        self.assertTrue(sip_uas.is_demo(invite('"PanelSim" <sip:202@h>')))
        self.assertFalse(sip_uas.is_demo(invite()))
        self.assertFalse(sip_uas.is_demo(invite('"Gate panelsim test" <sip:201@h>')))

    def test_own_call(self):
        self.assertTrue(sip_uas.is_own_call(invite("<sip:100@127.0.0.1>"), "100"))
        self.assertTrue(sip_uas.is_own_call(invite('"100" <sip:anonymous@h>'), "100"))
        self.assertTrue(sip_uas.is_own_call(invite("<sip:+100@h>"), "100"))
        self.assertFalse(sip_uas.is_own_call(invite("<sip:1001@h>"), "100"))
        self.assertFalse(sip_uas.is_own_call(invite("<sip:201@h>"), "100"))
        self.assertFalse(sip_uas.is_own_call(invite("<sip:100@h>"), ""))

    def test_sdp(self):
        self.assertEqual(sip_uas.sdp_audio_dest(SDP), ("192.0.2.10", 10012))
        self.assertEqual(sip_uas.sdp_media_dest(SDP, "video"), ("192.0.2.11", 10014))
        self.assertEqual(sip_uas.sdp_video_pt(SDP), (True, "99"))
        self.assertEqual(sip_uas.sdp_video_fmtp(SDP, "99"),
                         "packetization-mode=1;profile-level-id=4D001E")
        self.assertEqual(sip_uas.sdp_video_fmtp(SDP, "96"), "packetization-mode=1")
        audio_only = "v=0\r\nc=IN IP4 198.51.100.7\r\nm=audio 4000 RTP/AVP 8\r\n"
        self.assertEqual(sip_uas.sdp_video_pt(audio_only), (False, "96"))
        self.assertEqual(sip_uas.sdp_media_dest(audio_only, "video"), ("198.51.100.7", None))
        self.assertEqual(sip_uas.sdp_video_pt("m=video 0 RTP/AVP 99\r\n"), (False, "96"))

    def test_answer_sdp(self):
        a = sip_uas.answer_sdp(41000, 41002, "99", "packetization-mode=1")
        self.assertIn("m=audio 41000 RTP/AVP 8\r\n", a)
        self.assertIn("m=video 41002 RTP/AVP 99\r\n", a)
        self.assertIn("a=fmtp:99 packetization-mode=1\r\n", a)
        self.assertNotIn("m=video", sip_uas.answer_sdp(41000))

    def test_response(self):
        m = invite()
        r = sip_uas.build_response(m, "180", "Ringing", "<sip:webtab@127.0.0.1:5080>").decode()
        r2 = sip_uas.build_response(m, "200", "OK", "<sip:webtab@127.0.0.1:5080>", sdp="v=0\r\n").decode()
        self.assertTrue(r.startswith("SIP/2.0 180 Ringing\r\n"))
        self.assertEqual(r.count("Via: "), 2)
        tag = sip_uas.to_tag("abc@127.0.0.1")
        self.assertIn("To: <sip:webtab@127.0.0.1>;tag=%s\r\n" % tag, r)
        self.assertIn(";tag=%s\r\n" % tag, r2)              # same dialog, same tag
        self.assertIn("CSeq: 7 INVITE\r\n", r2)
        self.assertIn("Content-Length: 5\r\n\r\nv=0\r\n", r2)
        r100 = sip_uas.build_response(m, "100", "Trying", "<sip:x>").decode()
        self.assertNotIn(";tag=", r100.split("To: ")[1].split("\r\n")[0])


class FakeTransport(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data.decode(), addr))


class FakeHandler(object):
    def __init__(self):
        self.calls = []

    def sip_invite(self, msg, addr):
        self.calls.append(("invite", addr))

    def sip_cancel(self, msg, addr):
        self.calls.append(("cancel", addr))

    def sip_bye(self, msg, addr):
        self.calls.append(("bye", addr))


class Dispatcher(unittest.TestCase):
    def setUp(self):
        self.h = FakeHandler()
        self.uas = sip_uas.SipUas(self.h, "127.0.0.1", 5080)
        self.t = FakeTransport()
        self.uas.connection_made(self.t)
        self.addr = ("127.0.0.1", 5060)

    def feed(self, text):
        self.uas.datagram_received(text.encode(), self.addr)

    def test_dispatch(self):
        self.feed(invite())
        self.feed(invite().replace("INVITE sip", "CANCEL sip", 1))
        self.feed(invite().replace("INVITE sip", "BYE sip", 1))
        self.feed(invite().replace("INVITE sip", "ACK sip", 1))
        self.assertEqual([c[0] for c in self.h.calls], ["invite", "cancel", "bye"])
        self.assertEqual(self.t.sent, [])

    def test_options_and_unknown(self):
        self.feed(invite().replace("INVITE sip", "OPTIONS sip", 1))
        self.feed(invite().replace("INVITE sip", "SUBSCRIBE sip", 1))
        self.assertTrue(self.t.sent[0][0].startswith("SIP/2.0 200 OK"))
        self.assertIn("Contact: <sip:webtab@127.0.0.1:5080>", self.t.sent[0][0])
        self.assertTrue(self.t.sent[1][0].startswith("SIP/2.0 405"))

    def test_ignores_responses_and_keepalive(self):
        self.feed("SIP/2.0 200 OK\r\nCall-ID: x\r\n\r\n")
        self.feed("\r\n\r\n")
        self.assertEqual(self.h.calls, [])
        self.assertEqual(self.t.sent, [])

    def test_handler_error_gives_500(self):
        def boom(msg, addr):
            raise ValueError("x")
        self.h.sip_invite = boom
        import logging
        logging.getLogger("iwt.sip").disabled = True
        try:
            self.feed(invite())
        finally:
            logging.getLogger("iwt.sip").disabled = False
        self.assertTrue(self.t.sent[0][0].startswith("SIP/2.0 500"))


if __name__ == "__main__":
    unittest.main()
