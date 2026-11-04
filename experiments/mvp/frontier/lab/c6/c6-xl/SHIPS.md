# Synthetic XL ships and TRAIN maps

Current qualification (the chronology below preserves superseded designs and failures):

| Current ship | Approximate cells/TEU | Geometry + hydrostatics | Declared three-condition full checker | Certified TRAIN maps |
|---|---:|---|---|---|
| synthetic_3600_closed | 3712 | pass | pass: original C1/C3, ballast-only amended C2 | 12 full-region, 1308–1775 boxes |
| synthetic_boundary285 | 6676 | pass | unqualified: original recipes and all bounded C1 ballast repairs failed | 8 requested fill maps +4 policy diagnostics, 2388–2813 boxes; not admitted |
| synthetic_8000_cert | 8016 | geometry pass; model rejects unsupported L/B>9 | not evaluable | none |

Physical qualification and producer certification are separate from an assignment
LP checker pass. Primary assignment outcomes are reported by the scoring harness.
All figures and geometry extensions are synthetic approximations. Earlier
prototype/incorrect-metadata cohorts remain diagnostic evidence and are superseded.

These are synthetic research ships. No field describes a real ship, approved
loading manual, class certificate, or operational capacity. The public KCS hull
offsets are reused as inert geometry, with their unchanged checksum.

The private copies in `ships/generator/` come from
`experiments/fleet-params/{generate,kcs_fit,design_tanks,freeboard}.py`.
Only explicit XL constants and private paths were added. The source's KCS
loft, raised hold floors, paired column pruning, full polygon tank carving,
shell/oil clearance, tank soundings, approximate freeboard screen and checker
physics remain unchanged. Generator-only Shapely 2.2.0 is installed privately;
the pinned runtime's NumPy is imported first and retained.

All XL values below are **APPROXIMATIONS**, confidence low:

| Quantity | synthetic_3600 | synthetic_8000 |
|---|---:|---:|
| Nominal TEU design label | 3600 | 8000 |
| Retained physical 20-foot cells | 3752 | 8104 |
| LOA / Lpp / beam m | 242 / 230 / 32.2 | 346 / 330 / 32.2 |
| Depth / design draught m | 19 / 10.8 | 26 / 13 |
| Lightship kg | 15500000 | 28000000 |
| Lightship LCG / VCG m | 105.8 / 12 | 151.8 / 14 |
| Grid bay pairs / rows | 13 / 12 | 22 / 12 |
| Requested hold / deck tiers | 6 / 7 | 8 / 9 |
| Reefer plugs | 450 | 900 |
| Ballast / HFO target m3 | 14000 / 2850 | 23000 / 4900 |
| Diesel / freshwater target m3 | 475 / 380 | 825 / 660 |

APPROXIMATION: the 3600 hull uses the KCS public reference Lpp/beam/draught,
but the source's 16 m sealed cutoff is stretched to synthetic depth19 m. The
8000 design further scales the same underwater offsets affinely. It is an
explicit slender/deep research approximation, not a typical real8000 ship. Neither is
a reconstruction of KCS topsides. Cb is integrated, not fitted to a TEU target.
Lightship values are newly declared synthetic design inputs, approximately
retaining feeder volumetric mass scaling and dimension-scaled CG. Tank targets
are approximate volume-scaled feeder targets; actual shapes, capacity and
soundings are recomputed, never scaled tables.

APPROXIMATION: container lengths/heights, 14 m bay-pair pitch, 2.5 m row pitch,
cover/coaming dimensions, shell allowances, 20/40ft stack ratings, DG space
permissions, bridge height offset, opening height offsets, and windage method
retain the copied feeder generator's explicit synthetic choices. No lashing
gear or checker safety limit is upgraded to secure a pass. The finite-angle
design GM cap is computed by the same D56 code from synthetic C1/C2/C3
departure/arrival conditions, using predefined DB+HEEL(+FPT0.50) recipes.
These recipes are design probes; each full-checker result must be reported.
The copied manual builder's old archive-source strings and raw generator's
reference-ship provenance snippets identify code/template lineage only. They
are superseded by these explicit XL approximations and are not sources for
the new dimensions, mass, CG, tank capacities, or ship particulars. The zero
reference DWT placeholder in derived hydrostatics is not a reference rating;
only integrated displacement minus synthetic lightship is meaningful.

