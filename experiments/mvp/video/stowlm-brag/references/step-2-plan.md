# Step 2: Plan

Brag writes a fresh `brag-plan.md` with an invented angle. We don't: the plan is `shots.json`, and its content comes from `script-v6.md`. Planning means keeping `shots.json` true to the script, timed, and sourced.

## Shot list (from script-v6.md draft 5)

| # | Shot | s | Lower third (Meta model or engine) | Chips |
|---|---|---:|---|---|
| 1 | Hook | 9 | none | Recorded · Synthetic document · Separate demos |
| 2 | The stake | 12 | none (face cam intro may play here) | Recorded |
| 3 | Mess in | 11 | Muse Spark, cloud | Recorded answer · Synthetic document · Cloud call |
| 4 | Bad radio line | 10 | Muse Voice Transcribe, cloud | Recorded answer · Generated voice · Cloud call |
| 5 | The reader | 18 | StowLM Reader, Muse Glimmer 30B fine-tuned by us, on device | Recorded answer · On this laptop, no cloud call |
| 6 | X-ray and confirm | 16 | StowLM Reader, traced token by token | Recorded answer · 200 validation sentences, not the locked test |
| 7 | The plan changes | 14 | Frontier Engine 0.13.0, search software | Stored plan, computed on 2 Nov 2026, rechecked now · Half-ship section |
| 8 | Drag a box | 13 | none | Recorded verdict · Demo cargo declaration changed for this challenge; no vessel or rule limit tightened |
| 9 | The copilot | 21 | Muse Spark, chooses the tools | Recorded answer · Real tools, two scripted planner replies |
| 10 | Receipt | 14 | none | Replay of a measured run · Our own signature, not a certificate |
| 11 | Held-out test | 9 | none | Held-out maps · the 3.1 label (see honesty.md) |
| 12 | Forward pilot | 24 | none (face cam outro over credits) | Projection |
| | Credits | 6 | | |

171 s of shots plus 6 s of credits is 177 s, under the 180 s cap. `shots.json` is authoritative if this table drifts.

## Per shot, `shots.json` must carry

| Field | Rule |
|---|---|
| `voice` | the script's line, fills applied, no `[[IF]]` text |
| `voice_at_s` | only where the voice must wait (shot 4 waits for the radio clip, about 3.7 s) |
| `chips` | labels from headline.md's Labels table, or the 3.1 label; the shot's take must not contradict them |
| `lower_third` | `name` is a Meta model or the engine; `role` says what it does in this shot |
| `numbers[]` | every number shown or spoken: `text`, `means`, `source` (repo path) |
| `delivery` | one line of direction for the user; mark never-trim lines |
| `trim_note` | the script's trim order, where it applies |
| `launch` | optional: `media_start`, `dur`, `caption` for the launch cut |

## Timing

- Voice at about 2.4 words a second. A line longer than `dur × 2.4` words won't fit at normal pace.
- When a recorded line runs long, `demo-cut/build.sh` slows the shot (playback rate down to 0.8). Below 0.8, trim instead, in the script's trim order: shot 7's "It replans from scratch; keeping changes small isn't built yet.", then shot 9's small-section sentence, then shot 2's "heavy low, dangerous cargo apart, the ship stable".
- Never trim: shot 5's "It missed two stricter gates", shot 6's "So a person confirms", shot 7's stored label, shot 11's limits, shot 3's "most boxes stop for a person", shot 12's "That's a projection, not a result".
- The total, including credits and any face cam, stays at or under 180 s.
- Readability floor from brag still applies to captions and chips: about 0.8 s settled for a short label, about 0.3 s a word for a sentence. Shot 11's card holds still long enough to read every limit.

## Music and ducking

Planned in `shots.json` `music`: bed at 0.32, ducked to 0.09 under the voice. Details in [audio.md](audio.md).

## Launch cut plan

Segments come from the `launch` blocks on shots 1, 4, 5, 7, 8 and 10 plus the end card in `launch.end_card`, about 22 s of footage and a 5 s end card. Captions are written in the shot's block, sourced like any other text, and say nothing the demo cut doesn't. The end card carries the 3.1 label in full and the music credit.

## Gate

- [ ] Every voice line matches the script with fills.
- [ ] Every number has a `numbers[]` entry with a real source file.
- [ ] Total at most 180 s; launch cut 20-30 s.
- [ ] Chips match headline.md's Labels table; shot 11 and the launch end card carry the 3.1 label and the short partial-run form.
- [ ] The three Meta models each have a lower third or credit line saying what they do.
