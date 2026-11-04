# Cycle 4 proof scope

The required interpretation of every proof/equality claim in this experiment is:

> fewest lifts among plans the checker passes, counting later ports by our D15 projection (fixed stow, no later loads)

This sentence is the scope required by CONTRACT Cycle 4 and the logic review's
F11. It applies to every table's `proved`/`optimal` flag, including inherited
machine field names. A CP-SAT `OPTIMAL` status alone describes the relaxation;
an equality claim additionally requires the independently full-checked upper
plan and the guard/provenance checks below. It is not a claim about an executable
minimum-crane-work future voyage policy.

## Family represented by the lower bound

The packing/rank relaxation includes arbitrary **current** MovePlans, including
temporary aboard placements, direct ship shifts and quay off/on restows. It is
not restricted to load-once plans or a page constructor. Candidate construction
can use a narrower family; failure of that constructor has no infeasibility
meaning for the full family.

Guards: empty decision-cargo arrival; no current discharge list; no jointly
planned later calls or forecasts; all required exports loaded; validated,
disjoint decision/fixed cell and stack catalogs; immutable background cargo;
and finite nonnegative objective coefficients. Empty decision arrival does
not mean an empty physical vessel. The fixed background can be extensive.
Unsupported arrival/forecast inputs use the existing counting-only fallback.
Preserve the recorded rule activation and all input/checker/helper source
hashes. Numerical results obtained with the legacy rule freeze off do not
establish the same measured equalities under a different activation mode.

First-aboard ranks remain necessary under staging: an original yard export
above another first boards earlier or incurs a yard relocation. A final upper
or deck box that never ship-restows has its first placement at its final
position, so any obstructed final lower/hold box must first board earlier.
Charging one ship-restow lift per flagged box is conservative even when an
off/on restow takes two lifts. In particular a direct shift costs one lift and
must remain represented. Export yard relocation costs, current ship-restow
costs and the non-export yard-blocker lower bound are disjoint categories.
These implications are necessary, not a complete executable-sequence model.

D15 uses the first subsequent matching POD call, no future loads, survivors at
departure coordinates and the configured blocking-box lift charge. Closure
passes through both supports of long boxes; each blocker is charged once per
call. It does not construct a future ballast or repositioning policy.

## Necessary static domains and cuts

F7's DG widening is mandatory. `safe_unary_mask` rebuilds every DG row from
optimistic geometry/eligibility and the checker's no-ship `_stowage` screen.
The no-ship screen defers unsupported upper-tier SW1/SW2 geometry. Do not
replace it with raw production `T.static` or an empty-ship full-placement gate:
those can reject a box that becomes legal when lower supports exist.

The final-static oracle reconstructs all decision departure placements and
closed covers, retaining the original `instance`. Current replay requires
covers closed at departure. `check_static_rules` reads immutable background
from the vessel itself; it is not necessary to insert fixed background boxes
into decision placements. Core background-capacity and physical weight-order
checks use those vessel records. Full-assignment no-goods are valid only for
authoritative hard final cargo-rule failures, independent of ballast or current
order. An exception, timeout, failed replay/order, or failed particular ballast
is not such a no-good.

E6 is checked on the final departure, so staging does not bypass it. Ordinary
bottom-opportunity implications retain occupancy/absence and any possible
lateral-OOG interference. Local E6 oracle cuts fix whole affected columns,
empty cells and all spanning-box closure; heat/OOG cuts retain conservative
support-floor intervals. Do not substitute an isolated box-slot prohibition
for a support-dependent geometric failure. Legacy E6 implications are disabled
under rule-freeze activation.

Securing cuts require the pinned all-ballast contradiction oracle and its
numeric guards, not a failed ballast search. A pure long-box column cut fixes
every occupied and empty cell in both complete support columns. Mixed or
spanning geometry falls back to the full-assignment cut. Its locality uses the
current synthetic checker's no-wind-shelter/no-sidewall-coupling assumptions
and hull-only minimum accelerations. These floating point safeguards are not
interval-arithmetic formal verification.

Cutoff `U-1` infeasibility proves a lower bound `U` only when the actual D11
objective is nonnegative and integer-valued and the full conservative objective
is constrained. API guards must not truncate weights/targets. UNKNOWN and
MODEL_INVALID provide no cutoff certificate. A passing plan at `U` is needed
for equality, even though it is not needed for the lower-bound inference.

## Experiment interpretation

Cold exact construction means no historical assignment, plan, ballast,
incumbent or objective target is supplied. Record separately the first raw
CP-SAT assignment, its static verdict, its first attempted expanded current
plan, replay verdict, first full-check verdict, and eventual bounded-run pass.
Internal oracle rejections count against first-raw-witness legality. A later
passing candidate cannot retroactively make the first attempt legal.

Bounds at a checked historical target are useful proof verification, but are
not cold plan-construction timings. Reusing historical ballast also requires a
warm label. Include model construction, exact search, order expansion, ballast
finishing and the independent full check in end-to-end timing. The separate
under-one-minute claim needs every claimed stage within that minute.

`val_small` is development data reserved here for proof counts, not tuning.
Freeze the generic algorithm, seeds, budgets, upper-source policy and rescue
policy before opening new validation outcomes. A predeclared generic longer
run and newly available checked upper plans may close the remaining maps.
Do not invent constraint or parameter changes from individual failures.

Medium 88-box inputs already passed V6 and are preselected as passable. V6 is
a pass-oriented constructor, not a lift benchmark. Compare against the named
layout checked upper plans. Medium differences are projected later-port lift
differences under D15, with the same fixed-stow/no-later-load caveat above.

For solver gain/time comparisons, use a fresh 0.9.0 plus E9 arm at the same
load and budgets; stored rows are descriptive evidence only. Extra passes due
to E9 are a bug fix. Wall-clock limits are load-sensitive: record marker state,
process/thread caps, work counts when available, and end-to-end timing.

This is a read-only source audit on 1 November 2026. No numerical solver,
tests, validation input maps or sealed data were opened by the reviewer.