The production loader has no fleet-path environment override. `install_runtime()`
registers the private `ships/` parameter directory in this process and leaves
cargo/port directories unchanged. Original feeder TOMLs are symlinks to the
pinned runtime. Numerical cache locations route into `ships/`; neither rules,
checker tolerances, nor numerical algorithms are modified. Each emitted JSON
also embeds its complete vessel. This location hook is needed because numerical
checker consumers resolve the raw hull/tank inputs by vessel ID.

`maps/requests.json` freezes 24 primary TRAIN maps before planner calls:
two roots each ship, fills60/70/80%, low/high masses (.65/1.0), same stratified
prefix and mass derivation as `strategy/fillsweep/generate.py`. Four roots are
245100000/245100001/245100100/245100101. Six additional diagnostic full8000 maps
use independently declared root245100200, seeking >=3000 boxes. Primary maps
use the original medium half-bay decision regions with the whole ship's
background/hydrostatics; full8000 diagnostics use the builder's full region.

The freeze audited433 request/cohort/manifest/split JSON metadata files <=20MB,
6878 extracted root seeds, with zero root-seed overlap. It pruned explicit
testv3/test-v3/testv2/test-v2/valsplit/unseen/c6-maps directory names before
opening. Audit review then identified historical `runs/test-*` metadata,
including small-v21 manifest metadata, among files read by that broad scan:
69 historical test-run metadata files are now classified closed.
These were metadata seed checks only; no held-out instance or plan was opened.
The exact original audit records/hashes remain in requests.json, not silently
removed. Future traversal also excludes runs, test-, v21 and v2.1 names. This is
a limitation against the strict sealed-path fence; it cannot claim every sealed
split was checked or every opened metadata path was unsealed. No outcome-dependent
replacement seeds. The full3600 fallback root245100300 was separately checked
against all364 permitted initial metadata paths,6836seed values,zero overlap;
the69closed historical paths were not reopened. This six-map fallback raises
the preregistered total from30 to36 (amendment-full3600.json).

Parents use the unchanged corpus builder, C1 and D58 gates. Derivatives retain
the exact parent background/yard/voyage and alter only demanded prefix/masses,
with roundtrip and clean provenance checks. Derivative acceptance is not a
loading-plan certificate. Parent inability to supply fill is a recorded failure.

Validation is separate from planning: hydrostatics/freeboard and independent
production soundings in ship generation records; full voyage checker on three
physical designer conditions in departure-check records; parent D58 gate in
each accepted map's audit. Do not admit a ship merely because its TOML exists.

One numerical thread is used. `nice -n5` was requested, but this sandbox denies
setpriority and then executes at inherited priority. The direct589 KN curve
evaluations and new hull/tank qualification may require >20min per new ship;
this is physical model preparation, not an assignment-LP timing measurement.
Solver scale times must exclude this preparation and count cold *plans* only.

The initial8380-cell8000 design (Lpp300, beam42.3, depth24, draught13m)
was rejected before planning by unchanged strength assembly: breadth>32.26m
requires an unsupported S11A6.1 additional torsion assessment. Its complete
parameters/generation record/error remain in `ships/rejected-wide8000/`.
Before any planner result, maps/design-amendment.json selected the supported-width
alternative above. No checker breadth gate was bypassed.

Generation observations: both primary3600 medium roots failed the unchanged
D58 gate at its default10s ballast-finisher budget. The first took115.75s
through all direct checks, then reported UNKNOWN, not infeasible. The empty
region witness had arrival mean draught7.19m, so B/d=4.48 exceeds the weather
formula's supported domain even while its area ratio was5.13 and containership
B2.3 ratio2.33. These are generator failures, not assignment-LP failures.
A separately labelled120s (at most300s) D58 budget-only retry is authorised;
criteria stay unchanged. Full-region parents have no empty-background gate and
require a separate loaded whole-ship D48 reference check before planner admission.
The qualified experiment admission requires valid hydrostatics, complete numerical
evaluation and departure stability; reference draught/trim breaches remain explicit. The full8000 parent explicitly uses the same
physical export-policy target quantity, because the generic legacy full rung
otherwise caps demand at2000 boxes. This quantity override is recorded in its
parent specification and does not change any generator/acceptance screen.

