# Public-rule rejection and exact ladder learning

The vertices are placed containers and tank states, support/hatch edges fix the
cargo geometry, and a sampled loading condition attaches the checker's exact
rule diagnostics to that geometry. A rejected loading condition never becomes
an accepted plan here. Lifts and eventual acceptance remain with the full
checker and the existing score runner.

`screen.py:screen(instance, plan)` returns only `fail` or `unknown`. Replay
violations and shut-outs are necessary full-check failures. Otherwise the gate
constructs departure with the public `departure_condition`, derives the exact
D57 departure context, and calls public rule functions. A hard violation from
a successfully evaluated public part is a full-check failure, since that same
part/context is included in the full check. Unsupported stubs, unexpected
exceptions and `ran=False` reports produce unknown: computation budgets are not
logical failure certificates. Tolerated violations alone never reject.

Measured cheap-first order is replay, departure visibility, static cargo rules,
lashing, stability. On stored 88/162-box plans, warm visibility costs
0.062/0.097 seconds, static rules 0.088/0.108, lashing 0.085/0.148, stability
0.206/0.203. Visibility precedes static because E6 can take seconds on small
plans. Replay detects invalid operational perturbations without numerical
work. The first hard violation stops screening; an unknown candidate still
requires the unchanged full checker.

`ladder_verdict(instance, plan)` preserves the C1 ladder's learning decisions,
not just its first failure. It computes static and all three D57 samples of
visibility, lashing and stability. These cover every visibility/securing/E6/
trim/draft flag and the bow-down predicate used by `learn`. Strength emits only
`iacs.s11a.longitudinal_strength` or checker-data/error identifiers, so it cannot
change those flags. A known hard failure plus a non-iscode violation fixes both
`ballast_only` and `hopeful` false, regardless of omitted strength. Later D57
visibility calls are skipped after a visibility violation has been witnessed;
later lashing calls after securing is witnessed; later stability calls after
bow-down is witnessed (which also proves the trim/draft flag). These positive
learning flags cannot be undone by another sample. Absence of a flag still
requires every relevant sample. This saturation affects only the ladder helper,
not the public `screen` control flow. Only then
may the helper omit the remaining full check. Otherwise it falls back to the
unchanged full checker. Full checker success is the only successful ladder
verdict. Activated rule-freeze-v1 and active stubs always use that fallback.
The full checker, strength and original ladder source SHA-256 values are pinned;
changed sources conservatively disable the omission. Returned rule lists may omit strength-only IDs in rejected cases; learning
predicates are equivalent. This is deliberately not a general full-report API.

The D57 validation and context construction in `_samples` is copied from
[`src/stowage/checker/full.py`](../../../../../../src/stowage/checker/full.py).
No checker or source files are changed. Public functions come from
[`checker/__init__.py`](../../../../../../src/stowage/checker/__init__.py).
The skipped-rule argument is specific to current
[`checker/strength.py`](../../../../../../src/stowage/checker/strength.py),
and learning predicates are from
[`c1/fx-scout/method.py`](../../c1/fx-scout/method.py). No new numerical method
or external paper is used for this one-sided logical gate.

Profiles (`results/screen-profile-{88,162}.pstats` and `.txt`) run the unchanged
full checker on stored C1 quick-3 plans. The 88-box full check was 13.23 s under
cProfile; 162-box was 15.08 s. These are instrumented profile costs, not speed
ratios against unprofiled warm partial calls. At 162 boxes, cumulative visibility
is 4.73 s, strength 4.12 s, stability 4.06 s. They overlap numerical helpers.
Skipping strength alone therefore cannot promise a threefold whole-check gain.
The stored plans passed, and all partial learning flags were equal.

The differential harness uses only frozen `sets/MANIFEST.json` inputs, verifies
their SHA-256, and reads stored baseline plans plus the two C1 quick-3 medium
plans (88 and 162 boxes). Later tiny plan-only perturbations alter move order,
truncate/omit operations, duplicate loads, collide destinations, empty declared
ballast, or leave the last hatch operation out. Full checking always runs,
including after illegal replay. Grouping perturbations by vessel avoids hull
LRU churn without caching verdicts. Atomic progress allows an interrupted run
to resume. Preliminary static-first rows are preserved separately and do not
count towards the final-code differential.

