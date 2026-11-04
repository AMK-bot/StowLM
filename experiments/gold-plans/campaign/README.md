# 6.1 gold → 7.1 dataset → D31 SFT

The campaign runs CPU solvers against explicit, regenerated frozen v2 instances.
It stores structured `boxslot` plans, checker reports, solver gaps and timing;
tokenization happens only when building the dataset. No GPU or cloud service is
used here. Production publication requires ballast finalization, the full
checker with bound input evidence, and production C2-on mask replay. The checker
decides acceptance. Exact search runs on tiny/small; scalable runs on every rung.

Historical names `tiny-gold@1.0` and `tiny-gold-manifest@1.0` are compatibility
envelopes used by existing SFT admission. The campaign supports all three splits
and all four rungs without changing that admission contract.

## Frozen epoch prerequisites

Follow [campaign-g1/README.md](../../campaign-g1/README.md): leader requalification,
freeze, regeneration, then G1 evidence. Preserve the existing v2 allocation.
Set bytecode suppression **before freezing** because the shared freezer currently
includes every file beneath `src`, including existing `__pycache__` files.

```sh
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export UV_CACHE_DIR=/private/tmp/stowage-w2-gold-uv

export FREEZE=experiments/campaign-g1/freeze-manifest.json
export EPOCH_INSTANCES=/absolute/path/to/regen/release/experiments/instances-eEPOCH
export G1_SUMMARY=/absolute/path/to/leader-approved-g1-summary.json
export GOLD=experiments/gold-plans/campaign/epoch-EPOCH-gold
export DATASET=experiments/sft-data/epoch-EPOCH

uv run --offline --no-sync python experiments/campaign-g1/freeze.py \
  --manifest "$FREEZE" --check
```

`G1_SUMMARY` must be a same-epoch leader decision with `epoch_id`, `g1_met: true`,
a filled `min_yield` scalar or per-rung mapping, measured `solve_yield` for every evaluated rung,
and no diagnostic lineage anywhere inside it. Observed yield must meet each approved floor. The automatic G1 synthesis leaves
`g1_met: false` until the leader completes the rule audit; experiment completion
alone does not approve G1. Tiny-only campaigns retain the documented G1 exception
and may omit `--g1-summary`. Their envelopes and manifests are explicitly
`data_mode: pipeline_test`, `purpose: tiny_g1_exception_pipeline_test`, never production SFT input.
A same-epoch approved G1 decision is required for production promotion. Every physical and publication gate still applies.

The freeze must contain the leader's `required_rules` policy pin. Gold verifies every required
catalogue ID and schema >= 1.9, and carries that exact pin in jobs, envelopes and publications.
The old tiny watcher and status tool require an explicit epoch root; the `instances-v3` default is retired.

The runner invokes `eval.splits_epoch.validate_epoch_manifest` and
`validate_epoch_instance`. The freeze, allocation, instance manifest, selected
input bytes, runner code, tokenizer and budgets enter immutable resume identity.
`--allow-drift` requires `--diagnostic`; such a run never publishes production
gold. Cache writes use private copies under the campaign output directory.

After the driver integrity fixes, use a fresh schema-2 freeze: numerical cache
hashes enter the epoch and are verified before reuse. Both the worker and its
spawned ballast children initialize verified private cache copies and the write
fence. Generated additions stay local to that attempt. Old unpinned freezes and
result-only done markers are intentionally rejected.

Interrupted retries quarantine prior results, state and publication envelopes;
children must echo the current attempt identifier. Nonzero or missing-output
children cannot inherit an older accepted plan. Checkpoints inventory every
returned artifact, including published envelopes, with hashes; publication and
resume verify that inventory. Restore corrupted committed bytes or choose a
fresh output. Diagnostic, tiny-run, allow-drift, unofficial and explicitly
ineligible lineage cannot enter production publication.

The 18:00 re-review fixes use a content-addressed cache-pin file, passing only
its path and digest to workers. G1 decisions and selected manifests are
validated and fingerprinted from one byte read; those validated digests feed
the run provenance directly. Calibration resume restores missing/stale state
from its validated checkpoint before calling the publication writer.

## Measure first, then choose the mix

