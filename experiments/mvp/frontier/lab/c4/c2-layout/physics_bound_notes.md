# Cycle 4: necessary physics in the whole-map lift relaxation

These rows are a lower-bound experiment, not a constructor or acceptance gate.
`bound_physics.py` copies C3 `c2-layout/bound.py`; `physics_envelopes.py` copies
C3 `c3-feasproof/physics.py` without alteration. `physics_bound.py` attaches
new integer-safe physics rows to every full supporting-stack allocation.
Only this Cycle 4 folder is written. Read the full CONTRACT including its
corrections, lab README, C3 bound proofs, feasibility proof and Benders report.
No numerical work starts until the parent grants the team's CPU token.

## Necessary map from any checker-passing plan

Retain the prior supporting-stack count variable `x[g,s]`, hatch/port charges,
safe E6 witness implications and union-counted cover restows. Actual cargo maps
to its allowed supporting-stack footprint. Each group receives the **minimum**
coefficient among all its original members, so grouping cannot strengthen a
row accidentally. The actual cargo CG lies above the minimum base height of
its supporting stacks plus half its ordinary height. The stability provider
uses ordinary height here, even though the securing/visibility provider uses
expanded OOG height. Rounded-down integer kg·m cargo coefficients therefore
underestimate the actual vertical moment.

The tank variable is `u[t] = ceil(real tank mass in kg)`, with domain from zero
through `ceil(capacity kg)`. Independent tanks relax operational settings and
consumables. Let `c[t] = floor(z_min[t] - K)` and let `m[t]` be actual liquid
mass. A liquid centroid always lies within its declared tank solid, hence its
vertical arm is at least `z_min[t]`, for every liquid heel and trim.

`c[t] u[t] <= (z_min[t]-K) m[t] + max(0,c[t])`.

For negative `c`, rounding mass upward only lowers the product; for positive
`c`, the extra mass is below one kg, costing at most `c`. This is why the
right side receives the sum of positive coefficients. Mass upper rows gain
one kg per tank, lower rows round the fixed-plus-cargo constant upward. The
loading-manual displacement interval is widened by the checker's normalized
comparison tolerance and outward integer rounding.

The weather applicability rule requires `KG_virtual <= 1.5*draught`. Its
nonnegative free-surface correction gives `KG_physical <= KG_virtual`.
Legal draught cannot exceed the smaller hull/port maximum, so take
`K = 1.5*(maximum_draught + 1e-7) + 1e-7` m, with an explicit tolerance guard.
Writing total mass as fixed plus cargo plus liquid mass gives the valid row:

`cargo_moment_lower + sum(c[t] u[t]) <= K*(fixed_mass+cargo_mass) - fixed_zmoment + rounding_guard`.

All constants before final floor/ceiling are exact Fraction arithmetic on
binary input values. This row is much weaker than a true finite-angle area
constraint, but remains globally necessary.

## Necessary securing, without identifying the two GM providers

The unchanged C3 outward envelope pins the lashing source hash and validates
the hull-reference dimensions. Securing's GM is independent of stability's
GM. Every passing securing state has positive lashing GM; we use only its
closed limiting floor zero. This preserves every positive value. Below half
the hull depth, rotational cancellation is allowed and only the immutable
surge/sway/heave floor survives. Above that height a positive rotational
lever strengthens the floor, with rational sine and square-root enclosures.

For each assignment group, minima of the own-box fitting and own-mass ratios
can exclude an allocation only if **every original member** necessarily
fails at that minimum-motion floor. The own-compression ratio is deliberately
unused: asymmetric upper reactions can be negative. The transverse force
coefficient includes the minimum end-share factor for asymmetric long OOG.
Each physical deck column supports at most two root end chains, with capacity
`2*(2*0.85*210000) = 714000 N`. Sum over **all deck columns**, so shifted long
chains cannot silently invalidate a per-bay claim. Integer force coefficients
round downward and the capacity receives a 1e-7 relative comparison guard.
The fixed global floor deliberately forgets root position, tier and most
securing interactions. No fitted reference motion, page pool or empirically
observed failure enters the certificate.

