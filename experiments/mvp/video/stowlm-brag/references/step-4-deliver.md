# Step 4: Deliver

Two builds, both local. Neither uploads or posts anything.

## Demo cut: `experiments/mvp/video/demo-cut/build.sh`

| Step | What it does |
|---|---|
| Voice | per shot, `record-kit/vo/shot-NN.wav` if present; otherwise a scratch track from macOS `say` (`scratch-NN.aiff`, converted to wav) |
| Fit | measures each voice file; slows the shot (rate down to 0.8) when the line is longer than the shot; total capped at 180 s |
| Compose | `tools/build_comp.py`, then `hyperframes check` and `tools/check_honesty.py` |
| Render | `hyperframes render` to `demo-final.mp4`, or `demo-draft.mp4` when any scratch track is used |
| Watermark | "SCRATCH" burned in whenever any scratch track is used |
| Poster | best settled frame to a `.jpg`, baked into frame 0 with ffmpeg (brag's method: overlay on frame 0 only, audio copied, same frame count) |
| Subtitles | `.srt` from the voice lines and their measured timings |
| Credits check | music credit, three Meta models, engine credit, build window present |
| Share copy | `share-copy.txt`, checked like the video |

Draft and final:

- **Draft** (any scratch): for timing and review only. Never posted, never submitted, never called final.
- **Final**: every shot has `vo/shot-NN.wav`, no `scratch-*` file is referenced, no watermark.

Rendering and the poster need ffmpeg, which isn't installed yet (leader decision pending). Without it, stop after `check` and `check_honesty.py` and report that.

## Launch cut: `experiments/mvp/video/launch-cut/build.sh`

About 27 s: the `launch` segments from `shots.json`, music (vol-11), big captions, then the 5 s end card. No voice. Writes the video and `share-copy.txt`.

## Poster

Pick a settled frame that stands on its own: the shot 7 replan with its "Stored plan, computed on 2 Nov 2026, rechecked now" chip showing, or the shot 11 card with its whole label. Chips and the whole-film label must be in the frame; a poster is also something people read.

## Share copy

One to three sentences, `polished` tone, postable as-is. Rules:

- Only claims already on screen, in headline.md's wording.
- If it quotes 24 of 24, the 3.1 label and the short partial-run form go with it.
- Always ends with "Music: Sascha Ende, ende.app (CC BY 4.0)".
- Names the Meta models for what they do if it names them at all.
- `check_honesty.py` runs on it like on the video.

Example shape (fill from `shots.json`, don't copy blindly):

```
StowLM: messy voyage papers and a planner's own words in, a checked plan and a signed handover out. Our fine-tuned Muse Glimmer 30B reads the instructions on the laptop; Muse Spark reads the paper; search software plans and a full checker decides. Synthetic ships and cargo, simulated port.
Music: Sascha Ende, ende.app (CC BY 4.0)
```

## Never

- `hyperframes publish`, `cloud`, `lambda`, `auth`, `feedback`, `skills`, `tts`, `transcribe`, `models install`.
- Uploading, posting or sending the video anywhere. The user posts.
- A file named final that contains scratch audio.

## Tell the leader

Where the video, poster, `.srt` and share copy are; draft or final; which shots used scratch; the total length; any shot slowed below 1.0; whether both checks passed.
