# Moment-aware page pricing (Cycle 4)

Copied `c3/c2-layout/pages_base.py` into this directory; no earlier-cycle code is
edited. `pages.py` keeps its pure-stack family, closed-hold cover design, optional
ordinary-heavy-in-hold construction restriction, static mask and unchanged
securing oracle. These are search restrictions, not necessary conditions for a
whole-map lower bound.

A page pattern stores an ordered stack. Its exact cargo mass, vertical,
longitudinal and transverse moments are additive between patterns. The new LP
has an optional vertical-moment ceiling and optional intervals for cargo LCG and
TCG. Each active row has a nonnegative, penalized violation variable, so an
impossible search target does not make the restricted master crash. Its dual
price changes the knapsack gain of each box at each tier. This is actual mass
multiplied by a centre coordinate, replacing the old mass times squared-height
preference. `vertical_weight` adds a linear vertical-moment objective even when
no hard ceiling is requested. These targets help construction; they do not
certify stability, securing or intact area.

The default `moment_geometry='stability'` follows
`src/stowage/checker/stability_loading.py:loading_condition`: body half-height,
actual body heights below, mean physical-support coordinates, no OOG centre
shift. For pure pages with aligned supports these cargo moments match that
loading calculation. Optional `moment_geometry='envelope'` uses expanded OOG
height and asymmetric envelope centre, matching the different visibility/native
securing construction convention. Cargo KG, intact-stability KG and native
securing KG cannot be substituted for one another. Benders' accepted D185
witness binds `containership_area_0_flood_mrad` at arrival, with 0.000158135 m rad
spare; neither a GM floor nor a cargo moment ceiling certifies that nonlinear,
phase-specific test. The unchanged full checker remains the pass gate.

The ordered knapsack retains both tier count and high-cube count. For ordinary
2.591/2.896 m bodies this determines the exact height below a candidate box, so
its vertical dual contribution is consistent with the whole-column master.
For exotic body heights or expanded OOG heights, the DP uses that two-height
proxy only to rank patterns; every returned gain is recomputed with the exact
whole-column moments. Consequently exhaustion is not a pricing certificate for
those boxes, and no master objective is reported as a whole-map bound.

Geometry is part of the pricing type and cache key, including signed TCG,
physical base height and slot coordinates. Pricing a low or central page then
copying the result to a geometrically different page would give wrong reduced
costs. Inherited `balance()` therefore no longer exchanges geometrically
different pages; the moment rows/IP or a separately checked exchange handles
heel. Complete greedy seeds remain in the pool, but bypassing LP/dive is allowed
only if they meet all configured moment targets. During diving a live list of
fixed columns is deducted from every active moment RHS. Tail and full-pool
integer construction include conservatively rounded moment rows.

Interface: `Model(..., vertical_weight=.02, cargo_vmoment_max=None,
lcg_min=None, lcg_max=None, tcg_min=None, tcg_max=None, moment_penalty=10.,
moment_geometry='stability', **old_options)`. Mass units are tonnes; moment units
are tonne-metres; CG targets are metres. `lcg_target` is a compatibility alias
for `lcg_max`. `column_moments(column)` returns `(mass, vertical, longitudinal,
transverse)`; `selection_moments(indices)` returns named totals and ratios.
`moment_rows(columns=variable_indices, fixed_columns=fixed_indices)` returns
`(name, coefficient_dict, residual_rhs)` triples, excluding fixed indices from
the variable coefficients. The parent fixed-charge neighbourhood adds those
rows to its IP; the LP's soft slacks are not a checker relaxation or proof.

Gilmore and Gomory (1961), [A Linear Programming Approach to the Cutting-Stock
Problem](https://doi.org/10.1287/opre.9.6.849), motivates box-cover dual pricing.
The added moment rows are standard Dantzig-Wolfe coupling rows: their duals enter
each page's reduced cost by the same linear coefficients as in the master.

Validation: eight synthetic tests pass with numerical threads fixed to one and
pytest's cache provider disabled. They check actual high-cube stacking, the two
OOG conventions, long-box mass sharing, fixed-column residual bounds, complete
seed rejection, negative upper-row dual signs, soft violation slacks, and
agreement of returned pricing gains with master coefficients. A fresh regression
loads the approved 88-box TRAIN incumbent, reconstructs 35 pure-page columns,
and compares all four cargo totals against the unchanged intact-stability
loading condition with versus without cargo. Maximum absolute error is
1.46e-11 tonne-metres (`moment-regression.json`). This is a geometry/row check;
it does not replace full checker validation of new stows.
