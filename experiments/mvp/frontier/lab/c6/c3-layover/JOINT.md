# Box and ballast loading rows in one assignment

The small development diagnosis exposed an affine contradiction: all available
box slots lie forward, while inherited tank moments make the cargo-only target
require boxes much farther aft than any slot. `JointLoading` lets the same
LP see tank fill fractions and cargo placement edges together. It replaces
that unreachable fixed-background cargo target with loading equilibrium rows.

`JointLoading(instance, reference_plan, gm_floor)` accepts a **complete concrete
plan built inside the current method invocation**. The plan may contain that
invocation's selected ballast recipe. The solver must never initialize it by
reading a saved plan or earlier ballast recipe. The reference supplies local
hull/liquid calibration, not a warm benchmark incumbent. The optional native
unit probe described below deliberately reads a development saved plan and is
labelled warm diagnostic work; it contributes no cold result or pass.

For departure, midpoint and arrival, the builder uses native D57 derivation,
`ExactContext`, and the native `_probe` loading conditions. Given consumable
masses remain in each phase's fixed background. Adjustable consumable tanks
are rejected by this lane because a fixed phase derivation could otherwise
misrepresent their draw-down. Adjustable water tanks share one fraction per
tank across all phases, so the LP cannot pump during the voyage.

Each reference context's base includes all modeled departure cargo. The
builder subtracts exactly that cargo's mass and x/y/z moments once, including
kept boxes. It retains lightship, background fixed loads and fixed liquids.
Every box/slot variable then re-enters with its own kg and kg·m coefficients.
A kept box's fixed variable equals one, so its contribution is constant without
being duplicated. Support heights and body half-heights follow native
`stability_loading`; the helper copies only its simple cargo accumulation.
Fixed-prefix support elevations are exact. The free-support height in the
assignment LP is a reference approximation if box heights vary; the recorded
cohort uses ordinary standard-height ISO20 bodies, for which it is exact.

The local `ballast_loading._assemble` extension adds these pair coefficients
**before** forming or normalizing any row. It receives cargo mass columns,
cargo moment columns and per-phase removed constants. The existing assembler
then creates the same buoyancy and longitudinal equilibrium equations,
loading-policy mass bounds, draught and trim bounds, minimum GM, and list
rows. No normalized right-hand side is reverse-engineered. Original contexts
are not mutated.

Native aft/forward draughts set each phase's hull reference. Native GM minus
the assembler's half-fill-tank affine GM supplies a local bias, preserving the
requested GM floor at that reference despite the half-fill FSM approximation.
The builder also anchors both hull equilibrium equations at that same native
reference: it adds `reference_model_mass - reference_hull_buoyancy` and
`reference_model_x_moment - reference_hull_buoyancy*reference_LCB` to their
physical right-hand sides before normalization. The reference model includes
the complete cargo and the selected tank fractions using the assembler's
upright fixed-liquid/half-fill adjustable arms. Thus unchanged reference cargo
and water at the native reference draughts satisfy the two affine balances
exactly. This local residual calibration is distinct from relaxing a draught,
trim, mass, GM or list policy limit; those bounds remain unchanged. It does not
make the tangent valid for arbitrarily distant cargo or water changes.
Tank geometry, finite-angle liquid shifts, GZ areas and visibility still need
ordinary full checking. Securing and sight rows remain in the assignment's
existing physical stack model; this builder only adds loading coordination.

The generic assignment interface returns:

- `A_pair`, with one column per native box/slot edge;
- `A_aux`, with adjustable tank fractions first, then two draughts per phase;
- bounds, costs, row names, and calibration metadata;
- `candidate_recipe(aux_values)`, which declares adjustable tank masses while
  leaving fixed tanks inherited.

Auxiliary bounds are physical: tank fractions 0–1 and draughts 0–hull depth.
The default MILP tail keeps water and draught auxiliaries **continuous** and
box edges binary, so local affine hull equations remain exact. The optional
`equality_grid_reserve=True` supports the older quantized CP path: only the
construction's hull equalities receive a band equal to the sum of one full
auxiliary grid step's row effects. Default fractions are 1e-4 and draught
steps 1e-3 m. This optional approximation changes no checker limit; policy GM,
draught, trim, mass and list rows remain hard. It is disabled by default.

The decoded recipe belongs to the assignment that produced it. Reordering,
exchange or repair after that solve may change moments; native replay and full
checking must judge the final pair together. Neither LP/MILP success nor a
local GM floor is a full pass or a whole-map infeasibility certificate.

## Optional pressed-or-empty branch

`JointLoading(..., tank_mode='pressed')` is a separate restrictive search lane;
the default `tank_mode='half'` remains the continuous-filling model described
above. Pressed mode uses full-capacity sounding arms and zero adjustable-tank
FSM. Every adjustable tank fraction is an integer 0 or 1 in the final MILP,
while draught auxiliaries remain continuous. Both endpoints have zero native
FSM; choosing full capacity is stricter than the native 98%-pressed threshold.
Fixed-tank FSM and the three phases' fixed consumable draw-down remain intact.
The LP preceding the MILP is only a relaxation of these binary fill decisions;
the decoder rejects a fractional pressed recipe rather than treating it as a
feasible endpoint solution. No failure in this lane proves map infeasibility.

