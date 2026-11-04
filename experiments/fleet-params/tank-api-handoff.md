# Implemented 2.1 tank hook: hull-following section solids

2026-10-27,1.6 ship-design correction. The original handoff documented that
`TankGeometry.from_params` rejected nonrectangular shapes. That issue is
historical: `src/stowage/vessel/hull_tanks.py` now implements section solids,
and `TankGeometry.from_params` dispatches `hull_sections` to it. No tank/checker
source was edited by1.6.

The final generator has regenerated the fleet through the production API,
with independent capacity/centroid/FSM cross-checks. This records integration
availability, not a current full-test pass or checker-approved loading plan.
The parent owns final outcomes in
[design-verification.md](design-verification.md); previous runs are historical.

New records preserve all existing Tank fields and upright `table` rows. Geometry:

```text
shape = "hull_sections"
x_m = [quadrature x, ...]
integration_weights_m = [positive dx weight, ...]
sections_yz_m = [ [ polygon, ... ], ... ]
polygon = [ [y_m, z_m], ... ] # counterclockwise section polygon
x_min_m, x_max_m, y_min_m, y_max_m, z_min_m, z_max_m # bounding extents
```

The three outer arrays have equal length. Each tank section is an explicit
intersection of the checksum-pinned KCS loft with its compartment and side,
excluding actual cargo, engine-room and retained consumable tank spaces.
P/S solids are mirrored. Sections may contain several disjoint polygons.
Bounding boxes deliberately overlap: they are not interchangeable with solids.
All x/weights are actual composite two-point Gauss values, including compartment
and exclusion boundaries. Source loft/clipping/quadrature assumptions are labelled.

Implemented API: `TankGeometry.from_params` dispatches this shape to a solid supporting
`capacity_m3`, bounds, `sounding(volume_m3)` and
`liquid(volume_m3, heel_rad=0, trim_rad=0)` with the current return types. For
finite heel/trim clip every section against the physical liquid plane and solve
its level at fixed volume. Integrate first moments with recorded x weights;
compute liquid-plane surface area/central inertia using the production axis
conventions. No bounding-box FSM, equivalent prism or upright-only correction
may replace finite-angle liquid movement.

The input tables are regenerated through production `TankGeometry.from_params`
and `sounding(volume_m3)` at empty,quarter,half,three-quarter and full fills.
The generator independently integrates clipped section polygons and compares
capacity, centroid and central transverse free-surface inertia before storing
production rows. Empty/full endpoint FSM is zero. `build_tanks` retains its
capacity/extent/table consistency checks for these physical solids.

The recorded Gauss abscissae/weights are authoritative; station gaps do not
supply missing continuous panels. Disjoint polygons within a tank share a
common liquid level by the declared interconnected-tank assumption. The
longitudinal quadrature and upstream hull/compartment clipping remain labelled
approximations; they are not replaced by a bounding-box liquid model.

Tank helper: `experiments/fleet-params/design_tanks.py`. Actual fleet records are
in `params/fleet/*.toml`; generator invokes helper only after tierwise cargo fit.
