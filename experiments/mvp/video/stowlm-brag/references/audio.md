# Audio

The demo cut has a human voice, the generated radio clip, and a music bed. The voice leads; music sits under it. The launch cut has music only.

## Tracks

All "Happy Beats / Business Moves" by Sascha Ende (ende.app), CC BY 4.0, vendored in `experiments/mvp/video/brag-vendor/skills/brag/assets/music/`. Copied into the composition's `assets/`, never referenced from the vendor folder.

| Cut | Track | Length | Why |
|---|---|---|---|
| Demo | vol-1 | 2:44 | the only bundled track near 3:00; steady enough to sit under speech |
| Demo | vol-10, after vol-1 | 1:00 | covers the last seconds and the credits once vol-1 ends; crossfade at a phrase end |
| Launch | vol-11 | 1:28 | warm, mid-energy, fits ~27 s with no voice |

vol-9 and vol-12 are unused. vol-12 (brag's `polished` pick) stays as a fallback if vol-1 fights the voice.

## Levels

| Element | Volume |
|---|---|
| Music bed, no voice (hook gaps, shot 11 card hold, credits) | 0.32 (`bed_volume`) |
| Music under voice or face cam | 0.09 (`duck_volume`) |
| Music under the radio clip | 0.09 |
| Voice | 1.0, recorded at about -16 LUFS (see [voice.md](voice.md)) |
| Radio clip | 1.0; it already has its noise baked in |
| Launch cut music | 0.32, swelling to about 0.4 on the end card; never above 0.5 |

Brag ducks to 0.12-0.15 for TTS. A non-professional human voice is quieter and less even, so we duck further.

## Ducking and fades

- Duck down over 0.3 s, starting 0.2 s before the voice; come back up over 0.6 s, starting 0.3 s after it ends.
- Gaps under 1.5 s between voice lines stay ducked; pumping between sentences sounds amateur.
- Fade in over 1.5 s at the film's start (under the shot 1 radio static). Fade out over 3 s on the credits, finishing at the last frame.
- Launch cut: fade in 0.5 s, fade out 1.5 s on the end card.

## Not used

- **SFX** in the demo cut (Kenney and OpenGameArt, CC0). The recorded shots carry their own sounds (radio static, the hook's hits if captured). A single soft accent on the launch end card is allowed if it helps; credit isn't required for CC0, but NOTICE.md lists the source.
- **Beat-locking** text to music cues. Voice timing decides where things land.
- **Audio-reactive visuals.** No RMS-driven glow or pulse; they compete with the gauges and labels.
- **Kokoro TTS** and any other TTS model. Scratch is macOS `say` only.

## Credit

"Music: Sascha Ende, ende.app (CC BY 4.0)" in the credits, on the launch end card, and in every post text. `check_honesty.py` fails without it.
