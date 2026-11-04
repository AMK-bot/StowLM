# Step 1: Inspect

Replaces brag's "read the project's website" step. Our material is already decided: a script and twelve recorded shots. Inspection checks that they agree with each other and with the claims contract.

## Read, in this order

| # | File | Take from it |
|---|---|---|
| 1 | `experiments/mvp/video/script-v6.md` | the 12 shots, voice lines, on-screen text, trim order, never-trim lines, branches, recording notes |
| 2 | `docs/headline.md` | Labels table, "Don't say", the 3.1 wording, the Meta model count, reader figures |
| 3 | `experiments/mvp/video/fill-v6.json` | values for every `{{name}}` in the script |
| 4 | `experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json` | the 3.1 held-out figures (24 of 24) |
| 5 | `experiments/mvp/ui/fixtures/film-figures.json` | film figures, such as the Laem Chabang projection |
| 6 | `experiments/mvp/ui/video-draft/v6/timing.json` | measured duration and capture size per shot, director errors |
| 7 | `experiments/mvp/ui/video-draft/v6/shot-NN-mid.png` | a mid frame per shot: what is actually on screen |
| 8 | `<kit>/shots.json` | the build's copy of all of the above |

Look at the frames, don't assume them. Shots 4, 5 and 9 are being re-recorded (p22); re-read `timing.json` and their frames whenever they change, and never copy shot files into the kit.

## Skip

`.env` and any key, token or credential file; `brag-vendor/` scripts (read as text only); sealed-run folders beyond the named figure files; anything with real company data (there should be none).

## Gate questions

Answer each in a line before planning. A "no" stops the build until it is fixed or recorded as a leader decision.

1. Does every `shot-NN.webm` named in `shots.json` exist, with `director_errors` empty in `timing.json`?
2. Does each shot's `dur` in `shots.json` match `duration_s` in `timing.json`? Does the total plus credits stay within 180 s?
3. Does every voice line in `shots.json` equal the script's line with fills from `fill-v6.json`? Any `[[IF …]]` block in a voice line is dropped, never voiced.
4. Are shots 4, 5 and 9 the current takes? If shot 9's take has no real-tools beat, has "On real tools, it negotiates two checked alternatives." been dropped (D293)?
5. Does each mid frame show the labels its chips claim (for example "Recorded answer", "Stored plan, computed on 2 Nov 2026, rechecked now")? A label burned into the take and a chip must not contradict.
6. Does every number in a voice line, chip, lower third, credit or launch caption appear in that block's `numbers[]` with a source file that holds it?
7. Is every on-screen label in headline.md's Labels table, or the 3.1 label?
8. Are all three Meta models named for what each does, and is no plan credited to StowLM?
9. Is anything on screen personal, company or vessel data the user hasn't chosen to show? (Notifications, terminal history, file paths with names.)
10. Do the music files and the radio clip (`radio_clip.path`) exist?
11. Which `record-kit/vo/shot-NN.wav` and `record-kit/cam/*.mov` exist? That decides draft or final in step 4.

Write the answers in the build log or the session's notes, not in a new file in the kit.
