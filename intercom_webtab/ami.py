"""Asterisk Manager Interface client and channel-selection rules.

* One persistent connection (``Events: off``) carries actions: Originate, PlayDTMF, Hangup.
  If it is down, an action is sent over a short-lived connection instead.
* ``channels()`` asks ``CoreShowChannels`` over its own short-lived connection, so a slow
  listing never blocks actions.

The selection helpers are pure functions (unit-tested). Lessons they encode:

* The PJSIP channel of our own "open the door" call (``Originate ... Application=Wait``)
  runs in the trunk endpoint's context, which is the same context as a guest's call. It
  must be recognised by ``app == "Wait"``, never by context.
* PlayDTMF on a channel that is still ringing is lost: only ``Up`` channels are used.
"""
import asyncio
import itertools
import logging

log = logging.getLogger("iwt.ami")

_ids = itertools.count(1)


# ---------------------------------------------------------------------------- pure helpers

def dial_string(cfg, panel):
    """Asterisk dial string for a panel: its own endpoint (door station registered to this
    Asterisk) or ``<number>@<trunk>`` through the SIP provider."""
    if panel.endpoint:
        return "PJSIP/%s" % panel.endpoint
    return "PJSIP/%s@%s" % (panel.number, cfg.asterisk.trunk)


def channel_prefixes(cfg):
    """Channel name prefixes of the trunk and of every panel endpoint."""
    out = []
    if cfg.asterisk.trunk:
        out.append("PJSIP/%s-" % cfg.asterisk.trunk)
    for p in cfg.panels:
        if p.endpoint:
            out.append("PJSIP/%s-" % p.endpoint)
    return tuple(out)


def parse_block(block):
    """One AMI message (``Key: value`` lines) -> dict."""
    out = {}
    for ln in block.replace("\r\n", "\n").split("\n"):
        k, sep, v = ln.partition(":")
        if sep:
            out.setdefault(k.strip(), v.strip())
    return out


def channel_from_event(ev):
    """A ``CoreShowChannel`` event -> {chan, state, ctx, app, cid} or None."""
    if not ev.get("Channel"):
        return None
    return {"chan": ev.get("Channel", ""), "state": ev.get("ChannelStateDesc", ""),
            "ctx": ev.get("Context", ""), "app": ev.get("Application", ""),
            "cid": ev.get("CallerIDNum", "")}


def on_trunk(chans, prefixes):
    """Channels that belong to the trunk or to a panel endpoint."""
    return [c for c in chans if any(c["chan"].startswith(p) for p in prefixes)]


def _num(x):
    return (x or "").strip().lstrip("+")


def find_call_channel(chans, prefixes, incoming_contexts, view_context, number=None,
                      up_only=True, logger=None):
    """The guest's call channel: on the trunk, in an incoming context, newest first.
    Our own Wait channels are never a guest call. Falls back to any trunk channel that is
    not a camera-view call (and logs it)."""
    cands = [c for c in reversed(on_trunk(chans, prefixes))
             if (c["state"] == "Up" or not up_only) and c["app"] != "Wait"]
    if number:
        for c in cands:
            if c["ctx"] in incoming_contexts and _num(c["cid"]) == _num(number):
                return c["chan"]
    for c in cands:
        if c["ctx"] in incoming_contexts:
            return c["chan"]
    for c in cands:
        if c["ctx"] != view_context:
            if logger:
                logger.warning("call channel found by the fallback rule: %s ctx=%r app=%r",
                               c["chan"], c["ctx"], c["app"])
            return c["chan"]
    return None


def find_new_wait_channel(chans, prefixes, before, view_context):
    """Our own fresh "open the door" channel: new since ``before``, answered (Up) and
    running ``Wait``. Identified by the application, not by the context."""
    for c in on_trunk(chans, prefixes):
        if (c["chan"] not in before and c["state"] == "Up" and c["app"] == "Wait"
                and c["ctx"] != view_context):
            return c["chan"]
    return None


def find_view_channel(chans, prefixes, before, view_context):
    """Our camera-view call: a new trunk channel in the view context -> (chan, is_up)."""
    for c in on_trunk(chans, prefixes):
        if c["ctx"] == view_context and c["chan"] not in before:
            return c["chan"], c["state"] == "Up"
    return None, False


def is_up(chans, chan):
    return any(c["chan"] == chan and c["state"] == "Up" for c in chans)


