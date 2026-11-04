# Evidence status after fix-t6-basemask

All results below are **historical**. The old “official” label identifies the
original run's protocol, not approval under current rules. Its six input byte
hashes still match, but all six lack voyage and frozen-v2 membership; the two
archived passing reports lack current checker binding. No historical catalogue
or model digest can be recovered from today's files. See
[runs/official-base-bo32-small-c2/provenance-audit.json](runs/official-base-bo32-small-c2/provenance-audit.json).

The six-instance summary is corrected: **168/192 dead samples = 87.5%** and
**6/6 instances with dead samples = 100%**. Its 2/6 archived checker passes have
not been rechecked or migrated into current approvals. The correction never
rewrites original inputs, config or per-instance records.

Current code checks ballast and every full-checker part for complete sampled
candidates before ranking feasible ones by weighted moves, behavior log-probability
and sample index. `candidate_selection_s` measures this added work independently
of `gen_wall_s`. An action-complete fallback remains available for repair when
no sampled candidate passes; `PlannerResult.shippable` remains the shipping gate.
Elapsed time limits are cooperative at setup/decode/check boundaries. A blocking
load, engine build, prefill, decode step or full check can overrun the deadline.

The replacement C2-on GPU run is **pending the leader**. Commands, staged compute
estimate and prerequisites are in
[the fix log](../review/fixes/fix-t6-basemask.md). `make_val_v3.py` now iterates
frozen-v2 requests, uses current voyage-producing generators and checkpoints
accepted/rejected audits after each request. The old historical seed commands
below do not reproduce a current-rules campaign. A dry run is CPU-only:

```sh
UV_CACHE_DIR=/private/tmp/stow-review-uv uv run --offline --no-sync python experiments/base-mask/make_val_v3.py --profiles small --c2 --dry-run
```

# 6.2 pilot: base Glimmer + mask (27 Oct 2026, stow-gpu)

Untuned Muse Glimmer 30B (MLX 4-bit, no adapter) plans under the 5.1 feasibility mask. Each instance is evaluated
through the 5.2 harness: `run_evaluation` → `run_planner`: plan → finalize (4.2 ballast) → full check → repair (5.7).
`solver.scalable` runs on the same val instances as the reference.

**Unofficial, stub-tainted row** (`tiny_run=True`), for two reasons:
- `uv run python -m stowage.cache_warm` exits 2: "production linear policy epoch is stale; requalify" (3.7).
- The real engine then fails closed: a depth-0 dead end, "Global stability model unavailable".

So both planners run under the same labelled stub of `feasibility.make_engine`, with `global_screen=0`. That means
**C2 is off**. The L1/L2 local checks and C1 stay on, and the checker is unchanged.

## Setup

- **Instances:** val split, from the **current generator** (`stowage.generate.instances_v3`). The canonical
  instances-v3 campaign was still a train-only pilot, so `make_val_v3.py` takes the module's own canonical tiny/small
  specs, keeps those whose pre-assigned split is val (55 of 1,600), and writes them with the module's `shard()`.
- **Selection:** the harness picked 12 tiny (6 boxes) + 12 small (20/27/34 boxes) in stable spec-key order, mixing
  both generators.
- **Planner:** `llm.stowlm.planner.StowLMPlanner(n_samples=32, temperature=1.0)`, best-of-32.
  - All 32 samples come from one `generate_masked` call: the prompt is prefilled once and its KV cache tiled 32×.
  - Among complete samples it keeps the lowest D11 objective (`count_moves(...).weighted_nonproductive`); the checker
    still judges the choice.
  - Batch 32 is the efficient MLX range (`experiments/glimmer-bench`).
- **Reference:** `solver.scalable`, default config (60 s limit). The harness rows are in `runs/scalable/`. Because the
  harness keeps the post-repair plan, I re-ran plan-only with the same stub to get each solver's own status and
  objective (`scalable_plans.json`).
- **Files:**
  - `runs/base-bo32-T1/` and `runs/scalable/`: harness records + `summary.json` + `ROW.md`.
  - `compare.json`: per instance.
  - `summary.json`: per rung.

## Results

