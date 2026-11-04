# C6 XL: cold assignment scale experiment

Scope: synthetic ships only; all writes under this directory; no commits. The
unchanged full checker alone establishes a pass. No held-out map or plan
payload was opened; an initial metadata-audit scope deviation is disclosed
below. GPU use is forbidden and disabled. Numerical children use one thread,
attempt nice 5, and have a 56 GB self-RSS guard plus a direct-parent watchdog.
The sandbox denies process-tree inspection and effective priority changes;
these limits are recorded rather than silently claimed to work.

## Preregistered decisions and assumptions

- Two synthetic sizes, approximately 3,600 and 8,000 TEU, generated from the
  same checksum-pinned public KCS offsets and fleet physical generator. These
  are scaled research hulls, never operational particulars or real ships.
  Every scaled/design quantity is an approximation; SHIPS.md provides details.
- Twelve TRAIN-style maps per ship: 60/70/80 percent fill, low/high mass, two
  replicate seeds each, base 245100000. Matched fill/mass variants share a
  parent, as the original fillsweep does. Seed audit precedes generation.
- The medium regional builder may expose only part of each full hull. Actual
  retained physical cells and demand, rather than nominal TEU, determine fill.
- Use all eighteen explicitly ordered feeder_1700 TRAIN maps as cold regression
  controls. No previous assignment, plan, recipe or solved ballast is read.
- The baseline is a private copy of c5-securing. The XL variant retains the
  feeder algorithm and tests sparse structured assignment columns above its
  chosen size threshold. A restricted-column failure is no infeasibility proof.
- Start with 240 seconds proposal, 60 seconds finishing, one candidate/worker.
  Any larger budget or distinct diagnostic run is labelled separately.
- A single-map run may exceed twenty minutes only to diagnose a measured
  preprocessing or integer-tail bottleneck on a new scale, under an explicit
  recorded hard deadline. This is necessary because those stages are outside
  the inherited LP time limit. Such a censored run is not a completed solve.
- At most two busy processes across the whole team, reduced to one while
  lab/c1/leader/BENCH-RUNNING exists. Never stop another session's processes.

## Reproducibility

`runtime/` snapshots src, params, public KCS offsets and top-level Frontier
modules; `runtime/SNAPSHOT.json` records byte hashes. This prevents concurrent
shared-source edits changing the checker midway through the comparison. The
snapshot checker is unmodified. Experimental raw ship-parameter registration
is process-local and used only to let the original consumers evaluate the
private XL vessel IDs. The whole Vessel is embedded in each instance JSON.

The current upstream score_ladder wrapper includes a sealed holdout lookup.
Its private copy replaces that lookup with this experiment's explicit own map
lists; it also suppresses catalog's automatic validation-list lookup, because
this task closes the entire valsplit directory. No original wrapper execution.

## Model and growth

The graph has cargo boxes on the left, legal physical slots on the right, and
an edge for an eligible assignment. One unit must leave every box, and physical
cells may be used once. Stack support, heavy-low, bookings, securing, and ship
mass moments add side rows. Lifts arise from blocked yard retrievals and
hatch/restow operations in the replayed plan, including the D15 later-port
projection with no later loads.

With 150 retained slots per box, assignment variables grow linearly in box
count. That does not imply linear running time: the old initializer first
builds every eligible pair; the static mask is box-count times slot-count;
securing repeats coefficients across stack rows; LP dives resolve after fixes;
and the CP-SAT tail searches a combinatorial integer problem. solver/SOLVER.md records
which changes were implemented and which larger mathematical alternatives
were only considered.

## Measurements

Guarded cold runs are underway. Stage times, peak memory, exact breach lists,
completion table and log-log growth fit will be written to bench outputs and
summarized here. Timing exponents must identify their sample count, range,
method, and whether full-checker passes or failures were fitted. Hard timeout
measurements are censored, never silently treated as successful solve times.

## Initial environment findings

The first launch was invalidated before measurement: the sandbox disallows `ps`,
so the original parent process-tree memory sampler could not run. Its only child
PID6268 had already exited when an exact-PID termination was attempted. The
harness is being changed to a child self-memory guard; no external process was
signalled. That invalid launch also exposed registration racing unfinished ship
preparation, not an assignment failure. Its row is excluded/replaced by a fresh
cold run. `nice -n 5` is used, but this sandbox reports `setpriority: Operation not
permitted`; achieved niceness cannot be asserted.

