# Cycle 4 arrival pages: ballast guidance and deterministic work

Copied `method.py`, `pages.py`, `page_base.py` and the prefix regression tests
from `lab/c3/c3-layover/`, then changed only these local copies. The original
model's fixed prefixes, residual mass/height/cells, blocked arrival covers,
paid prefix groups, and tiny direct ship shifts remain. Mutable `engine/`
is never imported by this method.

`ballast_local.ballast_targets(instance)` interpolates the ordinary tank
sounding arms at the inherited masses and combines native lightship and fixed
loads. Its cargo LCG target cancels background longitudinal offsets relative
to native hydrostatic LCB interpolated at estimated departure displacement; its TCG target cancels inherited transverse moment.
No exact physics call is needed during construction. Priced boxes are guided
by mass times vertical height and squared longitudinal/transverse distance
from those targets. First fit additionally uses the marginal squared global
moment, including the arrival boxes, so it can compensate an asymmetric
arrival rather than merely preferring centerline positions. Ballast-objective pricing types
include physical coordinates: a column priced on one side cannot silently
reuse a different positional cost. Coverage phase 1 has zero column cost and
no moment term; with no positional lashing oracle or LCG row it instead shares
the original identical-geometry/unary/prefix/residual-capacity types. Every
concrete replicated column still passes the page legality gate. The restricted-master integer objective
also includes this same preference. Replay lifts still rank returned candidates.

This is a **heuristic**, not a certified lift bound or a stability criterion.
Low cargo KG tends to improve righting area, but securing loads can worsen;
GM alone does not decide this trade-off. The local finisher's actual native
three-state righting-area and securing margins, followed by the unchanged
full checker, decide feasibility. Failure of pricing, the local LP or bounded
construction proves no whole-map infeasibility. Existing C3 bounds remain
separate and unchanged.

## Work accounting

`propose` ignores `budget_s` as a stopping rule and records it only for
compatibility. Global count ceilings are 4 prefix branches, 8 branch/layout
models, 16 greedy trials, 160 pricing calls, 6,000 admitted patterns, 24
restricted-master LP calls, 24,000 LP simplex iterations, 80 dive iterations,
8 CP calls and 1,200 balancing trials. The lite profile limits each LP to 1,000 iterations; the measured profile
uses 10,000 per LP under the global iteration ceiling.
Each single-worker CP search has 0.04 deterministic time units and 10,000
conflicts; neither is wall time. One hundred root column-generation rounds and two
rounds/two retreats per dive further bound loops. Seeds determine every
random choice. Count exhaustion preserves complete earlier candidates.

Each branch additionally receives a reserved share of the global construction
counts. Preserve-prefixes can use at most 20% of each initial count; clearing
all prefixes receives 60% of the then-remaining counts, and selective branches
share the remainder. Both layouts in one branch share that quota. Local
exhaustion does not consume later branches' reservation. Every admitted local
unit charges the global counter once. Per-branch quotas and usage are recorded
in `method_metadata.branch_work`.

Counters and their ceilings are stored in `context['method_metadata']['work']`.
`context['work_config']` accepts a `limits` mapping plus `cp_deterministic_ticks`,
`cp_conflicts` and `lp_maxiter`; it is copied without mutation. Unknown counters
are rejected. Model building, replay and scoring are finite fixed passes over
the input, not loops raced against a clock. The public legacy `deadline` and
`seconds` parameters on the copied page API are compatibility placeholders;
they do not affect decisions. `monotonic` occurs only in elapsed-time telemetry.

The regression suite compares identical seeds with requested budgets of one
microsecond and one million seconds, tests strict counter exhaustion, retains
real arrival replay/full-check tests, and checks physical-height sensitivity.
No numerical tests or pilots were run before team CPU admission while the
leader's `BENCH-RUNNING` marker existed.

The mathematical inheritance is Gilmore and Gomory (1961),
https://doi.org/10.1287/opre.9.6.849, for stack-pattern generation. Local exact
area/securing finishing follows the C3 Benders implementation's successive
linearization/Broyden references; its implementation/provenance belongs to
`ballast_local.py` and the finishing report. No new mathematical optimality or
convergence claim is made here.

The lite defaults are not the primary measurement profile. After a TRAIN
pilot demonstrated that three root rounds left 34 boxes uncovered despite
remaining work capacity, the fixed root ceiling was raised to 100 rounds, with phase-1 geometry sharing
restored so coverage pricing does not repeat identical dynamic programs. The
primary profile is passed explicitly as canonical `work_config`: 8,000 pricing
calls, 200 LP calls, 200,000 LP iterations, 12,000 patterns, CP deterministic
time 0.5 and 100,000 conflicts per solve, and 10,000 iterations per LP. All other counts retain their defaults;
the branch reservations remain active. This is a fixed-count profile for every
map, not a wall-time allocation. Per-map metadata records the exact profile.

A guided first-fit failure now tries the original C3 room-first completion
with the same original seed before invoking the LP. Every first-fit call,
including dive tails, charges the shared greedy-trial count. This fallback
changes only a construction preference; prefix legality and real replay stay
identical. CP status, branches and conflicts are recorded even without a
witness. LP per-call iteration failures are distinct from exhaustion of the
global iteration budget.

Heel balancing uses geometric legality groups rather than physical objective
price groups, permitting opposite-side exchanges again. Both destination
patterns pass concrete legality and lashing before a swap. The balancing
residual targets the inherited-ballast cargo TCG target; returned metadata
continues to report the raw cargo transverse moment. Moved pattern lift costs
are recomputed for the destination cover.
