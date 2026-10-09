# Ringtones and hearing

People hear different pitches differently, so a doorbell that is loud for one person can
be almost silent for another. The station has four ringtones that put their energy into
different frequency ranges. Choose one with `[ui] ringtone` in `webtab.ini`.

## The four ringtones

| Name | Sound | Frequencies | Loop |
|---|---|---|---|
| `low` | Two-note chime, E4 then C4, with a soft bell decay | 330 Hz and 262 Hz, a weak second harmonic, practically nothing above ~700 Hz | 2.0 s |
| `mid` (default) | Three-note descending chime, G5, E5, C5 | 784, 659 and 523 Hz. The fundamentals stay within 500–1000 Hz, where small tablet speakers are most efficient. | 1.6 s |
| `high` | Classic bright electronic trill, two bursts | 1800 and 2250 Hz, alternating 20 times a second | 1.6 s |
| `sweep` | Three rising pulses | Each pulse sweeps from 300 Hz to 3 kHz | 1.6 s |

All four are short 8 kHz mono WAV files, synthesised by `tools/ringtones.py` and embedded
in the page, so they work without network access. They loop until you answer, decline,
or the caller gives up.

To listen before choosing, open these in any browser. They need no token.

```
http://<server>:8080/ring/low.wav
http://<server>:8080/ring/mid.wav
http://<server>:8080/ring/high.wav
http://<server>:8080/ring/sweep.wav
```

## Which suits which kind of hearing

These are general guidelines, not medical advice. The only reliable test is listening at
the place where the person usually is.

| Hearing | Try first | Why |
|---|---|---|
| Typical hearing | `mid` or `high` | `high` cuts through room noise, a TV, a running tap. |
| **High-frequency loss**, the most common kind (with age and after noise exposure). High pitches fade first: birdsong, a microwave beep, "s" and "f" sounds. | `low`, then `mid` | `low` keeps all its energy below ~700 Hz. `high` may be inaudible. |
| **Low-frequency loss**, less common. Low voices and humming are hard to hear. | `high`, then `mid` | Energy at 1.8–2.3 kHz |
| Mixed, flat or unknown, or several people with different hearing | `sweep` | Each pulse crosses 300 Hz – 3 kHz, so every listener catches the part that falls in their audible range. |
| Hearing aids | test all four with the aids in | Aids differ in which bands they amplify. |

## Choosing in practice

1. Set the iPad's volume high while a sound is playing (see
   [ipad-setup.md](ipad-setup.md#sound)).
2. Play each tone from where the person usually sits, from the next room, and with the
   usual background noise.
3. Ask the person who must hear it, rather than the person installing it.
4. Put the winner into `webtab.ini`, restart, and reload the page:

   ```sh
   sudo systemctl restart intercom-webtab
   sudo -u webtab touch /var/lib/intercom-webtab/reload.flag   # or pull to refresh
   ```

5. Ring once for real with a demo call (see
   [door-stations.md](door-stations.md#demo-calls-never-open)), and tap the screen once
   first so iOS allows sound.

## The speaker matters

The built-in speakers of old iPads are small. They reproduce little below a few hundred
hertz, so `low` sounds quieter on them than the other tones. If `low` is the right pitch
but too quiet, connect a small powered speaker to the headphone jack. The ringtone then
gets both the right pitch and enough level.

## Fallback beeps

If the ringtone file does not start within 0.7 s, for example because iOS blocked it,
the page beeps with Web Audio instead. It alternates two square-wave tones in the range
of the chosen ringtone:

| Ringtone | Fallback pitches |
|---|---|
| `low` | 262 / 330 Hz |
| `mid` | 523 / 784 Hz |
| `high` | 1800 / 2250 Hz |
| `sweep` | 400 / 1600 Hz |

Square waves also contain odd harmonics, so the fallback sounds brighter than the
ringtone itself.

## Seeing a call, too

When someone rings, the camera picture fills the left half and the third button turns
cyan. The station has no flashing light or vibration, so place the iPad where it is
easily seen if sound alone is not enough.

## Regenerating the ringtones

```sh
python3 tools/ringtones.py          # rewrite static/ring/*.wav and the copies in index.html
python3 tools/ringtones.py --check  # exit code 1 if anything is stale
```

The output is deterministic. A very quiet dither, about −84 dBFS, replaces digital
silence.