## Mathematical alternatives reviewed

- Applegate, Díaz, Hinder, Lu, Lubin, O'Donoghue and Schudy (2021),
  [Practical Large-Scale Linear Programming using Primal-Dual Hybrid Gradient](https://arxiv.org/abs/2106.04756).
  Sparse matrix-vector iterations can avoid factorization cost. The installed
  SciPy interface exposes HiGHS simplex/IPM, not a PDLP method. Current
  [HiGHS solver documentation](https://ergo-code.github.io/HiGHS/dev/solvers/)
  describes CPU PDLP implementations but warns they may not compete with its
  simplex/IPM on CPU. No GPU or downloaded executable is used here.
- Geoffrion (1974), [Lagrangean Relaxation for Integer Programming](https://www.anderson.ucla.edu/faculty_pages/art.geoffrion/home/docs/Ch%209%20from%2050%20Years%20of%20Integer%20Programming.pdf).
  Price the coupling rows and solve smaller bay subproblems. This needs a
  master assignment across bays because every concrete box is used once;
  dropping that row would change the problem. Considered, not implemented.
- Cuturi (2013), [Sinkhorn Distances](https://arxiv.org/abs/1306.0895).
  Entropic matrix scaling handles plain transport efficiently; it does not
  enforce the securing, support, geometry and heavy-low side constraints.
  Considered, not substituted for the actual assignment polytope.
- Existing c2-cgscale pattern pricing provides a useful restricted-master
  precedent, but its fast LP stage did not imply any full-checker pass on its
  D185 cohort. This experiment must distinguish LP speed, replay completion,
  and unchanged full-checker passes.

## Physical model boundary encountered

The first 8,000-TEU geometry (8,380 cells, beam42.3m) could not be assembled by
the unchanged strength model: `S11A 6.1 additional large-breadth/torsional
assessment required`, because beam exceeds32.26m. It is preserved as rejected
physical-design evidence and is not a planning result. A narrower/longer
synthetic8,000 design is being evaluated within the supported domain. Its
slender proportions are an approximation, not a typical ship-class claim.

The first private3600 reference used a physical path in hull_offsets_ref, while
unchanged checker consumers expect the virtual params/fleet/id.toml#hull_form
reference resolved through the private data registry. That data-reference error
is being corrected before map admission. A designer C1 condition also exceeded
maximum draught, so numerical evaluation alone is not labelled physical
validation. Full reports are retained.

## Generator preparation amendments

Both initial3600 medium parents failed the unchanged D58 empty decision-region
background acceptance gate. The witness mean draught was about7.19m, giving
B/d≈4.48 outside the weather-table applicability domain; this is not an LP
failure or proof that a loaded plan cannot pass. A budget-only retry and a
preregistered full-region3600 fallback (seed245100300) were authorized. See
maps/amendment-full3600.json and its seed audit; the initial30 requests remain
unchanged and the fallback adds6 diagnostic derivatives.

The full-region producer spent several minutes before committing a parent.
The next preparation attempt will checkpoint its first deterministic candidate
before the producer's necessary-condition screening, preserving the exact
cargo and yard draw. These are explicitly uncertified TRAIN-style derivatives;
no final checker criterion is removed and no candidate is selected by solver
success. A loaded physical ship certificate is still required before planning.

The first full-region generation process was launched in a noninteractive tool
session before the self-deadline fallback existed. Sending Ctrl-C to its stdin
did not stop it, and this sandbox refused a sibling process's exact-PID signal.
It remains counted against the CPU limit until the owned session exits; no
additional numerical job is started in that slot. All later long numerical
jobs use a parent watchdog and a worker self-deadline. If this preparation
exceeds20minutes, the reason is diagnosing producer preparation at the new
ship scale, not permission for an unbounded solver comparison; its elapsed
preparation is kept separate from solver timings.

## Exact sparse graph projection added before benchmarking the variant

The producer interruption was delivered on entry to ClassFlow, after the
feasibility engine computed `(static.astype(int32) @ incidence.astype(int32))>0`.
Here static is boxes×slots and incidence is slots×cells, but each slot touches
only one or two cells. Dense multiplication nevertheless works through every
zero: about B*S*C operations, cubic when all dimensions scale together. This
same initialization happens inside Problem before the assignment LP starts.

The private variant will replace only this search-model projection with grouped
Boolean OR over the existing one/two-cell slot footprints. Each output remains
exactly `a box can use this cell through at least one eligible slot`; no gate
or checker rule is changed. Work follows the nonzero graph edges, about2*B*S.
The original ClassFlow grouping and all subsequent screens remain. Feeder loads
of500 or fewer take the original constructor. This fix and its equality tests
are frozen before the variant's first scored map; the baseline keeps the dense
original so its preprocessing limit remains measurable.

## Meaning of ship validation

The3600 loaded representative was numerically evaluated by every checker part
and passed the hydrostatic/stability/weather, strength and securing checks.
Its as-built reference loading condition nevertheless exceeded aft draught
(11.547m versus10.8m) and trim(3.063m versus2.3m). Those exact breaches are
retained; this reference is not a checker pass or a safe sailing condition.
A separate ballast repair is attempted without feeding its plan/ballast to the
solver. The order requires an evaluable ship and hydrostatics/departure
stability validation before planning, rather than requiring every arbitrary
reference loading condition to pass. Accordingly, physical admission records
these criteria separately from `reference_full_checker_passed`; every measured
solver plan must still pass ALL original rules, including draught and trim.

The separate3600 ballast repair subsequently produced a FULL-CHECKER PASS for
that fixed loaded reference(197.35s total validation/repair, peak0.631GB).
This is ship validation, not an assignment-LP result, and its plan/ballast is
never supplied as a solver start. The initial rejected reference remains saved.
Independent generator versus production hydrostatics differed by1.105kg of
53,243,615kg displacement(2.08e-8relative) and1.81μm LCB. The declared
cross-grid audit tolerance is1e-6relative displacement and1e-7relative LCB;
no checker limit or criterion changed.

A metadata correction: feasibility Tables include booked/onboard boxes minus
discharge, not all yard inventory. Total inventory is still reported separately
because it affects yard/replay costs. Decision cells and whole-ship physical
cells are also distinct: feeder controls have844 decision cells plus760 fixed
background cells, totalling1604. Growth fits identify these dimensions.

## Telemetry-only restart

The first baseline batch finalized3 maps, then its fourth worker wrote a result
but stalled while cancelling a faulthandler timed traceback. The watchdog dump
was truncated and the Python memory heartbeat stopped. The owned supervisor
was interrupted through its PTY; its direct-parent cleanup terminated only its
recorded child. That unfinished fourth run is archived in bench/telemetry-stall
and excluded. Remaining15 controls restart with telemetryv2: an ordinary daemon
Python stack sampler with no joins, plus the existing memory/timeout guards.
The checker, input parameters and baseline method are identical; only telemetry
changed, and each finalized row retains its own source hashes.

## Prototype geometry excluded from final ship results

A later generic fleet-parameter geometry audit caught an engine-room clearance
condition missed by the initial hydrostatic/departure checks. The3752-cell
prototype3600 retained20 hold cells touching the ER solid; complete paired
columns must be removed(40cells), yielding3712cells. The narrow8000 prototype
requires88cells removed, yielding8016cells. Neither hull, tanks nor gear is
strengthened. These are geometry corrections under the same fleet validation,
not rule changes. Corrected ships will repeat assembly and physical checks.
The first1356-box solver run therefore remains **prototype diagnostic only**,
excluded from final fleet completion/growth results. Its raw parameters,
embedded vessel, map and registry are preserved for reproducibility; final
corrected geometry must not silently reinterpret old embedded instances.

Prototype v1 ended normally: no candidates,463.570s,1.683GB. Search-engine
initialization4.096s included exact projection0.126s; Model initialization
298.634s; assignment graph3.087s; matrix build7.029s; root LP53.264s reached
its time limit(status1, no primal point); repeated tails found a box with no
retained slot; fallback found no stow. This is no infeasibility proof. A stack
sample at300s showed the sight-mask loop, not proof that every prior second
was spent there. Final v2 instruments heat and sight separately and applies
tested exact heat-envelope evaluation/grouped sight masking, with an XL-only
zero-candidate fail-fast. All21 algorithm tests passed before v2 freeze.

The corrected3712-cell ship subsequently passed all eight original generic
fleet geometry tests, the independent hydrostatic cross-check, and a loaded
reference full checker after the unchanged120-second ballast repair
(190.800seconds total,623.38MB peak). Its immutable handoff is
`ships/final3600-handoff.json`. Twelve first-draw UNCERT maps contain
1324–1778 booked boxes across two parents; no solved reference plan or ballast
is supplied to those maps or to the solver.

The first corrected final-v2 run (1324boxes,60%/low/seed245100300) terminated
normally with no candidates in243.0s,1.802GB. Problem initialization4.272s,
Model initialization1.321s (E8 0.0463s, sight0.3558s), first assignment graph
3.072s. Fifty demands have zero pairs before pool reduction:49DG and one
on-deck reefer. The code only samples a nonempty eligible list, so sampling64
cannot itself create these zeros. The original static mask versus later
Model filters was not separately observed. The necessary restricted-graph
gate skips LP and tails; four inherited fallback rounds dominate about220s.
No full-checker candidate was produced. This failure-time observation is not
evidence about successful-plan scaling or whole-map infeasibility.

Read-only input tracing explains some empty eligibility: the original sampled
DG includes classes6.2/7 that the unchanged synthetic DoC does not cover, and
all434 retained reefer plugs are hold-only while one sampled reefer requests
on-deck stow. The raw3600 parameters predate an explicit `closed_holds`
metadata declaration later present in the driver; that field is absent/None
in the frozen raw ship, assembled Vessel and embedded maps. Hashes preserve
those actual bytes. No plug, DoC, ship type or map was retroactively upgraded
after this outcome. The original producer's rejection/redraw gate is the
appropriate preparation path; UNCERT observations remain separately labelled.

### Serialization amendment, before the next cohort

Inspection identified the precise omission: the copied generator writes TOML
in dictionary insertion order. Appending a root scalar after table-valued keys
emits it inside the last `[tank_design]` section. The pre-run driver already
requested `closed_holds`; its serialized and loaded value was nevertheless
None. This is a data-writing defect, not evidence for weakening DG rules.

The boundary hull had no generated maps or planning outcomes at discovery.
Its pre-fix raw/model/assembly are archived, then root scalars are serialized
before tables and assembly/validation repeated. The measured3600 epoch remains
immutable. A distinct3600 vessel ID will receive the same serialization-only
correction, identical geometry/DoC/plugs, and repeated physical admission.
That new whole cohort is the primary scale attempt. The previous twelve maps
and single243s run remain a superseded diagnostic epoch; the remaining eleven
known-metadata-defective maps are not selected for measurement. Seeds300/301
and first-draw lineage stay fixed across the metadata correction. Original
C1-conditioned generation is preferred; any first-draw checkpoint without C1
must retain its explicit UNCERT label. No solver algorithm changes follow this
data diagnosis, and no old result is relabelled as corrected or certified.

### Certified preparation and boundary limits

The supported285m boundary geometry has6676cells. Its unchanged export policy
requests2813boxes/parent, yielding70.82/71.09%cell fill in first-draw parents.
The requested80%derivatives fail the original2.5-percentage-point supply
tolerance and remain generation failures. Eight60/70%mass/seed variants plus
four separately preregistered whole-parent low/high diagnostics are available;
no mass policy, physical capacity or count is increased to force3000boxes.
The missing originalf80-high baseline anchor is explicitly omitted; before any
boundary solver outcome, a separately labelled policy-high seed401 anchor is
preregistered in solver/MEASUREMENT-ALLOCATION.boundary-amendment.json.

The boundary loaded reference evaluated every part, without errors, in237.432s
at758MB. Stability, weather, strength and lashing passed, but draft/trim did
not: aft14.1205>13m and trim4.1804>2.85m (mid/arrival also breached). The
unchanged120s repair found no pass. This is not a safe/full-pass reference.
The earlier one-reference experimental admission is revoked pending the
original C1/C2/C3 design-condition full-check records, without filtering any
condition. The exact original fleet fixture requires ALL THREE `report.passed`
flags, including departure/arrival stability AND strength without hard
violations. Primary hull admission uses that strict full-checker requirement;
draft/trim are not excluded. The original boundary C1/C2/C3 reports all failed:
C1 and C3 only draft/port draft, C2 draft/trim/port draft. All parts evaluated
and all strength checks passed; these fixtures are not relabelled as passes.

Before any primary boundary solver run, a separate fixed-cargo physical
preparation amendment declares a120s original ballast-finisher search per
condition, with an independent full check. Hull, gear, operating limits,
cargo and every rule remain fixed. Only if ALL THREE explicitly declared
revised synthetic manual ballast conditions pass may the boundary ship enter
the primary experiment. These recipes are preparation evidence, never solver
warm starts. The same strict requirement applies to the new closed3600 epoch.
Original failed reference conditions remain visible beside revised ones.

All three original120s ballast searches returned not_found. A separately
frozen physical-preparation pilot exposes all nine existing tank pairs and
their physical0..1 fill bounds (the inherited local search retained only four
groups and a98% pressed/slack regime). No physical model or limit changes.
The first exact three-phase oracle call took75.293s; ten full finite-difference
directions cannot fit in the declared300s search budget. This is a physical
oracle/Jacobian cost, not assignment-LP timing. The pilot keeps its original
bound. A distinct cheap mass/moment LP preconditioner may use native tank
soundings and equilibrium to propose a fixed-cargo recipe, but only unchanged
independent full checks on ALL THREE declared conditions can admit a ship.

The metadata-only `synthetic_3600_closed` epoch passed all eight geometry
tests and repeated assembly (88.245s). Its ORIGINAL C1 and C3 full-voyage
conditions passed in57.488s and52.897s; original C2 failed only draft/port
draft (51.219s). The private original fixture was byte-pinned with matching
pre/during/post hashes. Original C1 was the preselected reference, not a
condition selected after seeing outcomes.

The separate cheap C2 hydrostatic preconditioner then passed the unchanged
full checker on its FIRST preregistered target: original52.577Mt displacement,
zero trim. Two native-equilibrium corrections changed aft/forward draft
11.508/9.650m to10.693911/10.693915m. All parts evaluated, full checker65.119s,
whole preparation70.050s, peak0.477GB, all12input/source hashes unchanged.
The accepted revised synthetic C2 changes only existing adjustable ballast;
original cargo, hull, gear, capacities, port and all limits are unchanged.
Original C2 remains recorded as FAILED. Original C1/C3 plus this declared
revised C2 provide all three full-passing physical design conditions before
primary map/assignment measurements. Evidence is
`prep/manual-closed3600-C2-pilot/original-displacement-zero-trim.json` and
`VERIFIED.json`. No manual ballast/placement enters a solver map or context.

## First qualified assignment observation (frozen final v2)

Primary201 is the certified1331-box60%low-mass root300 input on the qualified
3712-cell closed ship. It ended normally as `method_error` in144.034s,
peak2.305GB; source and433input hashes stayed unchanged. This is a real
primary terminal failure, not moved into the earlier unqualified diagnostics.

Problem initialization3.694s, Model1.478s, graph3.314s, matrix7.438s. LP
94119columns/83481rows/2853923nonzeros (35MB sparse storage); pairs
min64/median66/max69, no zero-pair demands. Root LP52.856s hit its time
limit(status1), with no primal point, objective or uncovered value. Both
CP-SAT tails returned UNKNOWN:87736variables, build7.65/7.63s and
solve15.29/14.75s. Neither establishes infeasibility.

The inherited fallback found a replay-legal stow with securing ratio6.637,
149illegal columns and exchange excess500.27 under its assumed search
physics. `exact_ratio(instance, scored.plan)` then raised
`ValueError: positive loading-condition GM, draught and physical block
coefficient required` through chainlab motions/lashing, before storing a
candidate or running its full checker. Actual GM/draft/Cb were not logged,
and assignment/plan are null; the record cannot identify which domain input
failed. No candidate is reconstructed or reused. No full-checker plan/pass
was produced. The final algorithm stays frozen for the complete cohort.

Failure-work growth fits must include such normally terminated exceptions,
labelled separately from full-checker pass-time and nonexception runs.
LP time-limit calls measure work spent, not optimal LP completion complexity.

## Boundary hull final physical outcome

The separate cheap C1 native-hydro sequence finished in244.429s at0.683GB;
all15frozen source/input hashes matched. Its first two fixed targets achieved
near-zero trim but failed the unchanged full checker on weather in all three
voyage conditions. For the first target, roll periods27.68s(departure) and
35.02s(arrival) violate the pinned weather applicability test `period < 20`,
even though weather-area ratios were positive. The third target was locally
unavailable to the moment LP; its unchanged original-ballast candidate still
failed draft/port draft. Every candidate evaluated all checker parts.

The preregistered stopping rule therefore stops C2/C3 searches. No geometry,
opening height, gear, DoC, plug, operating limit or checker criterion is
changed. The6676-cell hull is NOT physically qualified for the primary
experiment and NONE of its2388–2813-box maps enters planning measurements.
This is failure to find a fully validated manual condition in bounded
preparation, not proof that the hull or maps are physically infeasible.
Evidence: `ships/synthetic_boundary285.final-qualification-failure.json` and
`prep/manual-boundary-C1-hydro/{summary.json,VERIFIED.json}`. The8016-cell
8000 design separately remains outside the unchanged S11A L/B domain.

Prep-only exact geometry caching in prep/ preserves the original generator's
necessary C1 screens. The first call runs native code, keyed by complete
ordered geometry/coordinates/index-array bytes plus k or native OOG radius;
repeated identical keys return defensive copies.18 targeted tests passed.
It does not alter the checker, final solver algorithm or cargo screening.

The first original C1 parent for boundary seed400 passed in313.306s,
peak5.068GB:23attempts,216completion checks,167shipment redraws,
11provision rejections.159.138s was native screening. Rejections included
on-deck13, under-deck2, IMDG11, displacement11, C1two. Stack samples caught
rebuilding physical positions in each singleton policy screen, then deep-copy
of the full instance/yard in each singleton engine probe. These observed
preparation costs are separate from all assignment timings; no candidate is
selected by solver success and the second fixed seed is not substituted.

## Full-checker pass evidence versus wrapper candidate selection

The inherited scorer sorts all returned candidates by lifts, and tries1 can
choose a cheaper failing candidate even after the method found a higher-lift
full pass. The runner's observer retains the actual unchanged-checker report,
full plan and explicit all-boxes-aboard test. Completion and pass-found metrics
therefore follow completion.py T5 classification, with raw row.passed reported
separately as selected-finisher outcome. Any retained pass missing the outer
row.passed flag receives an independent saved-plan checker audit at the end;
that is verification only, timed separately and never used as a solver start.

## Pair-count regime caveat for timing fits

The inherited C5 method actually samples150 pairs only above300 demand boxes;
at exactly300 it retains every eligible pair. Final XL uses its separately
reported structured64-target pool only above500(whole pages/mandatory strata
can overshoot64). Thus a300-box feeder LP can be larger than a303-box LP.
Feeder timings are regression controls, not a clean asymptotic scale experiment.
Primary growth fits should use the consistent XL lane separately and report
actual variable/nonzero counts, parent-cluster uncertainty, hull/port changes,
and censored work. No fitted power is a proven complexity bound.

## Seed-audit scope deviation

The initial recursive seed audit read433 request/cohort/manifest/split metadata
JSON files. Its pruning was too narrow:69 historical test-run metadata paths,
including v2.1-labelled manifests, were included. No test map or plan payload
was opened, and the explicitly closed testv3, valsplit and c6-maps directories
were pruned. The metadata-read path/hash list is preserved in requests.json;
this deviation must not be described as every sealed item staying closed.
Subsequent audits exclude those69 paths and four additional reader/sealed
manifest paths, using360 permitted metadata files (6836 observed seeds), with
zero collision for the added fallback and boundary roots. We
cannot independently certify overlap against hidden sealed split contents;
that limitation is recorded rather than reopening them. No algorithm used
those test manifests, plans or outcomes for tuning or warm starts.
