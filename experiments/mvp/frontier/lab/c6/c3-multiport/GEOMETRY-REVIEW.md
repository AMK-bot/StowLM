# Static geometry review: development seed 263360013

This review reads only this experiment's saved development rows, fixed cohort
JSON and frozen runtime copies. No solver, checker or numerical experiment
was run, and no solver source was changed. Conclusions about causes are
limited to the saved traces and inspected control flow.

## Finding

The saved evidence does **not** identify a new port-accounting geometry bug.
It identifies a pre-existing fallback/repair limitation: both port-LP rounds
timed out in the integer tail, switched to the inherited page fallback, and
eventually emitted a complete replay whose global neighbouring OOG clearance
still failed. The unchanged checker correctly refuses it as unpeelable T0;
the row is `not_completed`, not a relaxed completion.

The port LP preserves `super().build()` and its OOG conflict rows. New rows
add port dependencies and costs; they do not remove physical occupancy or
OOG exclusions. Additional LP/Boolean work might indirectly make fallback
more likely, but no same-start timing ablation isolates that effect here.

## Concrete evidence

Input: `cohort/medium.feeder_800.p3.f70.s263360013.json`,
`random.medium.263360013.0.2`.

The saved port row has two rounds. Both report `tail: UNKNOWN` and
`fallback: true`; their post-export `illegal_columns` counts are 5 and 11.
The checked stows cost 284 and 316 lifts and fail E9, securing and visibility;
the second also fails list. The first is retained as `best_ineligible`, with
all boxes aboard but an unpeelable T0 violation.

Its sole OOG box, `QZXU0000499`, is 40 feet, body height 2.896 m, with 0.5 m
over-height and 0.25 m over-width on each side. At slot `0180304`, the checker
reports neighbouring conflicts with boxes at `0180104`, `0180106`,
`0180504` and `0180506`. The nominal row-centre spacing is 2.5 m, while two
standard widths leave just 0.062 m of horizontal clearance. A 0.25 m lateral
overhang therefore crosses that nominal gap. These numbers explain the
reported overlap; they are not substituted for the checker's raw evidence,
whose measured/limit fields are null.

The engine12 arm on this same input also retains only E9-ineligible stows.
Two of its observed candidates cost 114 and 246 lifts and fail only
`expert.e9.over_width_length`; its best ineligible evidence names this same
OOG box against an occupied neighbouring hold position. That is direct
evidence that this failure mode is not unique to the new port objective.
It is not a comparison with a fresh C5 securing-only arm, so it cannot
separate every performance effect of the new formulation.

## Relevant frozen paths

* `frozen_securing/gap.py:149` installs OOG conflict rows in the assignment LP.
  `oog_conflicts` (`:222`) enumerates lateral/longitudinal envelopes against
  nominal cells at the same tier and one tier on either side. It is a finite
  nominal-tier approximation, not the full actual-height neighbour geometry.
  The four conflicts above lie in the neighbourhood those rows address, but
  this map's emitted candidates came through the fallback rather than a
  successful assignment-LP tail.
* `frozen_securing/method.py` calls `cover_first` after a failed dive, then
  securing exchange/repair, exports columns, and finally calls
  `repair_oog(p, model, model.clear_oog(assignment))` before real replay and
  the full checker.
* `frozen_securing/gap.py:466` implements exchange with individual-column
  `model.legal`, securing ratios and physical-cell occupancy. Those checks
  do not enforce the complete stow's neighbouring OOG envelope predicate.
* `frozen_securing/pages_base.py:216` checks local page capacity, rank order,
  closers and static eligibility; `pages_sight.py:128` adds local sight roofs.
  Neither is a global cross-page clearance test.
* `frozen_securing/pages_base.py:889` repairs OOG by swaps with a limited
  same-length pool. `frozen_securing/repair.py` broadens swap partners and
  uses the unchanged native `placement_violations` predicate on the actual
  complete assignment, but it can still return the original conflicting
  assignment when no acceptable single swap is found. It does not search
  relocation to an empty supported top.
* The new `method.priced_assignment` considers original versus resorted tier
  order using local legality/securing and restow preference. The frozen
  `stow.to_assignment` already performed local resorting and could emit
  locally illegal columns with a recorded diagnostic. Neither exporter
  promises global E9 legality. Both feed the same final repair/check pipeline.

Consequently a candidate may be a complete legal **move replay** while
failing the full stowage rules. That distinction is preserved in the saved
row and must remain in the report.

## Smallest safe additional repair, if the remaining development cases justify it

This would be a construction improvement, not a correction to the new port
equations. Leave the native checker and frozen copies unchanged. The narrow
local hook is a wrapper around the copied method's final `repair_oog` call:

1. Keep the existing checked swap repair.
2. If an OOG box still conflicts and is currently the top box in **all** its
   support stacks, try a bounded list of free, supported top positions of
   its length. Do not remove a middle box and leave a gap. A candidate long
   slot must have both physical halves free and supported.
3. For each candidate, construct the complete ship with all other boxes fixed
   and call unchanged native `placement_violations` for the OOG box at that
   actual slot and actual lower prefix. Reject native E9 overlaps/clearance
   failures; retain required static placement checks and both affected
   columns' legality. This is the candidate mask: an exact check against the
   fixed other boxes, not a precomputed nominal-tier halo or a unary hold ban.
4. Retain all load-list boxes and pass the resulting assignment through the
   existing real replay, actual securing evaluation and unchanged full
   checker. A relocation can change moments, securing and visibility, so
   fixing E9 alone never establishes a completed plan.

The wrapper is naturally applied after exchange/export, where the current
pipeline already calls OOG repair. Guarding only the initial fallback would
be insufficient because later cross-page exchanges can recreate an overlap.
If no checked candidate is found, preserve the failure evidence and continue
the declared search; do not turn it into a relaxed T0 completion.

There is one OOG box here and substantial nominal spare capacity. A fixed
small trial cap, such as 32 candidate tops, makes this a plausible bounded
development change. However, free **long** supported tops may be scarce, and
all native placement calls inspect a complete stow. Practical runtime and
success are unmeasured. A global geometry gate alone would avoid futile
finishing but would not create a passing stow. Keeping the honest negative
result is reasonable if the remaining development results do not justify
another implementation/retest/freeze cycle.

Required tests before adopting that extension:

* The observed occupied neighbour is rejected by the native predicate, while
  a supported clear top is accepted when one actually exists.
* Both halves of a long footprint, actual high-cube/OOG support heights and
  the catalogue-default E9 clearance are respected.
* Moving a non-top source box is refused; moving an eligible top preserves
  every load-list box and all remaining support.
* Original and destination columns, followed by the full checker, are
  re-evaluated; a geometry repair cannot silently claim a securing/sight pass.
* A legal legacy OOG hold placement with genuine clearance stays eligible.
  Do not import the modern hold-guide restriction or change rule activation
  merely to make this legacy-cohort case easier.

No proposed hard ban on all OOG hold cargo or all neighbouring nominal tiers
is justified by this review. No claim of map infeasibility is made.
