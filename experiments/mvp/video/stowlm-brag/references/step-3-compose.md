# Step 3: Compose

Brag hands a written brief to HyperFrames and lets it design the composition. We generate the composition from data instead, so the honesty check can read the same source the video is built from.

## What `tools/build_comp.py` produces

Input: `shots.json`, the recorded shots in `experiments/mvp/ui/video-draft/v6/`, and the measured duration of each voice file (recorded `record-kit/vo/shot-NN.wav`, or the scratch file).

Output: a HyperFrames composition (`index.html` plus an `assets/` folder) at 1920×1080, 30 fps, with:

| Layer | Content |
|---|---|
| Video | each `shot-NN.webm`, in order, at the playback rate the voice needs (1.0 down to 0.8) |
| Captions | the voice line, timed to the voice, also written to the `.srt` |
| Chips | the shot's labels, top corner, held for the whole shot |
| Lower third | Meta model or engine name and what it does, in on the shot's first second, held at least 3 s |
| Whole-film label | "Synthetic ships and cargo, simulated port", always visible |
| Radio clip | `radio_clip.path` in shot 4 at `at_s`, labelled generated |
| Voice | one audio element per shot |
| Music | the bed, ducked under each voice element (see [audio.md](audio.md)) |
| Face cam | optional picture-in-picture: intro over shot 2, outro over the credits |
| Credits | `credits.lines`, with `{narration}` filled from what was used |

Animation is CSS keyframes and WAAPI, positioned by HyperFrames' `hf-seek` protocol so every frame renders the same when seeked. No GSAP, no CDN, no network fetches; fonts and media are local files inside the composition folder, referenced by relative paths.

## What we kept from brag

- The composition is a HyperFrames project and `hyperframes check` is the single pre-render gate, contrast failures included.
- Assets are copied into the composition's `assets/`, never referenced by absolute path.
- Brag's grounding rule: every claim on screen must exist in the project. Ours is stricter: it must exist in `shots.json` with a source, and `check_honesty.py` enforces it.

## What we changed

- No `brag-plan.md` or `composition-brief.md`; `shots.json` is both.
- No beat-locking, no audio-reactive extraction, no SFX in the demo cut. The voice leads.
- No HyperFrames storyline, registry effects or generic launch-video workflow.

## Run the gate

From the composition folder, with the env from SKILL.md:

```
<repo>/experiments/mvp/video/.tools/node_modules/.bin/hyperframes check
uv run python <repo>/experiments/mvp/video/stowlm-brag/tools/check_honesty.py <composition>
```

Fix every error. If `check` reports `0/0 text checks`, contrast is unverified: convert any `oklch()` colours to hex and run it again. `--no-contrast` doesn't satisfy the gate. `snapshot` is fine for a visual check of the frames.

## Self-review

- [ ] Each shot plays the current file from `video-draft/v6/`, not a copy.
- [ ] Each chip and lower third matches `shots.json` word for word.
- [ ] Shot 4's radio clip plays before the voice and carries "Generated voice".
- [ ] Credits name the three Meta models, the engine, the build window and the music credit.
- [ ] `hyperframes check` and `check_honesty.py` both pass.
