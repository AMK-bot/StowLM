# C4 pricing/integration review

Read-only review of the parent's `method.py`, `constructor.py`, `lns.py`, and the
moment-aware page component. No numerical process was started for this review.

## Confirmed issue, corrected before cohort freeze

The original two `Fast(instance, ship)` reference constructions obtained `ship`
from `apply_plan(instance, plan).final_state.ship`. `apply_plan` replays only
`plan.moves` from the arrival state. Its replay contains the inherited tank
masses; it does not apply `plan.ballast`. Consequently a fully checked warm stow
could be priced against securing motions from a different ballast condition.
This is a search-guidance error and can reject useful columns. It cannot cause
a false pass because the unchanged full checker still checks final plans.

The parent replaced both reference constructions with
`stowage.checker.full.departure_condition(instance, plan)`. That public helper
replays the moves, validates/applies the adjustable-tank declaration using
`_ballast`, and preserves fixed tanks. Omitted adjustable tanks in an explicit
ballast declaration become empty, matching the checker. The copied
`physics.PhysicsEvaluator` already used this same `_ballast` construction.
The subsequent full-cohort checks validate resulting plans. No checker or
physics rule was changed.

## Moment RHS and dual review

The partial pricing master is correct about fixed columns. `lns.improve`
saves/restores `model.fixed_columns` in a `finally` block, sets it to the
incumbent columns outside the freed covers during pricing, and the page model
deducts those fixed moment contributions from the RHS of every active moment
row. Their coefficients are excluded from the variable dictionary. Row duals
therefore price the moving columns against the residual cargo target. The
fixed contribution is constant and needs no additional pricing term.

The subsequent fixed-charge LP and IP include the outside columns explicitly,
fixing their variables to one. They correctly use the global, undeducted
`moment_rows(columns=use)` rows; deducting fixed contributions a second time
would be wrong. The IP uses conservative integer rounding via
`_cp_moment_rows`. Constructor `_integer` intentionally gives moment rows soft
slacks, retaining complete physical near-misses for exact evaluation. These are
construction restrictions or preferences, never necessary whole-map
inequalities or certificates.

The coverage LP's `actual_uncovered` is separate from summed moment violation
slacks. Constructor uses the former to decide whether to attempt an integer
selection, so a moment target that is currently violated does not itself drop
a complete page pool. The inherited dive holds a live list of its fixed
columns and tail CP deducts the same moment RHS. Complete greedy seeds do not
bypass configured targets. Repair or postprocessing can change moments; the
replay/full-check gate remains necessary.

## Withdrawn finding

An initial review message incorrectly suggested the finisher rejected
list/trim/draft failures because they were outside its rule allowlist. Actual
IDs are `iscode.loading.*`, so `rule.startswith('iscode')` includes them. That
finding was withdrawn; no change was requested for it.

## Remaining limitations

- `Fast` still uses one fixed reference motion condition and approximate stack
  securing. Newly generated stows require exact voyage-phase physics checks.
- Envelope descent and intact-stability moment targets use distinct documented
  CG conventions. An envelope descent is search guidance, not a proof that
  intact-stability area or native securing improves.
- Full checks and individual pricing operations can cross wall-clock deadlines;
  attempts/rounds/check counts are bounded, and measured time/load must be
  reported. The parent runs one worker and one numerical thread while the
  benchmark marker exists.
- The fixed-charge pool is restricted to generated pure-stack pages. Its LP/IP
  bound is not a bound on a whole map. Only separately proved necessary
  inequalities belong in the certified map bound.
