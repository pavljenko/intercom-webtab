#!/usr/bin/env python3
"""A minimal fake Asterisk Manager Interface for tests and development.

It accepts one login, records every action, and answers what Intercom WebTab uses:
Login, Logoff, CoreShowChannels, Originate, PlayDTMF, Hangup. An Originate creates a
fake channel after a short delay, like Asterisk would:

* ``Application=Wait``: a channel on the dialled endpoint, state Up, application Wait,
  context = the endpoint's incoming context (the same context a guest's call has: the
  station must recognise its own channel by the application);
* ``Context=...``: a channel in that context running Dial (a camera-view call).

Nothing real is ever dialled. Use it from Python (``FakeAmi.start_in_thread``) or run it::

    python3 tools/fake_ami.py --port 5038 --user webtab --secret test
"""
import argparse
import asyncio
import itertools
import threading
import time


class FakeAmi(object):
    def __init__(self, user="webtab", secret="secret", incoming_context="intercom-incoming",
                 up_delay=0.3, answers=True, verbose=False):
        self.user = user
        self.secret = secret
        self.incoming_context = incoming_context
        self.up_delay = up_delay
        self.answers = answers          # False: originated channels never come Up (busy panel)
        self.verbose = verbose
        self.channels = []              # [{Channel, ChannelStateDesc, Context, Application, CallerIDNum}]
        self.actions = []               # recorded actions (Secret masked)
        self.lock = threading.Lock()
        self.loop = None
        self.server = None
        self.port = None
        self._seq = itertools.count(1)
        self._thread = None

    # ---- inspection helpers (thread-safe)
    def add_channel(self, chan, state="Up", context="", app="", cid=""):
        with self.lock:
            self.channels.append({"Channel": chan, "ChannelStateDesc": state, "Context": context,
                                  "Application": app, "CallerIDNum": cid})

    def remove_channel(self, chan):
        with self.lock:
            self.channels = [c for c in self.channels if c["Channel"] != chan]

    def clear(self):
        with self.lock:
            self.actions = []
            self.channels = []

    def recorded(self, action=None, **match):
        with self.lock:
            out = list(self.actions)
        if action:
            out = [a for a in out if a.get("Action", "").lower() == action.lower()]
        for k, v in match.items():
            out = [a for a in out if a.get(k) == v]
        return out

    # ---- server
    async def _send(self, w, fields):
        w.write(("".join("%s: %s\r\n" % kv for kv in fields) + "\r\n").encode("utf-8"))
        await w.drain()

    async def _later(self, delay, fn):
        await asyncio.sleep(delay)
        fn()

    def _new_channel(self, act):
        dial = act.get("Channel", "")
        tech = dial.split("@", 1)[1] if "@" in dial else dial.split("/", 1)[-1]
        name = "PJSIP/%s-%08x" % (tech, next(self._seq))
        state = "Up" if self.answers else "Ringing"
        if act.get("Application"):
            ctx, app = self.incoming_context, act["Application"]
        else:
            ctx, app = act.get("Context", ""), "Dial"
        self.add_channel(name, state, ctx, app, act.get("CallerID", ""))
        if act.get("Application", "").lower() == "wait":
            try:
                hold = float(act.get("Data") or 0)
            except ValueError:
                hold = 0
            if hold > 0:
                asyncio.ensure_future(self._later(hold, lambda: self.remove_channel(name)))

    async def _client(self, r, w):
        w.write(b"Asterisk Call Manager/9.9.9\r\n")
        authed = False
        try:
            while True:
                blk = (await r.readuntil(b"\r\n\r\n")).decode("utf-8", "ignore")
                act = {}
                for ln in blk.split("\r\n"):
                    k, sep, v = ln.partition(":")
                    if sep:
                        act[k.strip()] = v.strip()
                name = act.get("Action", "").lower()
                aid = act.get("ActionID", "")
                rec = dict(act)
                if "Secret" in rec:
                    rec["Secret"] = "***"
                rec["_t"] = time.time()
                with self.lock:
                    self.actions.append(rec)
                if self.verbose:
                    print("AMI <- %s" % {k: v for k, v in rec.items() if k != "_t"}, flush=True)
                base = [("ActionID", aid)] if aid else []
                if name == "login":
                    if act.get("Username") == self.user and act.get("Secret") == self.secret:
                        authed = True
                        await self._send(w, [("Response", "Success")] + base +
                                         [("Message", "Authentication accepted")])
                    else:
                        await self._send(w, [("Response", "Error")] + base +
                                         [("Message", "Authentication failed")])
                        break
                    continue
                if name == "logoff":
                    await self._send(w, [("Response", "Goodbye")] + base +
                                     [("Message", "Thanks for all the fish.")])
                    break
                if not authed:
                    await self._send(w, [("Response", "Error")] + base +
                                     [("Message", "Authentication Required")])
                    continue
                if name == "coreshowchannels":
                    with self.lock:
                        chans = [dict(c) for c in self.channels]
                    await self._send(w, [("Response", "Success")] + base +
                                     [("EventList", "start"), ("Message", "Channels will follow")])
                    for c in chans:
                        await self._send(w, [("Event", "CoreShowChannel")] + base +
                                         list(c.items()))
                    await self._send(w, [("Event", "CoreShowChannelsComplete")] + base +
                                     [("EventList", "Complete"), ("ListItems", str(len(chans)))])
                elif name == "originate":
                    await self._send(w, [("Response", "Success")] + base +
                                     [("Message", "Originate successfully queued")])
                    asyncio.ensure_future(self._later(self.up_delay,
                                                      lambda a=act: self._new_channel(a)))
                elif name == "hangup":
                    self.remove_channel(act.get("Channel", ""))
                    await self._send(w, [("Response", "Success")] + base +
                                     [("Message", "Channel Hungup")])
                elif name == "playdtmf":
                    await self._send(w, [("Response", "Success")] + base +
                                     [("Message", "DTMF successfully queued")])
                else:
                    await self._send(w, [("Response", "Error")] + base +
                                     [("Message", "Invalid/unknown command")])
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def start(self, host="127.0.0.1", port=0):
        self.loop = asyncio.get_running_loop()
        self.server = await asyncio.start_server(self._client, host, port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    def start_in_thread(self, host="127.0.0.1", port=0):
        """Run the server in a background thread; returns the port once listening."""
        ready = threading.Event()

        def body():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.start(host, port))
            ready.set()
            loop.run_forever()

        self._thread = threading.Thread(target=body, daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise RuntimeError("fake AMI did not start")
        return self.port

    def stop(self):
        if self.loop and self.server:
            self.loop.call_soon_threadsafe(self.server.close)
            self.loop.call_soon_threadsafe(self.loop.stop)


def main():
    ap = argparse.ArgumentParser(description="Fake Asterisk Manager Interface (records actions)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5038)
    ap.add_argument("--user", default="webtab")
    ap.add_argument("--secret", default="secret")
    ap.add_argument("--incoming-context", default="intercom-incoming")
    ap.add_argument("--busy", action="store_true", help="originated calls never come Up")
    a = ap.parse_args()
    fake = FakeAmi(a.user, a.secret, a.incoming_context, answers=not a.busy, verbose=True)

    async def serve():
        port = await fake.start(a.host, a.port)
        print("fake AMI listening on %s:%d" % (a.host, port), flush=True)
        await asyncio.Event().wait()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