| | tiny (12) | small (12) |
|---|---|---|
| **Checker pass** (after finalize + check + repair) | base **0/12**, scalable **0/12** | base **0/12**, scalable **0/12** |
| Why | ballast `not_found` on every complete plan; repair attempted, never passed | same |
| Base: instances with ≥1 complete sample of 32 | 10/12 | 9/12 |
| Base: sample completion (complete / all samples) | **82 %** (316/384) | **21 %** (81/384) |
| Base: samples ending in a mask dead end | 68 | 303 |
| Scalable: complete plans | 10/12 (1 dead end, 1 timeout) | 9/12 (3 timeouts) |
| Instances where neither completes | 2 (engine dead end at depth 0 or after 1–4 actions) | 3 |
| **D11 objective** on instances both complete (sum; lower is better) | **base 120** vs scalable 146 (136 in the harness run); LB 12 | base 460 vs **scalable 323**; LB 65 |
| Base better / tie / worse vs scalable | **7 / 0 / 3** | 0 / 1 / **8** |
| Base, objective of the median sample (not best-of-32) | 196 | 670.5 |

### Timing (sustained GPU, batch 32)

| | tiny | small |
|---|---|---|
| Generation time per instance (32 samples) | median **15.9 s** (10.7–19.4) | median **64 s** (43–114) |
| Amortised per sampled plan | ≈ 0.5 s | ≈ 2 s |
| Prefill | 0.7 s | 1.5 s |
| Decode steps | 78 (longest sample) | 396 |
| Aggregate throughput | 125 tok/s | 130 tok/s |
| **Host share** (mask work on the CPU, out of decode wall time) | median **2.1 %**, max 3.6 % | median **3.9 %**, max 5.9 % |
| Harness: plan / plan + finalize + check + repair | 16.7 s / 95 s | 66 s / 129 s |
| Scalable plan time (harness run) | 0.7 s | 5.6 s |

### What it means

- **The mask works as intended.** Every sampled plan is engine-legal or a reported dead end; no sample hit the token
  limit. The mask's CPU work is ≤ 6 % of decode time at batch 32, so it isn't the bottleneck.
- **Untuned Glimmer can't plan well past tiny.**
  - **Tiny:** best-of-32 beats the scalable solver on 7 of 10 instances (120 vs 136–146). With 6 boxes, sampling 32
    plans and keeping the best is a decent search.
  - **Small (20–34 boxes):**
    - 79 % of samples walk into dead ends.
    - The best of 32 costs 42 % more moves than scalable, and scalable is better on 8 of 9 instances.
  - **Median sample:** 1.6× (tiny) to 2.1× (small) scalable's moves. That gap is what SFT (8.1) has to close.
- **Plan-generation time,** the real number 6.2 asks for:
  - ≈ 0.16–0.2 s per decode step at batch 32, and 7 steps per action, so ≈ 1.1–1.4 s per action for all 32 samples
    together;
  - a 34-box small instance takes ~2 min for 32 samples;
  - a single greedy plan is cheaper in total, but has no best-of-k search.
  - Extrapolating to a 280-box full feeder (~2k decode steps at 0.16–0.28 s/step): ~5–9 min per instance at batch 32
    (UNVERIFIED; full-size v2 instances are engine dead ends, `experiments/sft-pilot/README.md`).
- **The binding blocker** is unchanged from the tiny chain: no plan from either planner gets a ballast condition the
  checker accepts (3.1/4.2), so pass rate and fallback rate can't be measured yet.
- **Scalable is time-limited and not deterministic run to run:** 2/24 objectives differ between the harness run and
  the plan-only re-run (tiny 19 vs 25, and 4 vs 8). The tiny totals are given both ways.

## Proposed `docs/evaluation.md` row (leader-only)

> 6.2 base Glimmer 4-bit + mask, best-of-32 @ T=1, 12 tiny + 12 small v3-generator val, **unofficial (C2 off, stub;
> linear epoch stale)**:
> - **checker pass:** 0/24 (ballast not found on every plan, same for scalable);
> - **completion:** 19/24 instances with ≥1 complete sample; sample completion 82 % tiny / 21 % small;
> - **D11 objective vs scalable on 19 shared complete:** tiny 120 vs 136–146 (base better 7/10), small 460 vs 323
>   (base worse 8/9);
> - **generation per instance:** 15.9 s tiny / 64 s small for 32 samples; host share 2–6 %.
>
> Rerun when 3.7 requalifies and 3.1/4.2 ballast lands: `experiments/base-mask/run.py`.

## Reproduce

```
uv run python -m stowage.cache_warm --workers 1                      # exits 2 until 3.7 requalifies
uv run python experiments/base-mask/make_val_v3.py --workers 4       # 55 val tiny/small, ~45 s
uv run python experiments/base-mask/run.py --planner base --run-dir experiments/base-mask/runs/base-bo32-T1      # GPU, ledger
uv run python experiments/base-mask/run.py --planner scalable --run-dir experiments/base-mask/runs/scalable     # CPU
(cd experiments/base-mask && uv run python scalable_plans.py)       # plan-only scalable status/objective
```