A slack reference's GM cannot be copied unchanged into the pressed model's
calibration: that would retain the adjustable free-surface penalty after the
lane has removed it. At the native reference pose, the builder computes the
adjustable liquids' actual FSM and vertical moment. It forms
`selected_GM = native_GM + adjustable_FSM / total_mass
              + (actual_adjustable_Z - full_arm_adjustable_Z) / total_mass`.
Comparing this value with the pressed affine GM removes both the adjustable
slack-FSM and adjustable-centroid differences. Native hull and fixed-liquid
effects stay in the local calibration. The precise terms are recorded in
metadata. This is a local solid-tank search reference, not a modification of
native GM, securing, stability, or any checker limit.

## Optional total TCG search window

`tcg_halfwidth_m=None` preserves the existing lane. A supplied value, such as
`.005`, adds hard affine search rows for **total ship TCG** at all three D57
states. They include fixed background, every modeled cargo edge and every
adjustable tank fraction. With mass row `M`, transverse moment row `Y` and
fixed background `base,my`, the two inequalities are
`Y - w*M <= w*base - my` and `-Y - w*M <= w*base + my`.
The rows therefore account for the mass changing with tank fill; they do not
mistake cargo-only TCG for total ship TCG. This inward search restriction is
separate from the native list limit, which remains unchanged.

The motivation is the worse-side righting-area cusp: complying with a broad
list allowance may still lose area when the CG has a small transverse offset.
Whether this restriction improves passes is measured separately. Native probes
record total LCG, TCG and KG alongside GM and draught at each phase. The warm
`joint_lane_diagnose.py` script also records saved reference area metrics,
margins and exact breaches, and can drop only the optional TCG row family to
diagnose a local conflict. These diagnostics never count as cold passes.

## Optional low-ballast objective

The default tank-fraction objective remains `.001` per fraction. Two optional
search weights, `tank_mass_reward_per_t=0` and
`tank_vertical_cost_per_tm=0`, change a tank's coefficient to
`.001 - reward*capacity_kg/1000 + z_price*capacity_kg*selected_arm_z/1000`.
The vertical arm follows the selected half-fill or pressed tank model. Thus a
fraction multiplies costs in tonnes and tonne-metres exactly once. A reward of
.1 with a vertical price of .01 favours low ballast below 10 m over high water,
subject to every existing loading row and the full checker.

This explores a physical hypothesis: draught-dependent second-check form
factors and righting area may improve with suitably low water even when GM
alone does not describe the tradeoff. It is not a claim that more ballast
always helps. Mass, draught, trim, GM, TCG/list and three-state voyage limits
remain unchanged. Negative objective coefficients cannot make the model
unbounded because every tank fraction has a physical bound 0–1; draught
auxiliaries are bounded too. Weights, coefficients, selected arms and finite
bound semantics are recorded in metadata. Nonfinite inputs, invalid arrays,
and negative reward/vertical prices fail closed.

## Optional native-reference KG feedback

`kg_caps_m=None` preserves the previous assembler. A dictionary of phase caps
adds `Z - cap*M <= cap*base - fixed_Z` for each selected D57 phase, using the
same cargo-edge, tank-fraction and fixed-background coefficients. Cargo already
aboard is removed from the reference constant and reinserted on its fixed edge
exactly once, just like new cargo. Water contributes both mass and vertical
moment; this is total-ship KG rather than a cargo-only vertical limit.

`method.py` defaults `joint_kg_step_m=0`. With a positive configured step, a
selected complete plan's second-check failure requests KG feedback. The next
joint reference already computes native KG in its three probes; the method
sets each cap to that native KG minus the step.
No extra native evaluation or search loop is added. Each newly selected area
failure rebuilds caps from its own native reference, replacing stale caps;
previous caps remain only in trace metadata. If a joint construction finds no
assignment, the next attempt relaxes each active cap toward its current native
reference by `joint_kg_step_m*joint_kg_backoff_ratio` (default ratio .5), removing
the optional cap on reaching baseline. Thus an overly restrictive local cap
does not consume all remaining rounds unchanged. The maximum round count and
the native checker remain unchanged. A zero KG step preserves old behavior.

This sends true area failure back to the box and water coefficients even when
fixed-water vertical targets have been disabled for the joint lane. It is an
inward search heuristic under the assembler's existing tank-arm assumptions,
not a righting-area formula, a checker relaxation, or a whole-map certificate.
Metadata records the native phase KGs, step, applied caps and scope. The hard
native stability, securing and other checks still judge the resulting plan.

Tests cover exact cargo subtraction/support heights, missing modeled cargo,
recipe capacities and invalid vectors, pair/aux matrix placement without
context mutation, and an explicitly warm native development reference across
all three D57 contexts. The native subtraction test compares each retained
base against a fresh native loading condition with decision cargo removed.

The combined joint/loading-seed/gap/target suite passed 41 cases in 12.47 s
after pressed-mode and reference-anchor integration. Evidence is in
`results/verification-joint-pressed.json` and its log. It ran with one numerical
thread, no pytest cache provider and the benchmark marker absent; `nice -n 5`
was requested but rejected by the macOS sandbox. Later changes to the gap
root-LP completion bound have separate verification and are not covered by
this particular recorded run.
