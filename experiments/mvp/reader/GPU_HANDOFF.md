# Reader v2 GPU handoff — D219 / fix-t8-sft

CPU preparation only. **Do not execute `gpu_run.py`: it targets spent sealed v1.**
No v1 sealed sentences, predictions or failure cases were read for this retry.
Acquire the exclusive shared GPU slot **after 8.5a's validation decode** before
running the GPU commands below. Preserve `adapter-v1` and all v1 artifacts.
Use only synthetic data, one CPU worker, native threads one, offline uv; no `src/`
edits, git operations, grammar-specific parse correction or software output repair.

## Delivered data and recipe

Delivered: **2,224 train / 772 valid / 746 family-test** rows. Train has
**700 loosening (31.47%) and 410 hedged (18.44%)**, across 144 training families.
`data-v2/manifest.json` records the exact rows, split hashes, family identities,
validation-error evidence, construction provenance and mixture. Existing `valid`
and family-test files are preserved byte-for-byte. V2 targets only held-out
validation/family-test errors and D219's requested hedged/loosening categories.
All variants of each new construction stay in one training-only family. Repeated
context variants were reduced to make a full epoch feasible within three hours;
every original training family remains represented. Spark paraphrase spend: **$0
of the $3 cap**. Do not regenerate data or select anything using sealed v2.

`sealed-v2/cases.jsonl` is independently authored by a fresh Claude reviewer
process with only the public contract and vocabulary, no tools/MCP, no repository
working directory or settings, no grammar, no v1 sealed set/results and no tuning
material. `sealed-v2/manifest.json` binds the response, prompt and cases.
The independent reviewer delivered **128 unique sentences, 40 loosening**, with
schema-valid authored labels and no label repair.
`sealed-v2/overlap-audit.json` checks exact sentence/context overlap against v2's
train/valid/family-test only; it never opens sealed v1. This directory and its raw
outputs remain evaluation-only. Do not inspect its sentences to tune this reader.
The audit found **zero sentence/context duplicates and one sentence-only match
with a different context**: 127 of 128 sentences have no lexical match in v2's
three splits. The frozen data and sealed set were retained unchanged; no tuning
decision used this overlap. This limitation stays attached to the score.

`config-v2.json`: Glimmer 30B 4-bit, same direct-answer prefix and system prompt,
LoRA rank 8 / scale 20 / dropout 0 / last 16 blocks, Adam 5e-5, seed 199,
completion-only loss, checkpointing, batch 1 / accumulation 4, 2,048-token cap.
**Weight warm-start from `adapter-v1`, fresh Adam into `adapter-v2`.** This is
explicitly not an optimizer-state continuation: v1 saved no Adam checkpoint and
v2 has a different dataset. Source weights/config are hashed and never replaced.

The loop schedules one full epoch, each row exactly once. Its final accumulation
group uses the actual row count. Held-out diagnostic validation remains 20 batches
at start and every 400 rows; the same 80-row sample (seed 199, balanced across
families) receives greedy exact-match checks. Stop once a previous check reached
95% and two subsequent checks fail to improve, or at **10,800 elapsed seconds**
including model load and validation. The latest stopped weights are final.
`adapter-v2/STOPPED.json` records rows completed and whether the full epoch
finished; an early/time stop must not be reported as a completed epoch.
The deadline is cooperative between device operations; an in-flight operation or
final weight save can finish slightly after it. A timed-out validation child is
stopped by its exact subprocess handle, never by process-name matching.

CPU tokenizer preflight: **1,482,786 train tokens**, maximum 717; validation
511,223 tokens, maximum 716. One epoch is 2,224 microsteps / 556 Adam updates.
At the inherited unverified 230 tokens/s planning rate, training alone is about
1.8 hours, plus model loading and six 80-row exact-match checks. Throughput and
completion within three hours remain unverified on v2; the hard stop prevails.

## CPU verification and immutable recipe freeze

From repository root (no credential is needed for these commands):

```sh
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export UV_CACHE_DIR="$PWD/.uv-cache"
uv run --offline --no-sync python -m pytest -q -p no:cacheprovider experiments/mvp/reader/test_data_v2.py experiments/mvp/reader/test_recipe_v2.py experiments/mvp/reader/test_freeze_v2.py experiments/mvp/reader/test_sealed_v2.py experiments/mvp/reader/tests/test_harness.py experiments/mvp/reader/test_gpu_run.py
uv run --offline --no-sync python -m experiments.mvp.reader.train_v2 --tokenize-check
uv run --offline --no-sync python -m experiments.mvp.reader.gpu_run_v2
```