When C2 is available, drop the stub in `run.py` (or make it conditional on the engine building) and run with
`tiny_run=False` for an official row.

# Official-path rerun, C2 on (27 Oct 2026, 18:00–18:30 EDT)

`cache_warm` now exits 0 (the linear epoch is qualified). These runs use the real engine with C2 on, no stub, and
`tiny_run=False`: **historically official rows, unqualified under current rules**. Base Glimmer is best-of-32 at T=1. The mask is stow-mask's worker-thread
version (`generate_masked(prefetch=True)`). Pipeline: `run_planner` (finalize → check → repair).

## Tiny val (the 12 C2-unscreened v3-generator instances above): `runs/official-*-tiny`

- **0/12 complete, 0/12 checker pass, for both base and scalable.** Every instance is a **C2 dead end at depth 0**:
  "No fractional completion meets the linear stability/strength rows" (`iscode.stability.margin`,
  `iacs.strength.margin`).
- About 2 s per instance (engine build 0.3–6.4 s).
- These archived candidates were rejected by the then-current C2 model. The pilot's bounded ballast
  searches returned `not_found`; they did not prove that no ballast or alternative stow exists. D49 subsequently
  corrected the direction of C2's empirical necessary margins.
- **Regenerating tiny val with the C2 screen** (`make_val_v3.py --c2 --profiles tiny`): **0/29 val specs accepted**.
  Each exhausted 50 attempts, mostly on C2 (e.g. 42 of 56 completion checks).
- **No candidate was accepted in those 29 specifications and 50 attempts per specification.** This is a
  measured failure of that finite search under that historical C2 model, not a universal impossibility claim.
  Current tiny feasibility requires regeneration and evaluation under D49 and the current physics epoch.

## Small val, C2-screened (`make_val_v3.py --c2 --profiles small`: 10/26 accepted): 6 instances, `runs/official-*-small-c2`

| | base best-of-32 | scalable |
|---|---|---|
| **Checker pass** (shippable, no repair) | **2/6** | 1/6 |
| Complete plan | 5/6 (sample completion 24/192 = 12.5 %) | 2/6 complete incumbents (others: dead end or timeout at 60 s) |
| Ballast on complete plans | ok ×2, not_found ×3 | ok ×1, not_found ×1 |
| D11 objective where base passed | 44 (LB 4); 67 (LB 0); scalable had no plan on either | — |
| D11 objective where both complete | 44 vs **40** (scalable passed, base no ballast); 44 vs **36** (neither ballasted) | |
| Generation per instance (32 samples) | median ~88 s (66–125 s), 245–288 steps | plan 60 s (time limit) |
| **Host share** (mask CPU / decode wall) | **52–62 %** (2–6 % with C2 off) | — |
| Plan + finalize + check + repair | 85–218 s per instance | 73–145 s |

- **These are the first checker-passing StowLM-planner plans** (untuned model, best-of-32), and the first official
  6.2 rows.
- With C2 on, `legal_actions()` (the C2 LP screen per state) is the bottleneck: the mask's CPU work is now about half
  of decode time even with the worker thread. GPU decode itself took ~35–45 s of each 66–125 s.
- **This makes 4.4/5.1 C2 latency the priority for scaling 6.2 / 9.1 / GRPO rollouts.**
- **Small sample, n = 6.** Not a quality claim.

Reproduce (official):

```
uv run python -m stowage.cache_warm --workers 1     # must exit 0
uv run python experiments/base-mask/make_val_v3.py --workers 5 --c2 --profiles small --out experiments/base-mask/instances-v3-val-c2-small
uv run python experiments/base-mask/run.py --planner base --no-stub --limits small=6 --instances experiments/base-mask/instances-v3-val-c2-small --run-dir experiments/base-mask/runs/official-base-bo32-small-c2
uv run python experiments/base-mask/run.py --planner scalable --no-stub --limits small=6 --instances experiments/base-mask/instances-v3-val-c2-small --run-dir experiments/base-mask/runs/official-scalable-small-c2
```

Per-instance detail: `official_tiny_compare.json`, `official_small_c2_compare.json`. The current harness resumes only an identical selected-input configuration.
A changed `small=10` selection requires a new run root; the original row predates that stricter contract.
