# Cycle 2 mathematical and source review

Read-only source review, with this document as the sole write. No numerical
map process ran in this subtask. Required reading included the entire lab
contract and Cycle 2 update, lab README, fx-finish NOTES/REVIEW/early_bound/
fast_finish, fx-scout NOTES/pages, SCALE-SOLVER and the E9 wrapper. Additional
checker sources were inspected to establish the dependencies below. Measured
latencies, stow counts and passes belong to the executive's run artifacts;
none is asserted by this review.

## Model and the two kinds of cut

A page-pattern variable chooses a complete ordered stack, covers its boxes,
and occupies its physical stack or stack pair. Given an integer selection,
the ballast subproblem chooses tank masses subject to the checker's three
D57 voyage conditions. Cargo moments are sums of pattern contributions.
Nonproductive yard, hatch and restow operations determine lifts; ballast
changes do not directly reduce that objective. One real small and one medium
example are already documented in fx-finish/REVIEW: the small TRAIN plan has
19 operation states and 18 adjustable tanks, the 163-load medium plan has 263
operation states and 18 tanks. These are graph examples, not new acceptance.

For an outward LP written `A b <= d - B y`, with every tank bound included as
a row, a ray `lambda >= 0`, `lambda A = 0` proves the necessary master cut
`lambda B y <= lambda d`. Save the complete matrix, right-hand side, tank
bounds, witness and independently checked residuals. A small nonzero dual
residual must be bounded using the finite tank box before declaring a strict
contradiction; a solver status alone is not an independently verified ray.

Logic-based cuts use a different proof. If preserving a complete securing
chain always violates a ratio, forbid any master selection preserving that
chain. For one isolated pure-stack page-pattern this is simply `y[p] = 0`.
For several patterns needed to preserve the proof's topology, use
`sum(y[p] for p in C) <= len(C)-1`. A whole-stow no-good is weaker but safe
after a valid fixed-stow proof. A rejected *local LP* or a failed single
ballast recipe does not authorize any of these physical-feasibility cuts.

In the inherited scout code, `lcg_target` is explicitly a soft row with a
penalized slack. Tightening that parameter guides search; it is not a hard
Benders cut. Learned hard rows must also appear in future priced columns,
integer/tail solves and any direct completion path. Post-LP filtering alone
can recycle prohibited candidates or invalidate pricing-based exhaustion.
The original rounded-up knapsack weights, fitted lashing filter, restricted
ordering and pure-stack family make the scout's `proved` flag insufficient
for an unrestricted-map impossibility claim.

## What ballast can change

| Checker quantity | Dependency and valid conclusion |
| --- | --- |
| Displacement | Exactly affine in tank masses. Tank capacity bounds and explicit displacement overrides support outward linear tests. |
| Trim, aft/forward draught, list | Depend on nonlinear hull equilibrium and tilted-liquid centroids. A local affine approximation is search guidance until a uniform outward error bound is established. |
| GM, GZ, weather | Depend on KG, free surface, liquid regimes and hull geometry. Ballast can improve or worsen them; maximizing GM alone need not improve securing. |
| Visibility | Cargo/cover silhouettes are fixed, but the sightline projection uses actual trim, draught and list from visibility's own loading helper. A default-fill sight failure is not ballast invariant. |
| Securing | Actual forces depend on motion/GM/draught. Proven motion floors yield ballast-independent lower ratios; exceeding a limit with such a lower bound is a contradiction. |
| Physical stack mass/height, fixed OOG envelopes | Ballast does not change these inputs. Only source-authoritative hard violations with the required support/background semantics justify a fixed-stow proof. |
| Strength | Both buoyancy and tank liquid distribution change. It remains an exact-check obligation even if trim, draught and list are acceptable. |

In particular, `visibility.check_visibility` computes a waterplane normal,
along/across basis, eye height, projected bow range, roof clipping and angular
blind intervals from `visibility._loading_condition`. It would be incorrect
to report sight among rules ballast categorically cannot rescue. An outward
pose-domain obstruction proof could establish a specific invariant failure,
but no such proof follows from one failed sight report. Stability's loading
helper is not interchangeable with visibility's helper for bounding that pose.

