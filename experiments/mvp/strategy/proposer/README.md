# D185 proposer test harness

Preparation only: no Spark calls, Glimmer inference, training or GPU use. The
four arms are zero-shot Spark, untuned base Glimmer, random/local search and the
shared software rule. Every candidate uses the same restricted two-step → V4 →
full terminal checker → conformance audit. The leader starts collection/runs.

## Frozen inputs

`maps_d185.json` retains the original 20 small train maps and consumes **every**
medium row, in order, from `../blockrule/cohort-medium.json`. That external file
is published atomically only after all current-epoch medium train generation
requests complete. Its request list fixes 12 maps; partial progress, archived
maps and replacements chosen by outcomes are forbidden. The new medium inputs
are under `blockrule/maps/train/medium/`, with producer audit hashes and explicit
`D185-current-epoch` lineage. No val, test or showcase input is accepted.

The local cohort must be frozen before any arm can run or preflight. The runner
refuses the historical `maps.json` cohort. Create/verify it with:

```sh
uv run --no-sync python experiments/mvp/strategy/proposer/cohort.py --wait
uv run --no-sync python experiments/mvp/strategy/proposer/cohort.py
uv run --no-sync python experiments/mvp/strategy/proposer/cohort.py --token-sizes experiments/mvp/strategy/proposer/token_sizes_d185.json
```

`--wait` polls only for the final external medium list, then freezes locally;
`--freeze` freezes immediately if that final list exists. Neither overwrites an
existing freeze. Input/audit digests are checked again by map workers. The old
`maps.json` and earlier verification folders remain historical evidence.

## Rules and checked-evaluation budget

`rule.py` imports `blockrule.rule.port_blocking_layout` verbatim: one port per
hatch cover on small; contiguous fore-aft stations with paired transverse covers
on medium. Source hashes and the schedule are registered before evaluation.
Odd proposals use the rule, even proposals use an unrestricted default fallback.
Every fallback is a separate, counted proposal/evaluation; there are no free
baseline solves, winner certificates or shared checker results. The schedule
uses no outcome feedback. Rule/source changes fail closed.

Each arm targets **k completed terminal checker reports per map**, including
rejections (`k=1,4,16`). Invalid text and planner/V4 failures consume proposal
tries without advancing k. Terminal exceptions are tracked separately. A fixed
`--max-proposals 256` cap, source exhaustion or unavailable certificates leave
a map incomplete. Incomplete maps cannot silently enter an equal-budget merit
comparison. Each k=4 run supplies both checked prefixes 1 and 4; k=16 is diagnostic.

`terminal_certificate_check_calls` counts parent terminal calls;
`checked_evaluations` counts their completed reports. V4's child-process checker
work is **unmeasured** and `all_full_checker_invocations` is null. Equal k does
not claim equal total checker invocations or CPU work. Interrupted terminal work
without a durable certificate refuses replay. Native libraries use one thread;
default one map worker plus its V4 child; maximum two plus two = four CPU workers.

The evaluator remains headroom's `run.evaluate` / `run.finish_check`, with seed 0,
15 s small / 60 s medium, global_screen=0, rule freeze disabled, ballast reset,
V4 enabled and a 600 s finisher. Only checked, conforming plans supply moves.

## Registered D185 verdict

At **k ≤ 4 on medium**, Spark or Glimmer must beat the better of rule and search
by **≥2 more passing maps**, or **≥5% fewer moves on mutual maps with no loss of
passes**. Small must be within **5% of the rule**. If no arm passes **≥4 medium
maps**, the result is **INCONCLUSIVE** and the LLM stays parked. Search or rule
tying/winning means fine-tuning is **NOT_JUSTIFIED**. No training starts here.

To avoid choosing a weaker baseline after seeing results, the pass branch uses
`max(rule passes, search passes)`. The move branch requires the 5% margin against
**each** baseline on its respective nonempty mutual maps, with passes at least
that maximum. Small compares mean moves on nonempty model/rule mutual maps at
≤105% of the rule. There is no extra small pass-count gate. Threshold arithmetic
is exact. All four arms must complete the same checked budget across the frozen
small and medium cohorts for a merit verdict; otherwise it is inconclusive.
A qualifying k=1 or k=4 suffices; k=16 itself never justifies tuning.
`--only`/`--limit` runs are labelled diagnostic subsets and are rejected by the
comparative report. Full cohort identities and order are checked independently;
editing a cohort's rows while keeping its freeze attestation is also rejected.

## Exact commands

From the repository root; use the existing Python 3.12 environment. If needed,
prefix uv with `UV_CACHE_DIR=/private/tmp/proposer-uv-cache`.

After the cohort is frozen, these are plan-only checks (no solver/provider call):

```sh
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm spark --k 4 --output experiments/mvp/strategy/proposer/runs/d185-spark-preflight --dry-run
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm glimmer --k 4 --output experiments/mvp/strategy/proposer/runs/d185-glimmer-preflight --dry-run
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm search --k 4 --output experiments/mvp/strategy/proposer/runs/d185-search-preflight --dry-run
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm rule --k 4 --output experiments/mvp/strategy/proposer/runs/d185-rule-preflight --dry-run
```

