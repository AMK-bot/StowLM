# Certified hatch and projected-restow packing

This component copied `c1/fx-exact/bound.py`, `measure_bounds.py` and
`test_bound.py`, then extended the model here. `bound.py:lower_bound` retains
its original interface. `bound_measure.py` reads only catalog-approved sets.
Every write is inside `c2/c2-hatch`; no sealed data was opened. This component
constructs lower bounds and optimistic witnesses, not full-checker plans.
The root executive owns fresh scorecard results and the overall final table.

## Graph and counting model

A box chooses one eligible physical footprint. Exclusion prevents two boxes
sharing a cell; occupied cells form prefixes of physical ship columns. Hold
covers and their first subsequent POD calls have Boolean usage variables.
A used closed cover costs two current lifts and a used hold cover at a future
POD call costs two projected lifts. Initially open covers retain the original
one-lift departure closure accounting.

For each later call, make a vertex for every box that has not yet discharged.
Discharge targets start in a required set. Opening a target's hold cover adds
all active boxes on that cover's deck columns. An upward edge in either
physical column of a required box adds its upper blocker. A spanning long box
connects both columns, so the closure can propagate sideways and upward. Each
non-target box in the closure costs the configured number of future restow
lifts, once per call even if several targets/covers require it. First matching
subsequent calls, including repeated POD visits, define active cargo exactly as
in the unchanged `moves._projection.project`.

The CP model encodes this closure by implications. `required[box,call]` is true
for targets. A deck placement and used cover imply its box is required. Each
column has a monotone `below` flag; a required chosen box sets that flag for
higher tiers, and a higher active chosen box above a set flag is required.
Every actual D15 closure satisfies these implications. Conversely, the least
solution is that closure. Positive restow coefficients select the least-cost
closure; zero coefficients can leave harmless extra required flags. Frozen
crane-crossing additions remain relaxed, never charged speculatively.

## Additional necessary packing conditions

Legacy E1 heavy-bottom, E2 class order, no-20-over-long and top/overheight
closure conditions hold in each physical column. All lower/upper pairs are
used for E2 because adjacent rank inequalities alone permit accumulated drift
when tolerance is nonzero. These cuts are disabled with rule-freeze-v1, which
uses different weight rules. Existing conservative E6 bottom-slot witness cuts
are retained unchanged; arbitrary heavy-deck rejection would be unsound.

The full checker's `checker.lashing._load_graph` unconditionally raises for a
mixed 20/long hold support chain. This is a hard securing failure, with no
special-consideration override. With prefix occupancy, any two length classes
in a hold column necessarily create such an adjacency. A single column-type
Boolean therefore forbids mixed 20/long hold occupancy under both rule modes.
The same restriction is independently reported by
`solver.securing_geometry.securing_geometry_violation`. Forty and forty-five
foot boxes share the long class. This cut improved one map's lower bound by
another two lifts.

Physical stack demands round downward and capacities outward. Objective
coefficients round downward to 0.001. The returned certificate always uses
CP-SAT's **best objective bound**, never the incumbent objective. The existing
counting bound and mandatory non-load-yard blocker bound remain valid fallbacks.
`hatch_lower_bound` is a legacy field name: in Cycle 2 it denotes the combined
hatch-plus-projected-restow lower bound. `witness_hatch` and `witness_restow`
separate a feasible relaxation incumbent's two terms.

## Measured lower bounds

Final source hash and all per-map JSON artifacts are recorded in
`results/bounds/final-hold/SUMMARY.json`. Seed 0, 60-second per-map maximum,
one CP-SAT worker, one numerical thread, `nice -n 5` requested. All ten solves
proved their relaxation optimal in **29.93 seconds total**. No solve needed
its time cap. This supersedes `final/`, which precedes the hold homogeneity cut.

| quick_small map | Stored checked upper | Certified lower |
| --- | ---: | ---: |
| random.small.40100011.0.8 | 22 | 22 |
| random.small.40100023.0.8 | 22 | 22 |
| random.small.40100036.0.2 | 11 | 9 |
| random.small.41100002.0.2 | 12 | 12 |
| random.small.41100018.0.2 | 10 | 10 |
| sim.small.40100010 | 4 | 4 |
| sim.small.40100025 | 26 | 8 |
| sim.small.40100034 | unavailable | 6 |
| sim.small.40100046 | 9 | 8 |
| sim.small.41100007 | 9 | 8 |