The three D57 states are derived by `conditions`, including consumable
drawdown and phase density. Independent tanks in each state relax shared
ballast decisions and can be outward for rejection, but a relaxed feasible
answer may not correspond to any one usable recipe. Adjustable consumables
must be re-derived per candidate, as the native evaluator already does.

## Securing proof and its stated family

The pinned `early_bound` follows the checker's actual support/end-frame graph.
The scantling block coefficient, surge, sway and heave give tank-independent
acceleration floors. Elevated cargo CGs retain stronger positive pitch/roll
gravity terms only after their rotation lever and angle range are proved.
There is no adjacent-stack wind shelter in this checker, so changing an
unconnected neighboring page does not reduce the fixed chain wind.

For a chain of n boxes with stiffness k and rod anchor a, let `f[j]` be the
equivalent joint load. The inverse of the grounded unrodded chain has entries
`min(i+1,j+1)/k`. If `r = rod_k*cosine**2`, the rod-anchor displacement is

```
u[a] = sum(f[j]*min(j+1,a+1)/k) / (1 + r*(a+1)/k)
rod_force = rod_k*cosine*u[a]
```

All coefficients are positive. Equivalent joint loads are nonnegative linear
combinations of transverse inertial forces and fixed winds when every
CG-height fraction is in [0,1]. Thus substituting acceleration floors can
only lower rod force. Both source stiffness cases and all three source rod
limits remain required. The implementation cross-checks its formula against
the source solve and equilibrium residual, rounds downward, and uses a
stricter rejection guard than the checker. These floating guards are not an
interval-arithmetic formal proof.

The corresponding useful family is: fixed vessel, support topology, footprint,
tier count, actual box heights and CG fractions, OOG/eccentricity, base height,
background and gear. The rod lower bound is then a positive weighted sum of
`max(gross_mass, checker_minimum_mass)` plus a fixed wind term. A necessary
weighted-mass row can therefore exclude heavier assignments within that same
geometry family. This is stronger than banning one identity-labelled pattern.
It does not prove that every cargo stow on the map fails.

For a simple implementation, ban the exact full pure-stack pattern after
verifying it preserves the complete governing chain and its two end-frame
supports. Do not generalize witness-slot lists blindly: mixed 20-under-long
support can join pages, and adding/removing tiers changes the rod anchor,
angle and stiffness. Any smaller no-good must retain every premise needed by
the proof. Existing corner/fitting/guide bounds have separate nonnegativity
guards; a guard failure means unavailable bound, not infeasibility.

## Linearization, time and verdict semantics

Finite differences of the checker's hydrostatics use the right source but do
not make a global outer approximation. Tank endpoint/slack transitions,
finite-liquid centroid changes and free-surface rules are nonlinear and can
be discontinuous. Empirical residuals on sampled recipes are not uniform
error bounds. An infeasible such LP must be labelled `unknown` for ballast
rescue, with LP-only diagnostic status. A feasible recipe must be rechecked
exactly, and only the unchanged full checker establishes a pass.

An LP with roughly 18 variables and a few dozen loading inequalities is a
reasonable candidate for subsecond *LP-solve* time. Exact context construction,
finite differences, three-state checks and full acceptance have distinct
costs; no source argument proves subsecond end-to-end latency. Report each
separately, including warm/cold setup and failed numerical evaluations. The
spring-chain test needs no tank-equilibrium solve and is the first rejection
screen to measure. Earlier cycle timings are historical observations, not
fresh Cycle 2 benchmark results.

Use three outcomes: `ballast_fixes` only with a full-checker pass and recipe;
`proved_cannot` with an outward or logic-based certificate and stated stow
family; `unknown` for every remaining case, including search exhaustion,
local LP infeasibility, failed exact root and timeout. Report complete
replay-legal stows separately from full-checker passes, as the Cycle 2
contract requires. E9 search correction must be installed before the master
builds its tables; checker rules and tolerances stay unchanged.

