# Exact bounds and bounded local repair

All files and measurements in this experiment stay under `lab/c1/fx-exact/`.
Inputs come from the shared catalog's fixed validation/TRAIN `quick` selection.
No sealed map, customer data, downloaded code, or source outside this folder is
used or modified. Read `lab/CONTRACT.md` first and `lab/README.md` when available.

## Mathematical objects and actual costs

`graph.py` models boxes choosing geometric ship slots, with physical cells,
support stacks, initial yard stacks and hatch covers. Its two fixed-map examples
and limitations are in [STRUCTURE.md](STRUCTURE.md). The first small map has 18
loads and a complete geometric box conflict graph K18; the first medium has 88
and K88. Their physical incidence min-fill width upper bounds are 20 and 64.
Those are graph diagnostics, not lift bounds. Unreformulated global physics also
couples all box decisions. Plain box-assignment tree-decomposition DP therefore
does not presently offer a small-width route to whole-map exact optimization.

The optimization object is a state transition graph, not a graph edge count.
Current yard relocations, ship restows and cover off/on each have the instance's
corresponding lift weight. Productive loading is free in this objective. Later
restows and cover lifts use the real move engine's D15 projection: no future
loads and surviving cargo retaining its departure coordinates. A lower bound
here is a bound on that objective, not a minimum over arbitrary future policies.

## Global lower bound: optimistic hatch packing

`bound.py:lower_bound` assigns every load box to one production-static-compatible
slot, excludes overlapping physical cells, and enforces bottom-up support plus
outward-rounded stack mass/height envelopes. A used closed hold cover costs two
current lifts; an initially open cover costs one departure closure. Each used
(cover, first subsequent POD call) pair costs two later lifts. Restows and
load-order restrictions are relaxed. The existing lower bound on non-load yard
blockers is added because relocation and cover lifts are disjoint costs.

CP-SAT's **best objective bound**, rather than its feasible objective, supplies
the certificate on timeout. Objective coefficients round down; physical demands
round down and capacities up. The existing counting bound is retained if it is
stronger. Empty arrival and no joint forecast are required for the packing model;
other instances retain the existing counting bound. An optimal relaxation is
not a globally optimal full-rule plan. Equality with a full-checker-passing
same-domain upper bound is necessary to claim a full-objective optimum.

Legacy E6 is subtle: heavy deck cargo is permitted when all compatible empty
hold opportunities are disabled. Simply forcing every heavy box below deck is
unsound. Any E6 strengthening in `bound.py` must preserve this fallback and is
restricted to independently justified ordinary bottom-slot witnesses. Source
comments and tests state the precise guard conditions.

## Exact restricted repair and its bound

`method.py:solve_neighborhood` represents a tree with one level per incumbent
slot access. At selected levels it chooses an unused same-length box from one
hatch or one original yard stack. Every occupied slot and the slot-access
schedule stay fixed; other boxes retain both their slot and action position.
The original `length_ft` classes remain separate, including 40 versus 45 feet.
The default expander determines actual yard relocations. Thus this is a precise
restricted neighborhood, not unrestricted reoptimization of every slot beneath
a hatch. It reuses the same exact enumeration to produce a plan and a certificate.

An arc costs its emitted current weighted nonproductive lifts. Every pending
prefix lower-bounds every continuation because remaining current and projected
lifts are nonnegative. Branch-and-bound prunes against the best full replay
score. Exhaustion certifies the restricted optimum. On timeout the cheapest
pending prefix (capped by the incumbent) remains a valid restricted lower bound.
Prefix arithmetic uses Python integers within the binary64 exact-integer range.
For nonnegative fractional weights, pruning is disabled and the timeout bound is
zero; exhaustive enumeration still evaluates the real replay objective. Negative
or nonfinite weights receive no timeout certificate. No compressed decision
diagram, state merging or full-domain proof is claimed.
The incumbent is retained, including as the fifth fallback after four proposed
candidates. Atomic engine/replay calls may overrun a wall deadline by one call.