Before any8000 map/solver outcome, amendment-8000-port.json selects the existing
Singapore-like10-tier quay profile for its9-tier deck;3600 retains the existing
LaemChabang-like7-tier profile. No port limit is modified. Cross-ship timings
therefore also change port context, in addition to hull/cell count.

The unmodified full3600 producer was interrupted after approximately626.916s
(startup to final tracebackmtime) in C1 ClassFlow(cellcompat) initialization.
Its original non-TTY launch had no parent watchdog; external owned-PID signals
were sandbox denied and Ctrl-C was deferred. The intended1200s deadline could
not be guaranteed. After observed exit130, all further jobs use ships/run_owned.py
parentPopen plus worker self-timeout/stopfile and56GB RSS monitoring every0.1s.

The authorised checkpoint amendment keeps the same builder cargo and yard draw,
but saves its first full-region candidate before expensive necessary C1 screens.
C1 is explicitly not run, producer acceptance false, and derivatives are
uncertified TRAIN-style inputs. No feasibility-based seed/candidate selection.
Medium D58 is not bypassed. The final full checker remains the only solver pass
gate; a separate loaded whole-ship departure-stability and complete numerical evaluation
certificate is required before planning, with all reference breaches explicit. Six3600 full diagnostics have1356/1582/1806 boxes at60/70/80% fills,
low/high twins. Parent checkpoint5.354s; all six maps10.532s, peak RSS287.77MB.

The initial3600 D48 loaded reference evaluated every checker part in78.775s,
peak RSS530.51MB. Weather, all stability curves/GM/free-surface checks, strength
and securing evaluated successfully. Its full checker failed solely on aft
draught11.547189>10.8m (mid11.248169,arrival10.948621) and trim3.062692>2.3m
(mid2.937227,arrival2.816800). It is not a full-pass loading condition. The order's
hydrostatics/departure-stability evaluation requirement is met for measured LP
experimentation; every resulting solver plan still needs the unchanged full
checker, including draught/trim. The separate ballast-repair diagnostic does not
alter any map's initial ballast or become solver warm input.

Final-design amendment (before final primary planning): the complete fleet
parameter geometry suite found20 prototype3600 and88 prototype8000 hold cells
intersecting the unchanged engine-room box. Delete complete paired hold columns,
including the20ft partner column, rather than leave unsupported40ft slots.
New IDs `synthetic_3600_cert` and `synthetic_8000_cert` retain3712 and8016 cells;
prototype raw/vessel/maps/cache bytes and their virtual hull references remain
under the old IDs. The solver also archived the frozen prototype input snapshot.
No old map is reinterpreted with the new geometry. Final maps use distinct
`CERT.*` instance IDs and a separate `maps/producer-certified/` tree.

The8000 opening-clearance test additionally found the synthetic ER-air aperture
atx24.75m inside a45ft deck envelope. Its final design routes the aperture to the
nearest longitudinal bay-walkway midpoint atx25m, retaining its transverse and
vertical coordinates, unprotected closure and progressive-flooding status.
This0.25m routing change is an approximation, not evidence of a real arrangement.
The failed test and pre-correction raw parameters are retained. Both designs
undergo the same original hold/hull/ER, tank/hull/cargo/tank, opening, windage,
DG,45ft-slot, operating-limit and referential checks. The general referential
contract removes only original feeder-specific fixed capacities/deck-tier
assertions; all physical assertions remain. Hull form, tanks, engine room,
operating limits and securing gear are unchanged. No unsupported torsion gate
is bypassed. Physical/departure validation is rerun on the corrected IDs.

The corrected8016-cell8000 passed all eight geometry/referential checks but
production assembly rejected itsLpp/beam=330/32.2=10.25. The unchanged S11A1.1.2
wave-load applicability permits5<=L/B<=9. Error and71.857s/RSS429.21MB execution
record are retained. Thus it has no evaluable departure or planning result;
geometry-only success does not qualify it. At supported beam<=32.26m (above
which unsupported torsion is required), this implementation's wave formula
also restrictsLpp to<=9B, about290m. No wave/torsion gate was bypassed.

