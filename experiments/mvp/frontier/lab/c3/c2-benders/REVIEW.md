# Cycle 3: actual righting-area barrier and securing tradeoff

This subtask read the current contract, including Cycle 2 and Cycle 3 updates,
retained C2 rung-5 full-check reports and native source. It ran no numerical
process: while `c1/leader/BENCH-RUNNING` exists, the parent owns the team's sole
CPU-busy slot. This is the sole file written. No engine module was imported or
copied, no native source was changed, and no sealed map was opened. Numeric
values below are extracted from retained artifacts, not fresh measurements.

## The actual failed criterion

The 98-lift stow's search GM floor is a local search setting, not its actual
GM and not the source rule. The .6 artifact passes every rule except
`containership_area_0_flood_mrad`. All other containership area/lever tests,
initial GM, weather, securing, sight, strength and loading rules pass in its
retained full report. The precise source margin is

`A_positive(0, min(downflooding, first_vanishing_GZ)) - .029/C`,

subject to any source-authoritative tightening. This is neither a scalar GM
test nor the area only to 30 or 40 degrees.

| Recipe | Phase | Actual stability GM, m | Area, m rad | Required, m rad | Area margin |
| --- | --- | ---: | ---: | ---: | ---: |
| .6 search floor | departure | 1.288385 | .266726 | .289923 | -.023197 |
| .6 search floor | midpoint | 1.186614 | .275956 | .296393 | -.020437 |
| .6 search floor | arrival | .972665 | .243343 | .307794 | -.064451 |
| .9 search floor | departure | 1.575518 | .264751 | .275198 | -.010447 |
| .9 search floor | midpoint | 1.484682 | .279638 | .281080 | -.001442 |
| .9 search floor | arrival | 1.285905 | .256324 | .290736 | -.034411 |

Source artifacts are `c2/c2-benders/results/ballast-exact-r5-gm0p6.json`
and `ballast-exact-r5-gm0p9.json`, specifically `full_checker_physics` and
`full_checker_violations`. The .9 recipe still fails all three total-area
tests, as well as 28 securing emissions. Raising the floor did not solve the
stability criterion. Departure area actually decreased while its requirement
also decreased. Arrival remains the largest deficit.

At .6 departure, the curve vanishes at .829380 rad before flooding at .831344;
arrival vanishes at .823920 before flooding at .850839. Midpoint ends at
flooding .839127. The .9 recipes end at flooding in all three phases. These
changes alter derivatives and invalidate a single fixed integration endpoint.

Actual lashing GM is a separate quantity: .6 departure/midpoint/arrival is
1.350644/1.265460/1.041974 m; .9 is 1.637561/1.556431/1.354721 m.
The lashing helper's cargo centroid, finite-liquid and tangent-FSM arithmetic
differs from stability's loading helper. The .6 departure rod ratio is already
.996425, fitting ratio .990014 and rack ratio .979220. At .9 these maxima rise
to 1.049959, 1.054435 and 1.030943. The .9 limiting departure forward draft is
7.034803 m against 7.05 m: only .015197 m spare. Its arrival drafts are
6.251054/6.842507 m and trim -.591453 m, with .486547 m lower-trim slack.

## Why finite liquids offer an additional knob

The .6 recipe presses DB-A and DB-M pairs full, leaves only 31.852 t in
DB-F-P, and leaves every wing/HEEL/peak tank dry. The .9 recipe keeps DB-A/M
full, fills DB-F-P, puts 33.540 t in DB-F-S, and adds 235.758/222.204 t to
WING-A-P/S. Thus the observed comparison simultaneously changes displacement,
trim, KG, free surface and finite-angle liquid motion. It does not establish
a one-dimensional GM impossibility frontier.

There is a particularly relevant source distinction:

- `stability_loading.loading_condition` includes ballast in the regulatory
  correction list only for `0 < volume < .98*capacity`, subject to its existing
  small-tank exemption. At or above .98, that ballast's initial FSM and
  `curve_correction` contribution are omitted by the declared policy.
