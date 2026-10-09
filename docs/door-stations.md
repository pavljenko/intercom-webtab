# Door stations: how opening works

Most SIP door panels and building intercoms open the lock when they receive a **DTMF
digit** during a call, often `1`, `#` or a short code such as `*1`. Intercom WebTab sends
that digit through Asterisk (`PlayDTMF`) into a call with the panel. There are two cases:
the visitor's own call, and a short call the station places itself.

Per-panel settings in `webtab.ini` (see [configuration.md](configuration.md#panelid)):

```ini
[panel:gate]
dtmf = 1                          ; digit(s) that open the door
open_settle = 2.0                 ; s after the line comes up before the first digit
open_repeat = 0.7, 1.4, 2.1, 3.0  ; repeats, s after the first digit
hold = 12                         ; s our own door-opening call stays up
```

## During a visitor's call

1. The visitor presses the button and the station rings.
2. You tap **Answer**, then **Open**, or just **Open**, which answers first and waits
   0.7 s for the line to come up.
3. The station finds the **visitor's channel** in Asterisk: on the trunk or the panel's
   endpoint, in an incoming context, from the panel's number, in state `Up`, and not one
   of our own `Wait` calls. It retries for up to about 2.4 s.
4. It waits until `open_settle` seconds have passed since the call was answered, then
   sends `dtmf`.
5. It repeats the digit at each time in `open_repeat`, counted from the first digit. The
   button reports success right after the first digit, while the repeats run in the
   background.

Each digit lasts 250 ms. In a multi-digit code, digits are 100 ms apart.

If the station cannot find the visitor's channel, it answers "No call channel — try
again". **It never calls the panel during a call.** A demo or unrelated call must not
end with a real door opening.

## From a camera view (no call)

You can open a door while just watching its camera:

1. **Open** makes the station call the panel: `Originate` to the panel (its `endpoint`,
   or `number@trunk`) with `Application=Wait` for `hold` seconds, and CallerID
   `own_callerid`. The panel must answer such calls automatically (see
   [asterisk.md](asterisk.md#calls-to-the-panel)).
2. The station polls every 0.15 s for that new channel to come `Up`. It recognises the
   channel by the application `Wait`, because it shares the incoming context with
   visitors' calls.
3. It waits `open_settle` seconds after the line came up, sends the digit, and repeats
   it as above.
4. **A second press within `hold` seconds** sends the digit into the same line. A new
   call to a panel that is already in a call would fail.

If the panel does not answer within `hold` seconds, you get "Panel busy". A panel that
is talking to another flat does not take a second call, so "busy" and "no answer" look
the same. If a visitor calls while the station waits, the open is cancelled ("Cancelled:
incoming call"), so the visitor's new channel is never mistaken for ours.

If the camera view itself runs over SIP (no RTSP camera), the digit goes into that view
call instead, and no extra call is needed.

## Safety rules

- **The station never answers by itself.** Only Answer or Open answers a call.
- **One panel at a time:** while one panel rings or talks, Open for the other panel is
  refused with "Another panel is ringing" (HTTP 409). A finger resting on Open in the
  gate view cannot let in the visitor at the entrance.
- **Closing a view never ends a call.** If a visitor rings a moment before you close a
  view, the call survives.
- **Calls are matched by SIP Call-ID.** A late hang-up of an old view never ends a
  visitor's call.
- **Demo calls never open a door** (see below).

## Tuning

Timing differs between panels. Many ignore DTMF for a second or two after the line comes
up. A spare digit is harmless, because the door is already open, but a missed digit
means no opening. The defaults suit most panels. Change them when you see one of these
symptoms:

| Symptom | Try |
|---|---|
| The door opens only on the second press | Raise `open_settle`, for example to `3.0` |
| The door opens, but late | Lower `open_settle` and rely on `open_repeat` |
| The panel beeps or reacts badly to repeated digits | Shorten `open_repeat`, or leave it empty (`open_repeat =`) |
| The panel needs a code | `dtmf = *1` or `dtmf = 1#` (up to 8 digits) |
| A phone keypad opens the door, the station never does | Wrong DTMF transport: change `dtmf_mode` ([asterisk.md](asterisk.md#dtmf-modes)) |
| "Panel busy" from a camera view | Raise `hold` if the panel answers slowly. Make sure it accepts calls from the server or your number. |
| The panel stays busy for visitors after you open from a view | Lower `hold`. While our call is up, the panel cannot take a visitor's call. |

Watch the log while you test:

```sh
journalctl -u intercom-webtab -f
```

Every digit is logged with its timing:

```
open door: DTMF #1 2.0 s after connect -> PJSIP/provider-0000002a
open door: DTMF #2 2.7 s after connect -> PJSIP/provider-0000002a
```

After a change, restart the service: `sudo systemctl restart intercom-webtab`.

## Demo calls never open

A demo call lets you check the ringtone, the video and the buttons without anyone at the
door:

- start one with `POST /api/demo` (see [asterisk.md](asterisk.md#testing-with-the-panel-simulator))
  or with `tools/panelsim.py`;
- the simulator's display name `panelsim` marks the call as a demo;
- **Open** on a demo call answers it and shows "Demo: nothing was opened". No DTMF is
  sent and no call to a panel is made;
- through Asterisk, simulator calls come from `127.0.0.1` and land in the demo context,
  which has no route to any panel;
- hanging up a demo call hangs up only channels in the demo context.

Demo calls are still real calls to the station. Do not start one while you expect a
visitor.