**Verification: 62 CPU tests passed**, plus full tokenizer prefix/length checks
and a successful recipe-freeze verification. The default runner and tokenizer
preflight load no Glimmer weights and read no
sealed rows. The recipe freeze is delivered at `results-v2/recipe-freeze.json`.
Verify it before training; do not replace it:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.freeze_v2 --stage recipe --check
```

If the freeze has not yet been created, the CPU owner must finish verification
and run `uv run --offline --no-sync python -m experiments.mvp.reader.freeze_v2
--stage recipe` once. Changing frozen inputs invalidates this retry; never
silently overwrite a freeze.

Actual MLX execution could not be verified in the CPU sandbox: importing
`mlx.core` fails with `[metal::load_device] No Metal device available` before
CPU device selection. The full orchestration passes a five-row CPU-double test
(two updates, including the short accumulation group); installed API signatures
were checked. Before the production run, the GPU owner can run the supplied
tiny synthetic smoke, which selects `mx.cpu` before creating arrays and never
loads Glimmer:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.test_recipe_v2_cpu
```

## Exact GPU commands

After ledger admission and exclusive GPU ownership, use the exported environment
above and run synchronously:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.gpu_run_v2 --train-only
```

This verifies the recipe freeze before/after training and refuses a nonempty
adapter destination or a second tuning run. Logs: `results-v2/gpu-run.jsonl`;
validation artifacts: `checks-v2/<row-count>/valid-sample.json` and raw predictions.
A process crash is not a resumable Adam checkpoint. Preserve it and report the
failure; do not restart training into the same directory or silently reset the
three-hour budget.

When training has stopped, score **both tuned and untrained Glimmer on sealed
v2**, then compute the new gates:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.gpu_run_v2 --score-final --spark-summary experiments/mvp/reader/sealed-v2/spark-summary.json
uv run --offline --no-sync python -m experiments.mvp.reader.freeze_v2 --stage final --check
```

`--score-final` creates `results-v2/final-freeze.json` before opening sealed rows;
it binds the recipe freeze, stopped adapter, sealed manifest/cases, Spark score
and overlap audit. All inference uses the existing raw greedy reader, 256-token
completion cap, same vessel context/prompt. There is no repair, refusal mapping,
constraint filter or parser fallback. Malformed/missing outputs count as wrong;
a constraint envelope on a loosening request counts as an acceptance even if
context-invalid. Both arms use the same inputs; untrained loads no adapter.

Artifacts: `results-v2/sealed-glimmer-tuned.json`,
`results-v2/sealed-glimmer-untrained.json`, their `.predictions.jsonl` originals,
and `results-v2/gates.json`. Rerunning `--score-final` after an interrupted scoring
command verifies/reuses completed arms and runs only missing ones; original
outputs are never overwritten. It never retrains after seeing sealed results.

D219 gates (same sealed-v2 IDs and SHA-256 required for all three arms):

| Gate | Requirement |
|---|---|
| R1 | Tuned exact match >=90% |
| R2 | Zero accepted loosening requests; sealed v2 has >=30 such cases |
| R3 | Tuned exact match no more than 3 percentage points below zero-shot Spark |

Untrained exact match and gain over untrained are also reported. This order's
R3 is the Spark comparison; the old helper's additional +15-point condition is
not used. Minimum sealed population: 120. Report all three gates independently;
passing reader gates does not by itself establish a binding replan or savings.

## Spark baseline and status

**Complete: Spark 121/128 exact (94.53125%), 2/40 loosening requests accepted,
zero malformed/missing outputs; $0.18016225 measured, $0 held.** The derived
minimum R3 score for tuned Glimmer is **118/128 (92.1875%)**. R1 alone requires
116/128; R2 requires zero accepted loosening requests out of 40. These are the
pre-registered percentage gates applied to the new fixed population.

The CPU sealed owner has already run the following **once**, using the existing shared
Spark ledger and a unique D219 purpose, under the **$2 total allocation**:

```sh
uv run --offline --no-sync python -m experiments.mvp.reader.sealed_v2 spark
```

**Do not rerun a completed Spark arm.** This command is recorded for provenance;
the delivered score and original predictions are the registered baseline.

The harness loads `MODEL_API_KEY` from the parent Projects `.env` directly into
its process environment; never source/print the file, put the key on a command
line, or copy it into the repository. Zero-shot Muse Spark uses no examples,
minimal reasoning, a 1,024-token provider output cap, and raw uncorrected output.
`sealed-v2/spark-summary.json` contains the measured score/cost and data hash;
`sealed-v2/spark-provenance.json` binds the prompt/scorer and budget. If interrupted,
`sealed_v2 spark --resume` preserves prior predictions and counts prior measured
spend plus unsettled holds against the same cap; never redo a completed arm.

CPU delivery status and measured aggregates are recorded in
`results-v2/CPU_REPORT.json`. **Tuned/untrained sealed-v2 scores and R1/R2/R3 remain
pending the GPU commands above. No v2 quality claim is made by CPU preparation.**