Final3600 geometry-qualified map preparation: the unchanged C1 producer with
exact Boolean slot/cell projection was censored after109.720s, RSS1.726GB;
its stack was constructing OOG slot-pair zones for repeated singleton DG
necessary-screen probes. It produced no accepted parent. A separately labelled
corrected-geometry checkpoint amendment retains the same first deterministic
cargo/yard draw and predeclared300/301 roots, with C1not_run and producer
acceptance false. It creates12 `UNCERT.*` TRAIN-style inputs in
`maps/checkpoint-corrected/`, separate from both prototype diagnostics and
pending `CERT.*` inputs. Root300 has1324/1545/1766 boxes, root3011333/1556/1778,
each low/high mass twins. All12 took16.326s, RSS338.97MB. A final full-checker
plan pass remains mandatory; these are not producer-certified maps.

One final boundary design is preregistered in`maps/boundary-preregistration.json`:
Lpp285m, beam32.2m, depth26m, draught13m,18baypairs×12rows, unchanged8hold/9deck
tiers. All are APPROXIMATIONS. LOA=346*(285/330)=298.818m; lightship mass and
tank targets scale285/330 from the rejected330m design, longitudinal CG scales
by the same factor, VCG14m is retained because depth/beam are unchanged.
Reefer plugs round900*(285/330) to777. Securing gear/ratings and checker criteria
are unchanged; achieved retained cells determine its nominal capacity label.
Independent roots245100400/401 were checked against360 previously read,
permitted metadata files and6836seed values,zero overlap. An additional four
reader `sealed` metadata paths were excluded on this audit; no closed path was
reopened. This still makes no assertion about hidden sealed split seeds.
If this single supported-envelope boundary design fails physical/model
validation, no further geometry search is authorised.

Corrected3600 final physical admission: all eight geometry/referential checks
passed; production assembly67.625s. Generator/production design-displacement
difference1.104965kg (2.08e-8 relative), LCB difference-1.81micrometres meet the
explicit independent-integration audit tolerances, without changing checker
tolerances. Loaded whole-ship representative passed the unchanged full checker
in190.800s after the original120s legacy ballast repair, RSS623.38MB. This is
ship preparation only. All12 UNCERT inputs passed hash/schema/provenance,
parent-vessel/yard/background preservation, realised-fill, nested-prefix and
mass-twin checks; immutable handoff is`ships/final3600-handoff.json` and
`maps/checkpoint-corrected/admitted-synthetic_3600_cert.{json,txt}`. These inputs
contain neither a placement plan nor the physical certificate's ballast repair.

Boundary geometry achieved6676cells (2788hold), after44cells in complete paired
ER-touching columns were removed. Its synthetic ER-air apertures move from
x21.375m to the nearest internal bay-walkway midpointx30.5m, retainingy/z,
unprotected closure and progressive-flooding status. This9.125m duct-routing
choice is an APPROXIMATION and is validated again; there is no aperture-height
or checker-limit increase. All eight original/generalized geometry checks pass.

A serialization bug was identified before any boundary map generation: the
copied generic TOML writer emits dictionary insertion order. A new root scalar
appended after`[tank_design]` is parsed inside that table. Thus the intended
`imdg_ship_type=closed_holds` field was missing at root in the frozen3600 design
and its assembled/maps; its prior results remain under that exact metadata.
No frozen3600 file is changed. Boundary pre-map raw/vessel/assembly evidence is
archived byte-for-byte under`ships/rejected-serialization/boundary285/`.
Boundary root scalars are reordered before tables, and parsed root
`closed_holds` plus absence inside`tank_design` is asserted before reassembly.
This declares the already modelled closed weather-deck/hold architecture;
DoC class permissions, plug placement, hull, tanks and gear are unchanged.

Read-only3600 first-draw diagnostics confirm unsupported demand was retained
by checkpointing: all434retainedreeferplugs are under deck while one requested
reefer is on deck, and DG includes classes6.2/7 excluded by the unchangedDoC.
The original C1 producer must condition/redraw these; no capability is upgraded.

Strict final physical-admission amendment: the original fleet restoration test
requires full checker passes on all C1/C2/C3 departure/arrival fixtures. Earlier
D48-only stability qualifications and the superseded3600 UNCERT run are
historical diagnostics, not primary same-fleet-validation passes. No rule,
including draught, trim or port draught, is excluded from final admission.
All three original condition reports are retained even if they fail. A separately
declared synthetic manual may change departure ballast only, with original cargo,
hull, tanks, gear, limits and route fixed; each failing condition has120s of the
original ballast finisher followed by an independent unchanged full checker.
All three declared conditions must pass. Such physical witness ballast is never
inserted into solver input maps or supplied as a warm plan.

