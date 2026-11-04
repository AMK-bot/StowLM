# Cycle 4: total-area feasibility windows

**Verdict: keep part. No medium12 infeasibility proof.** The known
`random.medium.185100000.0.2` plan and every saved passing TRAIN f60/f70
plan remain feasible. The other eleven medium12 maps remain **unknown**.
`windows.json` is the handoff to c2-benders: per-map phase KG, mass, draft,
upright trim and deck-load windows, shared departure tanks, fractional
allocation targets and known legal targets. Candidate area bounds are kept
separate and are **not installed as strict cuts**.

The construction is a class-to-real-bay/tier fractional transportation graph
with shared cargo moments and departure tank masses over the three D57
voyage conditions, necessary securing inequalities, and a spatial interval
cover of buoyancy/finite-angle GZ poses. This is a relaxation of a stow,
not a complete stow constructor. It does not establish that any of the eleven
unresolved maps is impossible.

## Scope, activation and provenance

Read the entire CONTRACT through Cycle 4 and lab README; MEDIUM-WALL;
C2 Benders/layout notes; C1 early_bound.py and REVIEW; C3 Benders NOTES and
BINDINGS; and the native checker loading, hydrostatics, geometry, liquid,
stability curve and voyage providers. All writes are under this C4 directory.
No sealed data, mutable engine imports, source checker patches, commits,
external messages, or process-pattern kills are used.

The native rule catalogue is unchanged, `RULE_FREEZE_V1` is false and no frozen
stub override is active. The all-requested-onboard gate and ordinary full
checker establish passes. The 0.9+E9 constructor is not benchmarked: this is a
proof/window task, so there is no paired lift-improvement claim. Preselected
passing f60/f70 development plans are validity controls, not an unseen set or
a lift benchmark. The D185 plan is the existing warm targeted 114-lift pass.

Local `_legacy/physics.py` and `_legacy/rod_bound.py` were copied from C3
feasproof. `joint_model.py` began as a copy of C3 packing, with the joint
voyage/moment model added here. The legacy files' lashing source hash remains
pinned. Input, plan, native source, interpolant contents and experimental
source hashes are retained in the JSON artifacts. They permit stale evidence
to be detected; they do not certify empirical interpolation error.

## Why the area rule remains unresolved

The binding rule is `containership_area_0_flood_mrad`, the positive GZ area
up to flooding or first vanishing, with its containership form-factor target.
It is not scalar GM. The accepted D185 arrival values are 0.291164308202 and
0.291006173 m rad, only about 0.000158135 m rad apart.

`area_bound.py` represents every finite free-trim waterplane by heel, slope
angle alpha and halfspace offset. Alpha covers [-pi/2,pi/2]; upright trim
limits are not reused as finite-angle trim limits. For each interval box,
certainly-wet and possibly-wet polygon shells enclose volume and first
moments using exact rational clipping of the checker's own sections and
quadrature weights. Mass and longitudinal residuals include the native
2e-6 solver acceptance band. Tank callback movement and signed transfer
corrections enter separately. A box is rejected only if its entire residual
interval is disjoint; unfinished boxes stay in the upper envelope.

The source integrates a piecewise-linear sampled curve, not an ideal
continuous GZ function. The slab lane dilates endpoint bounds by the native
two-degree span; a second knot lane uses exact fixed knots and one outward
bonus for a possible arbitrary flooding endpoint. Integrating positive upper
GZ through 90 degrees relaxes flooding and vanishing. Thus failure to refute
a coarse cover conveys no feasibility conclusion.

The default checker can use qualified tensor-cubic hull and liquid tables.
Their qualifications sample errors; they are not uniform mathematical error
certificates. `table_bound.py` instead encloses the actual interpolating
polynomials using rational Lagrange-to-Bernstein conversion, tensor interval
contraction and operation reserves. It splits stencil changes and covers
hull edge clipping versus liquid edge extrapolation. Native hull/liquid
ranges are unioned with direct geometry. The liquid union explicitly keeps
prismatic zero-trim subdomains and the special pressed-full centroid.

**A remaining proof obligation prevents a checker infeasibility claim.**
Exact rational clipping encloses real polygon geometry, but it does not by
itself enclose every floating result of the native direct fallback. At extreme
free-trim slopes, normalization and polygon-plane transforms can be poorly
conditioned. A guessed fixed epsilon would not prove uniform containment.
Direct liquid fallback arithmetic has the same outstanding obligation.
Consequently native-union/default-checker area results have
`checker_outward=false` and `infeasibility_proof_eligible=false`. Regression
inclusion of the known plans is necessary, but cannot repair this theorem gap.

