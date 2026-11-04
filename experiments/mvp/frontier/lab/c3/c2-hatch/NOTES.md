# Cycle 3: exact small-map certificates

All writes stay in this folder. Read the complete lab contract including Cycle
2 and Cycle 3 updates. While `lab/c1/leader/BENCH-RUNNING` exists, the entire
team runs at most **one CPU-busy process**, one numerical thread; all numerical
jobs use `nice -n 5` and OMP_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1,
VECLIB_MAXIMUM_THREADS=1. Read-only logic review runs independently. No sealed
data, GPU, cloud, process-pattern kills, commits or pushes.

## Predeclared evaluation boundary

Only the fixed quick maps are used to develop or tune constraints. The leader's
`engine/bench/val_small.txt` will be opened only after the algorithm and budgets
are frozen. That benchmark is used for proof counts and timing only. Its outcomes
will not be used to change constraints, heuristics, budgets or candidate logic.

The method copies Cycle 2's optimistic packing/restow model and extends it with
necessary geometry and completion rules. Engine modules are changing, so none
are imported: the 21 checked `quick-0.10.0` rows are copied into
`references/engine-quick-0.10.0/` as immutable upper-bound evidence. Source and
input digests will be recorded. Other source/checker code is imported unchanged.

## Mathematical object and proof scope

A box chooses one compatible ship footprint, physical cells exclude overlap,
and each used hold-cover/discharge-port pair pays a fixed charge. At each later
port, upward closure from discharge targets and required hatch clearing charges
each surviving blocker once. Necessary heat, E6, OOG and support conditions
strengthen the packing relaxation. First-aboard ranks can lower-bound current
yard and ship-restow costs without fixing a particular topological order.

As before, the first real small model has 18 loads and 14 unavoidable non-export
yard relocations; the first medium model has 88 loads and the same cover/port
incidence construction. The medium model is illustrative, not a new medium
solution. D15 is the checker's fixed-coordinate, no-future-load projection;
certificates do not establish an optimum over arbitrary executable future-port
policies. Each emitted certificate will name its additional domain assumptions
and whether it covers arbitrary legal current moves or a load-once family.

## Sources

