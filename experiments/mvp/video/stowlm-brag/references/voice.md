# Voice

The user narrates in their own voice. They aren't a professional, and they don't need to be: clear, steady and unhurried beats polished. This file is for whoever directs the take and builds with it. The user's own instructions are in `record-kit/RECORD.md` and the teleprompter.

## How `demo-cut/build.sh` picks audio

| Per shot | Used |
|---|---|
| `record-kit/vo/shot-NN.wav` exists | the user's recording |
| it doesn't | `scratch-NN.aiff` from macOS `say`, converted to wav; the whole render is a draft with a SCRATCH watermark |
| `record-kit/cam/intro.mov` exists | its audio replaces shot 2's voice; picture in picture over shot 2 |
| `record-kit/cam/outro.mov` exists | plays over the credits; the credits' music stays ducked under it |

Shots can be recorded in any order and swapped in one at a time. A retake just overwrites `shot-NN.wav`.

## Pacing

- About 2.4 words a second (`words_per_s` in `shots.json`). Shot 12 is the tightest (63 words in 24 s, about 2.6 a second), then shots 2 and 10 (2.5); shot 4's line starts at about 3.7 s, after the radio clip.
- If a line runs long, the build slows the shot to as low as 0.8. Past that, trim in the script's trim order (see step-2-plan.md), never a never-trim line, and never by speaking faster than is clear.
- Pause where the `delivery` note says (after "12 left" in shot 9; on "47" and "92" in shot 5).

## Retakes

- Three takes per shot is plenty. Keep the clearest, not the most energetic.
- Read the line exactly as written: the words are checked against `docs/headline.md`. A changed word ("instant", "the whole ship", "beats") can break the honesty rules. If a line won't come out naturally, change it in `script-v6.md` and `shots.json` first, then record.
- Mispronounced numbers get a retake; captions show the number, and the voice must match.

## Levels and room

- Mono, 48 kHz, 24-bit wav. Peak around -6 dBFS, never clipping; speech around -16 LUFS.
- Quiet room, soft furnishings, laptop fans down. Same mic and distance (a hand span) for every shot, so they match.
- Half a second of silence before and after each line; the build trims it.
- No processing beyond a gentle high-pass. No noise-removal plug-ins that need a model download.

## Face cam (optional)

| Clip | Where | Notes |
|---|---|---|
| Intro, `cam/intro.mov` | over shot 2 | the user says shot 2's line on camera; its audio replaces shot 2's voice; same length limit (about 12 s) |
| Outro, `cam/outro.mov` | over the credits | short sign-off, about 6 s; say nothing that isn't already in the film |

On camera, the user says only what they choose to. Check the background for anything personal or any company name before using a take.

## Hero sentence (not done by this kit)

The user may record `vo/hero-sentence.wav` saying "Nothing goes into bay nine on this call." to replace the stock-TTS radio clip in shot 4. It can't be used yet: the reader's result on screen came from the stock clip, so a new clip needs a fresh Muse Voice Transcribe transcription and a fresh reader read, captured as a new frozen take (user item 134; D299). Until then shot 4 plays the stock clip, labelled "Generated voice". Never splice the user's voice over a transcript that came from a different clip.

## Credits line

`{narration}` in the credits fills from what was used: "the author's own voice" for a final; a draft says "scratch voice (macOS say), not final".