One numerical thread and one CPU-busy process are used here. `nice -n 5` was
attempted but this sandbox denies setpriority. No sealed inputs were opened.
The final 200-case differential is authorized to exceed twenty minutes if
needed: the order explicitly requires 200 comparisons to unmodified full checks,
each taking roughly 2–10 seconds here, plus gate time. This is the reason for
that possible long run. No individual check or numerical tolerance is shortened
to manufacture a verdict. The final run began before the message-prefix and
ladder saturation additions; neither changes the default `screen()` status
path: saturation is guarded by `complete_learning=True`, and the prefix adds
message text to already-produced violations without changing severity/rules.

Pinned omission-domain source identities:

- `checker/full.py`: `ff3e593771ef30c29e1e6cecd434090961ed099e2e51b66c37b268113eee7fa1`
- `checker/strength.py`: `f7d1dbdd49ec29f461800113cad05a9bb79ad1b1f3d6eeea3cef6880a21e1b81`
- `c1/fx-scout/method.py`: `a51bb85ea09c79b713a4d06dae0c0af0dffb5dd00c8e12d83875af98e3f5eece`

## Final gate differential and verification

`results/screen-differential.json` contains **200 completed, unchanged full
checker comparisons, zero false fail**. `screen-differential-summary.json`
discloses **92 distinct (instance, plan) inputs**: 19 stored plans and 181
perturbation cases, with repeated empty/reversed/colliding tiny plans. The cases
are 9 small, 189 tiny and 2 medium; the original 88/162-box stored plans are both
included. This is 200 cases, not 200 unique plans or 200 medium plans.

There were 170 gate failures and 30 full-check passes. Every passing input
returned unknown; all 170 full-check failures were caught on this particular
corpus. Full-check call times summed to 924.014 seconds; gate calls summed to
89.181 seconds (10.36x gate-only ratio). Adding gate time and full-check time
only for unknown cases gives 321.553 seconds, a **2.87x paired-call estimate**
for the screened pipeline. This is a rejection-heavy diagnostic corpus, and
that sum is not a separately timed pipeline run or a whole-ladder speed factor.
It does not satisfy the 3x keep gate by itself. No move, accepted lift count or
acceptance rule was changed.

The 11 focused safety tests passed: no pass shortcut, tolerated/hard distinction,
indeterminate numerical error handling, unsupported overrides, illegal replay,
shut-outs, unchanged full-check success, changed-source fallback, positive flag
saturation, absence coverage, and exact D57 message-prefix handling. Command:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c2/c2-gates \
nice -n 5 .venv/bin/python -m pytest \
  experiments/mvp/frontier/lab/c2/c2-gates/test_screen.py -q \
  -o cache_dir=experiments/mvp/frontier/lab/c2/c2-gates/.pytest_cache
```

Production `screen.py` SHA-256 is
`2eccf46f812dedb834e63111fcf2000d2e60adfdf38303ff163422b61081830f`.
The executive additionally ran the shared scorecard suite with these tests:
36 passed (25 shared + 11 gate tests).

The executive owns paired scorecard passes/lifts and actual medium ladder wall
times. `benchmark_screen.py --mode ladder` measures AB/BA calls on identical
saved reference-rung plans and asserts equivalent verdicts and all learning
flags, writing `results/screen-ladder-paired.json`. Positive-saturation speed
is unclaimed until that measurement and the independent scorecard finish.

Verdict for this component alone: keep the safe rejection/learning API as an
experimental building block; no measured 3x whole-ladder keep claim yet. The
next useful test is the fixed medium rung transcript comparison, followed by
the paired medium scorecard. Successful plans still run the unchanged full
checker, so their partial-probe overhead must be included.
