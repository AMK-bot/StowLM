# Score-first exact component fusion

`lazy_fusion.recombine(problem, parents, *, deadline, seed=0, keep=1)` has the
same parent and ordered-action output representation as `recombine.py`.
`fuse` is an alias. It reuses that module's occupancy components and mask
enumeration but does not change the eager implementation or its evidence.

The expensive feasibility engine is deferred. All distinct assembled children
are first given `alns.Sequencer`'s canonical order and globally scored by
`FastScorer`. A None from that scorer always falls back to `problem.score`:
None alone cannot distinguish unsupported cases from illegal ones. Only a
real replay None excludes a child as replay-illegal. Complete children are
sorted by their whole-plan objectives, then mask-checked in ascending order.
The first feasible child is best in this family if enumeration finished.
Every returned child that used a fast numeric score is independently replayed;
any numeric disagreement raises rather than silently preserving a bad order.

There is no independent component-cost or min-cut claim. The certificate is
restricted to the union of the enumerated parent-pair component-switch
families with the fixed canonical load-order policy and replay/mask admission.
It does not cover all per-box fusion states, all load orders, ballast finishing,
or full-checker/global stowage optimality. No pass is established here.

`recombine.last_stats` exposes pair component counts and 2^k family sizes,
unique/duplicate children, fast/fallback counts, scoring/sequencing/mask/replay
verification times, and retained objectives. `enumeration_complete` records
full family scoring coverage. `best_proven` additionally requires admission
and real replay of the best feasible child within the deadline. `complete`
also covers exhaustive proof that no scored child is mask-feasible. An
enumeration deadline, or a deadline during the required best-child mask or
verification, withdraws the best certificate. Later optional children cannot
invalidate a proof already obtained for the best. Indivisible calls can still
overrun; this is measured rather than hidden.

## Verification and preliminary measurement

The root executive ran all ten recombination tests successfully: four eager
enumeration checks and six lazy-admission checks. The lazy checks cover a
globally coupled best feasible child, unsupported-score fallback, real replay
rejection, interrupted scoring, mask deadline, and fast/real numeric mismatch.
Both new Python files also passed syntax compilation.

Root's first actual fresh-bank lazy-fusion measurement, on official quick-small
map `random.small.40100011.0.8`, reported 192 parent-pair family masks, 184
unique children, 35 replay-legal scored children and **one** mask check. It
finished in **6.91 seconds**, with `enumeration_complete` and `best_proven`
true. The best was **22 candidate lifts**, equal to the best parent: an exact
restricted-family certificate and a feasibility-work reduction, with **no
observed lift gain**. These are preliminary root-run replay/mask results, not
additional full-checker passes. The eager comparison and second map were
still running when these notes were written; root owns final comparative
results and their saved evidence.

The unchanged shared parent bank is recorded in
`results/from_starts60/random.small.40100011.0.8.json`; root's
`fusion_experiment.py` runs both arms on the same saved parent tuples with a
15-second allocation per arm. No additional CPU experiment was started by
this subagent.
