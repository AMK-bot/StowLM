# Finisher correctness and mathematical review

Scope: source/document review and lightweight JSON inspection only. No numerical
map solve ran in this subtask. Read `lab/CONTRACT.md` first. Nothing in `src/`,
the sealed cohorts, or another experiment was changed. References below are
primary papers, checked on the public publisher/author pages on 2026-11-01;
they are research inputs, not executable code.

## Real small and medium models

The fixed cargo-operation plan is a directed path `s_0 -> ... -> s_N`.
`s_i` is the full cargo/cover state after move `i-1`; an arc is one executed
load, discharge, relocation, restow, or cover operation. A yard-only relocation
still advances this path and available crane time even if ship cargo does not
change. Cover operations change the hull-opening context, so equal cargo
placements alone do not establish equal hydrostatic states.

Inspected small TRAIN instance:
`experiments/mvp/corpus/train/small/feeder_500/simulated_yard/seed-030100254.json`.
Its stored `frontier/runs/train12-60/sim.small.30100254.json` plan has 12 loads,
2 yard relocations, 2 cover-off and 2 cover-on moves: N=18, 19 path vertices,
and 2 physical covers. This is a stored 0.3.0 development row, not the pending
lab quick-set 0.9.0 paired baseline. Its reported weighted lift count is 14;
that figure also includes projected later-port work and must not be inferred
from the 6 current nonproductive operations alone.

Inspected medium TRAIN instance:
`experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-high/seed-192200100.json`.
Its stored `scale/runs/v6/maps/medium-feeder_800-f60-high-s192200100/raw.json`
contains 163 loads, 83 yard relocations, 8 cover-off and 8 cover-on moves:
N=262, 263 path vertices, and 8 covers. Both instances have 18 adjustable tanks
(9 named P/S pairs), no initially placed decision cargo, and a dedicated
anti-heeling HEEL pair declaration. Parsing these artifacts established graph
sizes, not legality or a new full-checker certificate.

For a time-expanded ballast skeleton, create a vertex `(t,i)` per tank and
operation state. A carry-over arc `(t,i)->(t,i+1)` stores water up to tank
capacity. Intake/discharge arcs connect sea/pump nodes to tank nodes; permitted
transfer arcs connect tank nodes. Flow conservation is
`m[t,i+1] = m[t,i] + intake[t,i] - discharge[t,i] + transfers_in - transfers_out`.
The small graph has 342 tank-state vertices and 324 carry arcs. The medium
graph has 4,734 tank-state vertices and 4,716 carry arcs, before pump arcs.
Arc costs can represent pumped mass or pumping time. The lift objective is on
the cargo-operation arcs, not ballast-flow arcs: current relocation/restow/
cover moves and checker-projected future restow/cover work receive the exact
instance objective weights; productive loads/discharges do not count toward
`weighted_nonproductive`.

At each layer a coupled physics constraint sees the entire 18-tank vector
and cargo state. Draft, finite-liquid equilibrium, GM/GZ/weather, strength,
visibility, and securing therefore destroy a pure network incidence matrix.
Linearizing these constraints produces a sparse LP with network structure plus
side constraints, not a plain min-cost flow problem solved by network simplex.

## Which pathway actually runs

The selected medium declaration and scale V4 protocol both say rule freeze
OFF. The live process environment remains authoritative and must be recorded.
With freeze OFF and default `intermediate_stability=False`, `checker.full._check`
does not call in-port replay. V4's `Evaluator` solves the departure fixed stow
over 18 tank fill fractions and evaluates three D57 voyage conditions. Its
diagnostics explicitly identify `lp_status = not_used: exact nonlinear probes`.
`ballast_successive.search` uses an LP only after exact finite-difference
samples; its default gradient uses one probe for every adjustable tank per
iteration. Thus a whole-lift LP is not a direct replacement for the measured
V4 path unless in-port policy is activated. Do not toggle activation to obtain
a speed result.

When rule freeze is ON, the unchanged checker evaluates P1--P6 at every
required operation state, constructs the arrival/departure changeover and D74
pumping programme, checks both pumping-window endpoints, builds/refines HEEL
intervals, and checks the exact selected HEEL path. P7--P9 strength/static
coverage is the checker's prescribed sample set: endpoints, hatch events,
discharges, bay completions and the top three strength-risk states. Cold
independent state evaluation is mandatory for D74 pass 1; the checker rejects
`warm_start=True` there. A plug-in may search with cheaper screens but cannot
remove any of this final coverage.

