# Cycle 4 layover finishing lanes

`finishing.py:finish` first makes an explicit declaration of the inherited
adjustable-tank masses and calls the unchanged full checker. If already
declared, the candidate recipe is preserved. Native checker code accepts
`ballast=None` using the reached-state masses despite the stricter sentence
in `model/plan.py`'s docstring; no missing-ballast bug or solver gain is claimed.

If only water-sensitive physical parts fail, `ballast_local.py:optimize`
tries a local loading LP and successive LP steps over symmetric tank-pair
increments and a HEEL split. The objective reduces **actual native righting
area, loading, securing and visibility deficits**, not merely a scalar GM target. Tank
capacities, the 98% correction regime and the raw-TCG absolute-value cusp
restrict each local step. Finite differences refresh every local iteration.
Every retained finished candidate receives the entire unchanged checker;
partial physical feasibility never establishes a pass. Local failure is
unknown and never a whole-stow or whole-map infeasibility proof.

All decisions in the local lane are work bounded: default 28 exact native
oracle calls, two LP-search iterations, one optional loading-seed LP, 2,000
simplex iterations per LP. Numerical threads are one. There is no search
deadline, worker process or source monkeypatch. A native physical evaluation
may take substantial time, so the work bounds do not promise a wall limit.
`finalize_s` applies only if explicitly enabling `v4_fallback`; that optional
native V4 lane is wall bounded and separately labelled, disabled by default.

The initial trust radius is configurable as `trust_radius`, default .15
(15 percentage points of tank capacity per grouped direction). Each proposed
step is evaluated with the native area/securing oracle at scales 1, .5 and
.25 until it improves the exact merit or is rejected. Derivatives remain
local at at most .01 fill fraction; capacity, current pressed/slack regime
and raw-TCG restrictions still apply. Rejected full line searches halve the
radius for the next iteration. The complete checker remains the final gate.

These are ceilings, not a repair guarantee. For the usual nine tank pairs
plus HEEL split (ten directions), an inherited probe, one loading-seed probe,
two full ten-direction Jacobians and up to three line-search trials per step
use `2 + 2*(10+3) = 28` calls. Thus the fixed profile permits two full local
LP attempts at this dimension. More directions can exhaust the cap sooner;
directions not differentiated under the cap stay fixed. The earlier 14-call,
.03-radius pilot generally permitted only one small step. Its failure and
runtime remain pilot evidence, not evidence about the frozen 28-call profile.

`ballast_targets(instance)` is a cheap pricing helper: it interpolates the
native inherited tank tables, estimates departure displacement, interpolates
native even-keel hydrostatic LCB (accounting for current/table water density),
then cancels lightship/liquid/fixed-load moment offsets about that LCB and
zero total TCG. Lightship LCG is a fallback only when hydrostatic rows are
unavailable. It also provides a low-KG weight. It performs
zero exact physical evaluations. These preferences guide construction only;
they are not area/securing certificates. The local finisher reports the real
`containership_area_0_flood_mrad` margins for diagnosis.
Outside the tabulated displacement range, interpolation uses the nearest
endpoint LCB; the cargo target itself is not clamped to available cells.

## Copies and references

`ballast_physics.py` is adapted from C3
`lab/c3/c2-benders/physics.py`: it adds the ordinary native visibility provider
for departure, mid-voyage and arrival, includes its provider identity in
memoization, and normalizes blind distance by that report's native positive
`limit_m`, total blind angle by 20 degrees and minimum clear angle by five
degrees. The earlier stability/lashing provider calls and unrepresented-hard
violation rejection are retained. An individual blind-sector violation that
has no corresponding negative native scalar margin still rejects physical
feasibility. No native checker/provider implementation is edited.
`ballast_loading.py` is copied
from `engine/benders/ballast.py`, itself copied from C2 Benders. Only its
unchanged `_assemble` function is retained; older wall-bounded solvers are
excluded. It constructs the same approximate hull-tangent/loading rows.
The inherited-recipe lane follows the idea in C1
`lab/c1/fx-finish/fast_finish.py:finish_inherited`, without its optional
physics-kernel patches or dispatcher modifications.

The local linearization follows Griffith and Stewart (1961),
[A Nonlinear Programming Technique for the Optimization of Continuous
Processing Systems](https://doi.org/10.1287/mnsc.7.4.379). C3's more general
optimizer also uses Broyden (1965),
[A Class of Methods for Solving Nonlinear Simultaneous Equations](https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/);
Broyden was consulted only through that earlier method: **no secant or
Broyden updates are implemented here**. This short local search rebuilds
finite differences at each iteration. No downloaded code is used.

## Integration review

The exact oracle varies only native adjustable tanks within capacity; fixed
tanks remain at reached departure masses. Every trial re-derives D57 midpoint
and arrival consumable states from its own departure masses. The affine seed
is disabled for adjustable consumables because fixed phase contexts would
be invalid there. Only ordinary native stability/lashing calls supply local
margins, including the actual current-threshold
`containership_area_0_flood_mrad` row. Ordinary native visibility supplies the
same three-phase local margins, preventing premature physical-feasibility
declarations when visibility still fails. Strength remains a full-checker
gate whose sensitivity is not modeled by the local objective. The copied
affine loading seed has no visibility rows; its candidate is evaluated by
the same expanded native oracle before selection.

Water-repair gating matches exact aggregate part names or D57 part suffixes,
not arbitrary substrings in condition ids. Nonwater hard failures, errors or
unrun parts skip local search. The final unchanged full checker still decides
every accepted output. Pricing targets assume inherited departure tank masses
and an even-keel LCB, and omit list/trim/free-surface and future drawdown;
they remain heuristic construction preferences, with no certificate claim.

Verification is delegated to the executive's single admitted process while
`BENCH-RUNNING` exists. `test_finishing.py` covers work caps, the actual area
row rather than GM alone, pressed/slack differences, pricing moments,
explicit inherited recipes, static-rejection skipping and the full-check gate.
