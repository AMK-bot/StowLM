# Cycle 4 lower-bound proof audit

**Outcome:** certified totals stay **65 / 90 / 70 / 51**, at checked upper
bounds71/106/70/55 and gaps **6 / 16 / 0 / 4**. No provisional trim row was
attached to a reported bound or a cohort constructor. The70-lift equality has
the scope required by the contract: minimum D11 lifts among plans passing the
declared checker, with D15 fixed-stow later-port projection and no later loads.

| Component | Proof status | What the measured result establishes |
|---|---|---|
| Forced yard relocations + C3 port-Hall/E6 certificate | Retained independent certificate on the four hash-matched TRAIN inputs | Supplies65/90/70/51 independently of every new physics row. |
| Displacement and weather physical-KG inequality | Necessary source-algebra row under the stated departure/tank domain; integer translation outward | New physics CP did not exceed the retained certificate. |
| Minimum-motion own-fitting/global deck-force inequalities | Necessary source-algebra rows with the pinned lashing formula, GM_lashing floor0 and relaxed root location | No own-fitting allocation was excluded on the four maps at that safe floor. |
| Cell/tier/no-gap minimum-height support floors | Necessary on the admitted empty-arrival, disjoint fixed/decision support geometry | Stronger low500 relaxation still has optimum51; no bound gain. |
| Proposed trim/list CG enclosure | **Uncertified; diagnostic only; never attached** | Seven analytic/safety tests pass. High88 diagnostic4.987s/40tableboxes is not a new bound. |

The new supporting-stack and tier variants return the maximum with trusted C3
certificates after matching instance ID and input SHA. Every final number is
therefore also backed by that older certificate. None of Cycle4's new physics
claims is needed to assert an improvement in the bound: **there was none**.

## Audit of the retained new physics algebra

`physics_bound_notes.md` gives the full derivation; this records the sensitive
rounding and provider distinctions independently of construction quality.

1. **Fixed masses are counted once.** Exact Fraction arithmetic on binary input
   mass/CG values supplies lightship plus FixedLoad aggregates plus load-list
   mass. The per-box geometry of a FixedLoad does not add another mass.
   Closed final covers remain in lightship. Empty arrival, no discharges,
   forecasts or later loads and nonnegative objective coefficients are guarded.

2. **The weather row concerns upright physical KG.** Native weather applicability
   requires virtualKG/draft<=1.5; FSC is nonnegative. Consequently ordinary
   upright physical KG<=1.5*legal maximum draft. Cargo CG uses ordinary half
   height, matching stability_loading, while the separate securing floor uses
   .45 of expanded physical OOG height. Upright sounding polynomials are an
   analytic representation of the tank solid, not the cubic tilted-liquid
   transfer interpolant discussed below. Every upright liquid arm is bounded
   below by the declared solid's z minimum. The implemented upperKG is widened
   by1e-7m guards, and displacement comparisons by their normalized tolerance.

3. **Ceil-kg liquid variables are outward.** For actual massm, mapu=ceil(m).
   For any raw coefficienta, setc=floor(a). Then
   `c*u <= a*m + max(0,c)` for nonnegativem: negativec is helped by roundingu
   upward; positivec costs less than one extra unit. Each upper moment row
   receives exactly that positive-coefficient allowance. Cargo coefficients
   round down, right sides round up. Upper mass rows gain one kg per tank;
   lower mass rows round their constant upward. Capacity domains use the same
   declared capacity product as full._ballast's strict adjustable-tank check.
   Scope additionally assumes fixed/arrival tank contents within their nominal
   declared capacity; a generic application beyond these TRAIN inputs should
   explicitly guard that input premise. Stability's local1e-12 overfill
   tolerance alone is not a reason to assume arbitrary over-capacity inputs
   satisfy that domain.

4. **No stability GM floor is lent to securing.** Visibility/lashing and stability
   have different liquid/FSC policies. Only the necessary lashing-GM limit0+
   is used. The copied envelope pins lashing source SHA, derives the immutable
   surge/sway/heave terms and rational lower sine/square-root bounds, and permits
   rotational cancellation below half hull depth. It takes minima over every
   member of an aggregated class. Extra upper boxes cannot reduce the retained
   own horizontal-force contribution. Own compression is deliberately omitted
   because asymmetric reactions can be negative. Ratio exclusions use1e-7
   slack, wider than the checker's1e-10 limit comparison.

