# C1 evaluation driver (plan 9.1 prep) and the 6.2 base+mask runner (w2-c1-prep, 28 Oct 2026)

`run_c1.py` evaluates StowLM (base Glimmer or an 8.1 adapter) under the 5.1 mask with best-of-N sampling, then runs the
authoritative pipeline (`run_planner(..., allow_repair=True)`):

1. ballast finisher;
2. full D50 checker;
3. 5.7 repair fallback;
4. checker again.

The 6.2 base+mask ablation is the same command without `--adapter`.

| File | What |
|---|---|
| `run_c1.py` | The driver. **Harness mode** (`--instances <epoch root>`) wraps `eval.harness.run_evaluation`: frozen split membership, epoch/regeneration and input-hash validation, weight-content pinning, per-instance resume (changed config refused), `summary.json` + `ROW.md` in the `docs/evaluation.md` format. **Loose mode** (`--loose files`) is diagnostic only: the same pipeline over inputs outside a release, resumable by input hash, never a results row. |
| `pool.py` | Process-pool mask backend: each sample's `MaskedDecoder` lives in a spawned CPU worker; `RemoteDecoder` proxies it, push-based (a worker computes the next allowed set as soon as a token arrives). |
| `pooled_planner.py` | `StowLMPlanner` with the pool plugged in through the `decoder_factory` hook (`--mask-workers W`; 0 = in-thread), plus the all-samples mask-override statistic. |
| `decode_trace.py`, `run_folder.py` | The run-folder contract: `manifest.json` (including the adapter's data mode and taint) and per instance `result.json`, `report.json`, `trace.jsonl`. |
| `paired.py` | Paired comparison on one identical selection (own-plan population, D68). |
| `host_bench.py`, `bench/` | CPU-only mask host benchmark and in-thread/pool equivalence. |
| `model.py`, `schedule-table.md` | Throughput model and the projected C1 schedule. |
| `tests/` | Pool ≡ in-thread decoder, terminal status on the last token, trace, manifest, driver flags, stratification, paired command. |
| `SRC_PATCH.md` | src/ changes C1 needs, with patches and tests. |
| `runs/` | Dry-run folders and logs. |

## Commands

These run after the freeze and regeneration (6.2 before SFT; C1 after it), on the regenerated test root. Reserve the
GPU plus 6 cores first: the parent and 5 mask workers fit the harness's cap of 6 workers × planner workers.

The selections are the leader's decisions (docs/compute.md, 29 Oct). They are built in as `--preset`, which sets
`--limits`, `--stratify`, `--subset` and `--n-samples`; don't pass those flags as well:

| preset | run | selection | k |
|---|---|---|---|
| `c1` | C1, StowLM adapter | all tiny/small/medium (75 each) + a stratified 20 full | best-of-8 |
| `6.2-a` | 6.2 base + mask, part A | all tiny/small + the paired 10 of C1's 20 full | best-of-8 |
| `6.2-b` | 6.2 base + mask, part B | a stratified paired 40 of the 75 medium | best-of-4 |

```
COMMON="--instances <regenerated test epoch root> --split test --temperature 1 --mask-workers 5 --c2 --no-stub"
uv run python experiments/eval-c1/run_c1.py $COMMON --preset 6.2-a --run-dir experiments/eval-c1/runs/6.2-a
uv run python experiments/eval-c1/run_c1.py $COMMON --preset 6.2-b --run-dir experiments/eval-c1/runs/6.2-b
uv run python experiments/eval-c1/run_c1.py $COMMON --preset c1 --adapter experiments/sft/stowlm-v1 \
  --run-dir experiments/eval-c1/runs/c1-stowlm-test
```

- **Why 6.2 is two runs:** one harness run has a single planner, so a single k.
- **Selections:**
  - Ship × generator stratification (`run_c1.stratified_selection`) is deterministic and fixed before planning.
  - The 6.2 subsets re-apply the rule to C1's stratified set (`nested_subset`), so 6.2's 40 medium and 10 full are the same instances as C1's paired rows.
  - Each run writes its keys to `selection.json` and passes them to the harness as `spec_keys=`; the run is labelled partial.
- **Pass@1 beside best-of-k (D76):**
  - Every instance records `pass_at_1`: sample 0 by index, judged by ballast + full checker, with no selection. It reuses the selection's verdict when sample 0 was checked there; otherwise sample 0 gets one extra check.
  - It's written next to the checker-selected best-of-k result in `outcome.json`. The manifest's `outcomes` summary counts both.
  - The manifest's method label states k, e.g. "StowLM (LoRA on 4-bit Muse Glimmer 30B), checker-selected best of 8, T = 1".
- **Time limit:**
  - `--no-stub` defaults to `--time-limit 3600` per instance.
  - A hit is its own outcome, "mask time limit". The manifest counts it apart from `model_failures` (checker fail, dead end, error).
- **Resume:** rerun the identical command.

**Paired comparisons** (never compare ROW.md means across rows). Both methods go through the same harness, selection,
seed and time limit. The headline uses the **own-plan** population (D68), with the shipped population beside it:

```
uv run python experiments/eval-c1/paired.py --first stowlm --adapter experiments/sft/stowlm-v1 --second twostep \
  --instances <test epoch root> --limits tiny=75 small=75 medium=75 full=20 --c2 --no-stub \
  --run-dir experiments/eval-c1/runs/paired-stowlm-vs-twostep
```

The StowLM-vs-6.2 pairs come from the nested selections above: the 40 medium and 10 full rows shared by `c1`, `6.2-a` and `6.2-b`.

The dry run (done here):

```
uv run python experiments/eval-c1/run_c1.py --run-dir experiments/eval-c1/runs/dry-base-tiny-b16-w2 \
  --loose <5 pilot-d58 tiny inputs> --references experiments/solver-diag-1028/pilot-d58/runs \
  --temperature 1 --time-limit 600 --prompt-shim --n-samples 16 --mask-workers 2
```

`--prompt-shim` was a temporary diagnostic flag for the D61 prompt crash. It has been removed since L1 restored the bounds and the prompt became stowlm-prompt@2.2.

## Caveats

- The dry run used **C2 off** (stub: the linear policy epoch was stale during requalification) and the **prompt shim**. Every artifact is tainted, so `shippable` is always False there by design. The checker-pass column reports the checker separately.
- `src/` changed under the run: the catalogue was edited at 18:38 and 18:46. Runs started before about 18:47 dead-ended every sample at depth 2 or hit catalogue ValidationErrors; they were discarded or are labelled. Only `dry-base-tiny-b16-w2` is a coherent snapshot.

## Dry run (base Glimmer 4-bit, best-of-16, T=1, C2 off, prompt shim; DIAGNOSTIC, `dryrun.md`)

**Not a result; do not cite.** These are tainted C2-off, prompt-shim runs on 3 instances, made for timing and tooling only.

Inputs: pilot-d58 tiny instances (4 boxes), which have exact-optimal references.

| instance | mask backend | samples complete/dead/checker-feasible | final checker | objective / exact | decode tok/s | decode s | mask host ms/step | GPU stall | selection s | pipeline s | peak GB |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| random_yard.tiny.110280000 | in-thread | 16/0/15 | pass | 6 / 6 | 67.9 | 9.2 | 0.14 | 0.2 % | 270 | 291 | 16.5 |
| random_yard.tiny.110280000 | pool W=2 | 16/0/16 | pass | 6 / 6 | 54.1 | 11.2 | 21.8* | 1.7 % | 302 | 336 | 16.5 |
| random_yard.tiny.110280001 | pool W=2 | 16/0/16 | pass | 6 / 6 | 61.9 | 9.8 | 1.37 | 0.0 % | 243 | 257 | 16.5 |
| random_yard.tiny.110280002 | pool W=2 | 16/0/16 | pass | 8 / 4 | 110.1 | 3.6 | 15.7 | 9.3 % | 361 | 372 | 16.4 |

\* Includes the workers building the engine on first use.

- **Decode is not the cost.** Best-of-16 selection (ballast + full check of every complete sample, sequentially) takes 83–97 % of the wall time.
- **The pool doesn't help on tiny.** Engine steps are ~2 ms, so IPC wait dominates. The pool's case is small and up, where steps are 77 ms to 1.4 s.
- **Not comparable:** the in-thread row ran under a different catalogue hash, so its 15/16 vs 16/16 is not a backend effect.
- **Shipping:** nothing is shippable, because the C2-off stub taints every artifact.
- **Quality:** objectives equal the exact optimum on 2 of 3 instances; the third is +4 moves. This is base Glimmer with no SFT.

## Projected C1 schedule (`schedule-table.md`, from `model.py`; re-fitted 28 Oct for the harness cap)

The table assumes the official settings: **8 samples, 5 mask workers (the harness cap), rank-order early stop (now in
`planner.py`), `--time-limit 3600`**.

| rung | instances | per instance (min) | rung total (h) | GPU busy | CPU-h |
|---|---:|---:|---:|---:|---:|
| tiny | 75 | 1.0 | 1.3 | 7% | 1.5 |
| small | 75 | 2.4 | 3.0 | 21% | 5.0 |
| medium | 75 | 6.9 | 8.6 | 46% | 22.6 |
| full | 20 | 23.5 | 7.8 | 24% | 28.7 |
| **total** | 245 | | **20.7** | | 58 |
| full if deep prefixes dominate (capped at 60 min) | 20 | 60.0 | 20.0 | | |

- **C1 ≈ 21 GPU-h**, rising to about **33 h** if deep prefixes dominate the full rung. The 60-minute time limit caps each full instance, and a capped instance counts as a failure.
- **6.2 base + mask costs about the same again** on the same selection, which is why it runs before SFT.
- **ASSUMED inputs:**
  - ballast + check cost per candidate for small, medium and full (30/60/120 s), plus the D64 in-port checker (an extra 8/15/25 s; unverified);
  - three checks per instance under early stop;
  - small and medium prompt lengths;
  - tok/s from `docs/compute.md`;
  - mask costs from perf-1028 (that gate was invalidated; timing only).
- **Pilot first:** run 1 full instance and re-fit `c`, `h` and the time limit from its record before admitting the other 19.