The delivered area sweep uses one complete heel interval and all eight initial
alpha boxes, without discarding unresolved boxes. This is fast and very loose.
The large candidate caps do not narrow KG. The tighter knot/spatial APIs are
available, but two larger pilot covers were interrupted through their own
recorded exec sessions when exact clipping was too expensive; neither yielded
a retained complete bound or a refutation. Local optional GMP rational
arithmetic and cached native-table extrema were then added. An exact-pose
D185 hull/table query and a complete coarse arrival cover were exercised.

## Cargo, tanks and securing windows

Exact cargo mass/height classes are allocated to signatures of real physical
cells. Cell capacity, prefix occupancy, summed stack mass/height and unary
eligibility are necessary rows; row identities and detailed support are
relaxed. Hold capacity forces deck load. The exported deck-count lower bound
also charges the physical-cell deficit divided by the maximum footprint;
for D185000 it is at least 66.5 boxes in the continuous relaxation.

Cargo X/Z moments are shared across phases. Fixed tanks retain exact onboard
masses; adjustable tanks have one shared departure variable. Default D57
consumables scale by 1, .55, .1 and arrival uses its own density/environment.
Mass-dependent explicit kg-consumption declarations are unsupported and must
be unknown. Exact rational fractional-linear tank ratio bounds give scalar
KG/LCG windows. Four short cargo-moment support LPs provide algebraic bounds
with exact rational bounded-residual Lagrangian certificates, verified by
`audit.py`'s separate implementation. The certificate proves the serialized
matrix bound; source-coefficient outwardness is a separate obligation.

Securing uses its own native GM provider. It is never equated with stability
GM. Necessary own-fitting/global force rows and positive-chain rod response
rows from the early_bound argument apply at a lashing GM lower endpoint of
zero. Rod resultant/horizontal/vertical rows include occupied bottom-root
capacities, avoiding fictional rods under empty columns. These are weak
necessary constraints for every phase, not achieved securing ratios. Phase
KG or upright draft is not silently substituted into a securing GM surrogate.

## Verification and reproduction

`controls.json` contains fresh unchanged checks of all 19 distinct saved
passing plans on 12 maps, including all passing f60/f70 plans, the 88-box and
162-box examples and D185000. All 19 are replay-legal, complete and full
checker passes; 57 voyage-phase area measurements are retained. Check time
is 335.78 s total. No new constructed legal plan is claimed.

`run_windows.py` serially processes medium12 and all 11 distinct f60/f70
control maps, saves per-map timings, witnesses, independent raw mass audits,
exact support certificates, and projections of every control. `run_area.py`
adds all 69 map-phase coarse covers and tests all 57 positive-control phase
areas. `MEASUREMENTS.md` records the final exact counts/times and per-map
surviving windows. Unit tests cover both rational backends, real toy native
geometry/liquid behavior, source interpolant edge semantics, and independent
certificate verification. No new broad test suite is needed for this local
experiment.

Run one CPU process at a time while BENCH-RUNNING exists, with all numerical
threads set to one and bytecode/cache outputs inside this directory:

```sh
PYTHONDONTWRITEBYTECODE=1 STOWAGE_VESSEL_CACHE_DIR=experiments/mvp/frontier/lab/c4/c3-feasproof/cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=src:experiments/mvp/frontier/lab/c4/c3-feasproof:experiments/mvp/frontier/lab/c4/c3-feasproof/vendor .venv/bin/python experiments/mvp/frontier/lab/c4/c3-feasproof/run_windows.py
PYTHONDONTWRITEBYTECODE=1 STOWAGE_VESSEL_CACHE_DIR=experiments/mvp/frontier/lab/c4/c3-feasproof/cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 PYTHONPATH=src:experiments/mvp/frontier/lab/c4/c3-feasproof:experiments/mvp/frontier/lab/c4/c3-feasproof/vendor .venv/bin/python experiments/mvp/frontier/lab/c4/c3-feasproof/run_area.py
```

The optional local GMP wheel is gmpy2 2.3.2; standard-library Fraction remains
supported and tested. No global environment dependency was modified.
All jobs were serial across this team. `nice -n 5` was attempted; this managed
macOS session rejects setpriority. No completed run exceeds 20 minutes.

Keep the joint windows and independent checks. Next certify the direct
floating kernels with an explicit error enclosure, then use volume/longitudinal
contractors to eliminate the extreme-slope tails before refining heel knots.
Only then can a small area/securing contradiction become a valid map proof.

## References

Moore (1966), *Interval Analysis*, supplies the interval-enclosure framework.
McCormick (1976), *Computability of global solutions to factorable nonconvex
programs*, motivates outward nonconvex relaxations; no sampled tangent is
used as a global bound here. The polynomial range construction uses the
Bernstein convex-hull property; see the primary paper
[Robust arithmetic for multivariate Bernstein-form polynomials](https://www.sciencedirect.com/science/article/pii/S0010448500000567).
This implementation uses interval polygon geometry and Bernstein conversion,
not a claim that McCormick envelopes alone certify the checker.
