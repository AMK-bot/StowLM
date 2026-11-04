# Cycle 6: cold arrival-aware box-slot LP

All writes remain in this folder. CONTRACT through Cycle 5/6, rule-tiers, and
C5 securing NOTES/method/gap/stow/rows were read. No sealed data or c6/c6-maps
is opened. No checker, src, engine, policy-table or prior-cycle edit; no commit.

## Predeclared data and development

C4 cohort bytes are unchanged:72 synthetic TRAIN snapshots, seeds331401000–331401071,
12 per size/occupancy cell. These are structural arrivals, not certified prior
voyages or representative mixed cargo. Native dry20, no DG/OOG; E9 is no solver gain.
DEV.json was recorded before any Cycle6 solves: first two seeds per cell (12maps).
All tuning uses that subset only. Freeze source/input/config hashes before
all72primary maps; report the12DEV and60other TRAIN maps separately, never unseen.

## Model

Box-to-slot edges choose one placement per departure box. True kept arrivals
are fixed edges at original coordinates, contribute securing mass/wind and
moments, and consume cells, support, height and stack mass. Expander-priced
keep/all/deck/cover prefix branches pay actual restow closure; final replay is
always against the original instance. GRAPH.md has concrete small/medium graphs.
The native input loading condition supplies firstGM. Input-derived background
moments and loaded native LCB/KM replace dense-empty-ship targets; actual departure
GM/checker feedback updates later rounds. Heat checks use physical support height.

## Implementation and copies

Frozen C5 gap/rows/stow/chainlab and C4 layout repair/ballast_opt/physics are
copied locally; ROOT-COPIES.json and LP.md pin source hashes. The runner,
cohort and engine snapshots and tiny page lane are copied from C4. Leader
completion.py and tiers.toml are exact local copies; tiers.py reads that local
policy. No live lab folder is imported. Runtime src/params are read-only native
checker/model dependencies, hashed by the runner. No earlier plan, assignment,
recipe or computed ballast is read. Input onboard tanks are given data, hence cold.

LP dive, integer tail and exchange use count/deterministic-work ceilings.
Budget_s is construction telemetry. One affine loading-only tank LP provides a
cold seed; exact full checking still decides. Optional checked local ballast
finishing in the final CP profile uses evaluation/iteration ceilings; its
requested native wall setting is telemetry. Atomic ordinary physics calls may
outlast wall settings. Historical/default finishing remains explicitly separate. Failure of
any LP or bounded search means no plan found, never whole-map infeasibility.

## Tier reporting and compute

Full checker pass means T5 only. All generated concrete plans retain their
unchanged reports and measured/limit/unit breaches. After the fixed full-rule
work is exhausted, existing complete candidates are classified by the exact
leader peel ladder; no active relaxed-constraint constructor is claimed yet.
All original retained cargo and every new booking must be aboard, with due
cargo absent, for completion. A plan leaving boxes ashore is partial and its
percentage aboard is separate. Never peel to save lifts. Unknown hard IDs/parts
fail closed at unpeelable T0. Later lifts are fixed-stow D15, with no later loads.

At most2CPU-busy processes team-wide; only1 while BENCH-RUNNING exists.
All numerical threads/masterworkers1; request nice5 (sandbox may deny it).
PIDs/sessions are recorded and only owned jobs may be controlled. No GPU/cloud.
A complete72-map comparison can exceed20minutes because every predeclared map
and failed arm remains in the denominator, with isolated cold processes, exact
checker physics, and bounded per-map LP/CP/GM rounds. An individual map can also
exceed20minutes under deterministic CP/dive caps and atomic exact physics calls;
its count limits and elapsed time are retained rather than adapting to load.

## Results and frozen configuration

Pending development and freeze. Development profiles are not primary claims.
Primary measures fresh cold 0.9, frozen Engine and the new LP on all72 inputs;
accepted archived C4 Pages remains the unchanged paired historical reference.
No speed or algorithm credit is inferred from differing budgets or stored time.
Tiny keeps the accepted C4 page lane. Tests and final source hashes will be
recorded before primary; only unchanged full-checker passes supply feasible
lift upper bounds. See LP.md/ARRIVAL.md/GRAPH.md for implementation limitations.

