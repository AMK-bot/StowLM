# Medium showcase settings review — 29 October 2026, 14:01 EDT

Read-only source/status review; no solver or generation jobs were run for this review.

Use the leader's adopted booking arguments `booking_slack=.25` and
`booking_mass_fraction=.55` for medium. The latest
`experiments/mvp/solve47/slack/small-yield-finalizer/recommendation.json`
(rendered 18:00:23 UTC) qualifies only feeder_500 small (.25/.55, 4/4);
feeder_800 and feeder_1700 are unqualified. No measured per-ship refinement has
been adopted for these medium attempts.

Recommended generation API:

```python
from stowage.generate.instances import GenerationReport, generate_simulated_instance
audit = GenerationReport()
instance = generate_simulated_instance(
    "medium", seed=seed, noise=.7, vessel_name=ship,
    port_name="laem_chabang_like", max_attempts=200, report=audit,
    booking_slack=.25, booking_mass_fraction=.55,
)
```

The CLI equivalent is `uv run python -m stowage.generate.instances --size medium
--count 1 --vessel SHIP --port laem_chabang_like --noise .7 --seed SEED
--booking-slack .25 --booking-mass-fraction .55 --max-attempts 200 --output OWNED_DIR`.
There is no generation deadline CLI argument: impose the <=600-second stage
deadline in the supervising driver.

Recommended fresh deterministic cells: feeder_800 / 681029801,
feeder_1700 / 6810291701, feeder_800 / 681029802. A text search across current
solve47, corpus and runs JSON found no occurrence of these seed integers.
Record actual DG and reefer counts among the export load_list, and require both
positive for the requested showcase mix; default enabled DG sampling alone does
not establish presence. `Container.dg is not None` and `Container.reefer` are the
schema fields. Keep actual generated noise/yard audit evidence.

Use default `ScalableSolver(ScalableConfig(feasibility_config=FeasibilityConfig(
extra={"global_screen": 0})))` with the explicitly bounded solver time. Constructor
v4 remains off. Do not silently disable default search modes under a claim of
default scalable behavior.

Ballast v4 is an explicit opt-in, default off: set environment
`STOWAGE_BALLAST_V4=1` only while calling
`stowage.planner.finalize(instance, result, time_limit_s=600)`, then restore the
prior environment in `finally`. Leave it absent during generation/construction
and restore absent before full checker and replay. The slack study documents
that global v4 causes duplicate kept-plan checks in generation's D58 worker and
generation timeouts. This scoped environment is a workflow workaround; it does
not alter acceptance rules. Current ballast STATUS reports medium 0/3 for both
legacy and v4 on its completed-plan benchmark; success remains unverified.

The existing complete production mask helper is
`experiments/mvp/solve47/slack/run_slack.py`:
`token_replay(instance, plan, False, row, require_complete=True)` followed by
`summarize_replay(row)`. It creates the engine with C2 off, uses the production
tokenizer and MaskedDecoder, checks all actions and end-plan tokens, and sets
`row['passed']` only for a complete, equal-action replay. Full checker PASS is a
separate required gate; a successful mask prefix is never a complete plan.

ASSUMPTION: the same .25/.55 synthetic demand conditioning is useful for medium
despite being adopted for small. It is not a claim of medium feasibility.
APPROXIMATION: three fresh deterministic draws are a bounded showcase search,
not an evaluation sample or a yield estimate.