5. **Global chain capacity survives shifted long pairs.** A physical deck column
   has at most two root end chains; each root fitting's two corners allow
   `2*.85*210000N` transverse force. Summing gives714000N per physical column.
   Long OOG coefficients use the minimum nonnegative end share. The model sums
   globally, avoiding an unsupported claim that shifted chains stay in an odd
   bay. Force coefficients round down; capacity receives1e-7 relative slack.

6. **Tier floors follow support, not nominal cell pitch.** On the stronger
   variant every physical cell is used at most once, and occupancy at tierk
   implies allk preceding physical cells are occupied. Each predecessor's
   ordinary height is at least the minimum load-list height. Therefore actual
   support is at leastbase+k*h_min. Fractions round this floor down; a long
   footprint takes the minimum across supports. Background cargo is admitted
   only on disjoint fixed stacks/cells. Support-height equality, ordering,
   visibility, exact securing and finite-angle area remain relaxed.

The algebra uses explicit outward integer/rational steps. The copied nonlinear
motion lower envelope retains its documented source-floating force guard;
this is not a formally verified implementation of IEEE/libm or actual-ship
physics. No new empirical hull interpolation error is promoted to a proof.

## Why the trim implementation is deliberately ineligible

The exact rational clipping and Bernstein-polynomial identities are useful
building blocks, and the tests establish their analytic controls. They do not
by themselves certify the **whole native checker dispatch**.

* Native direct polygon integration evaluates signed distances/intersections
  in floating arithmetic. The direct diagnostic's operation-count/gamma and
  geometric allowances do not yet have a completed perturbation proof covering
  almost-parallel edges, rounded intersection closures and all reductions.
  A large guard or passing analytic control is not a uniform proof.
* Native hull tables use signed tensor cubic Lagrange weights. Their sampled
  qualification certificate is not a uniform error bound. The diagnostic does
  correctly transform the complete stored polynomial to Bernstein form and
  records table bytes; it never assumes positive interpolation weights.
* Loaded `liquid_cg` may independently use cubic **liquid moment** tables and
  grouped/batched providers. Physical-solid tank CG boxes alone need not bound
  every interpolated loaded CG without an envelope for those active tables.
  Enclosing the hull table alone therefore leaves another proof obligation.
* Loading trim/list applies to the listed floating state. It does not impose
  that trim on the zero-heel/free-trim finite-angle GZ curve. No finite-angle
  area bound follows from these diagnostic CG ranges, and GM is not substituted
  for the binding containership_area_0_flood_mrad test.

Every diagnostic returns `certificate_eligible=False`. The adapter refuses it,
and the safety test checks that refusal. More than the admitted number of table
boxes returnsunknown, never a truncated enclosure. No full CP search was run
with those provisional rows. `trim_bound_notes.md` records the algorithm,
runtime and exact limitations. This is a proof-work negative result, not a
whole-map infeasibility claim.

## Measurements and resource record

The six retained-physics/tier tests passed, including fresh replay and necessary
assignment images of all four checked incumbents. Seven separate trim analytic/
safety tests passed in0.69s. Numerical jobs were admitted by root, sequential
while BENCH existed, and at most two team processes after it cleared; all were
one numerical thread, no bytecode/pytest cache, local vessel cache. Nice was
requested but priority changes were denied. All owned sessions exited normally,
no process was killed, and the numerical token was explicitly released.

The decisive longer control is `bound_long_results.json`: high88 at240.001s
found a feasible **18-shipping-lift relaxation witness**, four covers/five
edges/zero counted restows. That equals the independent Hall shipping bound18.
Adding the already certified objective cutoff would make the augmented
relaxation optimum18 (65total). Thus further solving of that same relaxation
cannot close the six-lift operational gap. The witness is not a legal stow.
Signed trim/list, actual support heights, visibility, exact securing and the
binding finite-angle area are precisely the missing physics/geometry; this
audit does not pretend any one of them has been proved to close the gap.

Main links: [physics derivation](physics_bound_notes.md),
[trim diagnostic and limits](trim_bound_notes.md),
[four-map results](bound_results.json), [tier results](bound_tiers_results.json),
[longer control](bound_long_results.json), [trim diagnostic](trim_envelope_results.json).