After external collection is authorized, evaluate its exact responses and the
software comparators (these commands do not collect model responses):

```sh
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm spark --k 4 --provider jsonl --proposals experiments/mvp/strategy/proposer/responses/d185-spark.jsonl --output experiments/mvp/strategy/proposer/runs/d185-spark
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm glimmer --k 4 --provider jsonl --proposals experiments/mvp/strategy/proposer/responses/d185-glimmer.jsonl --output experiments/mvp/strategy/proposer/runs/d185-glimmer
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm search --k 4 --output experiments/mvp/strategy/proposer/runs/d185-search
uv run --no-sync python experiments/mvp/strategy/proposer/run.py --arm rule --k 4 --output experiments/mvp/strategy/proposer/runs/d185-rule
uv run --no-sync python experiments/mvp/strategy/proposer/report.py --spark experiments/mvp/strategy/proposer/runs/d185-spark --glimmer experiments/mvp/strategy/proposer/runs/d185-glimmer --search experiments/mvp/strategy/proposer/runs/d185-search --rule experiments/mvp/strategy/proposer/runs/d185-rule --output experiments/mvp/strategy/proposer/runs/d185-comparison.json
```

## Prompt, format and provenance

`prompt.py` deterministically aggregates cargo by POD/catalogue VGM weight class,
block/free-cell capacities, retained cargo, yard spread, rotation and rules.
There are no plan labels, search outcomes or box-level placements in the prompt.
The strict entire-response grammar is `LAYOUT 1`, followed by one line for every
category: `POD/class = cover-id/hold,cover-id/deck`. Only actual block IDs and
nonempty permission sets are accepted. No prose, fences, duplicate rows/blocks,
blank lines, aliases, CRLF or repaired/widened permissions are allowed. Capacity
and operational feasibility remain the common evaluator's job.

JSONL rows require map_id, arm, attempt (1-based), text, collection_provenance;
optional usage is the only extra top-level field. `registration.py` fixes Spark
`muse-spark-1.3`, base Glimmer `meta-models/Muse-Glimmer-30B`, temperature 0.7,
top_p 1.0, maximum **2048 output tokens**, and generation seed `179+attempt-1`.
The smaller D185 output cap supports the strict Spark campaign budget; it is
separate from the planner seed. The future collector must include reasoning in
that total output cap.

`collection_provenance` requires the actual prompt_sha256 and frozen file
input_sha256, registered model/generation_settings/seed, base_model=true,
adapter=null, independent=true, feedback="none", zero_shot=true. Use
`collection_requirements(arm, attempt)` for fixed fields. Unverifiable metadata
fails closed before evaluation. This is an attestation, not proof of external
collector behavior. Collection and evaluation provenance remain separate.

The controller hashes and saves one immutable response byte snapshot. Every
worker checks that hash before parsing; resume and report reconstruct the exact
text/metadata from that source. Changed prompts, sources or cached response text
are rejected. Canned dry-run outputs are labelled `canned_smoke` and are never
eligible for model comparisons. Only offline providers exist in this harness.

## Spark total strictly under $2

`cost.py` validates **every Spark response in the entire frozen source**, even
unused rows, invalid layouts and wasted tries. Usage must provide either actual
`billed_cost_usd` with `billed_cost_includes_all_tokens=true`, or integer
input_tokens/output_tokens with `output_tokens_includes_reasoning=true`. Recorded
rates are $1.25/M input and $4.25/M output (`docs/compute.md`). Missing/ambiguous
usage, a source total ≥$2, or a campaign total ≥$2 fails closed.

The shared `budget/d185-spark.json` registry counts unique source pools once,
including across k=1/k=4 replays. Different pools aggregate under an owned lock;
two individually affordable pools cannot bypass the total cap. Reports recompute
source cost and validate the campaign registry. Plan-only checks do not register
spend; no call is made here. The registry attests imported pools, not external
unreported collection spend.

A future live collector must reserve each next response's maximum all-token cost
against the **same campaign's** remaining budget and stop before $2, even if it
cannot reach k. A checked-evaluation target is not a spending cap. Prompt-token
proxies exclude provider chat wrappers; actual collection usage must be retained.
Per-proposal estimates and a capped-output budget plan for the frozen D185 cohort
are recorded alongside its token measurements. They cannot guarantee k checks.
`token_sizes_d185.json` measures 776–874 raw prompt tokens on small and
1,147–1,591 on medium. At the registered 2,048-token output cap, four proposals
per map across all 32 maps estimate **$1.28** before Spark-specific input/chat
differences. This is a proposal-cost proxy, not a guarantee of four checked
evaluations; wasted proposals also consume the strict campaign budget.

## Verification

```sh
uv run --no-sync pytest experiments/mvp/strategy/proposer/
```

Tests cover strict format/provenance, immutable snapshots, completed-check budgets,
shared rule API/fallback/source pins, current-epoch freeze requirements, exact
D185 boundary verdicts and Spark single/multiple-pool caps. Previous headroom/text
physical-plan parity samples remain in `verification/review-fixes/parity/` (two
fresh pairs, zero mismatches); the evaluator itself is unchanged by D185.
Current readiness evidence is `verification/d185/READY.json`.
