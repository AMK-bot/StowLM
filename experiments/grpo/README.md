# GRPO infrastructure (task 6.4)

CPU code and mocks are implemented. **Glimmer GPU smoke is unverified and has
not been run.** The $100 Modal credit remains pending. No Spark calls are used.

GRPO samples a group of plans for each prompt and compares each reward to that
group's mean and standard deviation. Better than average plans receive positive
advantages for the policy update; no separate value model is needed.
[Shao et al., DeepSeekMath §4](https://arxiv.org/abs/2402.03300) describes GRPO.

## Offline rehearsal

```sh
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run pytest -q tests/llm/test_rl*
UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m llm.rl.dry_run --steps 1 --checkpoint experiments/grpo/dry-checkpoint.json
```

The fake scalar policy samples through the real `MaskedDecoder`, replays
`allowed_sets` to recompute masked log-probabilities, normalizes each prompt
group, updates its scalar, and atomically checkpoints every step. Repeating the
command resumes; `--steps 2` extends the same run. Tests also exercise a nonzero
update using an explicitly injected fake scorer. The default scorer is real:
the synthetic fixture has no hull, so its plans fail the full gate and the
policy correctly gets zero advantages when every reward is the same.
This rehearsal uses an ASCII tokenizer and is not a base-model smoke result.

## Deferred one-step base-model smoke

After the credit lands and the leader authorizes GPU spend, prepare the caches
as below, populate the `stowlm-physics-cache` Modal Volume with the cache tree,
and prepare one production-physics prompt locally. C2-on preparation must wait for task 3.7's current-policy qualification; do not bypass its stale-policy guard:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/grpo/prepare_smoke.py --seed 0 --output experiments/grpo/smoke-instance.json
```

This preparation is not part of the fast tests and has not been executed here;
admit its CPU/cache work separately under the fleet compute policy. It uses the
existing synthetic instance generator without weakening any feasibility check.
If the seed has an initial mask dead end, select another seed. Do not use the
hull-less schema fixtures as the funded smoke input.

The exact Modal launch command, from the repository root, is:

```sh
MODAL_CREDIT_READY=1 uv run --with modal python -m llm.rl.modal_grpo --instance experiments/grpo/smoke-instance.json --run-id base-smoke-1 --cap 3 --overhead-usd 0.50 --timeout-s 600 --steps 1 --group-size 2 --max-tokens 256
```

It constructs and runs a Modal app programmatically. **Do not execute it while
credit is pending.** Source is copied into the image with the current full Git
commit identifier plus a content digest (including current working-tree edits),
and the checker/mask/component versions and CUDA stack must match before models
load. This supports the shared uncommitted workspace while still freezing the
exact uploaded source bytes for a run.

Resources: two H100 GPUs, 8 vCPUs (4 physical cores), 128 GiB RAM. At the prices
recorded in `docs/compute.md`, the conservative estimate is
`1.5 × (2 × 0.001097 + 4 × 0.0000131 + 128 × 0.00000222)` =
**$0.00379584/s**, or **$2.28 for the 600-second timeout**. `--cap 3` reserves
headroom; unfinished work stops at the configured deadline. This compute estimate
excludes persistent Volume storage (weights alone about $0.17/day), egress, and
any separate cache-preparation job. The command explicitly reserves $0.50 of its
$3 total for those lifecycle/storage/egress costs; the operator must estimate
all retention/build/transfer costs before choosing that headroom. This is an
estimated admission cap, not a provider-enforced total-dollar ceiling. GPU throughput and startup time remain
unmeasured; a 600-second smoke may time out, leaving the previous complete
checkpoint available.

The smoke verifies: the bf16 Glimmer base loads on both GPUs; vLLM V1 applies
the feasibility mask while sampling; TRL's subclass replays that same mask and
compares masked log-probabilities against vLLM; forced tokens are excluded from
the loss; CPU finalization/checking supplies each reward; a clipped GRPO update
(`clip=0.2`, reference KL `beta=0`) and complete adapter/optimizer/RNG checkpoint
are written to `stowlm-grpo-checkpoints`. It uses the TRL subclass's forward in
an explicit loop, avoiding reliance on TRL's internal rollout batch schema.
This is an infrastructure smoke, not evidence that base-model plans pass the
checker or that one update improves quality.

To extend the run, use the same command with `--steps 2 --resume --prior-spend
<reserved_dollars from the run's budget.json>`, with enough authorized total
`--cap` for the additional full timeout reservation and explicit overhead. The resume validator must
agree with the stored manifest and prompt;
never change source, mask, checker, or reward implementation mid-run. Give a
fresh run a fresh slug. Checkpoints are committed to the Modal Volume every
step; partial directories are not resumable. A completed step is recovered even
if preemption interrupted the latest-pointer update. CPU admission commits the full
invocation reservation before allocating GPUs, and preempted attempts retain
that reservation even if no checkpoint completed. A $3 cap admits one
600-second invocation; it does not admit another such attempt. This conservative
ledger can exhaust the estimated cap before the actual invoice reaches it.

## Reward contract

`evaluate_reward` runs `stowage.planner.finalize`, then the full checker.
A passing reward requires fresh content-bound, complete, clean checker evidence
and canonical `PlannerResult.shippable`, including departure and arrival under
D50. No solver repair is rewarded; multi-call results still fail closed.

Let `c = move_weight * report.count.weighted_nonproductive` and let `-s` be
the higher of the fixed failure and dead-end penalties. Successful rewards are
`-s*c/(s+c)`, strictly above both failure penalties; zero moves receives zero.
The implementation avoids overflow and uses `nextafter(-s, 0)` when floating-point
rounding reaches the boundary. Extreme successful costs can tie at that boundary,
but can never tie or rank below a failure, for any accepted finite configuration.
Both fixed penalties default to **−10,000**. Raw D11 current-plus-projected move
counts remain available as `weighted_nonproductive`; the transformed training
scalar is not a move-count metric. This fixes the adversarial `move_weight=10001`
reward inversion without assuming an unproved instance objective bound.

## CPU checker caches

Prepare caches on the qualified source snapshot using
`uv run python -m stowage.cache_warm --workers 1` **before** launching GPUs.
Cache generation is separate, potentially expensive preparation; it is not run
by these tests or automatically on GPU startup. Preserve the warm-up report and
its content-keyed certificates. Mount a prepopulated Modal Volume over
`/root/stowlm/experiments/cache` and use `/root/stowlm` as the working directory: some physics
tables resolve paths relative to the working directory while others resolve
from `src/`. Both then point to the same mounted cache. Also ship `params/`,
rule data under `src/`, and selected instance JSON with the source snapshot.
After the current physics/linear qualification lands, explicitly prepare a
receipt on the exact source and prompt snapshot (admit this heavy CPU job first):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/grpo/prepare_cache.py --instance experiments/grpo/smoke-instance.json
```

This runs the owner's complete `stowage.cache_warm --workers 1`; any failed or
stale qualification stops receipt creation. It writes `grpo-preflight.json`
binding the source/parameter/component manifest, full prompt and SHA-256/size
inventory of every cache asset. Upload the entire cache tree including that
receipt. CPU Modal admission mounts this tree and rejects any absent, altered,
extra or mismatched asset **before the GPU reservation**. The receipt is an
integrity snapshot of an explicit successful warm-up, not a new physics
certificate. Do not generate it from a historical warm-up summary. Both receipt
and caches must be recreated after source/prompt/cache changes. The
normal checker never rebuilds expensive physics tables; missing data must fail.
The existing warm-up command additionally loads validators from
`experiments/stability/`, so run it in the complete repository, not in an image
containing only `src/` and `params/`.

## Adapter boundary and fallback

Smoke starts from the bf16 Muse Glimmer 30B base on the existing
`stowlm-glimmer-weights` Volume, path `Muse-Glimmer-30B@cec94fcf`.
Rollouts and training must share bf16 precision and synchronized adapters.
For task 10.1, pass `--sft-adapter <converted-PEFT-directory>`; its contents are
copied into the image and hashed in checkpoint configuration. The trainable
adapter loads before optimizer creation. Every rollout, including step zero,
uses merge → export → worker `apply_model(load_weights)` → unmerge. CPU tiny-model
regressions verify a nonzero adapter effect and masked agreement before and
after an effective optimizer update. The real SFT conversion and CUDA agreement
remain unverified. Omitting this option deliberately selects a fresh adapter for
the base-model smoke only.
Convert existing MLX adapters with
`experiments/grpo-venue/convert_adapter.py`; its round trip was exact in the
venue experiment. Re-evaluate the eventual adapter under MLX 4-bit deployment;
the quantized base can shift the distribution. See
`experiments/grpo-venue/README.md` for the measured limits.
`llm.rl.mlx_grpo` defines the fallback interface only and raises
`NotImplementedError`; the GPU owner implements that backend if needed.

## Verification boundaries

Mocks verify orchestration, masking, version mismatches, cap refusal, and
checkpoint/resume without importing CUDA dependencies. They cannot verify
driver compatibility, Glimmer's CUDA numerics, vLLM's live scheduler events,
GPU memory use, throughput, or actual billing. Those require the funded smoke.
The mandatory 1.5 billing margin is an estimate informed by stow-modal's CPU
measurement; a software deadline limits estimated compute, while Modal billing
still includes platform lifecycle/storage charges. The paid CLI fails before ledger or Modal operations unless the exact
`MODAL_CREDIT_READY=1` is deliberately set after credit confirmation. It reserves
the whole invocation cap in `experiments/grpo/spend.jsonl` using task 1.8's shared
authoritative `SpendLedger` locking, atomic summaries and persisted caps. The
Modal allocation and historical manual Modal charges in `docs/compute.md` reduce
remaining capacity across fresh run IDs and resumes. Only the managed Modal
summary row is synchronized; unrelated rows stay unchanged. The run's remote
`budget.json` additionally controls bounded GPU invocations and retries.

Success, failure and cancellation retain the entire project hold until final
provider billing is reconciled offline. Include every GPU, CPU, memory,
lifecycle/build, storage and egress component, even zero-valued categories:

```sh
uv run python -m llm.rl.spend --reservation-id <printed-reservation-id> --charges <final-itemized-charges.json> --billing-final
```

The JSON object must contain `gpu`, `cpu`, `memory`, `lifecycle`, `storage` and
`egress`, each with its final nonnegative USD amount. Use zero only for an
actually absent charge category.

Do not release storage headroom while retention is still accruing charges.
Unknown invoices stay charged; final actual amounts exceeding an estimate must
still be recorded. No software estimate guarantees the provider's total bill.