def format_action(fields):
    return ("".join("%s: %s\r\n" % (k, v) for k, v in fields.items()) + "\r\n").encode("utf-8")


# ---------------------------------------------------------------------------- client

class Ami(object):
    def __init__(self, host, port, user, secret, timeout=3.0):
        self.host, self.port = host, port
        self.user, self.secret = user, secret
        self.timeout = timeout
        self.w = None
        self.connected = False
        self._pending = {}             # ActionID -> action name (to log failures)
        self._auth_failed = False
        self._down_logged = False

    def _login(self):
        return format_action({"Action": "Login", "Username": self.user, "Secret": self.secret,
                              "Events": "off", "ActionID": "login"})

    async def _open(self):
        r, w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        try:
            w.write(self._login())
            await w.drain()
            # the banner line arrives glued to the login response
            blk = (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), self.timeout)).decode(
                "utf-8", "ignore")
            if "Response: Success" not in blk:
                if not self._auth_failed:
                    log.error("AMI login refused: check [asterisk] ami_user / ami_secret "
                              "in the secrets file and manager.conf")
                self._auth_failed = True
                raise PermissionError("AMI login refused")
            self._auth_failed = False
        except Exception:
            w.close()
            raise
        return r, w

    async def run(self):
        """Keep the persistent connection up (task for the whole process lifetime)."""
        delay = 3
        while True:
            try:
                r, self.w = await self._open()
                self.connected = True
                self._down_logged = False
                delay = 3
                log.info("AMI connected to %s:%d", self.host, self.port)
                await self._read_loop(r)
            except asyncio.CancelledError:
                raise
            except PermissionError:
                delay = 30
            except Exception as e:
                if not self._down_logged:
                    log.warning("AMI unavailable at %s:%d (%s); retrying in the background",
                                self.host, self.port, e.__class__.__name__)
                    self._down_logged = True
            finally:
                if self.w is not None:
                    try:
                        self.w.close()
                    except Exception:
                        pass
                self.w = None
                self.connected = False
            await asyncio.sleep(delay)

    async def _read_loop(self, r):
        while True:
            blk = (await r.readuntil(b"\r\n\r\n")).decode("utf-8", "ignore")
            msg = parse_block(blk)
            aid = msg.get("ActionID")
            name = self._pending.pop(aid, None) if aid else None
            if name and msg.get("Response") == "Error":
                log.warning("AMI %s failed: %s", name, msg.get("Message", "?"))

    async def action(self, fields):
        """Send an action without waiting for the result. False if Asterisk is unreachable."""
        fields = dict(fields)
        aid = "iwt-%d" % next(_ids)
        fields.setdefault("ActionID", aid)
        data = format_action(fields)
        if self.w is not None and self.connected:
            try:
                if len(self._pending) > 200:
                    self._pending.clear()
                self._pending[fields["ActionID"]] = fields.get("Action", "?")
                self.w.write(data)
                await self.w.drain()
                return True
            except Exception as e:
                log.warning("AMI write failed (%s), using a one-shot connection", e)
        try:
            r, w = await self._open()
            try:
                w.write(data)
                await w.drain()
                blk = (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), self.timeout)).decode(
                    "utf-8", "ignore")
                if "Response: Error" in blk:
                    log.warning("AMI %s failed: %s", fields.get("Action"),
                                parse_block(blk).get("Message", "?"))
                w.write(format_action({"Action": "Logoff"}))
            finally:
                w.close()
            return True
        except Exception as e:
            log.warning("AMI %s not sent: %s", fields.get("Action"), e.__class__.__name__)
            return False

    async def channels(self):
        """All Asterisk channels: [{chan, state, ctx, app, cid}] ([] on any error)."""
        out = []
        try:
            r, w = await self._open()
        except Exception:
            return out
        try:
            w.write(format_action({"Action": "CoreShowChannels", "ActionID": "list"}))
            await w.drain()
            while True:
                blk = (await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), self.timeout)).decode(
                    "utf-8", "ignore")
                ev = parse_block(blk)
                if ev.get("Event") == "CoreShowChannelsComplete" or ev.get("Response") == "Error":
                    break
                c = channel_from_event(ev)
                if c:
                    out.append(c)
            w.write(format_action({"Action": "Logoff"}))
        except Exception:
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass
        return out