Boundary original C1/C2/C3 full QA evaluated every part in about227s, with
strength passing throughout, but all three full reports failed. C1 failed
loading/port draught; C2 draught/trim/port draught; C3 departure aft13.554m and
mid-voyage13.237m versus13m, plus port draught. No other checker breach was
reported. Repaired declared recipes are recorded separately, not retroactively
called original recipe passes.

Original C1 producer conditioning succeeded for both boundary roots with all
necessary screens retained:506.954s total, peak RSS5.068GB. Root400 needed23
attempts,216completion checks and167shipment redraws; screening159.138s of
313.306s. It rejected unsupported deck requests, DG provisions and displacement
using the original policy. Certified primary60/70% maps have2388/2787 boxes
(root400) and2402/2801(root401), mass twins. Four separately preregistered full
export-policy diagnostics retain2813boxes each; actual fills70.297%/70.671%.
All four80% primary targets fail the2.5percentage-point supply tolerance and
remain in the completion denominator. The cohort is12accepted/4generation
failures, pending strict physical qualification and subsequent plan checking.

A distinct `synthetic_3600_closed` metadata-only epoch is preregistered before
its generation in`maps/amendment-3600-closed-epoch.json`: rootclosed_holds,
remove misplaced table scalar, new ID/virtual reference, and otherwise identical
3712-cell physical design. Reuse roots300/301 with explicit paired lineage.
The prior12UNCERT inputs and one failed solve remain superseded diagnostics;
they are never relabelled. All three original fleet fixtures will be rerun;
fixed originalC1 is the preselected reference, never a condition chosen by
passing outcome. A deferred120s D58 retry on original medium roots000/001 is
preregistered after the full-scale handoff, seeking~800boxes without any
weather criterion or hull change.

All three120s original-finisher attempts returned`not_found`, no candidate plan,
not an infeasibility proof. Their separately stored full reports are the
original failed reports retained for evidence; their ballast masses remain
original because no candidate was returned. Native exact-loading probes were
explicitly incomplete for full GZ/weather. No revised manual condition passed,
and boundary primary admission remains false. The clarification sidecar is
`ships/native-repair-failures-clarification.json`. A separately bounded all-existing-
tankgroup physical optimizer is being evaluated; it may use existing FPT/APT
capacity but may not alter cargo, ship geometry, gear or any limit. All original
failures remain failures, and no physical witness is a solver warm input.

New metadata-corrected3600 assembly88.245s; all eight fleet geometry checks
passed. Strict original full-fleet QA165.001s, RSS474.55MB: C1 full pass57.488s,
C3 full pass52.897s, C2 failed only loading/port draught in51.219s. Every part
was evaluated, strength passed in all three, and original C2 remains failed.
All original and declared conditions must still be full passes for primary
admission. A separate predeclared bounded ballast-only C2 optimizer may supply
an amended C2; original C1 and C3 are retained, not optimized or discarded.

The original manual fixture is privately pinned at
`ships/generator/fleet_manual_fixture.py`, SHA256
`609fe8eaa7f636d45e6788ed3cd8090523613a87ecc5babc1d3cf6f937f23e4d`.
Current3600 QA checked the shared source against that previously recorded hash
while running and again after exit; all hashes match. Its epoch record is
`ships/synthetic_3600_closed.fixture-epoch.json`. Future full-fleet QA uses
`ships/design_conditions_pinned.py`, importing this exact bytecopy and excluding
no checker rule. Frozen common code and earlier report/driver bytes are preserved.

The metadata-corrected3600 now satisfies the strict **declared synthetic manual**
three-condition qualification: original C1 and C3 full passes are retained;
original C2 remains a failure. A separately predeclared native-equilibrium
ballast-only C2 candidate passed every part of the unchanged full checker in
65.119s (parent wall70.050s, peak0.477GB). Its report/recipe is
`prep/manual-closed3600-C2-pilot/original-displacement-zero-trim.json`, SHA256
`a25a877ce561b96984be7860dcfbd9a1d73f2fd32f723498ffb8bc042f4617a6`.
Cargo, geometry, tanks, gear, route and limits were fixed; only masses in
existing tanks were amended. Aggregate immutable evidence is
`ships/synthetic_3600_closed.primary-declared-manual-validation.json` and
`ships/synthetic_3600_closed.physical-ready.json`. This is physical preparation,
not an assignment result; no manual recipe, physical witness placement or
ballast optimization state is supplied as a solver warm start.

