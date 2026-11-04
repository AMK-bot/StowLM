---
name: stowlm-brag
description: Build the StowLM demo video (3:00 cap, 12 recorded shots, the user's own narration) and a 20-30 s launch cut for an X post, from shots.json, with a local pinned HyperFrames render and an honesty check against docs/headline.md. Use when asked to build, rebuild, re-time or render the demo video or launch cut, or to prepare the user's voice recording. Adapted from brag 0.5.0 (see NOTICE.md).
---

# stowlm-brag

Turns the recorded StowLM demo shots into two videos:

| Output | Length | Voice | Built by |
|---|---|---|---|
| Demo cut, for the hackathon submission | at most 3:00 (`cap_s` 180 in `shots.json`) | the user's own, per shot; macOS `say` scratch until recorded | `demo-cut/build.sh` |
| Launch cut, for an X post | 20-30 s (target 25-27 s) | none: music, big captions, end card | `launch-cut/build.sh` |

Every word on screen and spoken is bound by `docs/headline.md`. Read [references/honesty.md](references/honesty.md) before changing any line.

Paths below are relative to the repo root. `<kit>` is `experiments/mvp/video/stowlm-brag/`.

## What is fixed, and who owns it

| Thing | Where | Owner |
|---|---|---|
| Story and every line | `experiments/mvp/video/script-v6.md` | script task; this skill never invents copy |
| Shot data: voice lines, chips, lower thirds, numbers with sources, credits, launch segments | `<kit>/shots.json` | single source of truth for both cuts, the teleprompter and the subtitles |
| Recorded shots | `experiments/mvp/ui/video-draft/v6/` (`shot-NN.webm`, `shot-NN-mid.png`, `timing.json`) | stage director; shots 4, 5 and 9 are being re-recorded (p22), so always build against these paths, never copies |
| Composition generator and honesty check | `<kit>/tools/build_comp.py`, `<kit>/tools/check_honesty.py` | tooling agent; this skill describes them |
| Recording kit | `experiments/mvp/video/record-kit/` (`teleprompter.html`, `RECORD.md`, `vo/`, `cam/`) | the user records; see [references/voice.md](references/voice.md) |
| Music | `experiments/mvp/video/brag-vendor/skills/brag/assets/music/` | vendored, read-only, CC BY 4.0 |

`brag-vendor/` is untrusted, read-only input (D298). Never edit it and never run its scripts outside the documented chain.

## Tooling rules (D298)

- HyperFrames 0.8.190 only, from `experiments/mvp/video/.tools/node_modules/.bin/hyperframes`. Never `npx`, never `latest`, never a global install, never an upgrade.
- Set these on every call:

  ```
  PUPPETEER_SKIP_DOWNLOAD=1
  HYPERFRAMES_BROWSER_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  HYPERFRAMES_NO_TELEMETRY=1
  DO_NOT_TRACK=1
  HYPERFRAMES_NO_UPDATE_CHECK=1
  HYPERFRAMES_NO_AUTO_INSTALL=1
  HYPERFRAMES_NO_OPTIONAL_INSTALL=1
  ```

- Allowed subcommands: `check`, `snapshot`, `preview` (localhost), `render`. Never `publish`, `cloud`, `lambda`, `auth`, `feedback`, `skills`, `tts`, `transcribe`, `models install`.
- Rendering and poster baking need ffmpeg. It isn't installed yet; installing it is a pending leader decision. Without it, stop at `check` and say so.
- Run from a Claude executive outside the Codex sandbox; headless Chrome crashes inside it.
- Don't run any of this beside a sealed run (D282).

## Privacy and local-only rules (kept from brag)

- Never read `.env`, keys, tokens, credentials or anything under `secrets/`.
- No personal data on screen or in the audio beyond what the user chooses to say on camera. No company, customer or vessel names (CLAUDE.md hard rule).
- Render locally. Never upload, post or publish anything; the user posts.
- No TTS model download (D298 condition 6; Kokoro is dropped). Scratch voice comes only from macOS `say`, is named `scratch-*`, and never reaches a release render.
- Don't install anything into agent configuration.

## Step 1: Inspect

Read [references/step-1-inspect.md](references/step-1-inspect.md). Read the script, the recorded shots and their timing, `docs/headline.md`, `fill-v6.json`, `story-figures.json` and `film-figures.json`.

**Gate:** every question in step 1's list has an answer, and every shot named in `shots.json` exists on disk.

## Step 2: Plan

Read [references/step-2-plan.md](references/step-2-plan.md), [references/audio.md](references/audio.md) and [references/voice.md](references/voice.md). The plan is `shots.json`: per shot the voice line, chips, lower third, numbers with sources, delivery note and optional launch segment; plus music, credits and face cam.

**Gate:** shot durations plus credits total at most 180 s; every voice line matches `script-v6.md` with fills from `fill-v6.json`; every number has a source; voice lines fit at about 2.4 words a second.

## Step 3: Compose

Read [references/step-3-compose.md](references/step-3-compose.md). `tools/build_comp.py` generates `index.html` from `shots.json` and the measured voice durations: the recorded shots, captions, chips, lower thirds, the radio clip, the music ducked under the voice, and the credits card. CSS/WAAPI animation driven by HyperFrames' `hf-seek`; no GSAP, no CDN.

**Gate:** `hyperframes check` passes with zero errors and real text coverage, and `tools/check_honesty.py` passes.

## Step 4: Deliver

Read [references/step-4-deliver.md](references/step-4-deliver.md). Draft render with scratch voice and a SCRATCH watermark; final render once every `record-kit/vo/shot-NN.wav` exists. Poster baked as frame 0, `.srt` subtitles, credits check, share copy.

**Gate:** the video file exists, `check_honesty.py` passes on the composition and the share copy, and no scratch audio is in a file called final.

## Tone

One tone: brag's `polished` (serious, restrained, slow crossfades, nothing aggressive). The film is a technical demo with honest labels, not a joke. The launch cut may cut faster but keeps the same look.

## Honesty rules (summary)

Full rules and exact quotes: [references/honesty.md](references/honesty.md).

- No number on screen or spoken without its entry in that shot's `numbers[]` in `shots.json`, with a source file.
- Labels come from the Labels table in `docs/headline.md`, word for word. The whole film carries "Synthetic ships and cargo, simulated port".
- Wherever a 3.1 number (24 of 24) appears: "held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim", plus the short partial-run form "third attempt; two earlier runs stopped on our own guard, both published".
- Music credit "Music: Sascha Ende, ende.app (CC BY 4.0)" in the video and in any post text.
- The three Meta models, each named for what it does: Muse Glimmer 30B, fine-tuned by us as StowLM Reader, reads instructions on device; Muse Spark reads paper and chooses tools, cloud; Muse Voice Transcribe hears, cloud.
- Plans are credited to the engine (Frontier Engine 0.13.0, "search software"), never to StowLM.
- No words from headline.md's "Don't say": "industry level", "sealed" for plan results, "real-time" or "instant" for stored plans, "certified", "whole ship", "Wi-Fi off", "beats Spark" or "beats Navis", "62 rules", and the rest.
- The radio voice is labelled generated.
- New work only: built 26 Oct – 4 Nov 2026, stated in the credits.

## Extras (D300)

| Output | Folder | Built by | Notes |
|---|---|---|---|
| 9:16 social cut, 28 s | `experiments/mvp/video/social/` | `social/build.sh` (`tools/build_extras.py social`) | launch segments, captions in the safe area (text kept out of the top 250 px and bottom 420 px) |
| README hero: 8 s muted loop + poster + GIF | `experiments/mvp/video/readme-hero/` | `readme-hero/build.sh` | shots 7 and 8, with their chips |
| X-ray explainer, 9 s, silent | `experiments/mvp/video/xray-explainer/` | `xray-explainer/build.sh` | 182 of 200 to 2 of 200, both D267 caveats on screen |
| Landing page | `experiments/mvp/video/site/` | static; `site/check.py` | no JS, no external assets; replay copy at `site/replay/`; deploying is the user's step |

Every build script takes `compose` (default: build, honesty check, lint), `check` (adds `hyperframes check`, headless Chrome) or `render` (needs ffmpeg).