## What is not proved

The binding D185 criterion is `containership_area_0_flood_mrad`, as established
by C3 Benders, not scalar GM. This module does **not** turn a sampled righting
curve, fitted KM or local Jacobian into a necessary inequality. Free-trim
finite-angle buoyancy and the tank liquid-transfer correction would need an
outward envelope over every legal mass, KG, trim, list and tank fill. C3's
universal geometric area bound is safe but far too weak. These rows therefore
relax that binding criterion, signed trim/list balance, visibility and exact
securing. A surviving low-lift witness is not a legal stow; a search failure
is not an infeasibility proof.

The CP-SAT `BestObjectiveBound` alone supplies a certificate, never its feasible
incumbent. Maximise the result with the hash-matched C3 port-Hall certificate
65/90/70/51; the copied CP contains only the earlier 63/90/70/51 analytic cuts.
The D11 objective scope includes D15's fixed-stow later-port projection and
no later loads, exactly as guarded by the frozen bound's input restrictions.

## Verification and sources

`test_physics_bound.py` tests outward mass and tank/moment rounding on exact
rational points of both coefficient signs. Its fourth test reads the four
approved C3 checked incumbents, performs real move replay, fixes every cargo
assignment in the new physics relaxation and establishes a feasible tank image
with a one-worker CP solve. All **four tests passed**, including all four map
images. These tests validate retained necessary rows; acceptance still belongs
to the unchanged full checker.

Root explicitly granted the sole CPU token while BENCH-RUNNING existed.
Owned numerical sessions were 26828 (four bound maps) and 55288 (final tests),
both exited normally and sequentially. Thread caps were one, bytecode disabled,
pytest cache provider disabled, vessel cache inside this C4 folder. `nice -n 5`
was attempted but the sandbox denied priority changes. No process was killed.

`bound_results.json` records raw CP certificates **63/90/70/51** and final
maxima with matching input SHA-256/instance-ID C3 Hall certificates
**65/90/70/51**. Build/solve wall seconds are **16.15/5.87/17.31/0.63**; nominal
15-second budgets include expensive E6/envelope setup, which alone can exhaust
the high-map budget. The high maps return unknown with the independent analytic
certificate; the two low maps solve the strengthened relaxation to optimum.
No own-securing allocations are excluded on these inputs at the safe zero-GM
floor. Thus these necessary physics rows **do not improve any measured bound**.
The low-map gaps remain a clean negative result, rather than being hidden with
unsafe fitted finite-angle inequalities.

## Further tier/support refinement

`bound_tiers.py` is a second copy of the same bound with every actual physical
cell retained in the assignment signature. A physical cell is used at most
once; occupancy in its kth tier cannot exceed occupancy immediately below.
Let `h_min` be the smallest ordinary height of any load-list box. With empty
arrival and background cargo confined to disjoint fixed stacks (explicit guard), every occupied kth
tier has exactly k occupied predecessors, each of height at least `h_min`.
Therefore its actual support height is at least `base_z + k*h_min`. A long
box takes the minimum floor of its physical supports, which is no greater
than their actual equal supporting height or the stability provider's average
support height. Exact Fraction arithmetic rounds this lower floor downward
to binary float before using it. The actual supporting heights and securing
OOG expansion may be larger, never smaller. This strengthens both weather
moment and minimum-motion securing coefficients without assuming a page pool
or fitted reference geometry. The projection of every operational plan still
obeys all retained constraints. Ordinary rather than expanded minimum height
also safely covers the stability provider's separate height convention.