## Primary papers

- J. N. Hooker and G. Ottosson (2003), [Logic-based Benders decomposition,
  Mathematical Programming 96, 33–60](https://link.springer.com/article/10.1007/s10107-003-0375-9).
  The [author manuscript](https://johnhooker.tepper.cmu.edu/benders2.pdf)
  explains deriving cuts by identifying the assumptions under which a
  subproblem proof remains valid. That is the role of the securing-chain
  witness here; a generic nonconvex local optimizer supplies no such proof.
- G. Codato and M. Fischetti (2006), [Combinatorial Benders' Cuts for
  Mixed-Integer Linear Programming, Operations Research 54(4),
  756–766](https://pubsonline.informs.org/doi/10.1287/opre.1060.0286).
  Its discrete master sends candidates to an LP and learns combinatorial
  cuts from infeasible subsystems. This motivates small conflict cuts after
  an actually valid ballast contradiction. The publisher abstract was read;
  no full-text theorem beyond that evidence is attributed here.

Primary pages were checked on 2026-11-01. No downloaded code was run. This
review establishes source/algebra conditions, not numerical correctness of
new implementation files; the executive owns their tests and map evidence.

## Implementation audit during Cycle 2

Source-reviewed `ballast.py`, `benders.py` and `audit.py` after their initial
implementation. No numerical process was started for this audit.

`ballast.solve_ballast` correctly keeps local hydrostatic LP infeasibility as
`unknown`, and its local ray explicitly has `physical_proof=False`. The sole
implemented outward hydrostatic proof is an intersection of shared adjustable
ballast mass intervals across the three phases, after validating identical
tank ordering/capacities and excluding variable consumables. Its enlarged
mass tolerance is conservative relative to the checker's absolute mass
tolerance. The exact loading probe requires the full phase-appropriate key
set, so early negative-GM returns cannot become false loading success. A
missing `stability` import on the residual-correction path was reported and
fixed by the author. The mass proof's arithmetic guard now uses the fixed
full-capacity total, rather than a progressively clipped interval upper bound.

The initial Farkas helper checks a small floating dual residual without its
worst-case effect over variable bounds. Such output is an approximate
numerical ray, even for its local LP. This does not compromise the physical
cut gate because the local ray cannot produce `infeasible_proven`; a rigorous
LP witness additionally needs bounded-residual or interval verification.
The author subsequently relabelled the helper as an approximate diagnostic
and added the same active-frozen-stub refusal used by the securing bound.

The rod-only partial cut in `benders._cut` is valid for the present pure-page
pool: the witness contains the entire governing chain, every witness slot
must belong to the planned assignment, the selected patterns fix the whole
page content, and the master forbids another chosen page from sharing any of
their physical support stacks. Thus changing an unselected disjoint page
cannot alter that chain. Other securing certificates use a whole-stow cut.
Mixed/holed seeds are rejected; background geometry is fixed. This review
does not authorize copying that partial cut into a mixed-page master.

The author's revised replay-combination exclusion is labelled uncertified.
That is appropriate: failure of a deterministic sequencer is useful search
feedback but does not prove that another order of the same cargo assignment
is impossible. The master reports restricted-pool exhaustion only, without
promoting it to full-family infeasibility. A full-checker acceptance and a
claimed physical proof on the same assignment must be treated as inconsistent
evidence, with no cut, rather than allowing either result to overwrite the
other silently. The final inspected master now raises an explicit error in
that case and defaults to calling the ballast subproblem for surviving stows.

`audit.py` correctly distinguishes historically reported stows from
reconstructed stows: the archived failures did not retain assignment/plan
bytes, so matching historical lifts cannot establish identity. Historical
verdicts consequently remain unknown. Its initial row-resume condition checks
the input digest only; the executive was advised to use fresh outputs after
changing implementation, budgets or check settings, or bind those inputs in
the cache key. Source manifests before/after verify unchanged dependencies
within a run, not equivalence of differently configured resumed rows.