`finish.py` keeps the incumbent ballast and returns the stored full plan verbatim
if cargo actions match. Changed cargo plans receive that ballast and enter the
shared runner's **full** `stowage.checker.check`. This is a fixed-ballast cargo
repair experiment; neither replay scores nor neighborhood proofs establish
regulatory feasibility. Missing stored medium incumbents produce no candidates.

## Sources and what was used

- Holger Dell et al. (2017), [The PACE 2017 Parameterized Algorithms and
  Computational Experiments Challenge: The Second Iteration](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.IPEC.2017.30),
  and [official challenge](https://pacechallenge.org/2017/): practical treewidth
  and minimum-fill benchmarking; our min-fill routine is written here.
- David Bergman, Andre A. Cire, Willem-Jan van Hoeve and J. N. Hooker (2016),
  [Discrete Optimization with Decision Diagrams](https://pubsonline.informs.org/doi/abs/10.1287/ijoc.2015.0648),
  [author-hosted paper](https://www.cs.cmu.edu/afs/cs/academic/class/15780-s16/www/BergmanEtAL.ijoc16.pdf):
  exact/restricted/relaxed state graphs and their separate proof roles. We use
  that distinction; no compressed diagram implementation is claimed.
- A. H. Land and A. G. Doig (1960), [An Automatic Method of Solving Discrete
  Programming Problems](https://doi.org/10.2307/1910129): branch-and-bound
  reference. Our prefix-cost bound does not reproduce their LP algorithm.
- Existing `src/stowage/bounds` counting proofs and `bruteforce.py` were read
  first. Their engine, lower-bound component functions, static eligibility
  tables and the unchanged `alns.Problem` are imported, not copied or modified.

## Verification and measurements

Final measured results and exact commands are appended below after completion.
The shared runner independently checks every reported pass. Stored upper bounds
are explicitly labelled as stored evidence when a fresh full check is not used.
All numerical processes use one thread; the team runs at most two busy processes.
`nice -n 5` was requested on jobs; this managed environment prints
`setpriority: Operation not permitted` and continues running the command.
No process is killed or stopped by pattern.

The single `quick` scorecard run may exceed 20 minutes: its purpose is the
contract-required fresh full-checker measurement on all 21 fixed maps, and the
first two checks alone took approximately 69 and 59 seconds despite reusing
finished ballast. Search is limited to 12 seconds per map, no new medium
construction is attempted, and the original passing plan remains a fallback.
This is the stated reason for allowing that run to finish beyond 20 minutes.

Reproduction from the repository root (prefix each numerical command with
`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1` and
`PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c1/fx-exact`):

```sh
.venv/bin/python -m pytest experiments/mvp/frontier/lab/c1/fx-exact/test_bound.py experiments/mvp/frontier/lab/c1/fx-exact/test_method.py -q
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c1/fx-exact/measure_bounds.py --set quick_small --budget-s 60 --out experiments/mvp/frontier/lab/c1/fx-exact/results/bounds-small-final
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py --set quick --method experiments/mvp/frontier/lab/c1/fx-exact/method.py:propose --finish experiments/mvp/frontier/lab/c1/fx-exact/finish.py:finish --budget-s 12 --finalize-s 60 --tries 5 --workers 1 --out experiments/mvp/frontier/lab/c1/fx-exact/results/quick-repair
.venv/bin/python experiments/mvp/frontier/lab/c1/fx-exact/measure_structure.py
.venv/bin/python experiments/mvp/frontier/lab/c1/fx-exact/report.py
```

The initial bound run used an E6-witness generation cap of 60% of its total
budget; the final source uses 30% to leave CP-SAT search time. The final bound
run uses 60 seconds and unchanged source throughout. Its rows supersede both
30-second pilot folders, which illustrate that stopping witness generation
earlier can weaken the bound even when CP-SAT solves that smaller relaxation.

The scorecard was already running while review corrected only certificate
scope/arithmetic and added import-time source hashes. Its launch configuration
hash therefore differs from later imports. Per-map `actual_method_sha256`, when
present, identifies the loaded implementation. Every selected map has unit
weights, checked again by `report.py`; the fractional arithmetic fixes do not
change their branching/pruning behavior. Every output plan still goes through
the unchanged full checker. A strict source-frozen timing replication should use
the final command above in a new output directory.

### Final measured results

The 60-second bound run finished all ten `quick_small` relaxations to optimality
in **90.34 seconds total**. The nine stored passing upper bounds average
**13.89 lifts**; their lower bounds average **10.78**. Three are equalities and
one further map is certified within 10% of optimal. This does **not** establish
that the whole small-map cohort is within 10%: the other gaps remain material.

| quick_small map | Stored/fresh checked lifts | Lower bound | Maximum excess over optimum |
| --- | ---: | ---: | ---: |
| random.small.40100011.0.8 | 22 | 22 | **0%, proven optimal** |
| random.small.40100023.0.8 | 22 | 20 | 10.0% |
| random.small.40100036.0.2 | 11 | 9 | 22.2% |
| random.small.41100002.0.2 | 12 | 8 | 50.0% |
| random.small.41100018.0.2 | 10 | 10 | **0%, proven optimal** |
| sim.small.40100010 | 4 | 4 | **0%, proven optimal** |
| sim.small.40100025 | 26 | 8 | 225.0% |
| sim.small.40100034 | no passing incumbent | 6 | unknown |
| sim.small.40100046 | 9 | 8 | 12.5% |
| sim.small.41100007 | 9 | 8 | 12.5% |

The excess column is `(upper/lower)-1`, a conservative ceiling, not a measured
improvement opportunity. The 26-lift map includes **16 projected restow lifts**;
the relaxation makes restows free, explaining an important source of looseness.
The first small map instead has 14 unavoidable yard relocations plus eight
certified hatch lifts. This verifies that dominant costs differ across maps.

Full `quick` repair measurement through `lab/score.py`:

| Size | Passes vs stored 0.9.0 | Paired mean lifts, repair / stored | Mean total time per selected map |
| --- | --- | --- | ---: |
| Tiny | 8/8 vs 8/8 | 6.375 / 6.375 (8 pairs) | 53.85 s |
| Small | 9/10 vs 9/10 | 13.889 / 13.889 (9 pairs) | 48.48 s |
| Medium | 0/3; 0.9.0 comparator unavailable | no pairs | 0.78 s |

All **17 retained plans passed the unchanged full checker freshly**. The three
small equalities therefore have fresh full-domain objective certificates; see
`results/CERTIFICATES.json`. No pass or lift improvement was obtained. The medium
result is a missing-incumbent limitation, not an attempted construction or a
proof of infeasibility. Across small maps, **11 of 31** attempted restricted
neighborhoods were exhausted; the others retain explicit timeout bounds and
must not be called locally optimal. The sum of all scorecard map times was
**917.93 seconds**; these times include full checks and reuse stored cargo and
ballast, so they are not a cold-planning speed comparison against 0.9.0.

Verification: **24 tests pass**, comprising 16 lower-bound tests and eight repair
tests. These include exhaustive move-engine oracle comparisons, heavy E6 static
oracle comparisons, both rule-freeze activation settings, initially open covers,
fractional/zero weights, lateral OOG, exhaustive independent permutation checks,
timeout certificates, engine cleanup, frozen positions and incumbent retention.
Graph min-fill was independently checked on known graph families and 150 random
graphs. `results/STRUCTURE.json` and the two `*-incidence.dot` files record the
real small/medium graph models.

**Verdict: keep the bounds and exact restricted-repair interface; no demonstrated
lift advantage from this repair neighborhood.** Stop optimizing the three proven
maps. Whole-map treewidth DP is unattractive in the tested variable formulation.
Next try a tighter state bound on projected restows for the 26-lift map and larger
slot neighborhoods. Partial D15 projection appears monotone when suffix actions
never evict a prefix placement, but that proposed pruning rule is **not enabled
or measured** here; exhaustively test its admissibility before using it.
