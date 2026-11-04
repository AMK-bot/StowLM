# Parent-component recombination

## Model and exactness

Each differing box is a vertex with two possible final slots, one from each
parent. Two vertices are adjacent when any of their possible footprints
overlap. A 40-foot slot contributes both physical 20-foot cells. Connected
components partition these occupancy conflicts. Choosing all boxes in each
component from one parent preserves box uniqueness and footprint uniqueness
when both complete parents have unique footprints. Common assignments remain
fixed. The implementation also records a conservative diagnostic partition
that joins boxes sharing a ship stack, hatch cover, or original yard stack;
unchanged boxes can bridge those dependencies.

The actual search uses occupancy components. With k components it enumerates
2^k complete children, subject to the absolute deadline. It does not score
each component independently. Each assignment receives the existing
`alns.Sequencer.order` load order, the real `Problem.score` move expansion and
replay, and `_mask_ok` feasibility-engine screening. This is an exact minimum
over the enumerated component-switch family and its deterministic load-order
policy **only if** `recombine.last_stats['complete']` is true. Inherited parent
load orders are also retained if better than their canonical order. Deadline
checks occur between calls, so one scoring or mask call can overrun.

This is a deliberately limited certificate:

- Stack support, 40-foot interactions, and hold/deck hatch precedence are
  checked after a child is assembled. They are not independent costs.
- Yard relocation depends on the global load sequence. A relocated box can
  land in another yard stack, creating interactions beyond initial yard-stack
  membership. Recombination always replays the complete child.
- Stability, lashing, sight, dangerous goods and other physical rules can
  couple distant components. Replay/mask acceptance does not replace the
  caller's ballast finisher and full `stowage.checker.check` gate.
- The family does not contain every possible box-wise fusion in general.
  When the parents use different occupied slot sets, some legal partial
  choices inside a component may be excluded by requiring an all-parent
  component. It also does not optimize every topological load order.
- At most the best 16 distinct assignments are returned, always retaining
  their cheapest evaluated order. Ranking cannot certify full-checker best
  until the caller finishes/checks the returned plans.

Stage A with no existing onboard or discharge cargo is the supported scope.
Parents must assign every `instance.load_list` box exactly once. Unsupported
problems return no proposals rather than changing the action model.

## Why this does not become a linear-time GPC or one min-cut

Whitley, Hains and Howe's partition crossover exploits the special tour
structure of a union of two Hamiltonian cycles. Their generalized version
finds several cost-two partitions and chooses their better paths with an
additive tour length. That proof does not transfer to a yard-replayed stow:
component costs depend on other choices through load order and physical
checks. The transferable idea is to inherit whole alternatives in a union
graph. The implementation therefore enumerates whole children instead of
claiming the best of 2^k alternatives in linear time.

Lempitsky, Rother, Roth and Blake's fusion moves formulate binary choices
between proposal labels. Efficient exact graph cuts require a suitable
pairwise energy representation; QPBO can leave some choices unlabelled for
non-submodular terms. We have not established a pairwise/submodular
representation of actual yard and hatch replay. An arbitrary min-cut would
therefore be an unjustified surrogate here; this implementation uses no cut.

## Source code reused

Imports `alns.Sequencer` and `alns._mask_ok` unchanged and calls the supplied
`alns.Problem.score`. No planner, feasibility, scoring, checker, or finisher
source was modified or copied. New union-find/component enumeration code was
written in this folder.

## Papers

- Darrell Whitley, Doug Hains and Adele Howe (2009), *Tunneling Between Optima:
  Partition Crossover for the Traveling Salesman Problem*, GECCO.
  [Author PDF](https://www.cs.colostate.edu/sched/pubs/gecco09tunnel.pdf).
- Darrell Whitley, Doug Hains and Adele Howe (2010), *A Hybrid Genetic
  Algorithm for the Traveling Salesman Problem Using Generalized Partition
  Crossover*, PPSN XI.
  [Author PDF](https://www.cs.colostate.edu/sched/pubs/ppsn10.pdf).
- Victor Lempitsky, Carsten Rother, Stefan Roth and Andrew Blake (2010),
  *Fusion Moves for Markov Random Field Optimization*, IEEE TPAMI 32(8),
  1392–1405, doi:10.1109/TPAMI.2009.143. Public technical-report precursor is
  dated May 2009.
  [Microsoft Research paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2009/05/fusion.pdf).

## Interface and current measurement status

`recombine(problem, parents, *, deadline, seed=0)` returns a list of complete
ordered `Place` tuples, cheapest first. Parents may be complete assignment
dictionaries, ordered action sequences, or `alns.Scored` objects. Multiple
parents enumerate each pair once. `deadline` uses `time.monotonic()`.
Diagnostics are available as `recombine.last_stats`, including occupancy and
conservative component counts, masks visited, family size, score/mask counts,
completion, elapsed time and retained objectives. Diagnostics are per-call
mutable function state, suitable for the lab's single-process invocation.

No dataset experiments have been run by this subagent: the root executive
reserved compute slots for profiling/integration. After an explicit slot
release, four deterministic synthetic unit checks passed in 0.007 seconds
(about one second including imports): complete four-mask coverage with a
coupled global cost, global-mask rejection of the cheapest child, conflict
detection through the second cell of a 40-foot footprint, and expired-deadline
certificate rejection. `py_compile` also passed. These checks validate the
enumerator plumbing; they do not substitute for real map and full checker
verification. No performance gain or full-checker pass is claimed here.
Root integration owns real-map modeling,
paired quick-small evaluation, finisher/checker verification, and the final
keep/drop decision. No sealed data was opened.
