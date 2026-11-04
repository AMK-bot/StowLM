# D172 CPU teacher completion

**Complete 2026-10-30 18:28 EDT:** `../best-d172/` published with 1,446 maps (733 small + 713 tiny), exactly 2 authorized exclusions, 1,127 gold and 319 two-step winners. Fresh blocking audit 30/30; 65 gate tests passed; post-audit change proof passed. Ledger released. See `summary.md` and `COMPLETE.json`.

Launched 2026-10-30 ~15:17 EDT; estimate 60–100 min (16:20–17:00 EDT).
Supervisor PID `62202`, initial workers `62204`–`62208` (5 workers, native threads limited to one).
Initial launch UUID `bee838fe-3df4-40c9-a145-0061feef8f3c`.
Manifest UUID `4274956d-da1f-41ca-97e9-03b4db0bb908`; expected outcome epoch `a8606f863639bb6b`.
No GPU, model loading, dataset construction, training, or edits to src, best-d151, or stowlm-v3.

Inventory: 775 completed passing small gold, minus exactly 40 held-out IDs = 735 eligible
(feeder_500 205, feeder_800 278, feeder_1700 252). Includes 26 not in maps.json, finished as late as 10:28 EDT.
607 failed and 3 unfinished cases excluded. Full list and metadata/hashes: `inventory/eligible-small.json`.

196 full two-step vs gold comparisons: 195 maps without rows plus D151's previously timed-out map.
The existing 1,252 D151 winners use verified evidence under the identical current outcome/checker epoch.
`d172_reuse.py` checks original admission/change proof, reselects the original winner, and binds the original
row, candidate, plan, report, mask, cost and current input/gold hashes. It labels reuse explicitly;
it does not claim fresh re-verification. This avoids unnecessary fresh checker startup on every retained row.
Two fresh unmodified-path smoke checks (one tiny, one small) agreed with the original costs and passed mask replay.
Assumption: the 467 D151 gold_only winners count as existing teacher rows, so preserve D151's deliberate gold choice;
they do not receive new two-step comparisons. Original exact union: 1,448 maps (735 small, 713 tiny). With the two leader-authorized timeout exclusions, expected export is 1,446 (733 small, 713 tiny).

`d172_supervise.py` launches existing `launch.py`/`run_teacher.py` detached with an isolated run directory,
retries inadmissible operational outcomes up to three launches, and calls `d172_finish.py` after exact receipts.
The finisher stages a new `../best-d172/`, requires only the two leader-authorized finisher-timeout exclusions, a source/epoch change proof, and a blocking
30-winner full-checker/mask/120-second-finisher audit. It publishes by one rename only on success.
Original D151 source/cost/provenance is retained; new comparisons keep gold on ties.

Progress: `supervisor.log`, `shard-*.log`, `rows/`, `receipts/<launch UUID>/`.
Success: `COMPLETE.json`, `summary.md`, `audit-sample.json`, `../best-d172/summary.md`.
Failure: `FAILED.json`, `excluded.json` if applicable. A failed gate never constitutes completion.
The ledger row is released only after success; inspect failure before releasing it manually.

After all recorded workers have exited, resume under the existing manifest (never replace its epoch):

```sh
.venv/bin/python experiments/mvp/gold/twostep-teacher/launch.py 5 --run-dir experiments/mvp/gold/twostep-teacher/d172
```

After that launch's workers exit and all receipts exist:

```sh
.venv/bin/python experiments/mvp/gold/twostep-teacher/d172_finish.py experiments/mvp/gold/twostep-teacher/d172 --workers 5
```

Manual completion requires releasing only this job's ledger row. Automated supervision handles that on success.
Validation inputs were never opened; val.jsonl was read only for exclusion identities.
Verification before handoff: 57 synthetic gate tests passed; initial worker results checked.

## Leader-authorized finish after three launches

The leader authorized excluding only `train/small/feeder_1700/simulated_yard/seed-031100375` and
`train/small/feeder_500/simulated_yard/seed-030100118`: both exhausted their 120-second two-step finisher budget
after three launches. `manifest.json` retains all 1,448 entries and adds explicit pinned authorization records;
`manifest.before-authorized-exclusions.json` preserves the original. No other gates change.

Corrected detached finish supervisor PID `97409` (current PID in `resume-finish.pid`), 3 CPU audit workers, no GPU; `d172_resume_finish.py` releases the ledger only after success.
Logs: `resume-finish.log`, `finish-authorized-exclusions.log`, `audit-progress.json`. Final counts and exclusions: `summary.md` and `COMPLETE.json`.
Verification: 65 gate tests passed; current-launch receipts still cover exactly 1,448 maps.