## Safe acceptance and reuse boundaries

1. Keep `stowage.checker.check(instance, final_plan)` as the acceptance authority
   with normal activation/environment and all parts enabled. A passing search
   probe or a successful LP is not a certificate. Save the exact finished plan,
   report/input hashes, full-checker result, lift count, and timing separately.
2. V4 exact loading probes test displacement, GM, trim, list, draft, and berth
   limits at all three D57 phases. They do not test GZ areas/weather, strength,
   visibility, lashing, move legality, static/DG rules or instructions. A
   numerical `probe_unavailable`/failed equilibrium is unknown, not a physical
   contradiction. Preserve public-checker deferral and fallback near limits.
3. Visibility and lashing intentionally share
   `visibility._loading_condition`. Exact-content caching of this helper can
   eliminate duplicate work. Do not substitute `ExactContext` or stability's
   loading result: visibility uses its own cargo-centroid, finite-liquid/FSM
   and roll-GM calculations, while stability has separate correction policies
   and exemption handling. The helper identities are mathematically different.
4. A cache key must bind all used vessel/geometry, cargo/cover/placement,
   liquid masses/densities, port density, scalar numerical inputs, environment
   modes and implementation identity. No rounded tank fills, vessel-name-only
   keys, nearest-neighbour physical reuse or stale mutable result objects.
   Scope monkeypatches to the experimental invocation, restore them on exit,
   and perform independent full acceptance with patches disabled if it is
   search-only caching. Existing part-cache entries are already keyed by exact
   tank-fraction bytes, phase and part; duplicate report caching adds little.
5. Endpoint and slack-tank regimes are discontinuous: the current probe uses
   the same 98%-pressed convention, small-tank exemption and finite-liquid
   policy as stability. A derivative/Jacobian from one regime must not silently
   govern another. All experimental proposals still require exact evaluation.
6. The three voyage conditions include consumable drawdown and phase density.
   Equal adjustable ballast does not make their full loading inputs identical.
   When adjustable consumables exist, V4 intentionally disables fixed-context
   cheap screening. Preserve that behavior.
7. Securing depends on GM and equilibrium, so cargo-only securing caches are
   unsafe. Visibility depends on exact trim/list and tests angular intervals,
   not just nominal roof height. Strength depends on actual liquid distribution,
   buoyancy, residual closure, all assessment cuts and the permissible envelope.

## Practical candidate accelerations

**First choice: eliminate repeated exact numerical work, after profiling.**
Share exact visibility loading results between visibility and lashing calls.
Reuse identical direct-liquid physical integrations within one search workspace
using complete bit-preserving physical keys; do not reuse verdicts. The V3
audit already found tank/cargo construction tiny compared with public physics,
so optimizing JSON/tank-object setup alone is unlikely to deliver 10x. Existing
`perf.evaluation_cache` only owns a full-check evaluation; isolated public part
calls in V4 do not automatically share that workspace. An experimental scope
can share allowed numerical intermediates across search calls while the final
full checker creates its own clean workspace. This must be measured for actual
hits and bounded memory; V3 already implements some direct-liquid reuse, so do
not claim the same optimization twice.

**Second choice: rank exact rejection screens by cost and rejection rate.**
Current V4 runs phase-major stability/strength/visibility/lashing after cheap
probes. A part-major visibility/lashing pass across all phases can reject a
later-phase failure before departure curves. Keep every part, all prefix/margin
semantics and numerical-unknown handling. If most candidates pass those parts,
there is little saving. Conversely, extending the inherited-fill direct full
checker lane beyond 16 loads avoids duplicate prechecks on already passing
medium fills but can hurt hard fills. Use paired measurements, not intuition.

**Third choice: amortize exact derivative calls.**
V4 finite-differences all 18 tanks each local iteration. A secant/Broyden update
can reuse an approximate Jacobian after an accepted exact step:
`J_new = J + ((y - J*s) outer s)/(s dot s)`, where `s=x_new-x` and
`y=margin_new-margin_old`. Initialize/rebuild on active-margin changes,
endpoint/slack regime changes, failed progress or bad prediction. Always
evaluate every proposed step exactly and keep the trust region. This changes
search guidance only. Grouped mirrored-pair perturbations can reduce initial
probe count from 18 to roughly 9, but equal P/S fills are a search restriction
and cannot prove infeasibility. Neither method changes the full checker.