- Every positive physical fill is nevertheless retained in `actual_tanks`.
  Under the selected method 1, `liquid_cg` uses those tanks' actual tilted
  liquid centroids in floating/free-trim equilibrium even at .98 or above.
- `visibility._loading_condition`, also used by lashing, has no .98 cutoff
  and no small-tank exemption. It sums actual tangent FSM evaluated at the
  final loaded trim/list for every positive liquid fill. A .99 ballast tank
  can therefore affect lashing GM while remaining outside stability's
  regulatory correction list.

The promising hypothesis is to unpress a shallow DB tank just enough to
lower tangent lashing GM without losing comparable finite-angle area. Tangent
inertia and finite-angle liquid transfer are different functions of fill;
high-angle transfer can saturate while the small-angle penalty remains large.
Whether this improves the combined margins is an exact numerical question,
not a proved derivative sign or an infeasibility certificate. In particular,
crossing .98 changes a source branch and requires fresh exact evaluation.
Do not change the free-surface method, tank geometry, exemption or cutoff.

Four targeted probes suggested to the parent, starting from the .9 recipe:

1. Set both DB-M fills to .99. This separates unpressing/tangent effects from
   crossing stability's .98 correction boundary; about 6.25 t is removed.
2. Set both DB-M fills to .975. This crosses that boundary, removing about
   15.62 t, and maps the combined finite-liquid/regulatory effect.
3. Set both DB-M fills to .95. This tests whether the effect persists rather
   than being a tiny near-full numerical transition; about 31.24 t is removed.
4. Set both DB-A fills to .975. Only about 2.51 t is removed, isolating another
   shallow DB pair and a different longitudinal location.

These are manual proposed mass estimates from known tank capacities, not run
results. If another probe is affordable, transfer the drained DB-M mass into
equal HEEL-P/S fills to compare near-constant displacement with the drainage
probe. Exact soundings must determine the changed moments. Pair symmetry is
a useful search restriction but cannot prove infeasibility of asymmetric
recipes. A DB-F-P-to-S transfer can separately test list/FSM sensitivity.

## Exact nonlinear rows and a small search

Use the source margins for the actual three voyage states. Optimize normalized
`containership_area_0_flood_mrad` jointly with at least the actual lashing,
fitting and racking margins, and the full loading margin set. Keep all remaining
stability/weather margins available: improving one area can break the 30–40
area, downflooding or weather applicability. Other securing ratio families
remain acceptance obligations even when currently slack.

A nine-pair representation, optionally plus a few independent P/S split
variables, substantially reduces the derivative burden versus 18 unconstrained
tanks. First test the four physical probes above; use their exact differences
to decide which directions deserve a local Jacobian. Keep finite differences
one-sided at empty/full tanks. Refresh on .98 crossings, first-vanishing versus
flooding switches, governing-chain changes, new active margins, failed roots or
poor predicted progress. A trust region and exact re-evaluation of every
proposed recipe are necessary. Neither the local LP nor a Broyden Jacobian is
an outward model, so its infeasibility remains unknown.

The form factor used by `stability_criteria` is

`C = d*Dprime/Bmid**2 * sqrt(d/max(K_eff,d)) * (CB/CW)**2 * sqrt(100/L)`.

At fixed mass, trim, hull geometry and liquid correction, lowering effective
KG raises C and lowers `.029/C`; it also increases solid righting lever by
`-delta_KG*sin(phi)`. For a fixed endpoint, the area derivative is
`-(1-cos(endpoint))`. These conditional signs explain why lower cargo CG is
useful, but they are not global derivatives when drafts, free trim, liquid
centroids or the endpoint move. Cargo polish that reduces the actual heavy
deck-chain loads while lowering KG may enlarge the available ballast region;
merely lowering cargo KG can also raise lashing GM and must be rechecked.