Begin with small, explicit calibration shards on the **same epoch**. Search caps
are resource limits chosen by the leader, not assumed solve-time measurements.
Use one worker until its measured time and memory support a fleet reservation.
`--count N` selects N materialized instances per split/rung before seeing results.
The runner never substitutes another instance after a failure.

Append `--calibrate-first-shard --calibration-rung tiny` to a gold command to
execute one pending tiny shard, checkpoint and stop. Repeating it measures the
next pending shard; choose each rung in turn. These flags are operational and
can be omitted to continue the identical selection. Selection and solver budget
changes still require a new output directory. In particular, a preliminary
all-rung pilot used to choose a smaller final mix is a separate campaign; its
measurements remain valid for that epoch but its plans are not merged silently.

`summary.json` records selected and completed denominators, pending requests,
published and diagnostic candidate counts, per-rung and ship × generator × port × noise
yields, attempted-versus-published input distributions, per-rung elapsed costs, solver attempts,
timeouts and reasons. Failed attempts belong in the solve-rate denominator and
cost average. Never project medium/full cost from tiny. Generation rejections
belong to the regeneration manifest; gold solve rates are conditional on the
materialized inputs. CPU forecasts here exclude regeneration and cache warming. Input distributions show explicit
container and DG counts, the background witness's arrival B2.3 ratio and arrival GM.
Unavailable audit values are counted as unknown; no value is inferred from solver success.
Gold is conditional on solver success: attempted inputs that solvers fail to publish remain
in every yield denominator and can make the published training sample systematically easier.

Create a measured JSON input for the budget planner. Supply one row per measured
rung; `available_instances` counts eligible **train** inputs in this epoch,
including the measured calibration inputs. A row looks like this schema (values
below are illustrative, not current evidence):

```json
{"label":"EXAMPLE ONLY; replace with epoch and measurement files",
 "measurements":[{"rung":"tiny","attempted":10,"published":2,
 "total_attempt_seconds":50,"prompt_tokens":591,"output_tokens":43,
 "available_instances":1500}]}
```

Use `per_rung.R.attempts`, `accepted`, and `total_core_s` from gold summary;
use `stats.json.rungs.R.splits.train` for admitted prompt/completion mean lengths.
If no plan is admitted, the measured rate is zero and token lengths can be zero.
Do not invent a positive yield or fill unknown rungs with another rung's data.

```sh
uv run --offline --no-sync python experiments/gold-plans/campaign/plan_budget.py \
  --measurements MEASURED.json --target-tokens 12000000 --cores 4 \
  --epochs 1 --token-shares tiny=.10,small=.30,medium=.35,full=.25 \
  --min-count full=300 --json-out BUDGET.json

# Runnable demonstration; ALL rates/times/lengths are synthetic, not evidence:
uv run --offline --no-sync python experiments/gold-plans/campaign/plan_budget.py \
  --example --epochs 2 --cores 4 \
  --token-shares tiny=.10,small=.30,medium=.35,full=.25 --min-count full=300
```

The production planner allocates tokens by training design: tiny at most 10%,
small 30%, medium 35%, and full the remainder, with at least 300 unique full plans.
Unused tiny allocation goes to full. Capacity or unmeasured costs in one rung
cannot be replaced by cheap tiny examples; the result stays `insufficient_capacity`.
Efficiency may order work inside these shares and never changes the mixture.
`--pipeline-test` enables the historical efficiency-only sizing for isolated pipeline
experiments and labels its output non-production.
It prints selected **gold counts**, expected **attempt counts**, already-spent
calibration time, additional CPU time, ideal N-core wall time, and D31 GPU time.
Exit 2 means insufficient estimated capacity. Unknown or zero-yield rungs cannot
silently supply gold.

The planner's additional time assumes calibrated plans are retained in the
same selected campaign. If the final mix needs a fresh output directory, use
the printed **total** CPU time: the pilot inputs will be solved again there.

**APPROXIMATION:** the forecast extrapolates same-epoch empirical means; it is
not a confidence bound or an exact integer optimum. The N-core wall estimate
assumes one CPU per attempt and excludes scheduling overhead. Epochs multiply
training tokens, not unique gold or solver cost. The 200 tok/s GPU rate comes
from [D31](../../../docs/compute.md); actual long-run throughput remains unverified.

## One resumable gold command