### Primary baseline and archive protocol

Fresh 0.9 uses the copied planner with `search_s=min(120,max(20,6×new loads))`,
requested finishing120seconds, two tries and seed0. Its search is not240seconds.
The frozen Engine is C4's0.11 snapshot before page-prefix integration; extra
work is240seconds and requested finishing120seconds, seed0. Native Engine
behavior retains the300second finishing floor for at most60new loads; otherwise
the requested120seconds applies. Both preserve their original algorithms and
one master worker. The new LP uses deterministic construction/finishing work
caps declared in the selected final profile; its `budget_s=240` is construction
telemetry. These are as-is budgets, not equal-work or fresh speed claims.

Archived Pages keeps the accepted original C4 cold runs, seed0, two tries,
requested finishing60seconds, and the unchanged count profile in
`pages_copy/PROFILE.json` (up to28 exact oracle calls per finished candidate).
It is not rerun or used to warm a fresh solver. Formal freeze pins all72 original
Pages JSON files by bytes without parsing non-DEV contents; reporting afterward
verifies their input, native-checker and parameter hashes, and original work
configuration. Accepted full-pass costs/restows and original times remain
historical. Failed full reports were discarded: archived non-T5 tiers/breaches
are unavailable, never imputed zero or partial. Exact leader completion and
non-T5 diagnostic tables therefore cover only the three fresh arms.

Three-arm execution uses all six permutations twice per12-map cell, and child
workers record the parent's complete arm ordering. Four-arm optional runs retain
their previously recorded four-way rotation. DEV12 and remaining TRAIN60 totals
are separate, alongside the full72-map result. `run.py` accepts job selectors
only; solver settings cannot override the frozen profile through trailing flags.

Initial v1 DEV probes on small024 and medium048 returned no concrete stow
(17.01 and126.11seconds). Full-coverage LP vertices could not survive the
integer tail. A source audit found inherited C5 integer encoding defects:
ceil rounding could zero a negative soft-moment coefficient, turning it hard,
and the slack objective could round to zero. The C6 copy now uses shared
physical slack quanta, finite excess-derived bounds and a common objective
scale; hard capacity constraints remain enforced. Constant soft violations
are priced, and identical tail retries stop after fixing history is exhausted.
Four added regressions pass with all24 LP/row/target tests. The same two DEV
maps are rerun as v2 with unchanged targets; none is a primary result yet.

The v2 probes completed legal replays on both maps, but neither passed (small
259.40s, medium310.62s). These are development results only. Moment auditing
found agreement with native cargo moments to floating-point precision. On
small024 every eligible cargo slot lies at x85.83–91.97m, while the frozen-water
target implied cargo LCG44.14–53.29m. Tank-only native loading seeds corrected
draught/trim by draining ballast but lost GM. Labelled warm fixed-stow probes
found no useful candidate with GM floors1.3–2.0m; this proves nothing about map
feasibility. See diagnostics.md and JOINT.md.

The next cold formulation jointly chooses binary cargo edges, continuous tank
fractions, and aft/forward draughts in all three native voyage conditions. It
subtracts modeled cargo from native reference constants and reintroduces every
box edge, including fixed arrivals, exactly once. References are complete plans
built inside the same invocation, never read from prior output. Hard affine
loading rows are search restrictions; native checking alone determines passes.
The integer tail retains continuous physical auxiliaries, and joint placements
are neither resorted nor exchanged after solving. Tank/draft and GM tangents
are local approximations and receive exact-check feedback. A node ceiling alone
does not bound the work inside a node; further count limits are under review.

The copied C4 layout evaluator lacked visibility. The local evaluator is now
the frozen C4 layover three-part version. Nonfinite error telemetry no longer
masks an unavailable physics call. The integrated regression suite passed59
tests before the first joint cold probes (results/tests-joint-integrated.log).
An optional fresh Pages wrapper preserves C4 construction/finishing counts,
isolated from LP options; its only instrumentation retains already-checked
failed plans without additional checker calls. Primary uses archived Pages as
described above. The tiny LP lane uses the copied page constructor unchanged.

