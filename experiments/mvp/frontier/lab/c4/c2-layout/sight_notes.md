# Native range-cone roof restriction (separate constructor arm)

`pages_sight.py` extends `pages_balanced.Model`; it changes no frozen pricing,
checker, visibility provider or existing method. The source is
`src/stowage/checker/visibility.py:check_visibility` and
`visibility_range.py:range_blind_intervals`, read in full for the construction
formula. Frontier `sight.py` is a reference-tank gate; it does not derive a roof
line and cannot replace the native all-phase check.

Native visibility uses the bridge eye at `(bridge.x_m−2, 0, bridge.eye_z_m)`.
It adds no extra human height. Hull equilibrium supplies waterplane normal `n`,
forward vector `a`, transverse vector `b`, and eye height above sea `H`.
`R` is the projected eye-to-bow distance plus `min(2*LOA, 500 m)`; **LOA is not
LPP**. A transformed point `(u,v,h)` obscures sea beyond that radius precisely
when `H*hypot(u,v)+R*(h−H)>0`. Requiring the reverse inequality at every cargo
roof corner is a sufficient, deliberately stronger cargo restriction at the
supplied waterplane. Convexity of the norm keeps the entire rectangular cargo
envelope below that cone when its vertices are below it; bottom points are
also safe under the checked monotonicity condition. It produces no cargo blind
sectors in that waterplane and implies the wedge's forward-distance limit.

For upright water, the absolute roof limit is
`eye_z − (eye_z−draft)*hypot(x−eye_x, y)/R`. Thus **shallower**, not deeper, water
is the conservative upright draught for fixed cargo/eye geometry. A maximum
draught cannot be called a worst visibility condition. In a tilted waterplane
the wrapper uses the same native normal/forward/across transformation and
solves each corner's monotone cone inequality by bisection, retaining the
feasible endpoint. A derivative bound is checked before bisection. The minimum
of all footprint-corner caps across all supplied conditions is used, less an
explicit 0.15 m default search margin.

`legal()` checks every box's roof using actual cumulative body plus OOG heights,
and the box's expanded fore/aft/port/starboard footprint. A lower OOG box with a
wider footprint is checked in its own right, even if the upper footprint is
narrower. This follows the native visibility envelope convention and is
separate from intact-stability body-CG moments. A conservative static pricing
mask removes placements whose roof would already be too high even if every
lower box had the shortest body in the load list. Geometry/static cache keys
are refined after that mask. Exact column legality handles real high cubes and
OOG below a candidate.

Interface: `Model(..., sight_draft_m=None, sight_conditions=(),
sight_reference_ship=None, sight_roof_margin_m=.15, **moment_options)`.
`sight_conditions` is a list of dictionaries with `draught_aft_m`,
`draught_fwd_m`, `list_rad`, for example the saved native departure/mid-voyage/
arrival states. `sight_draft_m` adds one upright reference. An optional ship
uses the native ordinary `_loading_condition` helper to derive one reference
waterplane; the caller must first apply declared ballast with
`departure_condition`. No provider is replaced or patched. With no sight
conditions the wrapper adds no sight restriction.

This is **not a necessary whole-map inequality or a guarantee for a changed
ballast/cargo equilibrium**. It is a search restriction at explicit reference
waterplanes. Fixed silhouettes/covers may still obstruct; they are not changed
by per-page cargo caps. New plans must undergo real replay and the unchanged
full native checker at all voyage conditions. A robust legal-draught/trim/list
envelope would need a separate proved enclosure, not a guessed single draught.

Eight synthetic tests pass for upright algebra, shallow-draught
monotonicity, physical margin, tilted-corner inequalities, cumulative high-cube
height, OOG footprint, hold-page preservation and invalid eye conditions.
Balance and sight tests ran together after the benchmark marker cleared and
the parent granted the second slot: **17 passed**, exit 0, 0.95 seconds, one
numerical thread, scoped vessel cache and pytest cache disabled. No native
physics evaluations were run for this component; generated stows still need
real replay and full all-phase native checking.
