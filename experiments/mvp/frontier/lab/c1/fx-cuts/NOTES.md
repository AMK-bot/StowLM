# Capacity-aware cuts, cycle 1

Scope: only this directory is written. The pinned Frontier, strategy rules,
checker, parameters and environment are imported unchanged. No sealed maps or
evaluation-only public cargo data are opened. Results are development evidence
on the lab's fixed validation/TRAIN sets.

## Model and assumptions

Cargo groups are vertices, physical hold/deck blocks are labels, and a cover
opening for a discharge port is an activation hyperedge. A later deck box over
an earlier hold port adds two restow lifts. Unlike a one-port-per-station rule,
groups can share a cover when capacity requires it. Supported slot choices make
the capacity model aware of indivisible long boxes, reefer compatibility,
stack height, stack mass and hold length exclusivity.

This constrained assignment is solved by CP-SAT, not described as an ordinary
minimum cut. Adjacent stack-port inversions and balance remain surrogate terms;
conservative group maximum masses can exclude feasible packings. A witness is
only a proposal. The normal placement oracle orders it; the lab V4 finisher
and unmodified full checker decide every pass.

Small-map local moves preserve occupied cells by swapping equal-length boxes.
Disjoint swap pairs are binary vertices, with unary/pairwise costs for yard
precedence, overstow and hold-port mixing. A signed-label consistency test
determines whether the energy becomes submodular after label reversals. Those
neighborhoods use an implemented Dinic minimum cut; inconsistent cases with at
most 12 variables use enumeration. Exactness applies only to that pairwise
surrogate neighborhood. Real replay and the placement oracle accept candidates.

The stored 0.9.0 assignment and load order are explicit fallback candidates.
At most one local-search candidate plus one packing candidate are added, so
three finishing tries can reach the fallback. This uses incumbent access
provided by the contract; it measures refinement, not independent construction.
Medium maps have no stored 0.9.0 assignment and use independent construction.

## Compute and verification plan

All jobs use one numerical thread, nice priority 5, at most two busy processes
across this executive's team. Constructor and small-search agents have separate
files; the theory audit uses a short single-worker structural relaxation.
The full fixed `quick` and `medium12` sets use the shared lab scorecard once
available. Budget: 60 s proposal, 60 s per finishing try, up to three tries.
If a full set exceeds 20 minutes, its reason is the mandatory complete fixed-set
comparison and independent full-checker gate; there are no extra maps/restarts
selected by outcome. Failure is recorded, never relabelled infeasibility.

## Sources and measured results

The companion [THEORY.md](THEORY.md) contains real graph examples, all structural
bounds and primary references: Kolmogorov and Zabih (2004), Boykov and Kolmogorov
(2004), Rother et al. (2007), Karypis and Kumar (1998), and Heuer et al. (2021).
The implemented flow is plain Dinic; the randomized binary-energy tests compare
its answer with exhaustive enumeration rather than relying on an implementation
copied from those papers. No downloaded code is run.

