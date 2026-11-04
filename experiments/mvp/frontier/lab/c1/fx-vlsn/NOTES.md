# Cyclic exchange, exact component fusion, and score speed

Scope: only this directory is modified. No owner source is copied verbatim or
patched. `CachedSequencer` reimplements ALNS's yard-first topological order and
memoizes its slot graph; `FastScorer` independently implements a guarded subset
of the expander/replay semantics. Everything returned for admission is replayed
by the real `Problem.score`; the shared scorecard's unchanged full checker is
the only pass authority. No sealed map/list or evaluation-only corpus is read.

## Model before search

`model_examples.json` writes the actual small and medium graph examples. On
`random.small.40100011.0.8`, 18 load boxes occupy a vessel slice with 20 physical
stacks and two hatches. Its stored 22 lifts are 14 yard relocations, four hatch
lifts now, four hatch lifts later, and zero restows. This checks the contract's
warning: this particular map has more relocation headroom than the illustrative
3+4+4 example, but no future-restow headroom. On TRAIN
`D192.medium-feeder_500-f60-high-s192200000`, there are 88 load boxes, 121 initial
yard stacks, 64 ship stacks and six hatches. There is no stored 0.9.0 result for
that input; V6 evidence is not relabelled as a baseline.

Vertices are boxes and occupied slot positions. Directed support edges connect
lower to higher occupied cells (a 40-foot box participates in both physical
columns); hatch edges put every hold load before deck loads on the same cover.
Yard stack order adds retrieval dependencies and relocation costs. A cyclic
exchange rotates box identities among slots without requiring any intermediate
pair swap to be feasible. A lift is a yard relocation, current/future restow,
or cover off/on operation, weighted exactly by the instance objective. These
costs are not a sum of independent exchange-arc costs: moving a box can alter
yard relocation targets, load order, hatch opening calls, and 40-foot blocker
closures. Global weight/visibility/ballast feasibility also couples components.

## Papers and adaptation