On the nine stored passing pairs the lower-bound mean is **11.444444**, up from
Cycle 1's 10.777778, while stored upper mean is 13.888889. Five stored upper
bounds attain the new lower bounds. The known Cycle 1 checked eight-lift plan
on sim.small.41100007 would make six equalities, subject to the root's fresh
checker gate. The root's fresh plans may further reduce gaps; this component
makes no separate fresh-plan/pass claim.

For **sim.small.40100025**, the final relaxation still admits an eight-lift,
zero-restow assignment, with homogeneous hold columns and necessary E1/E2/E6
cuts. Its stored sixteen future restow lifts therefore are not forced by
this packing/closure relaxation. That does not prove a legal zero-restow stow
exists: omitted mask, DG and securing/stability interactions may prevent it.
The witness is supplied for repair, not described as a real optimal plan.

## Verification and reproduction

Thirty tests passed: sixteen inherited exhaustive move-engine/oracle cases,
twelve fixed mixed-POD deck/hold cases compared with the real future projection
(including zero and fractional weights), and two spanning-box propagation and
duplicate-blocker cases. In the latter, an early left-column target requires a
long left/middle blocker, which in turn requires a middle/right blocker that
does not overlap the target's column. The second case makes both lower boxes
targets and verifies the shared blockers are charged once. Two further tests
compare the homogeneous hold cut with the real checker securing geometry under
both freeze modes; the root owns their final invocation after CPU release.

Run from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c2/c2-hatch \
nice -n 5 .venv/bin/python -m pytest \
-o cache_dir=experiments/mvp/frontier/lab/c2/c2-hatch/.pytest_cache \
experiments/mvp/frontier/lab/c2/c2-hatch/bound_tests.py -q

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c2/c2-hatch \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c2/c2-hatch/bound_measure.py \
--set quick_small --budget-s 60 \
--out experiments/mvp/frontier/lab/c2/c2-hatch/results/bounds/final-hold
```

The environment reports `setpriority: Operation not permitted`, then continues
running. No process was killed. No run exceeded twenty minutes.

## Sources and scope

The closure and new inequalities are direct derivations from unchanged local
checker and move-projection semantics. No downloaded implementation, Benders
cuts, or unsupported pairwise restow edge sum is used. The inherited
branch-and-bound/lower-bound distinction follows Land and Doig (1960),
[An Automatic Method of Solving Discrete Programming Problems](https://doi.org/10.2307/1910129),
and Bergman, Cire, van Hoeve and Hooker (2016),
[Discrete Optimization with Decision Diagrams](https://pubsonline.informs.org/doi/abs/10.1287/ijoc.2015.0648).
These references motivate certificate roles; the model does not claim to
reimplement their algorithms. Production masks, rules and full checker are
imported unchanged. Only the root's `lab/score.py` results establish passes.

## Additional negative first-aboard-order trial

`bound_joint.py` separately adds distinct first-aboard ranks. An export box
initially above another export must either board first or incur a yard
relocation. A final upper/deck box must either board after every relevant
final lower/hold box or incur at least one current ship-restow lift. A box
that never restows must retain its first-aboard placement, which proves the
latter implication even with arbitrary current shifts/off-on restows allowed.
Unique per-box flags count these costs once, and these export yard moves and
current ship restows are disjoint from the inherited non-export yard bound.

This joint model still proves only 8, 8 and 6 on sim.small.40100025,
sim.small.40100046 and sim.small.40100034 respectively, in 3.262, 1.319 and
0.398 seconds. All three witnesses pay zero new order cost. Four dedicated
order-cycle enumeration tests pass, including zero/fractional weights; all
sixteen inherited oracle cases also pass against the joint model. The main
32-test bound suite passes unchanged. See `bound_joint_NOTES.md` for the
complete proof, artifacts, and limits. No main-table bound changed.
