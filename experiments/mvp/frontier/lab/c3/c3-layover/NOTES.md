# Cycle 3: arrival cargo and fixed-prefix pages

Only this directory is writable. All new inputs are synthetic TRAIN maps; no
sealed data was read. The unchanged full `stowage.checker.check` is the only pass
gate. A complete replay-legal stow and a full-checker pass are reported separately.

## Scope and provenance

Read the full lab CONTRACT (including Cycle 2/3), README, scout GRAPH-MODEL,
Cycle 2 scout/layout NOTES, engine NOTES, Frontier README/RESULTS, evaluation
lines 192–200, the layover fixture and simulated-instance generator. The generator initializes an empty
ShipState and exposes no arrival-occupancy argument, so a local wrapper is
necessary. The fixture illustrates current discharge with a through box above it.

`engine_copy/` snapshots the engine's Python files and Frontier dependencies;
`engine_copy/MANIFEST.json` records original paths and copied-byte SHA-256s.
The snapshot identifies itself as 0.11.0 (the current engine, rather than assuming
0.10.0). Runtime source files are never edited. Any relocation-only import
adaptation to the copy will be documented explicitly.

## Model

A physical stack is a page whose arrival cargo fixes its bottom sequence.
New patterns consume only residual tiers, height and mass. A crossing between
an arriving box's discharge and a new box's discharge can force a future
restow; clearing an arrival prefix or a blocked hatch can instead pay lifts now.
A cover vertex joins its hold pages to deck pages: an arriving deck box blocks
opening even if no newly loaded deck box is assigned there. Construction is
heuristic; only the separate relaxed model may certify a lower bound.

Concrete small/medium cohort graphs appear below; the final per-size/occupancy
comparison is in [TABLE.md](TABLE.md). Bounds and their proof are in
[BOUNDS.md](BOUNDS.md).

## Sources