- Roberto Roberti, Enrico Bartolini and Aristide Mingozzi (2015, online 2014),
  [The Fixed Charge Transportation Problem](https://doi.org/10.1287/mnsc.2014.1947):
  fixed activation costs motivate the cover/port incidence objective; this is
  not a reproduction of their branch-and-price solver.
- J. N. Hooker and G. Ottosson (2003),
  [Logic-based Benders decomposition](https://doi.org/10.1007/s10107-003-0375-9):
  final-state necessary-rule checks can justify cuts; a mask or replay rejection
  alone is not a globally valid assignment cut because another order may work.
- A. H. Land and A. G. Doig (1960),
  [An Automatic Method of Solving Discrete Programming Problems](https://doi.org/10.2307/1910129):
  certificate roles in minimization branch-and-bound. A feasible relaxation
  objective is not a lower bound; use the solver's best objective bound.

Measurements, family audit, tests and final tables follow after implementation.

## Development outcomes on quick only

The declared supported-floor interval proves `40100034`'s heat-booked box cannot
use ten of its former box/slot choices, raising the bound from six to eight.
OOG cuts use the checker's horizontal proximity plus vertical-interior rule;
heat cuts use its Euclidean clearance. Both use outward-rounded expressions for
the *actual regular heights below*, preserving alternate supporting cargo.
E6 cuts preserve a complete compatible-opportunity context, including empty
cells and OOG-support columns, rather than assuming every heavy box goes below.

First-aboard ranks plus these necessary final-state rules raised `40100046`'s
lower bound to nine, equal to its checked upper. `40100025` initially remained
at eight with no yard-relocation or current-restow flags. Its ordered witness
replayed at eight, but the independent full checker rejected securing.
The unchanged Cycle 1 ballast-independent securing oracle proved that exact
four-long-box deck stack has rod ratio at least **1.2224030362** under *every*
ballast setting. It is not a whole-map infeasibility proof.

The oracle is copied privately as `bound_securing.py`, retaining its lashing
source hash and numerical guards. The master may exclude a proven complete
assignment, or a complete pure-long column component with every occupied and
empty cell fixed. Other cargo cannot affect this grounded rod chain; the
acceleration floor depends on the fixed hull. No full-checker rejection at a
single ballast setting becomes a global cut. See `proof_scope.md` for the
independent read-only audit and exact guards.

One early 60-second threshold trial on 40100034 was ineffective: it restarted
the same unchanged CP model with the same seed after each five-second UNKNOWN,
discarding internal search progress. Its log records twelve UNKNOWN solves,
not a sustained 60-second feasibility proof. Development review caught this
before validation. The final loop permits a sustained remaining-budget solve
when no witness has been found and retains an independent cheap packing bound.
No conclusion is drawn from the failed restart policy.

After excluding that certified bad stack pattern, the next eight-lift witness
passed the full checker freshly through **`lab/score.py`** in **11.35 seconds**:
`results/pilot25b/sim.small.40100025.json`. It uses eight hatch lifts, no yard
relocations and no current or future restows. It improves the engine's nine
and matches the eight-lift lower bound. The successful load order comes from
the solver's first-aboard ranks, rather than a separate heuristic sequencer.

## Explicit proof family

See `proof_scope.md` for the audit. Certificates cover **all checker-legal
complete current-call MovePlans**, including arbitrary current yard relocation,
staging, direct ship shifts and quay off/on restows. There is **no load-once
restriction on the lower-bound family**. The returned eight-lift witness happens
to load each export once. The joint relaxation charges at most one necessary
yard relocation and one necessary ship-restow lift per export, so it remains
optimistic when actual plans move a box repeatedly.

The guards are empty **decision-cargo** arrival, no current discharge list,
no jointly planned later calls or forecasts, nonnegative declared objective
weights, prescribed reciprocal decision-cell catalogs disjoint from immutable
fixed background, and the recorded rule activation/source versions. Fixed
background need not be empty: hundreds of background containers remain in these
small instances and participate in the securing graph. Fixed cells are not
selectable decision destinations in the move/checker contract.

Future costs use **D11 with D15's first-matching-POD, no-future-load,
fixed-survivor-coordinate projection**. Proofs do not establish the minimum
of arbitrary executable future-port policies. Legacy E6 is a completed departure
rule, so temporary hold blocking cannot evade it. Under activation it is soft
and corresponding hard cuts are disabled.

To remove a generality flaw in the inherited static domain, DG rows are rebuilt
optimistically rather than retaining empty-ship geometry false negatives.
An unsupported upper box can become geometrically legal once lower boxes are
present. This is a domain issue; relabelling a result “load-once” would not fix it.

## Frozen evaluation plan

After quick-only development and tests, freeze all algorithm modules and run:
quick tiny with 10 seconds per map, quick small with 60 seconds per map, then
the leader's previously unopened `engine/bench/val_small.txt` with **15 seconds
per map**, seed zero. Validation outcomes are proof-count/timing evaluation
only; no algorithm or budget changes follow them. Every claimed upper bound
will also be freshly admitted by the unchanged `lab/score.py`, one worker while
the leader benchmark marker exists. Unknown or failed full checks are not proofs.

If the fixed validation scorecard exceeds twenty minutes, let it finish because
the cohort and checker gate were declared before observing outcomes; dropping
slow failed checks would bias the proof count. This is the reason for that run,
not permission for unbounded per-map tuning. Bound budgets are cooperative and
may overrun by one atomic model/checker call; timing records retain such costs.

## Final quick certificates and admission

The algorithm was frozen at **2026-11-01 14:01:19 UTC**, before the validation
list was opened at **14:02:17 UTC**. `FROZEN.json` records all eight algorithm
module hashes, budgets and seed. `references/VALIDATION_MANIFEST.json` records
the copied list hash: 45 maps, zero overlap with `quick_small`. No algorithm,
heuristic, budget or candidate changes follow that first read.

The final frozen runs prove **8/8 quick tiny** in **2.589 seconds** of bound
work (4.564 seconds evaluator elapsed) and **10/10 quick small** in **19.444
seconds** (21.841 seconds evaluator elapsed). Each bound equals a separately
freshly full-checked upper; both mean gaps are zero.

| quick_small map | Fresh checked upper | Lower | Gap | Proved |
| --- | ---: | ---: | ---: | :---: |
| random.small.40100011.0.8 | 22 | 22 | 0 | yes |
| random.small.40100023.0.8 | 22 | 22 | 0 | yes |
| random.small.40100036.0.2 | 9 | 9 | 0 | yes |
| random.small.41100002.0.2 | 12 | 12 | 0 | yes |
| random.small.41100018.0.2 | 10 | 10 | 0 | yes |
| sim.small.40100010 | 4 | 4 | 0 | yes |
| sim.small.40100025 | **8** | **8** | 0 | yes |
| sim.small.40100034 | **10** | **10** | 0 | yes |
| sim.small.40100046 | **9** | **9** | 0 | yes |
| sim.small.41100007 | 8 | 8 | 0 | yes |

The fresh `lab/score.py --set quick` gate has tiny **8/8**, small **10/10**
and inherited medium **2/3** complete replay-legal stows and full-checker passes.
Paired small lifts are **11.555556 versus 13.888889**, 3 wins, 6 ties, 0 losses
on the nine baseline-passing maps (paired bootstrap delta interval
[-6.333333, -0.111111]). Across all ten small maps the mean is **11.4**.
Mean admission wall times are tiny **6.301 seconds**, small **7.088 seconds**
and medium **4.005 seconds**. These are replay and full-check admission times
for stored/witness plans with inherited ballast, **not cold planning times**.
The full table and proof-family/source metadata are generated in
`results/TABLE.md` and `results/CERTIFICATES.json`.

Verification: **56 model tests** (32 inherited packing, 4 inherited order,
20 strengthening) and **25 shared scorecard tests** passed. `git diff --check`
is clean. Requested `nice -n 5` is unavailable in this sandbox
(`setpriority: Operation not permitted`); every numerical invocation nevertheless
sets all three numerical thread limits to one. The marker was absent for the
two-process bound/check evaluation; it existed during earlier development,
when the whole team used only one CPU-busy process.

Reproduce the frozen proof evaluation from the repository root, with
`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`,
`PYTHONDONTWRITEBYTECODE=1` and
`PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab`:

```sh
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c2-hatch/evaluate.py --set quick_tiny --budget-s 10 --out experiments/mvp/frontier/lab/c3/c2-hatch/results/bounds/quick_tiny
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c2-hatch/evaluate.py --set quick_small --budget-s 60 --out experiments/mvp/frontier/lab/c3/c2-hatch/results/bounds/quick_small
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c3/c2-hatch/evaluate.py --set experiments/mvp/frontier/lab/c3/c2-hatch/references/val_small.txt --budget-s 15 --out experiments/mvp/frontier/lab/c3/c2-hatch/results/bounds/validation_small
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py --set experiments/mvp/frontier/lab/c3/c2-hatch/references/val_small.txt --method experiments/mvp/frontier/lab/c3/c2-hatch/method.py:propose --finish experiments/mvp/frontier/lab/c3/c2-hatch/method.py:finish --budget-s 0 --finalize-s 120 --tries 3 --workers 1 --out experiments/mvp/frontier/lab/c3/c2-hatch/results/checked-validation
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py --set quick --method experiments/mvp/frontier/lab/c3/c2-hatch/method.py:propose --finish experiments/mvp/frontier/lab/c3/c2-hatch/method.py:finish --budget-s 0 --finalize-s 120 --tries 3 --workers 1 --out experiments/mvp/frontier/lab/c3/c2-hatch/results/checked-quick
.venv/bin/python experiments/mvp/frontier/lab/c3/c2-hatch/report.py
```

## Frozen validation outcome and verdict

The previously unopened 45-map `val_small` cohort yields **32/45 optimality
certificates** with the frozen 15-second budget, **224.069 seconds** summed
bound work and **233.407 seconds** evaluator elapsed. Independent admission
through `lab/score.py` produces **42/45 complete replay-legal stows and 42/45
full-checker passes**, using **281.922 seconds** summed admission wall time.
The bound and admission jobs overlap in time; their sums are not end-to-end
elapsed time. Three maps have no supplied passing upper, and ten have a
positive remaining bound gap. No UNKNOWN solve or absence of a plan is called
infeasibility. There was no validation tuning or second solver trial.

All **50/63 certificates** (8 tiny, 10 quick small, 32 additional small) have
the same explicitly recorded family: arbitrary current legal moves, including
ship restows, under the empty-decision-arrival/no-discharge/immutable-background
guards and D11/D15 projection described above. Thus **42/55 small maps** across
the two disjoint small sets are proved. The per-map table retains unproved
cases and missing upper bounds. Validation was used only to count/time
certificates, not to choose an algorithm or rank lift performance.

The final `--set quick` run completed after validation. It reused this cycle's
fresh full-check results through the scorecard's unchanged source/input/plugin
cache keys. `results/VERIFICATION.json` checks all **63 bound rows**, all **66
admission rows**, unchanged frozen algorithm hashes, unchanged shared source
hashes, and every bound/upper/input identity. The checker/source digest is
`54c4a017ce3c51804d91695f026450334072b53a0f28c7abef4dd577115eb765`.

**Keep.** The work closes all ten quick-small maps, improves 40100025 from
the engine's nine to a checked and proved eight, and generalizes without tuning
to 32 additional proofs. The remaining validation cases are open, not disproved.
Next: have the logic reviewer verify the necessary-rule cut derivations, then
test any further construction improvements on a separately declared TRAIN set;
do not tune on this validation cohort. No cold runtime speedup is claimed.

Entry points: `certified.py:lower_bound` for bounds and
`method.py:propose` / `method.py:finish` for full-check admission. New model,
tests, copied references, proof audit, reports and all generated artifacts are
confined to `lab/c3/c2-hatch/`.