**Later: sparse multi-step SLP with lazy states.**
For the activated in-port pathway, use the tank-time skeleton and add local
linearized violated-state constraints to an LP. Start with endpoint/changeover/
hatch states and previously violated states, then use exact separation over all
states before acceptance. This can save model construction and repeated
optimization calls but cannot skip the final checker. Nonconvex physical
tangents are guidance, not global cutting planes. The critical states may move
when liquid distribution or cargo sequence changes; endpoint dominance requires
proof and is not established here.

## Early infeasibility certificates

The existing `ballast_proof.prove_infeasible` already contains valid outer-set
contradictions: exact displacement mass bounds, disjoint forward-draft bounds,
ballast-independent stack weight/height violations, and complete sealed-hull
buoyancy as an upper bound. It explicitly rejects treating empirically
calibrated local linear-model error as a uniform global bound. Reuse these
before spending physics time.

A Farkas vector proves infeasibility of its particular LP. It proves physical
infeasibility only if every exact feasible plan maps into that LP's feasible
set. Conservative/inner LPs and local SLP tangents fail this requirement.
For `A*x <= b`, a checkable infeasibility witness is `y >= 0`, `y*A = 0`,
`y*b < 0`, after incorporating all variable bounds as rows. Store the full
matrix/bounds and witness and independently verify residuals with justified
numerical guards. No certificate is claimed here. D74 explicitly reports extra
pumping as delay rather than rejecting solely for unavailable crane-time
capacity, so a pump-rate lower bound above crane time cannot automatically
certify an infeasible plan under that contract.

### Implemented optional securing bound

`early_bound.py:securing_bound(instance, ship)` returns lower ratios without
solving loading equilibrium. It pins the reviewed `lashing.py` SHA256 and uses
its actual cargo-support graph and scantling hull integration. In `Motions.at`,
the square-root formulas imply `al >= .7*abs(surge)`, `at >= abs(sway)` and
`av >= abs(heave)`. Those surge/sway/heave values depend on length/scantling CB,
not tank fills, actual GM or draught. Cases where those physical prerequisites
cannot be established return unknown.

For hold cell guides the contact force vector has nonnegative components
`(mass*at, mass*al)/4`; adding adjacent components and taking a Euclidean norm
is monotone. The guide stress ratio is also monotone in that norm. For deck
longitudinal racking, all coefficients of `al` are nonnegative provided each
CG-height fraction lies in [0,1]; the exact fixed wind term is retained. A
fraction outside that range disables that particular bound. For longitudinal
corner compression, average the checker's + and - direction reactions: all
horizontal moments cancel. Increasing each vertical acceleration above its
heave lower bound increases every resulting corner reaction if each box's own
corner-weight coefficients are nonnegative. The helper verifies the sufficient
condition `abs(dx)/(2*length)+abs(dy)/(2*WIDTH) <= 1/4` for every box; otherwise
it omits corner bounds. A maximum over the two actual directions is at least
their average. Container gross mass above the fixed 30,500 kg securing rating
is a separate exact contradiction.

Only lower ratios exceeding `1+1e-8` produce a fixed-stow proof; the checker's
ratio rejection tolerance is `1e-10`. Other ratios/statuses are diagnostic and
never mean feasible. This guard separates ordinary floating roundoff from a
contradiction but does not constitute interval-arithmetic formal verification.
No kernel result replaces complete final checking. The executive must benchmark
this optional entry point and compare measured ratios before enabling it in
the plug-in. It was written after source review; no speedup is claimed here.

## Primary references and why they fit