All **six tests passed** after this extension: the four previous rounding and
accepted-image tests, plus exact tier-floor rounding and independent occupancy/
ordinary support-height reconstruction on all four accepted incumbents. Owned
test session 36685 exited normally before stronger solve session 47607 began.
Root again explicitly admitted the sole CPU token. The initial stronger solve
47607 exited before solving because its initial broad guard rejected any
background cargo. Source review confirmed fixed cells cannot be decision
destinations; the corrected guard requires actual fixed/decision support
disjointness, which all four inputs satisfy. The retry is session 82908.
Retry 82908 exited normally. `bound_tiers_results.json` gives final certificates
**65/90/70/51**, combining raw CP bounds **63/88/70/51** with C3 Hall bounds.
Statuses are unknown/unknown/unknown/optimal, with wall seconds
**15.61/15.00/18.04/10.09**. Models contain 20,396/57,890/18,310/20,396 variables.
The small low map's strengthened relaxation has optimal shipping cost four,
so exact physical cells, no-gap prefixes and necessary minimum-height moments
still leave its four-lift operational gap untouched. The larger models weaken
bound progress within the fixed time budget on the other low map; only the
independent Hall maximum prevents certificate regression. No fitted inequality
is introduced to hide this negative result. Both physics variants are useful
as proof scaffolding; do not spend tier-model cost in the constructor without
additional evidence. No numerical process remains after explicit token release.

The stronger reproduction command changes `bound_physics.py` to
`bound_tiers.py` and output to `bound_tiers_results.json` above. Add
`test_bound_tiers.py` to the pytest command for the six-test suite. The guard
correction is justified by inspection of `rules._physical_geometry_index`:
complete physical geometry never makes fixed cells decision destinations;
all four maps' fixed stack identities are disjoint from decision stack IDs.

## Longer high-88 control

To distinguish startup delay from relaxation weakness, root admitted one
exclusive **240-second** run of the smaller, unchanged `bound_physics.py` on
the first TRAIN map. `bound_high88.txt` fixes that single approved path;
`bound_long_results.json` records the input SHA and trusted C3 Hall maximum.
Owned session 14831 exited normally at **240.001 s**. BENCH-RUNNING existed
both before admission and after completion; only this one process was busy,
one numerical thread, local cache/bytecode disabled, with denied nice request
as documented above. The CPU token was explicitly released afterward.

The raw solver bound stayed **63**, and the final certificate stayed **65**.
However the solver found an **18-shipping-lift feasible relaxation incumbent**:
four used covers, five port-cover edges, zero counted restows. This matches
the independent Hall shipping certificate18. Inserting the already certified
Hall objective lower cutoff into this relaxation would therefore make its
minimum exactly18 shipping (65total). This is a genuine structural negative
result: longer solving cannot close the remaining six operational lifts in
the current supporting-stack/necessary-physics relaxation. The incumbent is
not a concrete checker-passing plan. Its omitted exact support heights,
visibility and especially finite-angle righting-area conditions remain open.

Reproduce with thread caps, `PYTHONDONTWRITEBYTECODE=1`, the local vessel cache,
and `PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab`:

```
.venv/bin/python -m pytest experiments/mvp/frontier/lab/c4/c2-layout/test_physics_bound.py -q -p no:cacheprovider
.venv/bin/python experiments/mvp/frontier/lab/c4/c2-layout/bound_physics.py --list experiments/mvp/frontier/lab/c3/c2-layout/train4.txt --base-results experiments/mvp/frontier/lab/c3/c2-layout/bound_ports_results.json --budget-s 15 --out experiments/mvp/frontier/lab/c4/c2-layout/bound_results.json
```

The core enclosure principle is R. E. Moore (1966), *Interval Analysis*, as
documented in [SIAM's author bibliography](https://epubs.siam.org/doi/10.1137/1.9780898717716).
The supporting-stack master retains the existing Gilmore–Gomory relaxation
perspective, [1961 original paper](https://doi.org/10.1287/opre.9.6.849).
No external code was downloaded. The new rows are direct algebraic
consequences of the unchanged checker, with source/proof reuse credited above.
