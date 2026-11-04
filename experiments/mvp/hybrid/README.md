# Hybrid frontier development: D205/D206

**Released after skill-check Part 2; four D206-admitted trials are running (controller PID46590).** The previous waiting controller stopped cooperatively with zero starts and is archived at `runs/val-d204-wait-only`. All earlier cohorts are excluded.

The fixed cohort is 40 validation-small maps: all32 entering maps from the logic review forecast, plus the lexicographically first8 of its23 no-entry maps for A/A. Exact IDs, costs, source hashes and deterministic selection rule are in `cohort-selection.json`; the original forecast is copied to `review-starts.jsonl`. Each new manifest declares both groups before any result. There are120 map-interleaved trials, four workers, seed0, unchanged search6s/box capped120s and finisher300s. No decoding, repair, GPU, src/frontier edits or git.

## Prepare and hold

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run --offline --no-sync python experiments/mvp/hybrid/run.py --workers 4
```

The default command seals the runtime, writes the manifest and `ARMED.json`, creates `HOLD`, and exits. It creates no live trial queue. Only after explicit leader release, the same command with `--start-trials` removes HOLD and starts the queue. Do not use that flag before release. Create `STOP` to stop new starts cooperatively; active subprocesses drain. `HOLD` also blocks trial starts. Quarantined/aborted directories cannot resume or contribute results.

Every trial must read the leader's `experiments/fleet/core-state.json`: integer mask with popcount≥8, finite nonnegative load1≤45, source timestamp age≥0 and<120s. A missing, malformed, future or stale reading waits. The full raw reading, path, observation/source times, age, derived core count and load persist per trial. Historical row validation recomputes freshness at admission time. Exactlyfour workers even if cores recover. No sysctl call or supplied-mask override. Estimated120 trials take about3h at four workers after release/admission, plus admission waits and actual finisher overhead; timing is provisional until real rows finish.

Adapters use repeated `--llm-candidates PATH` / `--random-candidates PATH` and a new `--out` under hybrid. Candidate paths can name a root, adapter root, instance directory or JSON file. The declared40-map cohort stays fixed across adapters; actual extra-start selection is reported separately.

## Decisions and reports

`planner.py` imports frontier0.9.0 helpers and preserves all no-extra choices, stable sorting, 2/3+1/3 shares, finishing and fallback order. Stored starts are compacted/ranked/replayed using K1b, cheapestthree admitted, then compete through the same sight and objective gates. No reserved extra search share. Tests compare full baseline decision traces.

Each admitted seed's exact box-slot choices persist. Actual checked final moves determine retention: `|F∩S|/|L|`; uniqueness is `|(F∩S)\N|` against fresh none. H1 requires LLM origin, ≥50% retention and ≥2 unique pairs on≥20%mutual passes, and means presence only. H1m reports qualifying maps saving≥1 and signed savings. Random receives the same analysis. Zero-retention rebuilds never count. All search runs reaching the final stow are recorded, including discarded candidates. Shared stows are displayed asshared; mixed software/extra stows are excluded from exclusive H1/H1m and reported separately. H3 now additionally requires passes≥random's.

`HYBRID.md` orders start-level tables, predeclared A/A noise, signed saved moves, H1/random, then gates. It also includes outlier sensitivity with/without40100025, posthoc checker-picked `min(llm,none)` portfolio, completion/pool counts, paired bootstrap CIs and sign tests. Fresh none is checked against matching historical start/objective iterations (≥70%) and the fixed12.14 mean (+3%limit); failure labels a weakened frontier. All results are development; no test-v3 inputs are opened. See `PROFILE.md` for the bounded iteration-gap investigation.

## Runtime and audit integrity

All executed Python, parameters, geometry and CPU mask tokenizer are copied into a hashed runtime snapshot. Editable/live project imports fail closed; unrelated src changes cannot void a resumed sealed cohort. Original validation inputs and candidate bytes stay pinned and checked. Margin-bank bytes and SHA are unchanged; the copied gold helper relocates absolute pinned resource paths only in scoped snapshot modules. Qualification evidence is individually hash-bound. Numerical cache links are read-only and sparsely mirror around pinned evidence; code cannot import through those links. Workers cannot write to snapshots or shared resources.

Resume and reporting share strict `validation.py`: cohort, input/arm/binding, plan and audit hashes, allthree costs, full fresh-process audit flags, independent admitted-seed and final retained-decision evidence, and exact historical admission. Every passing plan gets the full checker and separate fresh-process move/mask/completion/cost audit, no repair.

All arms use identical outer watchdogs: planner3600s, audit600s, then owned-group TERM5s and KILL5s. Only recorded owned PID/groups are targeted. Denied/unconfirmed cleanup stops the queue; waits staybounded. Normal finisher cleanup remains frontier's implementation.

## Historical PermissionError

The `jobs/*.tmp` PermissionError was deliberately induced to stop the first controller, which predated cooperative STOP, when sandbox signals were denied. Its jobs directory was temporarily0555; all active lanes drained and mode was restored before archival. Current startup explicitly rejects read-only job directories; cooperative stopping never chmods them. The traceback remains forensic evidence.

## Verification

```sh
UV_CACHE_DIR=$PWD/.uv-cache PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run --offline --no-sync pytest -p no:cacheprovider --basetemp=experiments/mvp/hybrid/test-tmp experiments/mvp/hybrid
```

`READINESS.json` records current verification/hold state; `status.py` reads progress. Only a complete validated120-row cohort publishes a final report and DONE. Fleet ledger row stays staged in `ledger-row.md` because writes are restricted to hybrid.
