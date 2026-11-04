# Notice

## Adapted from brag

`stowlm-brag` is adapted from **brag 0.5.0** (latent-spaces/brag, commit 669ab9286a), vendored read-only at `experiments/mvp/video/brag-vendor/` (D298). We kept its four-step shape (inspect, plan, compose, deliver), its privacy and local-render rules, its HyperFrames `check` gate, its readability floor, its poster-as-frame-0 method and its share-copy file. Everything else is rewritten for the StowLM demo (D299).

brag is released under the MIT licence:

```
MIT License

Copyright (c) 2026 Shunit Haviv Hakimi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Music

"Happy Beats / Business Moves" (Vol. 1, 10 and 11 used) by Sascha Ende, [ende.app](https://ende.app/en), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Not covered by brag's MIT licence. Required credit, in the video and in any post or description: "Music: Sascha Ende, ende.app (CC BY 4.0)".

## Sound effects

brag bundles CC0 sound effects from Kenney (kenney.nl) and OpenGameArt (Keyboard Soundpack #1 by unicae_games). The demo cut uses none by default; the launch cut may use one soft accent. CC0 needs no credit; listed here for provenance.

## Rendering

HyperFrames 0.8.190 (HeyGen, Apache-2.0), pinned and installed project-locally in `experiments/mvp/video/.tools/` with `--ignore-scripts`, using the installed Google Chrome. Not redistributed.

## Deliberately not kept

| From brag | Why not |
|---|---|
| brag-slim model dispatch (`slim.md`, the Opus switch) | one workflow; the composition is generated from `shots.json` |
| The 15-25 s creative law for the main cut | the demo cut runs up to 3:00; the launch cut keeps 20-30 s |
| Kokoro TTS and `--voice` | the user narrates; no TTS model download (D298); scratch is macOS `say` only |
| `npx` | HyperFrames is pinned and called by path |
| Tone presets | one note only: we use `polished` |
| Beat-locking text to music cues | the voice leads; timing follows speech |
| Audio-reactive extraction | competes with gauges and labels; adds a Python step for no gain |
| SFX layer in the demo cut | recorded shots carry their own sound |
| Invented copy, hooks and angles | every line comes from `script-v6.md` under `docs/headline.md` |
| The generic "inspect the codebase or website" step | our inspect step reads the script, shots and figure files |
| `brag-plan.md` and `composition-brief.md` | `shots.json` is both |
| Telemetry, `feedback`, `publish` and other remote commands | local only; nothing leaves the machine |

Kept: the poster baked as frame 0.
