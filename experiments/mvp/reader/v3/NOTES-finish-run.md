# Reader v3 finish run (task 0.4, phase 1, 1 Nov 2026)

**This is a continuation with a fresh optimizer, not a single unbroken run.** The final adapter is
stopped adapter-v3 (row 1,200 of 4,121, sha256 `887d53c2...`) plus the remaining 2,921 rows.

## Why the first run died

`evaluate.main` calls `check_admission`, which raises when load1 is above 45. That ran in the child of
the iteration-1,200 held-out check, so `subprocess.run(check=True)` killed training. `checks/001200`
holds weights and no `valid-sample.json`, and the log has no check or stop event for iteration 1,200.

## Why continuing is sound

- Row order comes from `random.Random(199).shuffle` over 4,121 indices in `epoch_groups`: stdlib,
  deterministic. Recipe freeze `--check` verified the data and code hashes unchanged before launch.
- The stopped run's last log line is iteration 1,200, update 300, so it ended on a group boundary.
  Saved weights equal `checks/001200/adapters.safetensors`.
- `tail_rows` keeps the original accumulation groups, including the final one-row group.
- Each row is therefore seen once. Cost: Adam's moments restart at row 1,200, because no optimizer
  state was saved. It is not bit-identical to an unbroken epoch.

## What changed

No hashed file changed. `recipe-freeze.json` still verifies. New files: `finish_run.py`,
`test_finish_run.py`, this note. New output folders: `adapter-v3-final/`, `checks-final/`,
`results-final/`. `adapter-v3/` is read only; its hash is checked at start and end.

`finish_run.py` injects into the frozen `train_v2.run_train`:
- the tail rows, plus `sequential_groups` replacing `epoch_groups`;
- `ActiveClock` replacing `time`, so admission waits don't spend the hard stop;
- its own `Checker`: same 80-row sample and plateau policy, new paths, whole-epoch iteration numbers.

## Decisions

- **Hard stop: 14,400 active seconds of this process.** Config says 14400; the `selection` text in
  config, `GPU_HANDOFF.md`, `gpu_run` help and the "three hours" stop strings say 10800. The code uses
  config: `validate_recipe` caps at four hours and the deadline uses `cfg['hard_stop_seconds']`. The
  remaining work is about 11,000 s at the old pace of 24 minutes per 400 rows, too near 10,800, so
  14,400 stands. No raise past config. A stop reason reading "three elapsed hours" is that stale string.
- **Plateau history is seeded** with the stopped run's 0.7625 and 0.8875, so the policy spans the epoch.
- **Added check at iteration 1,200** (the frozen loop's first check). The stopped run never finished
  it. It also tests the waiting path and the weight load before the long stretch.
- **A check that fails for a non-admission reason is logged and training goes on.** The weights are
  saved, so nothing is lost, and the sealed score doesn't depend on it. A wrong decoder still raises.
- **If it crashes:** no silent resume. `run.pid` and `adapter-v3-final/` both refuse a relaunch.
  `checks-final/` holds a folder every 400 rows.

## For the sealed phase

`gpu_run.score_final` and `freeze.py --stage final` hardcode `adapter-v3`, which has no `STOPPED.json`.
They need a wrapper pointing at `adapter-v3-final`. `STOPPED.json` there is restated in whole-epoch
numbers and keeps `adapter_sha256`, so `require_stopped_source` accepts it.

## Launch health (measured, 1 Nov 19:42 EST)

Exact on the 80-row sample: 0.8875 at 1,200 (loaded weights), 0.75 at 1,600. Sample loss rose from about
0.006 to 0.37, then fell to 0.045 by row 1,500. This is the fresh-Adam kick: Adam's first steps move every
weight by about the learning rate. The first run had the same shape from v2 (0.7625 at 400, 0.8875 at 800).
The continuation is expected to recover over its remaining rows, but that is unproven until later checks.
Pace: 0.344 rows/s, a held-out check about 6.4 minutes. ETA about 22:30 EST, about 12,200 of the 14,400
active seconds used. Margin about 37 minutes.

## Selection and sealed chain (1 Nov, about 21:30 EST)

`SELECTION-RULE.md` holds the rule; read it before any score. `finish_chain.sh` runs `swa`, `select_adapter`,
`score_selected`, `record_traces` once PID 31798 exits. Status lines: `results-final/chain-status.jsonl`; log:
`results-final/chain.log`; sealed outcome: `results-final/sealed-summary.json`.

- **`select_adapter.py`, not `select.py`:** a `select.py` here would shadow the stdlib `select` module once
  this folder is on `sys.path`, and pytest puts it there because the folder has no `__init__.py`.
- **Sealed outputs stay in `results/`**, where task 0.4 and `gpu_run` expect them. `results/final-freeze.json`
  binds the selected folder in place of `adapter-v3`. No hashed file changed.
- **Cost:** a check takes about 5.8 minutes per 80 rows under this load, not 3, so about 27 minutes per
  candidate rather than 15, unless load falls. Finish about 00:50 EST.
- **Validation overstates sealed: v2 read 91.25 % on validation and 80.47 % sealed.**

## Mask study (1 Nov, about 22:00 EST)

`MASK-STUDY-RULE.md` fixes the rule. `mask_chain.sh` waits for `finish_chain.sh` to exit, then runs `mask_study.py`, so the
running chain wasn't edited. Rows append to `results-final/mask-study/`, so a rerun resumes. Provenance of stored
readings: `RECORDED-READINGS.md`. `app/v6.py` loads only `real-trace.json`, so any saved block trace still needs wiring.

## Bfloat16 crash and adoption chain (2 Nov, about 01:00 EST)

- **The first mask study measured nothing.** `recorder.py` couldn't read MLX bfloat16 logits, and `run_rows` stored each
  error as a finished row, so 269 adapter and 118 base rows "completed" with no trace. Stopped by PID (13028, 58062, 58063,
  58762, all mine). Stores kept in `results-final/mask-study/bfloat16-crash/`. Recorder fixed. A stored error now reruns,
  5 errors in a row stop the arm, and under 95 % recorded raises. One real trace confirmed the fix before relaunch.
- `adoption_chain.sh` runs: mask study, `adopt decide` (D263), `adopt xray`, intake lease, intake launch. `adopt xray` writes
  `results-final/final-xray.json`. If v2 is adopted its block trace is `real-block-trace-adopted.json`, and the base trace is
  shared. `app/v6.py` loads `real-trace.json`, recorded by this step through the adopted adapter.
- **`private_intake.py` changed for the new validator.** `text_provenance` authenticates only a whole JSON line with the
  row's id, so the base-container line now carries the id. Tests pass. Free-text fragments can't authenticate, so every
  model field becomes a planner question: fail-closed, not automatic. Frozen data and gate unaffected.

## D266 successor (2 Nov, about 02:10 EST)

`results-final/final-adoption-d266.json` succeeds `final-adoption.json`, which stays as written and is hashed inside the new file.
Queued work moved to `experiments/mvp/pipeline/reader_final/`. Traces recorded through v2 move to
`xray/fixtures/superseded-v2/`, because `app/v6.py` loads `real-trace.json` and its adapter hash must be the adopted one.