Set `ATTEMPTS_*` from the budget's expected attempt counts, rather than its gold
counts. Set solver caps from measured first shards and available wall time.
The variables below deliberately have no guessed production defaults.
Omit a zero-count rung from both `--rungs` and `--rung-count`, or set a positive
coverage minimum when planning the mix. Val/test selection follows `--rungs` too.

```sh
uv run --offline --no-sync python experiments/gold-plans/campaign/gold.py \
  --freeze-manifest "$FREEZE" --instance-root "$EPOCH_INSTANCES" \
  --g1-summary "$G1_SUMMARY" --output "$GOLD" \
  --split train val test --rungs tiny small medium full --workers 1 \
  --rung-count "tiny=${ATTEMPTS_TINY:?set measured attempt count}" \
  --rung-count "small=${ATTEMPTS_SMALL:?set measured attempt count}" \
  --rung-count "medium=${ATTEMPTS_MEDIUM:?set measured attempt count}" \
  --rung-count "full=${ATTEMPTS_FULL:?set measured attempt count}" \
  --scalable-s "tiny=${SCALABLE_TINY_S:?set cap}" \
  --scalable-s "small=${SCALABLE_SMALL_S:?set cap}" \
  --scalable-s "medium=${SCALABLE_MEDIUM_S:?set cap}" \
  --scalable-s "full=${SCALABLE_FULL_S:?set cap}" \
  --exact-s "tiny=${EXACT_TINY_S:?set cap}" \
  --exact-s "small=${EXACT_SMALL_S:?set cap}" \
  --ballast-s "${BALLAST_S:?set cap}" --check-s "${CHECK_S:?set cap}"
```

`--rung-count` changes train attempt counts only. Without `--count`, all available
val/test instances are attempted; their tokens are excluded from the train budget.
Tiny is submitted first. Completed shard markers bind result hashes; every
terminal result, including failure/timeout, resumes without solving again.
Interrupted shards retry. Publications and calibration summaries checkpoint as
shards finish. Re-run the identical command to resume; a changed selection,
budget, epoch or code requires a new output directory. `--shard-count K
--shard-index I` supports fixed disjoint partitions; use separate output paths.
The dataset builder consumes a single publication, so prefer one bounded pool
with `--workers 1..4` for the final dataset campaign.

Any increase above one worker needs fleet admission and its own ledger line.
This order's ceiling is **4 cores / 12 GB**, with no GPU. Worker count is a limit,
not admission to occupy currently reserved CPUs.

## One resumable dataset command

```sh
bash experiments/gold-plans/campaign/dataset.sh \
  --gold-manifest "$GOLD/manifest.json" \
  --instances-root "$EPOCH_INSTANCES" --out "$DATASET"
```

The build independently replays **100% of selected rows** through the production
mask and admits only published, clean, checker-passing gold. Any failed row,
missing split, diagnostic lineage, source drift or zero gold fails the entire
build. `stats.json` reports per-rung/per-split prompt, output and total lengths.
Completed resumes hash-check inputs, current source and outputs. Interrupted
builds retry full admission/replay. See [DATASET.md](DATASET.md) for the exact
resume boundary. A partial `--required-splits train` run is only a pipeline check.

## D31 SFT command — do not run without the GPU reservation

After a complete `production_gold` dataset exists, follow the authoritative
[fix-t8-sft command](../../review/fixes/fix-t8-sft.md). Fix the epoch count before
starting and calculate `ITERS = 4 * ceil(N_train * epochs / 4)` from
`stats.json.splits.train.examples`. The training schedule depends on this value.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m llm.stowlm.train \
  --data-dir "$DATASET" --adapter-dir experiments/sft/stowlm-v1 \
  --rank 8 --scale 20 --num-layers 16 --attn-block 1024 --max-seq-length 18432 \
  --batch-size 1 --grad-accumulation-steps 4 --grad-checkpoint true \
  --learning-rate 5e-5 --lr-schedule cosine --warmup-frac 0.05 --min-lr-frac 0.1 \
  --iters "${ITERS:?derive from fixed epochs and train row count}" --seed 0 \
  --steps-per-report 10 --steps-per-eval 100 --steps-per-save 100 --val-batches 25