Original-C1-screen producer certification for fixed full-region roots245100300
and245100301 runs under the existing600s/56GB own-process guard. A separate idle
`ships/watch_owned_sentinel.py` additionally observes `c1/leader/BENCH-RUNNING`
every0.1s and requests only its recorded owned child's stopfile if it appears;
common frozen registration and numerical methods remain unchanged. The first
root passed in48.218s and supplied six fill/mass twins at1331,1553 and1775 boxes.

Both closed3600 original-C1 parent producers passed; total157.250s and peak1.833GB.
All12 preregistered full-region derivatives are accepted (two mass levels,
60/70/80% physical-cell fill, roots300/301), box counts1331/1554/1775 and
1308/1526/1743 respectively. The earlier progress note1553 was rounded incorrectly;
the immutable cohort records1554. Independent admission QA completed7.676s,
peak0.400GB: schema, clean TRAIN provenance, map/parent hashes, embedded vessel,
yard/onboard preservation, physical fill, mass twins and nested prefixes pass.
Primary list `maps/closed3600-certified/admitted-synthetic_3600_closed.txt`,
cohort same basename`.json`; strict physical flags are in
`ships/synthetic_3600_closed.admission.json`. `ships/closed3600-handoff.json`
freezes exact admitted raw/vessel/code/map/manual-certificate hashes. This cohort
is now immutable. No assignment checker pass is claimed by this handoff.

Boundary qualification is now closed as **failed**, not pending. The final
predeclared native-hydro C1 pilot evaluated all three targets with the unchanged
full checker, wall244.429s, peak0.683GB; none passed. Original displacement/zero
trim and design-minus0.5m/zero-trim were evaluated, followed by the third fixed
target; no C2/C3 optimization or primary planning follows this stop rule. This
is a bounded search failure, not a proof of physical infeasibility. Exact
violations and source report hashes are preserved in
`ships/synthetic_boundary285.final-qualification-failure.json`; previous
original-recipe, native120s and expensive-GZ-pilot failures remain intact. No
new hull, gear, cargo, safety limit or seed was introduced to obtain admission.

The first two boundary native-hydro targets achieved near-zero trim but failed
`iscode.a2_3.weather` at departure, mid-voyage and arrival. The third target's
linear mass/moment projection was unavailable (`local_linear_target_unavailable`);
its retained original-ballast witness was still fully checked and failed
draught, not misreported as achieving12m. These results do not justify a new
geometric design search.

Deferred medium-region correction: the first120s-budget root000 preparation
failed the original weather/background gate. Its generator RSS was recorded,
but original `finish_ballast` uses a spawned process; that child's registry and
RSS were not explicitly certified. Root001 was stopped via the owned generator
stopfile while in C1 screening, before its background check. This preparation
epoch is telemetry/provenance-limited, retained under
`maps/closed3600-medium-certified`, and never admitted. A read-only `ps` telemetry
attempt was sandbox-denied without producing a process inventory; no alternate
process inspection or signal was attempted.

The corrected whole-cohort epoch is preregistered in
`maps/medium-spawn-guard-amendment.json`. BOTH original fixed roots000/001 are
rerun, each with unchanged120s background budget and all original acceptance/C1
criteria, into separate `maps/closed3600-medium-guarded-certified` IDs. The
pickleable `ships/medium_ballast_guard.py` target facade installs the private
fleet routing in the spawned child and records its own PID, PPID and self-RSS;
it enforces56GB, deadline and BENCH-RUNNING/owned-stop interlocks. Native finisher
function/arguments/results and all acceptance rules are unchanged. A real-spawn
probe returned the private vessel ID and verified exact parent/child ownership
and positive self-RSS; native shutdown used only its own Process handle. The
first probe exposed an atomic-write race, fixed with a lock, followed by two
clean probes. No assignment, ballast recipe or checker threshold changed.