For efficient probes, the existing exact `check_stability` part supplies all
required rows without a full operations/static-rule pass. Its expensive core
is `build_stability_curve`; skipping weather/peak/deck-edge postprocessing
alone cannot remove that cost. A copied search-only area evaluator could call
the same loading, floating, curve and criterion providers, but must preserve
actual liquid transfer, worst-TCG side, flooding root, vanishing root and
source angular sampling. It must never provide final acceptance. Cheap loading
and lashing rejection before curves is useful only when it does not discard
derivative samples needed to identify a simultaneous feasible direction.

## Primary references for sequential local models

R. E. Griffith and R. A. Stewart (1961), [A Nonlinear Programming Technique for
the Optimization of Continuous Processing Systems, Management Science 7(4),
379–392](https://pubsonline.informs.org/doi/10.1287/mnsc.7.4.379). The publisher
page and abstract were rechecked on 2026-11-01. It supplies the sequential
local-model motivation; no general convergence or global certificate is
claimed for this nonconvex ship model.

C. G. Broyden (1965), [A Class of Methods for Solving Nonlinear Simultaneous
Equations](https://doi.org/10.1090/S0025-5718-1965-0198670-6), Mathematics of
Computation 19(92), 577–593. The AMS endpoints returned access errors in this
session; the [original paper scan hosted by the University of New
Mexico](https://www.math.unm.edu/~vageli/courses/Ma576/papers/Broyden_65.pdf)
was read to verify the metadata and secant-update rationale. Reusing exact
function differences can avoid rebuilding an expensive Jacobian at every
step. Adapting that idea to inequality margins and nonsmooth branch changes
requires the safeguards above; the paper does not prove a ballast rescue.

## Source bindings and interpretation

The following SHA-256 values bind the reviewed providers. The parent should
check them again before/after numerical evidence; abort or mark evidence stale
if a provider changes. No hook or alternate checker policy is authorized.

| Source under `src/stowage/checker/` | SHA-256 |
| --- | --- |
| stability.py | 64c0a06761da330b5ab78efb2776c8cea12d37225f8059f581893dddfa5410e5 |
| stability_curve.py | 54a1c3406f25495775f61ba398a22fdbec17d19a867004ba53a32f2950ed1690 |
| stability_loading.py | c449d9d3df5ecf5e18f0bf899e6eba1f6677570cdfffc5fdb8d3acae26bc2533 |
| stability_criteria.py | ed293b8d45b8840ee4ffbcd7ea525b2950ddebe9099b036b19125f1643f48e09 |
| lashing.py | 6bf6a1ba92cf17687716fbb6eb8ac7bf528971356614134b84d5bb89f1ecfdb8 |
| visibility.py | 0f66102540b9038b2c8115b454b4671263540b32a0d131e8a92fed1da48cb560 |

The retained two recipes show a conflict, not a structural proof that no tank
setting or stow can pass. No Cycle 3 pass, timing improvement or numerical
derivative is claimed by this read-only subtask. Complete replay-legal stows
and full-checker passes remain separate gates. `c3/c3-feasproof` had no result
artifact at the time this review was written; any later family proof must
state which tank regimes and cargo geometries it actually covers.

## Subsequent read-only review of parent probes

The parent's `results/regimes/summary.json` subsequently supplied the proposed
drainage probes. Reading those records runs no numerical solver. They show
that unpressing is not, by itself, a rescue:

| DB-M pair fill | Arrival area margin, m rad | Arrival FSC, m | Departure rod ratio |
| --- | ---: | ---: | ---: |
| 1, baseline | -.034411 | .139392 | 1.049959 |
| .99 | -.035494 | .139491 | 1.045342 |
| .975 | -.051035 | .589474 | 1.036715 |
| .95 | -.058406 | .588007 | 1.019867 |
| .90 | -.072241 | .584867 | .980140 |

Crossing .98 creates a large stability correction jump, while lashing improves
gradually. At .90 departure rod and fitting pass, but arrival area has worsened.
The DB-A probes have much smaller effects. This evidence supports refreshing
the local model at the regime boundary and finding a combined redistribution
or cargo change; it does not support extrapolating one monotone rescue knob.
These are selected physics-part probes, not three-state full-checker results.

`physics.py` was source-reviewed: it calls ordinary stability/lashing parts
and re-derives candidate-dependent voyage conditions; exact mass-byte memo
keys include requested parts/phases and STOWAGE environment. It never calls
its selected-part feasibility a full pass. Current-threshold normalization
preserves raw-margin signs. Its source manifest originally omitted the central
`stability_loading.py` provider; the parent was advised to include it and the
liquid/hydrostatic dependencies or retain a broader before/after manifest.

`construct.py` was also read without executing it. Its transformed search
height `sqrt(10*z**p)` correctly gives `z**p` in both inherited phase-3 pricing
and master costs. It validates whole-pattern coverage/disjointness and real
replay before emitting a complete stow; the full checker remains external.
An inherited search-quality concern was sent to the author: when no reference
oracle is supplied, cgscale's page types omit base height, so reusing one
representative page's phase-3 vertical-cost pricing across pages of different
base heights can miss the intended lower-KG columns. This is a heuristic
quality issue, not an acceptance or physical-proof shortcut.

The newly arrived `c3/c3-feasproof` files describe a packing-only outward LP
and validation of two already-known passing 88/162-box plans. They do not yet
provide an area/securing contradiction or a new D185 full-checker pass.

## Sequential LP and subsequent constructor audit

The parent's `optimize.py` and the preserved initial driver under
`results/optimize-initial/driver.py` were read without execution. The local LP
signs are correct: `-J*d-s <= values-epsilon` means
`values+J*d+s >= epsilon`; the G/-G rows keep every tank in its capacity box,
and auxiliary absolute-step rows impose `u >= abs(d)`. Trial recipes are
evaluated by the ordinary three-phase physics oracle, and the selected final
recipe receives a fresh unchanged full checker. No local status becomes an
infeasibility certificate. The revised derivative builder stays within each
tank's .98 correction regime; the initial driver used a larger .025 step
which could cross that discontinuity. Exact probing made the initial run safe
for acceptance, though those differences are unreliable local derivatives.

A possible lost-candidate issue was reported: the search incumbent initially
updated only for strict merit improvement, while merit intentionally excludes
discrete validity flags. A physics-feasible zero-merit candidate must replace
an invalid zero-merit incumbent even on a merit tie; otherwise the final full
checker can inspect an older rejected recipe. This cannot create a false pass,
but can discard a real opportunity. Pair increments also preserve transverse
mass differences only when paired capacities are equal, as they are here up
to floating input precision; that comment is not a theorem for other vessels.

The retained initial search improved departure and midpoint total-area margins
above zero and reduced the arrival deficit to about .0135 m rad, with securing
only slightly above one. This is the parent's selected-part search evidence,
not an independent pass. The then-governing rod chain was
`0100282/0100284/0100286`, involving `QZXU0000380`, `QZXU0000729`,
`QZXU0001026`. A targeted legal exchange involving its heavy boxes can create
securing headroom while reducing cargo KG, which a blind scalar KG objective
may miss. A fixed-geometry solid-curve estimate associates .0135 m rad with
roughly .04 m effective-KG reduction; liquid/trim/endpoint effects prevent
using that estimate as an exact required move or a proof.

A further cheap physical target is raw upright `condition.tcg_m`, before the
finite-liquid equilibrium shifts. `build_stability_curve` subtracts its
absolute value projected on the transverse axis from the conservative
worst-side GZ. Reducing it can recover area across the curve even when final
equilibrium list is already legal. Pair-only increments lock the inherited
DB-F P/S mass difference of about 31.85 t. Adding one opposite-sign P/S split
direction at fixed pair total can test that restriction economically. Final
list alone does not determine the sign of the best split; use the actual
loading helper and then exact three-state curves. At fixed geometry the
area gain from reducing raw TCG by .01 m is of order .007 m rad for an
approximately .83 rad endpoint, before all coupled effects.

The appended constructor descent preserves exact concrete-box coverage with
two-column swaps/transfers and checks both affected full-page patterns. Its
LCG/heel/global-roof guards apply before final balancing/OOG repair, so the
author was advised to recheck those quantities after the actual replay if
reporting them as retained constraints. Its expanded-height vertical objective
matches the visibility cargo-geometry convention on supported pure pages;
stability loading uses ordinary box heights and supports instead. Horizontal
OOG offsets and fixed background likewise require the source helper for an
actual whole-ship moment. These distinctions affect search metrics, not the
unchanged full-check acceptance. The earlier page-height type-grouping issue
has subsequently been fixed by the author using per-tier height signatures.

The later full feasibility-relaxation row explicitly reports `unknown`,
`physical_proof=false`, and a feasible outward-model witness. Its physics scope
relaxes finite-angle and second-check curves, with no implemented finite-angle
area envelope. The constructive follow-up fails the full checker. Thus these
results supply neither a D185 pass nor evidence that every stow is impossible.

## Fresh verification and fixed-slot assignment audit

`verify.py` and `regression.py` were source-reviewed without running them.
The first verifier version reread the candidate plan after checking it, which
could bind the report to different bytes if an active optimizer overwrote that
path. The parent fixed this: input and plan bytes are now read once, parsed,
checked, copied and hashed from the retained snapshots. `complete_stow` now
requires both legal replay and every requested box aboard, and `accepted`
requires that gate plus the raw full-checker pass. The raw report outcome stays
separate. Imported native source hashes are retained; the broader before/after
source verification remains useful because the verifier's module manifest is
collected after its check rather than an immutable executable snapshot.

The regression finisher preserves the stored reference ballast recipe and
runs the unchanged full checker, after which the scorecard independently
checks again. Its explicit stored-reference lane and `cold_construction=False`
are honest: those passes test regression on known reference actions, not cold
constructor performance, a new medium rescue or an inherited historical
certificate. A failed present check cannot become a pass through metadata.

The current `permute.py` and its rank-relaxation update were read. It optimizes
box identities over a fixed occupied-slot topology. Exact length, ordinary
height, ISO category and E1/E6 heavy flags preserve physical support structure;
DG/OOG/heat/top-only and explicitly referenced boxes are fixed. In particular,
the native `T.fallback` flag denotes OOG. Every concrete assignment must pass
static eligibility, whole-column legality and real replay. Optional fitted
Fast-securing rows and their no-goods are explicitly search restrictions,
followed by direct Fast re-evaluation; none is a physical impossibility cut or
acceptance authority.

Rank relaxation is correctly implemented by assigning each occupied slot its
original concrete box's integer E2 rank and requiring every upper rank to be
at most every lower rank plus the unchanged tolerance. This reproduces the
source column check against the minimum lower rank. Keeping rank in a static
interchangeability group was a stronger heuristic restriction; dropping that
grouping field while adding these exact rows does not loosen E2.

The units are coherent: rounded mass grams times coordinate millimetres for
moment/objective rows, with tonne-metres scaled by 1e9. Stack share grams are
rounded upward and stack capacity grams downward, a constructive restriction.
Millimetre/gram rounding of the LCG/heel rows makes those search guards
approximate, so final exact moments should be reported separately. The chosen
topology's native stability-loading CG uses ordinary support/box heights;
expanded-envelope visibility heights remain a separate reported metric.
The ordinary full checker still decides every plan, including any projected
restow/lift changes from exchanging PODs. No numerical test of this module was
performed by the reviewer.

The near-legal redistribution reported by the parent (arrival area gap about
.000514 m rad and securing ratio about 1.0046) is still a rejected candidate,
not a pass. Neither that small residual nor the feasible outward packing model
establishes a whole-map impossibility proof.

## Independent artifact audit of the first D185 pass

The subsequently saved `results/verified-combined/` artifacts establish a
different outcome: a complete, legal 121-load plan passes the unchanged full
checker at 114 weighted nonproductive lifts and zero violations. This reviewer
ran only file hashing, JSON inspection and static action/load-identity checks;
no numerical checker or replay process was started. The parent's separate
fresh verification process supplies the acceptance measurement, 23.230286 s.

Raw byte SHA-256 values were independently recomputed:

| Artifact | SHA-256 |
| --- | --- |
| input.json | e5c94abe50f8bcf87f221d56c092e28c6b00e093a31de318db5765092015be8b |
| plan.json | b83cad9747d4edc2cf569f1a47fa8fb36021730cbae8fcc3359976b0f2a94891 |
| full-report.json | 0cb77cf9b322fc7006ef9428b733779e7eb9cefe2e62f4341cc0e3e3ea7851a9 |

All match `verification.json`. The input matches the actual approved TRAIN
file and its committed `lab/sets/MANIFEST.json` entry. Applying the exact
`full.py` canonical JSON serialization to the saved input and plan yields
instance hash `539d1f0b2518df6c432178f706baffaa94b95df5c20b9038bd68182d7d329f04`
and plan hash `509f0a6890cc8d6724656b1740f67a67f7530cc45afd18262167d914c94fa538`.
Both appear exactly in the report's `checker.*.sha256` provenance bindings.
Thus the report is bound to these exact saved inputs rather than matching
only their mutable IDs.

All 23 report parts ran, report no error and contain zero violations, including
ordinary moves/static rules and stability/strength/visibility/lashing/constraints
at departure, midpoint and arrival. `shut_out` is empty, provenance says
`created_by=checker.check`, and its frozen-provider `stubs` dictionary is empty.
The audited verifier imports no search oracle or hook wrapper and ran in a
fresh process. This supports the stated ordinary-checker acceptance path.

Static inspection independently found exactly 121 distinct load moves and 121
distinct placement actions, each set exactly equal to the requested load list;
none of those boxes was initially aboard. The report and verification both
retain legal replay and complete coverage. The checked objective decomposes
as 24 yard relocations + 12 current hatch lifts + 58 later restow lifts +
20 later hatch lifts = 114. No failed candidate lift count is used here.

All 88 imported-native source hashes in the verification manifest still match
current files. The six sources recorded at the start of the Cycle 3 physics
probes match their current hashes, and the six central providers recorded
earlier in this review also match. The current fleet/port parameter hashes
match the retained Cycle 2 full-report provenance: fleet
`797886f10c5e858a123ae61f0550a06be6e7d620c668544a215cf3f9a6b25281`,
Laem Chabang-like port
`ada7b775b71e48e7d7c7583d797b5017dca429f7f357bc998c97303149fa938d`,
Singapore-like port
`56ac0001aeb471a594d417cf8476303a9d5ee634736583ed3223ed70225b938e`.
This audit found no native-code/parameter change supporting the pass.

The separate `results/verified-independent/` process also records accepted,
121/121 loaded, legal replay, 114 lifts and zero violations, in 22.594679 s.
Its plan and full-report files are byte-identical to the first verification;
independent `cmp` checks succeeded. Arrival's total-area margin is only
`+.0001581347813580103` m rad, but the ordinary checker triggered its direct
near-limit refinement (`geometry_table_limit_fallback=1`, 8192 direct liquid
queries) and retained that positive margin. Departure/midpoint margins are
.0224356982/.0322662483 m rad. The maximum rod ratios are
.990776504/.987415084/.955614766 across the three phases.

No artifact-integrity or acceptance-gate defect was found. The remaining
qualification is scope: this is one adaptive TRAIN D185 full-checker pass,
with a tight arrival margin and the unchanged checker's prescribed three-state
voyage sampling. It establishes neither a cohort pass rate nor a global
infeasibility/optimality result. Repeating an identical plan validates its
certificate; it is not an independent cold construction run.
