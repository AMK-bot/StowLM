# Optional two-loading-call development case: prepared, not measured

This is an if-time prototype, subordinate to the fixed32-map comparison.
`optional-multiload.py` has not been executed. The executive must approve a
numerical slot before either fixture preparation or solving is launched. The
script accepts explicit `--prepare-only` or `--execute-approved` modes and
requires a new output directory. It never modifies the cohort, its manifest,
or any frozen solver source. All validation and performance claims remain
pending; only standard-library source inspection and AST syntax checking are
authorized at preparation time.

The raw source is the frozen development map263360000: feeder_500,86 mixed
equipment boxes in240decision TEU. No prior result, plan, assignment, recipe,
ballast solution or runtime cache is read. Sorted load IDs at even positions
are43new exports from THLCH; the other43are independently authored exports
waiting at SGSIN. The source yard stack composition supplies the two cohorts'
own nonload blockers. Cargo for Singapore is removed from the first terminal's
inventory; it is not teleported aboard or transported between terminals.
The Singapore inventory receives new compatible footprints in Singapore's
native operational yard, preserving equipment, DG zoning and powered rows.
The source's immutable vessel geometry and fixed background are identical
throughout the case; no background reconditioning follows a successful plan.

The authored rotation is THLCH(load1), SGSIN(load2), MYPKG, VNSGN, HKHKG.
Source boxes whose POD was SGSIN instead have HKHKG as their synthetic booking
destination. Thus every cargo POD is strictly after the second loading call;
there are no intervening discharges. Original cargo equipment, dimensions,
mass, DG declarations, reefers and special requests remain unchanged. First
group POL is THLCH, second group POL is SGSIN. Every call carries its actual
native profile archetype; Singapore is an operational profile, not an
environment-only density placeholder. Unsupported profiles or incompatible
yard positions cause a recorded preparation failure, never a substituted port.

Call0 requires a fresh cold run of the experiment's copied Engine12. No stored
starting stow is supplied. Call1 uses the copied Engine12's existing arrival
path; this is an operational onboard input, not a reused optimization recipe.
`warm_polish=False` avoids that optional within-run stage in this prototype.
The native search/finishing floors remain, so CLI stage budgets are not strict
whole-run limits. A long run may exceed20minutes because it retains full
finishing/checker work for both calls; the executive must allocate time before
launch. All numerical threads are fixed to one. Native worker PIDs are recorded.

The first selected plan must independently pass the unchanged full checker
before call1 can exist. Native `departure_condition` reconstructs its actual
departing cargo and adjustable ballast. Native D57 `derive_arrival_liquid_state`
then derives Singapore arrival liquids from that checked departure. Ballast
and cargo do not change at sea; there is no free arrival tank reset, bunkering,
or new fixed background. The default proportional consumable policy remains:
the first leg retains10% of its departure consumables, and the second leg's
ordinary native check applies to its remaining contents. The second plan may
choose a checked in-port adjustable-ballast change; that complete tank delta is
recorded with both departure states and the intervening arrival state.

The current schema does not encode timed ballast-transfer moves. These
records establish native departure/arrival continuity and the checker's
ordinary pumping/in-port gates where applicable; they are not a new proof
of a continuous hydraulic or every-instant ballast schedule. D57 still checks
its declared samples, not every point along a sea leg. Catalogue activation
is unchanged and must match the source cohort's legacy mode.

The second accepted plan must preserve every first-group container at its
exact checked first-departure slot. It may not move a resident container at
all: off/on restows ending at the original slot are also refused. This is an
additional experiment-family gate after the unchanged checker, not a checker
patch. Copied Engine12 may construct resident-moving candidates; those native
reports are retained, but such candidates cannot complete this fixed-onboard
case. Failure means no full fixed-onboard plan was found, not infeasibility.
PortAssignment currently rejects arrivals and is not claimed to support this
case. Extending its LP would require fixed resident occupancy/weight/length
support, resident securing loads and partial-stack fill, cover availability,
and a replay-legal plan that adds new loads without touching those residents.
Allowing resident movement later would additionally require explicit restow
decision variables and action/cost accounting, not free reassignment.

The script observes copied-solver checks without changing their results. It
stores concrete plans, full reports, every measured breach and the extra
resident-position gate, including failed candidates. D241/D242 tier completion
is classified after full-enforcement work with the frozen reporting table;
diagnostic relaxed candidates are labeled and cannot advance the vessel to
the next call. Partial plans remain separate with the percentage aboard. If
call0 has no full pass, call1 remains an explicit blocked/not-completed row.
If call1 fails, call0's pass and all failed second-call evidence remain. No
failed call, cargo group or source map is replaced after a result.

Two independently checked full passes are required for the rotation success
flag. The cost is actual current nonproductive lifts at call0 plus actual
current lifts at call1 plus only the future D15 projection after call1. The
two `weighted_nonproductive` totals must never be added: call0's projection
ignores the upcoming new loads and would duplicate later discharges. Its
weighted total is retained only as a diagnostic of that no-new-load D15 model.
The final combined cost still uses D15's fixed-coordinate later-discharge
projection and is not a proven minimum over executable adaptive future moves.