- Kolmogorov and Zabih (2004), [What Energy Functions Can Be Minimized via Graph Cuts?](https://www.cs.cornell.edu/~rdz/Papers/KZ-PAMI04.pdf): applicable binary energy/submodularity conditions.
- Boykov and Kolmogorov (2004), [An Experimental Comparison of Min-Cut/Max-Flow Algorithms for Energy Minimization in Vision](https://discovery.ucl.ac.uk/id/eprint/13383/): practical max-flow evidence; the implementation here uses Dinic rather than their specialized solver.
- Rother, Kolmogorov, Lempitsky and Szummer (2007), [Optimizing Binary MRFs via Extended Roof Duality](https://alumni.media.mit.edu/~szummer/papers/Rother-etal-QPBO-cvpr07.pdf): partial binary-energy guarantees; QPBO was considered, not implemented.
- Karypis and Kumar (1998), [Multilevel k-way Partitioning Scheme for Irregular Graphs](https://doi.org/10.1006/jpdc.1997.1404): coarsen/partition/refine principle, not a physical packing guarantee.
- Heuer, Maas and Schlag (2021), [Multilevel Hypergraph Partitioning with Vertex Weights Revisited](https://arxiv.org/abs/2102.01378): tight weighted balance and heavy vertices; no KaHyPar dependency installed.

The first `quick60` run uses the default V4 finisher and 60-second finish budget.
Its early rows re-finish even unchanged stored incumbents and can lose stored
passes to ballast timeout. This is recorded as a finishing-budget limitation,
not as evidence against the supplied cargo assignment.

A separate `incumbent_finish.py:finish` plug-in supports a refinement experiment:
identical ordered cargo uses the supplied finished incumbent; changed cargo
first tries the incumbent's tank masses through the full checker, then falls
back to V4 if that fails. The runner independently full-checks every returned
plan again. This is an explicitly different finishing treatment using solved
incumbents; its timing is not a cold-start speedup claim. Medium maps have no
such incumbent and use ordinary V4. Three tries include the retained fallback.

Every launch attempted `nice -n 5`; the sandbox's macOS priority call returned
`setpriority: Operation not permitted`. The command nevertheless continued;
the limitation is recorded, with one numerical thread and the team CPU limit
still enforced.

## Frozen pilot and corrected construction

`cut_layout.py` and `method.py` are the frozen first pilot. V1 conservatively
used the smallest of the three stack mass ratings and omitted the ban on short
boxes above long boxes on deck. Its witnesses were safe proposals because the
unchanged oracle rejected such stows, but the model wasted usable capacity.
`cut_layout_v2.py` copies that private implementation and corrects both issues:
20-foot-only, long-only and mixed stacks use their corresponding mass limits;
short boxes may sit below long boxes on deck but never above them. Positive
class-order tolerances are checked against every lower box to avoid drift.
On the D185 feeder_500 map a short-only hold stack permits 68 tonnes while its
long rating is 34 tonnes, so taking the minimum can lose half its mass capacity.

V2 keeps conservative maximum group mass, the cover hyperedge objective and
the separate dynamic oracle. No objective-clearing or extra search restarts
are silently combined with this correction. `method_v2.py` skips refinement
on already solved tiny incumbents. Its fixed quick run uses 30 seconds proposal
and 60 seconds finishing per candidate; medium12 uses 60 seconds proposal.
Both use the explicit incumbent finisher. Candidate plans worse than the
supplied incumbent are rejected, so retaining its pass and cost is by design,
subject to fresh full-checker validity. Cold-start speed claims are excluded.

Verified before V2 measurement: all 24 private tests passed (four binary energy
tests, including 175 random comparisons to brute force; twenty conditional
capacity/stack-order regressions). All maps still enter the shared full checker.

The complete default-finisher pilot `results/quick60/summary.json` measured:

| Set | Passes vs stored 0.9.0 | Paired lifts | Mean wall/map |
| --- | --- | --- | --- |
| quick tiny | 6/8 vs 8/8 | 6.50 vs 6.50, 6 pairs | 112.51 s |
| quick small | 1/10 vs 9/10 | 26 vs 26, 1 pair | 153.12 s |
| quick medium | 0/3, baseline unmeasured | no pairs | 50.48 s |

Eight small failures were ballast timeouts and one had no legal candidate.
All ten small geometric witnesses failed oracle ordering; three medium models
returned no complete witness. Twenty-four binary cuts on small maps produced
no checked lift improvement. The one replay improvement did not survive as
a checked gain. This pilot is a negative result, preserved rather than pooled
with V2 or used to claim fewer lifts.

## Corrected quick measurement

The complete fixed `quick` run is in `results/quick-v2-30/summary.json`.
Entry point: `method_v2.py:propose`; finishing plug-in:
`incumbent_finish.py:finish`. Proposal budget 30 s, finish budget 60 s, three
tries, seed zero, two workers and one numerical thread each.

| Set | Passes vs stored 0.9.0 | Paired mean lifts | W / T / L | Mean wall/map |
| --- | --- | --- | --- | --- |
| quick tiny | 8/8 vs 8/8 | 6.375 vs 6.375, 8 pairs | 0 / 8 / 0 | 28.51 s |
| quick small | 9/10 vs 9/10 | 13.7778 vs 13.8889, 9 pairs | 1 / 8 / 0 | 53.01 s |
| quick medium | 0/3, stored 0.9.0 unmeasured | no pairs | — | 27.95 s |

Small total changes from 125 to 124 lifts (-0.8%); the bootstrap 95% interval
for mean candidate-minus-baseline is [-0.3333, 0] lifts/map. This is one win in
a development set, not broad evidence of a general improvement. The win is
`sim.small.41100007`: nine to eight lifts, by removing its single yard
relocation. Three boxes change slots in the returned candidate. That candidate
comes from the small cut/single-swap pool and passes the full checker using the
incumbent's tank masses. The supported-packing candidate failed mask ordering.
The binary-cut contribution is **not isolated** from the included single-swap
scan; do not attribute the one lift specifically to minimum cut.

`quick_v2_parts.json` records the paired decomposition: hatch now 32 -> 32,
hatch later 34 -> 34, relocations 43 -> 42, later restows 16 -> 16, current
restows zero. Thus 0% of hatch lifts move, 2.33% of relocations move, and all
of the one-lift saving is a relocation. Stored hatch lifts are 52.8% of the
125-lift baseline, relocations 34.4%, later restows 12.8%.

The nine old small passes are retained through a checked-incumbent refinement
treatment, and eight pairs are unchanged. These wall times include an already
solved incumbent; comparing them with cold 0.9.0 solve times would be misleading.
No quick-medium packing witness or complete legal carrier is obtained within
the proposal budgets. This does not prove any map infeasible.

## Fresh optimum certificate

`optimum_certificate.json` pairs the independent reefer bound with the fresh
full-checker row for `sim.small.40100010`. Every complete legal stow needs at
least four weighted lifts; the newly checked plan attains four, proving a D11
optimum on this one map. Its deck blocks actually mix both PODs, so an optimal
stow need not have exclusive port labels. This does **not** prove that an
alternative strictly port-blocked stow cannot also attain four, and there is
no general small-map optimum proof. The other relaxed hatch bounds leave gaps.

Reproduce the certificate from the recorded fresh row:

```sh
PYTHONPATH=.:src:experiments/mvp/frontier:experiments/mvp/frontier/lab .venv/bin/python experiments/mvp/frontier/lab/c1/fx-cuts/certify.py experiments/mvp/frontier/lab/c1/fx-cuts/results/quick-v2-30/sim.small.40100010.json --out experiments/mvp/frontier/lab/c1/fx-cuts/optimum_certificate.json
```

## High-fill cohort and special-cargo follow-up

The fixed `medium12` V2 run returned 0/12 passes and no complete carrier;
eight rows exhausted construction and four feeder_1700 rows hit an OR-Tools
expression API error (`LinearExpr` has no `coefficient` attribute). Its mean
wall time was 42.71 s/map. This is not twelve valid algorithmic failures and is
not used as the final constructor comparison.

`cut_layout_v3.py` copies the private V2 model, accumulates explicit moment
coefficient bounds instead of inspecting OR-Tools expressions, and imports
Frontier's unchanged `blocks.place_specials`. Successfully fixed special loads
consume their real footprints, masses, heights and cover interactions; their
neighbor bans restrict ordinary cargo. Unplaced specials remain explicit cargo.
Four resource-overlap regressions join the earlier 24 tests; **all 28 passed**.
Every private Python file also parsed successfully after the measured runs.

The two-map probe `special_probe.txt` was selected from input cargo facts:
the failed OOG small map `sim.small.40100034` and the first fixed D185 map,
which has OOG/top-stow cargo. Both returned no legal complete candidate under
30 s proposals. The small CP model did produce a 12/12 geometric witness but
normal sequencing stalled at 9/12; its OOG box was not preplaced successfully.
The medium probe returned UNKNOWN. These remain negative results, not passes.

The final complete constructor measurement is
`results/medium12-v3-60/summary.json`, using `method_v3.py:propose`, the same
explicit finisher, 60 s nominal proposal, 60 s finishing, three tries, seed zero,
two workers and one numerical thread each. It has **0/12 packing witnesses,
0/12 complete legal carriers, 0/12 full-checker passes**, with no method errors.
All twelve private packing solves returned UNKNOWN. Mean actual wall time is
**81.48 s/map**. The four 428-box maps took 95.72, 96.35, 154.65 and 155.85 s;
the longest proposal itself took 154.45 s. Preparation/special-placement/model
work is not bounded adequately by the nominal proposal cutoff. This overrun
is a material implementation limitation, not hidden in the requested budget.
No finisher was reached on these maps. Stored 0.9.0 medium evidence is missing,
so there is no paired medium lift comparison or invented 0.9.0 zero-cost row.
Failure of this conservative bounded prototype does not prove infeasibility.

`medium12_v3_parts.json` records the final constructor census. The earlier V2
errors and all default-finisher pilot rows are retained separately. Only the
V2 fixed quick comparison supports the one-lift improvement claim; V3 was not
run on the whole quick set and is not substituted into that mean.

## Verdict and next step

**Keep part.** Keep the incumbent-based local refinement (one relocation saved,
nine small passes retained), explicit activation/hyperedge model, independent
four-lift optimum certificate and negative mixed-size matroid result. The small
gain is from a combined cut/single-swap search, not isolated evidence for cuts.
Drop the global constructor prototype from promotion: it did not complete any
medium carrier, still misses dynamic special/E6 constraints, uses conservative
group maximum masses, and V3 overruns its nominal proposal budget on large maps.
The target of a better high-fill medium block plan is **not achieved**.

Next, model exact feasible column patterns (including size composition, special
clearance and heavy-under-deck completion), then use shared cover activation
costs to select patterns. Feed specific oracle conflicts back as packing cuts
and enforce an outer construction deadline. This addresses the measured gap
between a capacity witness and a complete legal carrier before adding more
elaborate cut algorithms.

Reproduce the measured quick refinement (from repository root):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src:experiments/mvp/frontier:experiments/mvp/frontier/lab nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c1/fx-cuts/launch.py --set quick --method experiments/mvp/frontier/lab/c1/fx-cuts/method_v2.py:propose --finish experiments/mvp/frontier/lab/c1/fx-cuts/incumbent_finish.py:finish --budget-s 30 --finalize-s 60 --tries 3 --workers 2 --seed 0 --out experiments/mvp/frontier/lab/c1/fx-cuts/results/quick-v2-30
```

For the final negative constructor run, use `--set medium12`,
`method_v3.py:propose`, `--budget-s 60` and output `results/medium12-v3-60`.
`launch.py` records owned PIDs and helper hashes in `owned_processes.jsonl` and
forces one numerical thread. All launched runs exited normally; no unrelated
process was stopped. All new source, notes, tests and results are inside
`experiments/mvp/frontier/lab/c1/fx-cuts/`; no pinned solver/checker/strategy file
was edited, and nothing was committed or pushed.