Further DEV-only results: joint-v1 small024315.03s/T1, medium048625.79s/T0only;
anchored joint-v2 small024304.02s/T1; pressed-v1 small024699.32s and
medium048621.70s, both complete without a full pass. Pressed-v1 produced medium
plans whose only hard rule was containership second-check stability. Full
all-cargo root relaxations now fix uncovered variables to zero. Explicit
status2 with no fixing history skips integer search for that local model;
unknown statuses still permit the bounded tail. This removed repeated100s
integer rejections on medium048. No physical map infeasibility is inferred.

Warm row-family probes on these two DEV references found GM1.5 restriction
infeasible in both half-fill and pressed continuous relaxations; removing GM
alone restored feasibility. The minimum physical GM or true feasible envelope
was not computed. Existing references already have draughts near the port
limit; the older8.13m raw-stow draught is not their reference. These probes
read saved DEV plans and are explicitly warm, never benchmark scores.

Optional hard total-TCG windows account jointly for cargo, water and fixed
loads in every voyage state. Completion/breach rank now chooses new-method
finishing candidates before lift cost, and duplicate action stows retain their
best already-checked recipe. Pages keeps its original finishing order. A
predeclared distant-area gate can skip the small local repair when the largest
second-check area deficit exceeds0.05m*rad; it retains the complete failed plan.
It does not relax a rule. Low GM search starts and twelve outer attempts leave
more rounds to calibrate actual within-invocation joint candidates.

An additional development objective rewards ballast mass and penalizes ballast
vertical moment, using selected half/full tank arms. This tests the role of
draught in the containership form factor, beyond initial GM. It remains
bounded by physical tank, draught, trim, mass and GM rows. Its coefficients and
all elapsed work are recorded; it is guidance, not the lift objective or a
necessary feasibility bound. It is not frozen or measured yet.

## Papers

Lenstra, Shmoys and Tardos(1990), unrelated-machine scheduling,
https://doi.org/10.1007/BF01585745; Shmoys and Tardos(1993), generalized
assignment, https://doi.org/10.1007/BF01585178. The multi-row model carries
no theorem-derived approximation guarantee. LP-guided stack fixing, exact
integer tails and exchange are heuristic adaptations of C5, not claimed LST
matching-rounding implementations. Preserve exact securing signs: lower-tier
mass may relieve a tension row. All pass/breach decisions remain native.
Primary copies consulted: [1993 paper, Cornell](https://www.cs.cornell.edu/~eva/Approx.Algorithm.Generalized.Assignment.Prob.pdf)
and [1990 publication, CWI](https://ir.cwi.nl/pub/18055).

## CP/KG development checkpoint

The 12-map DEV subset remains fixed. Development has a cold small024 full pass
at99lifts, arrival restows38now+38later,451.26s, using the experimental MILP
tail. This is not a primary CP result. The experimental HiGHS node and
first-solution caps do not bound LP work inside MIP; its simplex limit does
not cover MIP subproblems. Primary therefore uses deterministic CP/conflict
ceilings,64sampled pairs per box, and an explicit affine-equilibrium grid band.
Sampling and tangent/grid failures never certify physical infeasibility.

Selected second-check failures now feed an optional total-KG cap into every
phase's joint cargo/tank rows. Existing native probes supply KG, with no added
physics call. Caps rebuild from the current invocation's reference and back off
after failed bounded constructions. Optional local WING-A directions release
a previously omitted aft/high ballast group within the same evaluation cap.
The final candidate profile is profiles/dev-cp-kg-v2.json:0.15m KG step,0.5
backoff,0.05m*rad local area gate. No primary freeze has yet been recorded.

Integrated verification before KG-backoff:112tests passed, exit0,
results/tests-cp-kg-wing.log. New pure backoff tests await the next admitted
verification process. Nice5 was requested and denied by the sandbox; actual
nice0 is recorded. An exact owned-PID interruption was also denied; owned
workers finish normally and PAUSE-DEV prevents new queued old-profile calls.
