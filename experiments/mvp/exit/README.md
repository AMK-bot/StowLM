# StowLM generations — D110/D111/D117

The loop improves measured outcomes: **selection** keeps a fully checked plan with
fewest D11 weighted moves; **inheritance** trains from that plan; **mutation** adds
sampling, bounded solver polish and varied child settings. Gold is a bounded-time
solver candidate, not an optimum or a ceiling. A valid, cheaper model plan can
replace it. Logs must demonstrate improvement; the design alone proves none.

This adapts [Expert Iteration](https://arxiv.org/abs/1705.08439): alternate searching
for better decisions with training a model to reproduce them. Here search means
sampling, a two-step baseline and local improvement. Related methods are
[ReST](https://arxiv.org/abs/2308.08998), [STaR](https://arxiv.org/abs/2203.14465) and
[population-based training](https://arxiv.org/abs/1711.09846).

```zsh
# After 8.1 completes, releases its GPU reservation, and its keyed smoke passes.
zsh experiments/mvp/exit/run_gen.sh \
  --parent experiments/mvp/sft/stowlm-v1/adapter --out experiments/mvp/exit/deployment \
  --gens 8 --maps 48 --k 8 --population 2 --steps 120 --workers 6 --B 8 \
  --dpo --audit-every 2 --audit-batch-size 4 --no-early-pruning

# CPU-only saved-plan exercise; no model weights.
zsh experiments/mvp/exit/run_gen.sh --cpu-dry-run --tiny 5 --small 5
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest tests/llm/exit -q
```

Defaults are 48 maps (16 tiny,32 small), k=8, two children,120 SFT microsteps,
six verification workers and a .67 replay ratio. `--B/--batch-size`, `--k`,
`--population`, `--maps`, `--steps` and `--target-generation-minutes` are exposed.
`--cached-activations` is reserved but **refuses before GPU admission**: the speed
prototype is not yet integrated or validated here. DAgger and pruning default OFF.

## One generation

1. Reserve train maps once, rotating ships and preferring maps without gold and
   outside the known adapter-training inventory. Labels are **unseen by any
   adapter** only when complete, hash-verified ancestor inventories establish
   that fact; otherwise **new to the loop**. Held-out IDs,seeds,canonical hashes
   and file hashes are excluded for inline records and files alike.
2. Children decode identical maps, sharing k stochastic/greedy samples between
   them. The incumbent parent additionally contributes one greedy sample per map
   after generation0. **D110 elitism costs one extra greedy decode per map**;
   it stays parent on fitness ties or losses. Generation0 has only the supplied
   completed 8.1 adapter and its k samples. Fitness is paired greedy pass count, then
   moves on all-member mutual passes. This training-map fitness is a selection
   heuristic, not evidence of generalisation or method superiority.
   Per-row temperatures put greedy + k-1 sampled rows in one B-sized batch:
   B>=k shares one prompt prefill. Default scalar sampler behavior is unchanged.
3. Pool complete model plans, matching gold, or a .3-second two-step v4.4 baseline
   when no gold exists. Rank cheaply by measured moves. Lazily verify in rank
   order. Polish only the best verified plan for15 seconds; replace it only with
   a strictly cheaper fully verified result. Every winner passes scoped V4
   ballast finishing,the full checker,and production/lookahead/securing replay.
4. Read **all** earlier loop winners and current train gold every generation.
   Seeded sampling balances rung/ship strata across that entire pool; it never
   takes a sorted prefix. Exclude the current maps by path,hash,identity and seed.
   Admit only a bounded selected subset, with .67 replay in the SFT mixture.
   Replay checker epochs hash actual sources; stale/unknown reports are fully
   rechecked into owned caches. Gold files remain untouched.
5. Train children from the fitness-selected parent. ChildA uses SFT; with
   `--dpo`,childB adds preference training. The base model stays resident while
   LoRA arrays swap between decoding,training and audits. Final children and 8.1
   are compared on the same validation maps with9.1's row-d recipe: greedy-first
   best-of-4,T=.7,backtrack32,90s candidate finishing,no solver polish. This final
   comparison is separate from the registered greedy audit curve.

The CPU verifier overlaps subsequent decoding. Training waits for checked targets;
we do not claim simultaneous checking and training of those same targets. Every
phase and actual trained method is logged. Model-alone wins below gold are
`model_own_beat_gold`; solver-assisted wins are `model_plus_lns_beat_gold`.
`model_beat_gold` is retained only as the model-alone compatibility field.

## Audit and GPU handoff

Before baseline decoding,`audit-rule.json` pins the rule and the exact30 small
validation maps in `experiments/mvp/valsplit/audit_small30.txt`. Tiny is excluded.
Heterogeneous prompts decode greedily through mlx-lm's batched generator,each with
its own full mask stack. A six-worker pool verifies outputs while decoding runs.
GPU behavior of this batching path must pass the keyed two-tiny smoke on 8.1.

Compare every gate with the **generation-0 8.1 SFT baseline**,not the preceding audit:
continue only for **at least two more passing maps**,or equal passes and **at least
5% lower mean weighted moves on mutual passes**. Audit after generations2,4,6,etc.
A05:30 EDT clock check audits the latest complete parent/children if no gate has
finished. It is cooperative at map and training-phase boundaries; an in-flight
phase may finish after the timestamp. Pending verification is cancelled where
possible and running owned jobs finish before the clock audit pool starts.

No detectable gain writes durable `terminal.json`,marks **keep the 8.1 adapter**,stops
before further child training,and releases model memory and the GPU reservation.
Resume honours this terminal outcome before GPU admission,including a crash
between recording a failed verdict and its terminal record. The record identifies
the original 8.1 parent as `fallback_adapter` with `fallback: keep_sft81`; retaining
that already trained adapter requires no new SFT run. Regression also rolls back
to the last audit.
On a no-gain stop,`survivor.json` is replaced with the original 8.1 adapter before
the terminal record is written. The terminal's `adapter` is that retained model;
`evaluated_adapter` preserves the candidate that failed the gain threshold.
A final audit binds every child to its own adapter-content identity before freeze.
Audit caches pin adapter,input,decoder and checker contents; reused outcomes cannot
silently score another adapter.

Both review reports and an evidence-bearing,hash-bound resolution inventory are
required before generations start and at generation boundaries. A new report or
open P0/P1 blocks deployment. The queue requires 8.1's successful DONE.json
(or sibling TRAIN.DONE),a successful current adapter/code smoke,and fleet admission.
The actual run_81.sh output is experiments/mvp/sft/stowlm-v1/adapter; the frozen
train.cmd confirms that path. A retired pilot2 watcher cannot enter a GPU job.
One shared
GPU/job lock covers all ExIt runs and smoke attempts. Smoke attempts are keyed by
adapter and code hashes; old unkeyed compute files cannot consume a new attempt.
Allowlisted mask speedups activate only between completed generations: checkpoint
population,sizing,next generation and source hashes; exit75 releases resident
weights and the GPU; the controller runs one current-code smoke under the shared
lock,then resumes in a fresh process. No mid-generation smoke or module reload
occurs. Further allowlisted edits coalesce into the pending epoch; unknown or mixed
changes fail closed. The initial measured baseline remains fixed and bound to its
8.1 adapter,maps and checker. Audit caches are code-epoch namespaced.
The loop never accesses test/showcase maps.

The resident process enforces a 2 GiB MLX reusable-buffer cache before model
allocation. Phase logs record entry/exit and one-second sampled peak RSS; sampled
peaks can miss shorter spikes. Securing bounds must remain available with no
fail-open events; positive force evaluations are required by preflight/smoke.

## Preference training and recovery

[DPO](https://arxiv.org/abs/2305.18290) teaches a model to prefer a checked winner
over a provably worse candidate,relative to a frozen reference. The reference is
**that child's completed SFT weights**,scored once before preference updates and
cached by exact weights,masks and token sequences. With action-token log-scores C,R
and frozen scores C0,R0,the loss is
`softplus(-beta*((C-R)-(C0-R0))) + nll_weight*chosen_token_mean_NLL`.
Defaults are beta=.1,NLL=.1,16 preference steps and at most16 pairs. Prompt and EOS
receive no preference loss. Pair priority is strictly more moves,then definitive
checker violations,then actual dead ends. Timeout/internal-error/incomplete checker
outcomes are uncertainty and never establish a negative label.

**APPROXIMATION:** a dead-end negative is a partial-prefix surrogate. Only tokens
of its final bad action or unfinished action after the last good boundary receive
rejected loss; earlier actions do not. Unequal lengths and credit assignment remain
limitations. Long pairs above2048 tokens are counted and excluded. DPO uses fresh
Adam within its phase; exact phase resume restores state. No-pair/error fallback
keeps intact SFT weights and records the actual method. Fitness edges alone do not
prove DPO helps; extra computation is not a matched experiment.

Optional `--dagger` is inspired by [DAgger](https://proceedings.mlr.press/v15/ross11a.html):
complete model-visited prefixes with a solver,then learn only the continuation.
A target still needs every gate and exact prefix preservation. Recovery spends at
most14% of measured existing CPU credit and four cases,with persistent reservations
before dispatch so restarting cannot replenish credit. Its owned subprocess forces
one checker process/native thread; watchdog termination touches only owned PIDs.

## Capacity,timing and approximations

GPU-saturated CPU capacity is approximately12 useful cores,per the leader's probe.
Start with six verifiers; throughput observations compare identical background-load
and GPU groups. Only fully occupied intervals during decoding inform saturated-GPU
sizing; idle feed gaps and CPU-only tails are separate. A labelled5% margin filters
noise. Hard admission still respects the16-core/112GB/one-GPU fleet ledger.
Normal loop work has no utility/background clamp.

The old50–75-minute forecast is withdrawn. Before the one-prefill fix,the logic
review derived roughly1.8–2.3h per48-map generation; this remains **unmeasured**.
D111 measured B8 small-map decoding at77.3s/map and3.5 complete samples/GPU-minute
under contention. Those are another adapter's measurements,not this loop's time.
Generation0 records decode,verify/polish,epoch refresh/dataset,each SFT/DPO phase,
and audit times. It then reduces maps/k using measured variable phases and fixed
training time to target75 minutes. **APPROXIMATION:** map-linear scaling and a
conservative future population/prefill factor; audits and nonlinear checker tails
can exceed the target. `sizing-after-gen0.json` exposes the calculation.

Existing CPU saved-plan evidence:5 tiny+5 small passed10/10 in112.9s,with110.8s
verification; reservation estimate195s/1core/2GB versus113.3s and1.01GB peak actual.
That was saved gold,not generated model improvement. CPU synthetic pruning overhead
and current unit-test timings are in REPORT-D110.md. No GPU timing,learning gain,
DPO superiority or recovery-yield claim is made before measurement.

D111 dropped pruning from deployment (~8% token saving in the speed study).
The disabled optional `--early-pruning` uses only monotone committed lift costs and a currently
verified complete gold bound. Future-restow projections can decrease and are
excluded. Greedy fitness samples are exempt. Pruned prefixes are distinctly counted
and excluded from DPO. CPU tests confirm actual token/KV row removal; GPU time saved
is unknown. Pending sampler/backtracking control-flow changes disable the policy
until its append-only semantics are reviewed. No production sampler edit is needed.

Artifacts include per-generation inputs,candidates,checked winners,replay selection,
phase times,method history,measured audit curves,and final/terminal evidence.
Changing sources or immutable parent/input identities fails closed on resume.
No cloud/API spend,no commits,and no test-set claims are introduced here.

## D124 checker availability (staged for a fresh-process boundary)

The reviewed bundle in `patches/d124/` runs candidate-admission and audit full
checks in an owned CPU subprocess. Its 300-second deadline includes startup and
report transport. A latched `BaseException` alarm and a parent kill/reap deadline
prevent a swallowed alarm from turning elapsed time into a physical violation.
Expiry, crashes, incomplete reports and checker internal errors mean
**verification unavailable**: no target, winner or DPO negative is admitted.
Reports separate unavailable checks from completed physical failures; fitness
retains every paired map and audits retain their frozen 30-map denominator.

Activation requires a committed generation-0 summary and sizing, released owner
locks, and the reviewed bundle hash. It writes a restart boundary without starting
GPU work. The host owner then uses `zsh experiments/mvp/exit/launch_on_host.sh`;
the usual current-code smoke, securing, cache and admission gates still apply.
Unknown source drift blocks this handoff. Never rewrite source pins to make it pass.

Operational limitation: LNS's internal qualification/proposal checks still use
its cooperative budget. Its output always receives guarded admission checking,
but an internal check can delay polishing. A follow-up watchdog must retain an
already verified incumbent on polish unavailability and count that attempt
separately. It must not change the incumbent's physical verdict.

CPU evidence for the staged bundle: `check_staged.py` passed 530 tests in 8.59 s.
One saved tiny training plan passed the subprocess check in 31.79 s (50 s estimate,
one CPU worker, no GPU), with 8 measured weighted moves. This is operational
verification evidence, not a learning-gain claim. See `patches/d124/REVIEW.md`
and deployment's `D124-READY.json` / `D124-real-train-check.json`.