* Ahuja, Ergun, Orlin and Punnen (2002), *A survey of very large-scale
  neighborhood search techniques*, [author's publication list](https://mitmgmtfaculty.mit.edu/jorlin/publications/).
  Sharma (2002), *Cyclic exchange and related neighborhood structures for
  combinatorial optimization problems*, [MIT thesis](https://dspace.mit.edu/entities/publication/d656bb4c-9c9e-4b15-af2a-441141a6c7cf).
  The useful picture is a cycle moving one element between sets. We evaluate
  whole cycles because stowage lacks the separable set cost needed to certify
  improvement from negative arc sums. This implementation samples depth 2–8
  cycles, biased toward matching discharge ports, with 15% top relocations;
  it does not claim an exact search of all subset-disjoint cycles.
* Helsgaun (2000), *An effective implementation of the Lin–Kernighan traveling
  salesman heuristic*, [publisher](https://www.sciencedirect.com/science/article/pii/S0377221799002842).
  Variable-depth exchanges motivate deeper simultaneous moves. This is not
  LKH: there is no TSP tour, alpha-nearness candidate construction or LKH
  positive-gain criterion in our model.
* Whitley, Hains and Howe (2009/2010), partition crossover and GPX; Lempitsky
  et al. (2010), fusion moves. Exact links, implementation limits and tests are
  in [recombine_notes.md](recombine_notes.md). Two parent plans create a graph
  connecting boxes whose alternative slot footprints conflict. A component
  chooses all its slots from either parent. With k components there are 2^k
  such children; we enumerate them and evaluate global cost and feasibility.
  A completed enumeration certifies the best child in this restricted family,
  under the canonical ALNS load order, not a global stowage optimum. Budget
  exhaustion explicitly withdraws the certificate. Linear-time independent
  component selection or binary min-cut is not justified by these costs.

## Measurement design and assumptions

The fixed sets are the lab's `quick_small` and `quick`, selected before our
results. Main same-start ablation uses the exact stored ordered 0.9.0 result as
a common warm start for every arm. It measures improvement *from* a strong
stored plan, not reconstruction of the four original construction starts;
those starts were not persisted. A map without such a start is left without
candidates. This is a search-only limitation, not evidence of infeasibility.

`experiment.py` runs swap/replay, cycle/replay, swap/fast and cycle/fast with
the same kernel, seed, start and 8-second search budget. Both modes recompute
the same cached topological order and mask-check accepted steps. Cycles also
include top relocations, so the neighbourhood comparison includes those
relocations. The unmodified `alns.search` is a fifth reference. The initial
pilot (`pilot_ablation.json`) used relaxed mask-infeasible traversal and is
discarded for effectiveness comparisons: it wandered into dead regions and
found no gains. Current traversal is mask-legal. Run order is rotated by map;
time-bounded arms can see different candidate counts and machine contention.

Timing is descriptive on a shared machine. Numerical threads are one.
`nice -n 5` is requested for long jobs, but this sandbox prints
`setpriority: Operation not permitted`; jobs continue at the inherited priority.
No process is killed by pattern, and no unowned process is touched. Team
experiments use at most two CPU-busy processes. The shared full `quick` run
has 21 maps and up to three separately bounded 120-second V4 attempts per
map. If it exceeds 20 minutes, the stated reason is completing the mandatory
fixed quick set through the unchanged finisher/checker, not extending search
or tuning on additional data. Search itself is only 12 seconds per map.

## Results

The exact score accelerator's separate differential test compared 1,333
sequences on all nine stored-pass `quick_small` maps with zero supported-domain
mismatches. Mean call time was 35.307 ms for the original scorer versus 0.869 ms
for the specialized scorer (40.6x); these are interleaved score-call timings,
not end-to-end search speedups. The cached sequencer matched original ALNS on
450 valid random assignments. Details and reproduction are in
[profile_notes.md](profile_notes.md).

The 8-second, same-stored-start 2x2 experiment is complete on all ten
`quick_small` maps; one has no stored start, leaving nine paired candidate
comparisons. The stored mean is 13.8889 lifts. The following are *candidate*
scores, before finisher/full checker admission:

| Kernel | Mean candidate lifts | Actual summed search seconds | Evaluations | Masks / rejected |
| --- | ---: | ---: | ---: | ---: |
| Swap, original replay | 13.7778 | 76.32 | 377 | 196 / 81 |
| Cycle + top relocation, original replay | 13.8889 | 92.70 | 324 | 193 / 116 |
| Swap, specialized exact score | 13.8889 | 79.96 | 574 | 265 / 110 |
| Cycle + top relocation, specialized exact score | 13.8889 | 84.16 | 435 | 255 / 160 |
| Unmodified ALNS | 13.8889 | 82.49 | 581 iterations | not instrumented |

At equal nominal budget, faster scoring permits more evaluations, but mask
checks and archive replays reduce the gain sharply: swap evaluation throughput
rose only 1.45x and cycle throughput 1.48x. There is no measured cycle lift
advantage. Several indivisible mask calls overrun the nominal budget; actual
times above include that. The swap/original arm found one lift on
`sim.small.40100025`: 26 to 25. The other arms did not find it in their short
run, illustrating that more evaluations need not follow the same trajectory.

That 25-lift candidate **passed the unchanged full checker**, through the
shared `score.py` with `admit.py:finish`, using the stored plan's tank masses
and rechecking the entire changed cargo plan. One paired map, 1/1 passes,
25 vs 26 lifts, 57.33 seconds for admission. This is not a fresh V4 ballast
search or a ten-map mean, and it came from the ordinary swap arm. Evidence:
`results/candidate_check/`.

Full saved-union admission on `quick_small` is now complete:
**9/10 passes versus stored Frontier's 9/10**, with **13.6667 versus 13.8889
lifts** on the nine paired passes (two wins, seven ties, no losses; -1.6%).
The second gain is `sim.small.41100007`, 9 to 8, found during recombination.
Its new assignment is absent from all the other saved 9-lift candidates, so
this is a new fused stow, not simply a new loading order of an inherited
assignment. Its fully checked parts are zero relocations, four hatch lifts
now, four later, and zero restows. Admission takes 31.44 seconds per selected
map on average, using inherited ballast and the unchanged full checker.
This is the union of several short searches (about 45 seconds nominal per
map), not a single 8-second method or a new V4 speedup. Evidence:
`results/ablation_checked/`. A 60-second unmodified-ALNS reference from the
same stored ordered starts is complete with the same inherited-ballast
admission (`results/frontier60_checked/`): **9/10 passes and 13.8889 mean
lifts**, no gain on any map, mean total wall time 78.75 s per selected map.
Thus the saved short-search-plus-fusion portfolio's 13.6667 is two fewer
lifts than that reference on the nine paired passes. This demonstrates a
small gain for the measured portfolio, not a general cyclic-search advantage.

The default-V4 `quick` run is complete (`results/quick12/`): tiny **8/8 passes,
6.375 vs stored 6.375 lifts**; small **9/10 passes, 13.8889 vs 13.8889 lifts**
on nine paired passes; no candidate on any of three medium maps because none
has a stored start. Medium stored 0.9.0 evidence is unknown, not 0/3 failures.
Mean total wall times per selected map are 107.63 s tiny, 99.70 s small and
0.37 s medium. Search is 12 seconds nominal plus separate V4/checking time;
these are warm-start optimization timings, not a new constructor/portfolio
speed claim. This main cyclic method does not meet the lift-improvement goal.

Exact recombination of warm-search archives had at most four occupancy
components (16 children per pair); conservative coupling often collapsed it
further. The 5-second allocation did not exhaust all parent pairs on any of
the nine maps. No global or completed-family certificate is asserted for
those whole calls. One additional fusion lift gain was found as noted above.
Synthetic tests
do verify completed four-child enumeration and rejection of a cheapest child
by global feasibility.

Fresh-bank comparison: `from_starts.py` regenerates the original constructor
families once per map, then gives the identical bank to both searches. On
quick_small maps `random.small.40100011.0.8` and `random.small.40100036.0.2`,
unmodified ALNS and cycle/fast/fusion tie at 22 and 11 candidate lifts. Both
receive 60-second search budgets; actual search times are ALNS 62.9/60.3 s,
experimental 62.3/60.0 s. Shared construction takes 38.0/17.3 s and is excluded
from both arms equally. See `original_starts_notes.md` for budget, one-worker
master and omitted sight-gate differences from a historical Frontier run.
These additional comparisons are replay/mask results, not extra certified
passes. For the first map's 51.56-second cycle phase, score calls consume
1.59 s, sequencing 0.087 s, masks 46.22 s, and archive replay 2.36 s. Thus masks
consume 89.6% of the phase after score acceleration. A faster score alone has
already moved the bottleneck.

`lazy_fusion.py` is a separate improved exact family enumerator: globally score
all whole children first, then mask-check from cheapest upward. A completed
enumeration plus acceptance of the first feasible child proves the best
mask-legal canonical child in that family. It still does not prove full-checker
optimality. The original `recombine.py` and measured main method remain intact.

Measured lazy versus eager fusion (`results/fusion15.json`), identical first
four parents per fresh bank and 15-second budgets: on map 40100011, lazy scores
all 192 masks (184 unique assignments) in 6.91 s and proves best=22 after one
mask check. Eager takes 18.29 s, overruns its budget, and covers only 85 masks
without a completed certificate. On map 40100036, lazy covers all 244 masks
(232 unique) in 4.72 s, proving best=11 with one mask check; eager also completes
in 12.77 s. Both outcomes tie the best parents. This is faster *restricted
family optimization*, not a full-checker or end-to-end planner speed claim.

Final verification: `pytest experiments/mvp/frontier/lab/c1/fx-vlsn -q`
passes all ten tests, including six for lazy admission and deadline/cost guards.
Candidate replay counts elsewhere in the artifacts are not full-checker passes.

Additional completed warm-fusion proof (`results/warm_fusion_proof.json`): four
saved 9-lift parents yield 140 component switches, 110 unique children and a
proven 8-lift optimum in **0.171 seconds**, including one mask check and an
independent replay. The new winner also **passes the full checker** with the
inherited ballast (`results/warm_fusion_checked/`, 35.17 s admission). This is
a different 8-lift stow from the earlier eager-fusion winner, checked separately.
The optimization certificate is conditional on that parent family and canonical
load order, while its returned plan's legality is a full-checker result.

`method_lazy.py:propose` now tests this approach as a live search plug-in:
70% of a 12-second budget to cyclic/fast search, then exact lazy fusion of up
to six good archive plans. It returns at most two challengers and the original
warm start, so three full-check attempts preserve the original fallback.
Its `quick_small` run uses the same inherited-ballast full-check admission as
the 60-second ALNS reference; no saved ablation candidate is read by this live
method. This live comparison is complete (`results/lazy12_checked/`):
**9/10 passes, 13.7778 versus the 60-second ALNS reference's 13.8889 lifts**
on nine paired passes, one win, eight ties, no losses (-0.8%). Mean total wall
time is **31.76 versus 78.75 seconds per selected map**, using the same
inherited-ballast full-check gate. Total proposal time is 89.02 versus 550.06 s
over the set (about 6.2x faster); total wall time is 317.64 versus 787.50 s
(about 2.5x faster), descriptive shared-machine timings. This is a 12-second
nominal search budget versus 60 seconds, not a whole-constructor comparison.

Attribution on the improved map is explicit: cyclic search ends at its starting
9 lifts with zero improvements. Lazy fusion then exhausts 28 component choices
(24 unique children) in **0.155 seconds**, proves best=8 in that canonical
parent family, and the full checker accepts that child. Thus fusion supplies
the measured lift gain. The separate score ablation shows a large score-call
speedup but only roughly 1.5x evaluation throughput while masks dominate.
No experiment establishes that cyclic parents are better than swap-generated
parents for fusion.

## Verdict and remaining assumptions

**Keep the guarded exact scorer and lazy exact-family fusion.** The live
combination passes the keeper's observed paired-small-mean criterion without
losing a pass. Keep cyclic exchange as one source of diverse good parents,
but drop any claim that these sampled cycles alone outperform ordinary swaps.
The saved five-arm portfolio is slightly better (13.6667), with 465.21 s search
plus 314.38 s admission across the set, versus the ALNS reference's 787.50 s
total: approximately the same total warm-optimization budget, two wins.

Limits: one seed, nine paired validation small maps, stored ordered plans as
warm starts; no independent held-out or medium success claim. Original four
constructor families were regenerated only for two additional diagnostic maps,
where both searches tied. Exact fusion certifies its restricted canonical-order
family, not a global stowage optimum; full legality is independently checked.
Inherited ballast avoids a repeated ballast search and is disclosed in all
paired scorecard configurations. Next test: equal-budget swap+fusion against
cycle+fusion, and profile/cut feasibility-mask cost before pursuing deeper cycles.

## Reproduction and entry points

Source audit: the shared bundle hash changed during this long task because
`lab/catalog.py`, `lab/score.py` and `lab/summary.py` were updated by their owner.
No `src/stowage/*.py` or Frontier owner file has a post-task-start modification
time, and this executive wrote only this directory. The default V4 quick run
retains its earlier bundle hash. Crucially, the saved-union, 60-second ALNS and
live lazy-fusion paired admission runs all use the same final shared hash
`54c4a017ce3c51804d91695f026450334072b53a0f28c7abef4dd577115eb765`.
Raw configurations and audit details are retained in `results/verification.json`.

From the repository root, set one numerical thread per process and import paths:

```sh
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c1/fx-vlsn
```

* `experiment.py --budget-s 8 --out .../results/ablation8` runs the stored-start
  2x2 plus original-ALNS comparison on `quick_small`.
* `from_starts.py --budget-s 60 --construct-s 40 --maps 0,2` runs the two
  independently regenerated, shared-bank comparisons.
* `fusion_experiment.py` compares eager and lazy exact-family admission on
  those saved banks with 15-second budgets.
* Shared scorecard: `score.py --set quick --method
  experiments/mvp/frontier/lab/c1/fx-vlsn/method.py:propose --budget-s 12
  --finalize-s 120 --tries 3 --workers 1 --out .../results/quick12` uses default V4.
* Saved ablation admission: the shared scorecard with `--set quick_small
  --method experiments/mvp/frontier/lab/c1/fx-vlsn/admit.py:propose
  --finish experiments/mvp/frontier/lab/c1/fx-vlsn/admit.py:finish --budget-s 0
  --tries 3 --workers 1 --out .../results/ablation_checked`. The zero proposal
  budget means replaying saved search evidence, not free online optimization.
  Search consumed four 8-second kernel arms, one 8-second ALNS arm, and up to
  5 seconds of fusion per map before this separate admission step.

Run Python scripts with `nice -n 5 .venv/bin/python`, expanding `...` to this
directory. Generic acceleration APIs are `FastScorer(problem).score(actions)`
and `lazy_fusion.recombine(problem, parents, deadline=..., seed=0)`; neither
replaces final checker admission. `summarize.py` consolidates retained evidence
and records helper-source hashes in `results/REPORT.json`.
