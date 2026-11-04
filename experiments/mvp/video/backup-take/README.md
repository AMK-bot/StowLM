# Backup take of the StowLM 3-minute video (task 10.2)

Recorded 30 Oct 2026, 16:02–16:16 EDT. **Silent:** tab capture only, no voice-over. A backup, not the final take.

| Item | Value |
|---|---|
| Variant | Worse (D163, D164) |
| Adapter | StowLM 8.2, `experiments/mvp/sft/stowlm-v2/adapter` |
| Source | Current tree. The post-freeze bundle was rolled back, so there is no `INSTALLED.json` (D171) |
| Checker epoch | `78971ce6a196fb647e328c74fbb1f4b0ab2c401c09bb0ee68ee58d0aec0ca9e4` |
| Physics epoch | `b2e90d4b8cfc665bd52c723ec814459558d74dfe4ab64eccb0281d85d99737f5` |
| Fill sources | `experiments/mvp/video/fill.json`, `experiments/mvp/ui/fill-sources.md`; `app/tests/test_fill_swap.py` recomputes them |
| Instruction card | `experiments/mvp/runs/stowlm-instruction-binding/` (D170), re-gated on every load |
| Duration | 174 s configured; director 174.01 s; `whole-take.webm` 174.825 s in Chrome (0.3 s head, 0.4 s tail) |
| Director errors | 0 (whole take and every shot) |
| Size | 1920×1080, 30 fps requested, VP9, 82.8 MB |
| Tests before the take | `app/tests`: 751 collected, 0 failures, 0 errors, 1 skipped |

## Files

- `whole-take.webm`: the continuous take; `whole-take.json` its record.
- `shot-01.webm` … `shot-10.webm`: separate passes, each with about 0.3 s black before the dip and 0.4 s after.
- `shot-01.png` … `shot-10.png`: frozen stills at each shot's `still_t`. `shot-07.png` is the ladder.
- `shot-07-instruction.png` (t = 24 s), `shot-08-strip.png` (t = 5 s): the instruction card and the three-call strip.
- `timing.json`: per-shot durations, wall times, sizes.

## Command

Server: `PYTHONPATH=.:src nice uv run --offline --no-sync python -m app --video-showcase --no-discovery --port 8851`, with the
timelines precomputed (RECORDING 2a) and every plan warmed.

```sh
PYTHONPATH=. nice uv run --offline --no-sync --with playwright python experiments/mvp/ui/dev/record_shots.py http://127.0.0.1:8851 \
    --final --whole --out experiments/mvp/video/backup-take
```

## Known limits

- **No sound.** The voice-over goes on top. Shot times follow the script's table.
- **Spark beat, shot 7 (12.8–15.6 s):** the bound Spark panel and its caption are on screen, gated on a bound explanation, but `SWITCH:SPARK-EXPLAIN`
  in `script.md` and `script.worse.md` still reads off. If the voice-over follows the script as it stands, that beat is silent. The script's own note for
  switching on: add the sentence and cut "Loosen a rule, and it refuses."
- **Machine load:** recorded at load 40 on a shared machine. The clock held, but headless tab capture can drop frames. The sampled frames were clean; not
  every frame was inspected.
- **webm header:** MediaRecorder writes no duration, so a player may show the length only after the first seek. An editor can remux it.
- **Slices only:** every plan shown is a 12–22-box slice of a simulated port. Shot 7's replan is one pre-registered call (n = 1), never a rate.
- **`{{REPO_URL}}`:** no link exists yet, so the end card drops that segment.
- **`SPOT:RECIPE-C`:** not filled. 9.1b hadn't reported at the take.