- L. R. Ford Jr. and D. R. Fulkerson (1958), [Constructing Maximal Dynamic Flows
  from Static Flows](https://doi.org/10.1287/opre.6.3.419). Models capacities and
  traversal time in a dynamic network. Use as the flow-over-time skeleton;
  coupled nonlinear ship physics lies outside its pure network theorem.
- R. E. Griffith and R. A. Stewart (1961), [A Nonlinear Programming Technique
  for the Optimization of Continuous Processing Systems](https://doi.org/10.1287/mnsc.7.4.379).
  A primary sequential-linearization paper with a numerical and process-model
  example. Fits solving nearby linear models and re-evaluating exact physics;
  it does not supply a global feasibility certificate for this ship.
- C. G. Broyden (1965), [A Class of Methods for Solving Nonlinear Simultaneous
  Equations](https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/S0025-5718-1965-0198670-6.pdf),
  Mathematics of Computation 19, 577--593. Reduces expensive function evaluations
  by updating an approximate Jacobian. Our use for inequality-margin search is
  an adaptation, guarded by exact evaluation and rebuilds at regime changes.
- J. E. Kelley Jr. (1960), [The Cutting-Plane Method for Solving Convex
  Programs](https://doi.org/10.1137/0108053). Explains the violated-constraint
  oracle idea. Its convex assumptions do not hold for general finite-liquid
  ship physics; use lazy search constraint generation without importing its
  global convergence or certificate claims.
- Q. Huangfu and J. A. J. Hall (2018), [Parallelizing the Dual Revised Simplex
  Method](https://doi.org/10.1007/s12532-017-0130-5), with
  [author manuscript](https://www.maths.ed.ac.uk/~jajhall/HuHa13/HuHa13.pdf).
  Practical sparse-LP benchmark evidence behind HiGHS. It supports using the
  installed solver once an LP is appropriate; this experiment still uses one
  numerical thread and should not optimize LP speed before measuring its cost.

## Verification and limits of this review

Source paths read include V4/evaluator/ExactContext/successive/rate-LP/proof,
full checker/in-port replay/rates/evaluator, stability/visibility/lashing/
strength, and move counting. JSON-only counts above were recomputed using
`collections.Counter`. No profile, pass count, new mean lift result or speedup
is asserted by this review; those belong to the executive's measured artifacts.
The unchanged full-checker call imposes an empirical floor for a fixed checker
implementation and hardware, not a mathematical complexity lower bound.
An observed 50-second checker would prevent a 10x reduction of a 150-second
successful finisher unless the checker numerical work itself is accelerated.
That would not rule out a 10x reduction of a 600-second timeout.

## Review of proof-only static subset (`fast_proof.py`)

Reviewed against the live `src/stowage/solver/ballast_proof.py` and
`src/stowage/checker/rules.py`, including `rules_background.py`,
`rules_evidence.py`, `rules_weight.py`, `rules_special.py`, and `rules_imdg.py`.
The source diff changes the module documentation and this one block only:
the original full `check_static_rules` call is replaced by `_core(...)` plus
construction of `PartReport(name='rules', violations=violations)`. All other
mass, draft, polygon-volume and singleton proof logic is preserved.

Verdict: the narrowed block supplies sound necessary-condition contradictions
under the ordinary unchanged checker providers. `check_static_rules` starts by
calling exactly `_core(instance, ship, call_index, notes=notes)`. It then appends
E6/special/IMDG results and deduplicates/sorts the combined violation list;
it never removes a core violation or changes its severity. `_core` itself
retains both D42 and legacy background-capacity audits, geometry validation,
mixed-footprint mass splitting, inherited-stage/catalogue severity selection,
the current freeze activation, and exact stack mass/height comparisons.
The proof still consumes only hard `expert.physical.stack_weight` and
`expert.physical.tier_height` violations, both ballast-independent quantities.
Omitting `notes` changes explanatory metrics only: helper note callbacks have
no effect on rule/severity results. `PartReport.ran` defaults to true, as it
does for an ordinarily completed full static report.

The omitted E6/special/IMDG scans have no current emissions for these two
target IDs. More fundamentally, a future omitted scan adding a target ID would
make the subset miss a proof, rather than create a false proof. If an omitted
scan were to fail numerically while `_core` completed, the subset might produce
a proof where the original helper returned no proof; the independently
established stack violation remains a valid contradiction, and a broken
unrelated checker part cannot make the full plan pass.

Recommended guards before enabling:

- Preserve public frozen override semantics: if `checker.check_static_rules`
  has an active `stub_override`, call the original proof path or return no
  proof. Direct `_core` bypasses that frozen decorator. Production evidence
  must already have clean provenance; an explicit guard prevents accidental
  misuse in tiny override tests.
- Record/pin `rules.py`, its background/evidence helpers, catalogue bytes and
  the original proof hash in experiment evidence. Do not substitute a custom
  severity, default geometry or a tolerance. No extra numerical guard is
  needed for this narrowing because it reuses the identical source comparisons.
- Do not expose the synthesized subset PartReport as a complete static checker
  report or reuse it for acceptance. It exists only to extract a contradiction.
- Keep errors as no proof and retain all ordinary static/DG/E6 checks in the
  full final acceptance gate. Restoring public helper hooks around that gate
  remains a separate requirement of `fast_finish.py`.

This review ran source reads and a textual diff only. No map solve, checker,
profile or numerical benchmark was run. The reported 68-second original proof
and 62-second E6 contribution came from the executive's profile and were not
independently remeasured here.

## Stronger securing motion floors (experimental, not primary frozen run)

`early_bound.py` now retains positive gravity terms for a box whose actual CG
height satisfies `z >= hull.depth_m/2`. The unchanged source always sets
`rotation_z = min(depth/4+draught/2, depth/2)`, hence the box's rotational lever
is nonnegative at every admissible draught. Its fixed pitch angle theta is a
positive function of hull length, capped at 20 degrees, and its pitch
acceleration coefficient is nonnegative. Consequently the entire longitudinal
acceleration is at least

`0.7 * hypot(surge, sqrt(max(L,110)/325) * G * sin(theta))`.

This uses zero as the pitch-acceleration lower bound, so it needs no actual
draught upper bound or linearized equilibrium assumption. For lower CGs the
helper retains the original `0.7*abs(surge)` floor because cancellation can
occur. Hull dimensions and the pitch-angle range are validated first.

A transverse floor is also available without a draught bound. Positive GM
implies `0 < roll_period <= 30`. Write `k=1` with bilge keels and `k=1.2`
otherwise. The source's degree-valued roll angle before its narrow-beam floor
lies between `9000*0.5*k/((B+75)*pi)` and
`9000*1.25*k/((B+75)*pi)`. For `B<32.2`, apply `max(25, ...)` to both ends.
The helper explicitly proves the entire resulting interval lies in [0,90)
degrees before using monotonic sine; positive beam makes its global upper
bound less than 57.3 degrees. If phi_min is the radian lower endpoint, then
`roll >= phi_min*(2*pi/30)^2`, and for nonnegative lever
`z-depth/2` the exact transverse acceleration is at least

`hypot(sway, G*sin(phi_min) + phi_min*(2*pi/30)^2*(z-depth/2))`.

No pitch-period extremum, draft limit, actual GM value, or tank state is used.
The existing monotone guide/racking bounds consume these per-box acceleration
floors unchanged. The vertical acceleration retains only the original heave
floor. Diagnostics report the elevated-CG count, fixed elevated longitudinal
floor, rotation upper bound, and proved roll-angle interval.

Verification for this revision is source algebra only, per the executive's
no-CPU instruction. No numerical witness or speed claim is established yet;
the executive will compare against the unchanged lashing report after the
profile slot becomes available. The primary `fast_finish.py` was not edited.

### Draft-derived pitch bound deferred; deck fitting shear added

Do not assume that a passing stability draft bound also bounds the draught
used inside securing. Stability rejects a loading draft margin below `-1e-10`
and refines near-table boundaries, but securing takes
`(t_aft+t_fwd)/2` from `visibility._loading_condition`. That helper uses
different liquid/free-surface/CG policies, including behavior different from
stability method 2 and small-tank exemptions. A guessed 0.0001 m guard does
not establish a uniform cross-helper discrepancy bound. Therefore the proposed
pitch-period floor derived from `hull.max_draught_m` is not implemented as a
certificate. The algebra would be valid if the same-helper draught upper bound
were proved separately; the present checker does not provide that implication.

Instead `early_bound.py` now bounds the exact deck fitting shear expression.
For each end chain and suffix `i`, the unchanged checker uses

`hypot((sum(trans[i:])+sum(wind_t[i:]))/2, longitudinal_shear[slot][end]/2)`

against `0.85*LOCK_SHEAR_SWL`. Transverse inertial terms are
`abs(mass*at*fixed_end_share)` and hence monotone in the proved `at` floor;
the exact fixed wind terms are retained. Longitudinal shear comes from
`_longitudinal_reactions(loads, +1, dynamic_vertical=True)`: all own horizontal
forces `mass*al+wind_l` and all propagated end shears are nonnegative, and the
recurrence consists only of sums and division by two. It is therefore
componentwise monotone in the proved per-box `al` floors. The helper checks the
resulting minimum shear arrays are finite/nonnegative before using them.
Vertical acceleration, eccentric moments and rod response do not enter that
shear recurrence. The norm is monotone in these two nonnegative components.

This deck fitting bound is evaluated even if a CG-height fraction disables the
separate longitudinal racking bound. It requires neither nonnegative corner
weight coefficients nor an actual draught/GM estimate. Review/implementation
were source-only; no numerical test or witness claim accompanies this revision.

### Deck rod-force lower bound

`early_bound.py` now calls the unchanged `_end_response` with the lower
transverse inertial forces, exact fixed wind, actual heights/CG heights and
both original stiffness values (15.7e6 and 3.7e6 N/m). It consumes only the
rod output and uses the unchanged three rod ratios; the rack output is not
assumed monotone. The call is enabled only when every height is finite/positive
and every CG-height fraction is in [0,1], ensuring all equivalent-force
coefficients are nonnegative. Nonfinite or negative rod/cosine/sine results
return unknown rather than a proof.

The stiffness matrix is a grounded spring chain plus one rod diagonal. For
any displacement vector u its quadratic form is

`k[0]*u[0]^2 + sum(k[j]*(u[j]-u[j-1])^2, j>=1) + rod_k*cosine^2*u[anchor]^2`.

All spring stiffnesses are positive; the bottom ground term and connected
chain make this strictly positive for every nonzero u. The matrix is symmetric
with nonpositive off-diagonals. Its inverse is entrywise nonnegative, which can
also be seen without importing a theorem: if nonnegative forces produced a
negative displacement minimum, the equilibrium equation at that node would be
nonpositive; equality at an ungrounded node propagates the same minimum along
the connected chain until the strictly grounded bottom/rod term gives a
negative force, a contradiction. Apply this argument to the difference of two
solutions to establish monotonic response to increased forces.

Thus actual equivalent forces dominate the minimum equivalent forces, the
anchor displacement cannot decrease, and the positive multiplier
`rod_k*cosine` makes the computed minimum rod force a lower bound. Rod
inclination and stiffness depend only on fixed box heights and the declared
gear, so the three allowable-load ratios are monotone in rod force. Both
stiffness cases and their exact synthetic limits are retained. This addition
needs no draught or GM estimate and does not alter final acceptance. It has
source-algebra review only; no CPU run or numerical witness yet.

### Independent compliance cross-check and witness

Final source audit of `early_bound.py` adds an independently evaluated
positive-coefficient formula for the rod. For the unrodded uniform grounded
chain, inverse entry `(i,j)` is `min(i+1,j+1)/k`. Adding rod diagonal
`r=rod_k*cosine^2` at anchor a gives

`u[a] = sum(f[j]*min(j+1,a+1)/k) / (1+r*(a+1)/k)`.

Every coefficient of equivalent joint force f is strictly positive because
the chain stiffness, rod stiffness, heights and rod cosine are positive. Input
forces/winds are nonnegative; CG-height fractions are in [0,1]. The helper
checks these conditions explicitly and evaluates the positive sum with
`math.fsum`. It compares against the original `_end_response` rod and
independently reconstructs source displacements from rack forces to check
the source matrix equilibrium residual. Relative rod disagreement or normalized
equilibrium residual above `1e-9` returns unknown. The used rod lower estimate
is `min(original, closed_form)*(1-1e-10)`; a proof still requires ratio above
`1+1e-8`. These are numerical safeguards, not an interval-arithmetic theorem.

The governing rod witness includes exact chain slots, stiffness, heights,
CG-height fractions, minimum inertial forces, fixed winds, equivalent joint
forces, positive rod coefficients, anchor compliance/feedback denominator,
original/closed/guarded rod values, inclination, allowable loads and residuals.
`selftest()` is available but was not executed by this subtask: it checks 128
small cases covering chain lengths 1--8, both source stiffnesses, CG-height
fractions at 0/1 and interior, positive force/wind increments, and invalid-input
rejections. The executive's profiler owns execution before the next map run.

The general matrix setting is discussed by M. Fiedler and V. Pták (1962),
[On matrices with non-positive off-diagonal elements and positive principal
minors](https://dml.cz/handle/10338.dmlcz/100526), Czechoslovak Mathematical
Journal 12(3), 382--400. The executive checked the primary metadata/PDF. The
specific spring-chain energy, maximum-principle and compliance arguments above
were derived directly from the pinned checker source; no exact theorem/page
claim from that paper is needed for them.
