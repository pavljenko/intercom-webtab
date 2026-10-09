# iPad setup

The station is a web page saved to the iPad's Home Screen. Nothing is installed from the
App Store.

## Which iPads

- **iOS 9 or newer.** The page is plain ES5 JavaScript and needs no newer browser
  features, so it suits the oldest iPads that still run Safari well: iPad 2, iPad 3rd and
  4th generation, iPad mini 1. Newer iOS versions work too.
- **Landscape, 1024x768 points.** The layout is fixed at 1024x748: landscape minus the
  status bar. This matches 9.7-inch iPads and the iPad mini up to the 5th generation.
  iPads with a larger screen show the same layout in the top-left corner with a black
  margin.
- Keep it on a charger all the time. Old batteries age; check now and then that the case
  is not bulging.

## Add the station to the Home Screen

1. Make sure the iPad can reach the server: same Wi-Fi, or a VPN (see
   [security.md](security.md)).
2. In **Safari**, open the address the installer printed:
   `http://<server>:8080/?k=<token>`. To see it again:
   `echo "http://$(hostname -I | cut -d' ' -f1):8080/?k=$(sudo cat /etc/intercom-webtab/token)"`.
3. Tap **Share > Add to Home Screen**. The name comes from `[station] title`, and you can
   edit it before saving.
4. Start the station **from the new Home-Screen icon**, not from Safari. It then runs full
   screen, without an address bar.

The token is part of the saved address. If you change the token, remove the icon and add
it again.

## Make it a wall station

| Setting | Where (it moved between iOS versions) | Why |
|---|---|---|
| **Auto-Lock: Never** | Settings > General > Auto-Lock (iOS 9), Settings > Display & Brightness > Auto-Lock (iOS 10+) | A locked iPad does not ring. |
| **Rotation lock in landscape** | Side switch set to "Lock Rotation" (older iPads), or Control Center | The layout is landscape only. |
| **Side switch not on Mute** | Settings > General > "Use Side Switch to" | On some iOS versions, mute silences Web Audio, which carries the visitor's voice. |
| **Volume** | Side buttons, see [Sound](#sound) | |
| **Notifications off** | Settings > Notifications, or Do Not Disturb | Banners cover the buttons. |
| **Automatic updates off** | Settings > General > Software Update > Automatic Updates (iOS 12+) | An unattended update can restart the iPad into a setup screen. |
| **Guided Access** | Settings > General > Accessibility > Guided Access (iOS 9). Newer: Settings > Accessibility > Guided Access | Keeps the station in front. See below. |

### Guided Access

Guided Access locks the iPad to a single app. For the station, that app is the
Home-Screen web app.

1. Turn on Guided Access and set a passcode.
2. Start the station from its icon, then triple-click the Home button and tap **Start**.
3. Under **Options**, keep **Touch** enabled. Keep **Volume Buttons** enabled if you
   want to adjust the volume. Disabling the **Sleep/Wake Button** prevents accidental
   locking.
4. Triple-click Home and enter the passcode to leave Guided Access.

## Sound

iOS plays no sound from a web page until the page has been touched once after loading.
The station handles this as follows:

- After loading, the status line says **"Sound is off — tap the screen"**. Tap anywhere
  once and the message goes away.
- The page then keeps the audio path awake, and the ringtone and the visitor's voice
  play without further taps.
- **Every reload needs one tap again**: a manual refresh, a self-update after a new
  version is installed, or a watchdog reload. If the station will hang unattended for a
  long time, check the status line now and then.
- The ringtone plays through an HTML `<audio>` element, so it follows the **media
  volume** and is not silenced by the mute switch. The visitor's voice uses Web Audio.
- Set the volume with the side buttons while something is playing: during a ring, a
  camera view or a demo call. If **Settings > Sounds > Change with Buttons** is off, the
  buttons always set the media volume, which is the one the station uses.
- To hear a ringtone before choosing, open `http://<server>:8080/ring/mid.wav` (also
  `low`, `high`, `sweep`). These need no token. See
  [ringtones-and-hearing.md](ringtones-and-hearing.md).

## Using the station

| Button | Idle | Ringing | During a call | Camera view |
|---|---|---|---|---|
| Panel buttons (1 or 2) | Show that camera | No effect | No effect | Switch camera |
| Third button | **Refresh** | **Answer** (cyan) | **Open** | **Open** |
| Close (top left, over the video) | Hidden | Decline (the visitor hears busy) | Hang up | Close the view |

- **Open while ringing** answers and opens in one tap.
- After a successful open, the button turns green and the caption says "Gate open" (from
  the panel's label). 20 s after the last touch, the station hangs up and returns to the
  weather panel. Touching the screen restarts that countdown.
- A camera view closes itself 90 s after the last touch.
- If something fails, the button shakes red and a short caption tells you why. For
  example: "Another panel is ringing", "Panel busy", "No connection to the server".

## Refresh and self-healing

- **Pull to refresh:** drag down from anywhere below the top edge. Avoid the top 24 px,
  where iOS opens the notification shade. The drag is 96 px when idle, and 160 px during
  a call or a view, where it must start outside the video and the buttons so a
  conversation is never cut by accident. Release when the hint says "Release to
  refresh".
- **Refresh button:** the third button, when idle.
- The page **never reloads without an answer from the server**, because a Home-Screen web
  app left on an error page has no way back. If the server does not answer, you see "No
  connection to the server — pull again". A second pull within 10 s forces the reload
  anyway.
- **Self-update:** every 2 minutes while idle, the page asks the server for the page
  version and reloads when a new one is installed. To make idle stations reload without
  a new version:

  ```sh
  sudo -u webtab touch /var/lib/intercom-webtab/reload.flag
  ```

- **Watchdog:** the page reloads itself if its scripts did not start within 30 s, if its
  timers stopped, if the event connection is silent for 3 minutes while HTTP still
  works, or if a call or view has been "busy" for 15 minutes. After three automatic
  reloads in a row it pauses for 10 minutes and shows "Station keeps failing — pull down
  to refresh".
- The page reconnects its event channel when the server's 5-second pulse stops for 20 s.
  iOS does not always report a dead connection. It also reconnects when the iPad wakes
  up.

## Supported iOS versions

| iOS | Notes |
|---|---|
| 9.x | The baseline. Everything is written for it: ES5, no flexbox or CSS variables, `webkitAudioContext`, MJPEG in an `<img>`. |
| 10-12 | Works the same way. |
| 13 and newer | Works. "Add to Home Screen" is still in Safari's Share menu. |

The page has no microphone or camera access, and it asks for none.