```

Re-run identically to resume training. D31 reserves one exclusive GPU, two CPUs,
70 GB RAM; 10–15M processed tokens take about 14–21 GPU-hours plus validation.
This preparation order does **not** run SFT.

## Diagnostic prepared on 28 October

```sh
uv run --offline --no-sync python experiments/gold-plans/campaign/diagnostic.py
```

This creates `diagnostic-1028/freeze.json` and an epoch root named in
`diagnostic-1028/result.json`, using one fresh v2 tiny request. It calls production
generation helpers without claiming central qualification. The D58 gate and all
physics remain active. All output says **diagnostic, not evidence**. The measured
fresh instance passed background acceptance on draw 1: **25.54 s, 0.54 GB peak
RSS**, versus a **60-s / 2-GB / one-core** estimate. No GPU or cloud call.

The exact gold/dataset dry-run commands and final verification results are in
[REPORT.md](REPORT.md). A diagnostic with no admitted plan cannot establish physical downstream
success: the full checker/mask success paths are tested separately with explicitly
synthetic bound evidence, and no synthetic fixture is production gold.

## Gold evidence and rule-coverage yield

The gold driver validates each evidence field against its declared type. A
`PlacementMapEvidence` placement source and a ballast-system source string are
valid domain evidence; neither is artifact `Provenance`. Artifact provenance is
still recursively inspected, including nested serialized evidence. Stub taint,
mixed input versions, and malformed declared evidence refuse publication.
The writer also restores the checker's declared D74 in-port programme and lashing
report subtypes without dropping their fields; unknown extensions still refuse.
Standalone resume commands include the explicit `--instances` epoch root;
historical states without a recorded root cannot invent a resume default.

`manifest.json.rule_binding_yield` and the final campaign summary report these
four observed buckets:

- `crossing_used`: fresh public move-engine D62 observations, with applicable
  status and `migrated_move_engine` source. Switch-off observations are unknown.
- `direct_delivery_present`: an explicit direct-delivery box in the current
  call's load or discharge list, observable even when the solve times out.
- `kg_tight_stacks`: an actual adjacent physical-tier pair in the replayed final
  ship, with gross masses differing by at most 1,000 kg (inclusive).
- `dg_segregation_binding`: an actual co-carried DG pair with a positive public
  IMDG numeric separation obligation. This is an obligation witness, not a
  measured tight distance margin; unsupported conditional SG/X/* cases and
  unobserved fixed-background pairs remain unknown.

Every terminal attempt, including a timeout or failure, stays in the denominator.
Each bucket reports present/absent/unknown attempts, actual published plans,
published unknowns, and yield over present attempts and over all attempts.
Publication counts use the exact admitted envelope's observations, once per spec;
a corrupt/excluded envelope contributes no publication. Missing returned plans
have unknown plan-dependent buckets. These descriptive observations never waive
the full checker, mask, ballast, C2, epoch, or taint admission checks.

## Migration-complete admission (L1 checklist)

Catalogue membership alone cannot approve G1. A production source freeze now
requires the activated semantic contract and the fourteen independent checker/mask
canaries. It records the prompt, checker, mask, exact/scalable solver versions
and the catalogue hash from the **loaded catalogue object**. Gold workers stamp
and compare that same component epoch before publication. Missing or old fields
remain unverified; an old checker report is never relabelled with new versions.

Regeneration and plan evidence follow the source freeze. The post-freeze journal
pins the freeze, regenerated instance manifest and campaign manifest.
G1 admission uses the nonempty 5.4 campaign solver-plan inventory, not stored
gold. Run the local gate producer described in
[the campaign README](../../campaign-g1/README.md#rule-migration-readiness-l1-checklist)
and pass its `approved-summary.json` to `--g1-summary`. The portable gate-signed
capsule verifies execution origin, exact epoch/components and the approved
population. Do not substitute raw checker/mask JSON. Ship the trusted public
`admission-trust.json` alongside the code; never ship `.admission-private/`.

D62 observations must come from the live move engine/mask and bind the exact
input/plan and frozen epoch. The gate derives rung × port × ship counts; unknowns
refuse, and every observed zero requires the leader's explicit acknowledgement.
See [the observation contract](../../campaign-g1/D62_OBSERVATION_CONTRACT.md).
