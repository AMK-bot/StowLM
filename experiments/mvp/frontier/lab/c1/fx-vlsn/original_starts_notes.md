# Fresh original-family start bank

`original_starts.build(problem, *, seed, budget_s)` returns
`(list[(label, alns.Scored)], stats)`. Use the exact same returned action tuples
as the initial parent bank for all search ablations on a map. Rebuilding a bank
separately for each algorithm would introduce wall-clock construction noise.
`stats` contains per-stage requested/actual times, solver statuses, objectives,
master tier/bound/cut details, exact retained action orders, and total overrun.

The implementation imports and calls the owner modules unchanged:

1. `stowage.solver.twostep.TwoStepPlanner`, configured with
   `FeasibilityConfig(extra={'global_screen': 0})`.
2. `blocked.blocked_plan` with that same configuration and `blocked.compacted`
   if the blocked stage still has time. The raw blocked start is also retained;
   a cheaper compaction receives a second label.
3. `planner._yard_first` with the supplied shared `Problem`, seed, and 40-round
   cap. All complete replay-legal results it returns are preserved.
4. `master.solve_master`, deck tiers 4, 3, 2, 1 in order, with **one worker**,
   followed by `sequence.order_legal` and the real `problem.score`.

Soft cumulative construction deadlines allocate 30% to two-step, 25% to
blocked, 15% to yard-first, and the remaining 30% to the four master tiers.
Unused time from early stages carries forward. Master time is divided evenly
over the tiers not yet attempted. Expensive owner-module operations, order
reconstruction, compaction and replay are indivisible; the builder reports
actual overrun and does not start another stage when its allowance is gone.
A result already produced by an in-flight call is still admitted after its
deadline rather than silently discarded. Master allocations of one second or
less are unlikely to yield a candidate because the original solve loop stops
when its remaining budget is at most one second.

Scope is an empty-ship Stage A loading call: no existing onboard placements or
discharge work. Retained actions must contain exactly one `Place` per load-list
box with no overlapping final footprints, including 40-foot halves. All plans
are replay-scored; yard-first already did that internally. The returned bank
preserves duplicate assignments with different source/order, allowing the
paired search kernel to choose the cheapest evaluated ordering. No source
outside this experiment folder was edited or copied.

This is **not an exact reconstruction of the stored Frontier 0.9.0 starts**.
Original Frontier gives two-step and blocked 120 seconds each, uses separate
search-dependent minimum budgets for yard/master, uses two master workers,
and may stop at the first sight-clear master tier. It finishes early starts
and uses their ballast as a reference for later sight gates. This bank has no
finishing or sight filtering and keeps all successful master tiers. These
differences are intentional for a reproducible **shared fresh parent bank**;
the original constructor algorithms themselves are unchanged. Replay legality
and construction masks do not establish a pass: search results must still go
through the lab's V4 finisher and full checker.

No new research paper or mathematical algorithm is introduced by this wrapper.
The four families and their sources are documented in Frontier's `README.md`,
`planner.py`, `blocked.py`, and the underlying constructor modules. The bank is
an experimental control for isolating neighbourhood and score-speed effects.

No actual-map constructor experiment was run by this subagent: the root
executive owns both current compute slots and will run official quick-small
maps after releasing one. Performance, full-checker pass counts and lift gains
remain unmeasured here.