The inherited column-generation idea follows P. C. Gilmore and R. E. Gomory
(1961), [A Linear Programming Approach to the Cutting-Stock Problem](https://doi.org/10.1287/opre.9.6.849).
One legal stack pattern plays the role of one cutting pattern; pricing generates
useful patterns without listing every combination. The original publisher
abstract was searched on 2026-11-01; no downloaded code was run.

The cover/port activation interpretation follows Roberto Roberti, Enrico
Bartolini and Aristide Mingozzi (2015, online 2014),
[The Fixed Charge Transportation Problem: An Exact Algorithm Based on a New Integer Programming Formulation](https://doi.org/10.1287/mnsc.2014.1947).
Opening a link incurs a fixed charge once; multiple cargo boxes using the link
do not each incur another cover-opening charge. We adapt that modelling idea,
not the paper's exact algorithm; only publisher metadata/abstract was consulted.

## Compute and reproduction

At most two CPU-busy processes across the entire team; while the leader's
BENCH-RUNNING marker exists, at most one. The marker was absent at startup.
All numerical processes set OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1,
VECLIB_MAXIMUM_THREADS=1, MKL_NUM_THREADS=1 and PYTHONDONTWRITEBYTECODE=1;
CP-SAT uses one worker. Caches and result artifacts live under this folder.
Long runs request `nice -n 5`. No unrelated process is stopped. A complete
cohort run may exceed twenty minutes because every size/occupancy cell retains
its baseline, engine and method full-finishing/full-checker measurements,
including failures; the engine's native small-map time floors are preserved.

The local runner calls the same public checker directly because lab/score.py
correctly rejects freshly generated input paths outside its approved manifest.
No approval-path bypass or change to score.py is made.

## Results and assumptions

The cohort selection rule was fixed before solver outcomes;
all retries, demand conditioning and any source-witness conditioning must be
recorded in the cohort manifest. Occupancy means occupied physical 20-foot cells
divided by decision-region cells, not fraction of load-list boxes.

## Frozen cohort

Six TRAIN seeds, 331101000–331101005, were assigned in advance in
(tiny, small, medium) × (30%, 50%) order. There is one map per cell, so these are
structural diagnostics, not population estimates. The native simulated generator
uses dense standard dry20 with no DG. Tiny uses its native empty-box option;
small and medium retain native loaded/empty masses. About 70% of physical cells
are represented by total arriving-plus-new cargo, and the lightest generated
boxes are selected aboard. Within each arrival stack heavier cargo is lower.
One hold box becomes due now below retained late-port cargo. This deliberately
requires real current restows and can make retaining every original prefix
unattractive for heavier new loads. Cargo attributes, occupancy, seeds,
transformations, input hashes and native generator reports are recorded in
`cohort/MANIFEST.json`; physical arrival checks are in
`results/cohort_validation.json`.

The attempted all-empty medium transformation failed the vessel builder's
background balancing LP before any small/medium solver outcome. Native masses
were therefore retained, with the same six predeclared seeds. That generation
failure and correction are recorded in `results/generation_decisions.json`.
No map was selected or dropped based on solver success.

| Size | Physical cells | Onboard, 30% / 50% | New loads, 30% / 50% | Actual occupancy |
| --- | ---: | ---: | ---: | --- |
| tiny | 6 | 2 / 3 | 2 / 1 | 33.3% / 50.0% |
| small | 68 | 20 / 34 | 28 / 14 | 29.4% / 50.0% |
| medium | 240 | 72 / 120 | 96 / 48 | 30.0% / 50.0% |

Concrete graph examples: the small 30% map has 28 new-box vertices, 20 arrival
vertices, 16 physical stack pages and two covers. Arrival mass is 160,714 kg.
The medium 30% map has 96 new-box vertices, 72 arrival vertices, 64 stack pages
and six covers; arrival mass is 514,484 kg. In each, the current discharge is a
source for upward/cover clearance dependencies. A current restow is a real
shift or off/on move; future restows follow the checker's D15 closure and use
its configured lifts-per-box. Cover costs are charged once per opening, not
once per cargo box. Arrival blockers participate even when no new deck box is
assigned above the cover.

## Bound measurements

All 19 bound tests passed, including exhaustive physical/static-rule oracle
comparisons in both rule-freeze modes. `bound-results/TABLE.md` reports
certified/counting bounds of 5/5, 6/6, 13/10, 19/16, 11/11 and 15/15 in cohort
order (15.052 seconds total). Only the tiny relaxations reached optimality;
small/medium rows have unknown status at short limits. Small dual bounds still
improve by three. Medium setup/presolve consumes the five-second budget, so
no medium strengthening is claimed. No relaxation witness counts as a pass.

## Verification and setup corrections

All 98 frozen engine/dependency copies were rehashed against their source-copy
manifest with zero mismatches. Their contents remain byte-identical. The
runner inserts the repository root explicitly, since the copied blocked-plan
adapter imports `experiments.mvp.strategy`. Initial missing-root setup errors
are preserved separately and excluded from the final comparison. The runner
records input, method and snapshot hashes and checks them before resuming.

The native 0.9.0 tiny layover runs expose a real action-domain assumption:
`Discharge` has no `.slot`, but its planner reads that field while handling a
finished layover plan. These are failed entry-point runs, not infeasibility
certificates. A missing returned plan makes the complete-replay gate
**unavailable**, not evidence that no complete stow existed internally.

## What the unmodified engine does with arrival cargo

Both copied page entry points (`engine_copy/engine/scout/method.py` and
`scout/layout.py`) explicitly return no candidates when onboard placements
exist. The copied exact hatch/restow and aggregate layout bounds also refuse
arrival cargo and retain counting behavior, so empty-ship certificates cannot
be applied to the inherited stow. The guarded cached-mask path falls back to
native behavior on nonempty arrivals, as its own engine notes promise.

The engine routes by **new load-list length**, not total onboard-plus-new
boxes: medium 30% has 96 new loads and uses the large route; medium 50% has
48 and uses the small route despite 168 total cargo boxes. The true-default
large route tried native column fallback after rejecting the empty-arrival
page model; on medium 30% it produced zero complete replay-legal stows.
Failure under these bounded constructors is not an infeasibility proof.

A zero-additive-budget engine run was initially recorded in `results/run1`.
That setting preserves small-map native floors but disables the large map's
construction budget. It is an **ablation only**, excluded from the primary
comparison. `results/engine_defaults.py` repeats all six maps with
`budget_s=None`: 120 added seconds for <=60 new loads and 375 for larger
ones, retaining the default switches, native floors and a 300-second finishing
allowance. Primary engine rows are `results/engine-defaults/engine/`.

The cohort is a thin-wrapper Stage B snapshot at the original call index,
not an advance through a fully simulated preceding voyage. Generated POL and
cargo provenance remain intact while selected PODs become current/late.
Historical arrival feasibility is not certified by the wrapper; the original
Instance schema, physical prefix invariants and complete finished-plan checker
are retained. The light-arrival/heavier-new-load conditioning is deliberate:
it exposes the choice to move several prefixes rather than quietly assuming
every inherited stack can accept new cargo.

A second native engine action-domain issue is visible in
`engine_copy/engine/planner.py:retain`: it compares the box IDs of **every**
action with only `instance.load_list`. A current `Discharge` or a Place/RestowOff
of an already-aboard box introduces an extra ID and the start is discarded
from the archive. The tiny engine runs still return a finished fallback, but
record `complete_replay_legal=false`, `replay_legal_stows=0` and an empty archive
even though independent replay/checking passes. Those native metadata fields
are therefore not reliable Stage B gates. The custom runner gates returned
plans itself; no returned plan means the engine's internal complete-replay
gate remains unknown. The snapshot remains unmodified for the comparison.
The new runner validates new loads exactly once while allowing explicit
current discharge and through-box restow actions.


## Final full-checker results and verdict

The complete 18-row size/occupancy comparison, lift and arrival-restow parts,
wall times, settings and failure gates are in [TABLE.md](TABLE.md), with exact
joined rows in `results/comparison.json`. Every selected map was run; no failure
was dropped. Primary engine rows use true default added budgets, not the
zero-extra diagnostic. There is no paired lift estimate against 0.9.0 because
it returned no passing plans.

| Size (two maps each) | 0.9.0 passes | Engine passes | Page replay-complete / passes | Paired page / engine lifts | Mean wall, 0.9 / engine / pages |
| --- | ---: | ---: | ---: | --- | --- |
| tiny | 0/2 | 2/2 | 2/2 / 2/2 | 6.5 / 8.0, two pairs | 17.03 / 10.74 / 8.34 s |
| small | 0/2 | 0/2 | 1/2 / 0/2 | no mutual passes | 17.32 / 28.53 / 204.09 s |
| medium | 0/2 | 0/2 | 2/2 / 0/2 | no mutual passes | 56.20 / 98.89 / 415.43 s |

At 30%/50% occupancy the tiny page plans cost 5/8 lifts, against 8/8 for the
engine. Arrival restow lifts now are 1/4 against 2/4; projected arrival restows
are zero for all four passed plans. Tiny 30% matches the valid 5-lift lower
bound, proving its D11 optimum under the checker's stated D15 model;
`results/optimality.json` pins the identical input hash and proof evidence.

All three larger replay-legal candidates timed out in the unchanged native V4
ballast finisher. Their replay scores (149, 357 and 396) are diagnostics, not
passed objectives or certified upper bounds. Small 30% produced no complete
candidate. No larger-map infeasibility is proved. The old mixed/special layover
fixture also remains outside the conservative pure-page constructor.

The requested 90-second construction budget was exceeded on medium 30%/50%:
109.11/95.00 seconds. Atomic model setup/pricing is a budget-management failure;
these runs support no 90-second speed claim. Outer finishing took
304.67/316.59 seconds on medium and 330.95 on small 50%, including setup/checks
around the native 300-second worker limits. One auxiliary parallel helper was
interrupted after a missing main guard made a spawned worker re-enter it;
it wrote no measurement. Its guard was corrected, and the original benchmark
worker supplied both final medium rows. See `results/auxiliary-worker.json`.

Verification: 29 tests pass (19 bound, ten page); all four primary returned
plans were independently reloaded, replayed and fully checked in fresh Python
processes. Input hashes, exact lift counts, all new loads, every through box
and removal of all due boxes match; all four have zero violations.
`results/audit-final.json` joins those audits. `results/verification.json`
records the exact test command and source hashes. All ten local root Python
files still match the tested hashes, and all 98 engine/dependency copies still
match their copied-byte manifest. No shared source, params, sealed data,
checker or official score-runner file was changed.

**Verdict: keep part.** Keep fixed-prefix/residual-capacity pages, explicit
arrival cover dependencies, the tiny direct-shift lane, and the valid arrival
bounds (small improvements 10→13 and 16→19). Do not claim a general layover
solver or larger-map pass from replay alone. Next fix the native discharge/
through-action archive and search assumptions, enforce construction deadlines
around model setup, and make prefix-restow selection and mixed-page securing
aware of the ballast conditions before integration.

Reproduce primary method rows from repository root (one process):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 PYTHONDONTWRITEBYTECODE=1 \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c3-layover/runner.py \
  --methods layover --budget-s 90 --finalize-s 300 --tries 3 \
  --out results/final-method
```

Use `cohort.py` to regenerate the fixed seeds, `runner.py --methods frontier09`
for the unchanged 0.9.0 entry point, and `results/engine_defaults.py` for the
true-default engine. `results/report.py` rebuilds the final table without any
solver work. Entry points are `method.py:propose` and `method.py:finish`;
the certified bound is `bound.py:lower_bound`.
