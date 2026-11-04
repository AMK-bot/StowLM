# Cycle 4: checked exact-model construction and certificate scaling

Scope of every optimality statement in this directory: **fewest lifts among
plans the checker passes, counting later ports by our D15 projection (fixed
stow, no later loads)**. This is not a claim about arbitrary executable future
voyage policies. Current-call lower-bound proofs allow arbitrary legal current
restows; the constructor may search a smaller load-once family without claiming
that family's objective bound as a global certificate.

All writes stay in `lab/c4/c2-hatch/`. The full contract, its Cycle 4 corrections,
and review F7/F11 were read. No sealed data is opened. Numerical jobs request
`nice -n 5`, set OMP/OPENBLAS/VECLIB numerical threads to one, disable bytecode,
and use scoped caches. While `lab/c1/leader/BENCH-RUNNING` exists a single
explicit CPU token is shared across this executive and all subagents. No process
is killed by name/pattern, and no other session is stopped.

## Model, copies, and sources

Each box selects a physical footprint; cell/support/stack constraints govern
packing. Used covers and cover/first-matching-POD edges pay fixed hatch charges.
Future blocker closure charges each projected restow once. First-aboard ranks
give necessary current yard and ship movement costs. Heat, E6, OOG, conservative
DG domains and reviewed final-state nogoods retain only necessary constraints
in the global bound. The first examples remain the quick 18-box/two-cover map
and the selected 88-box/six-cover map.

Copied unchanged from C3: the eight bound modules plus three test modules,
recorded in `COPIED.json`. `certified.lower_bound(instance, *, budget_s=15.,
seed=0, tables=None, upper_bound=None)` and its return interface remain unchanged
for engine integration. The optimistic DG row rebuilding is retained; raw
empty-ship `T.static` DG filtering is not used to certify a bound. Engine code
is not imported; checked result artifacts are copied and hashed as upper-bound
evidence. The unchanged full checker remains the sole plan pass gate.

Mathematical sources inherited and used:

- Roberti, Bartolini and Mingozzi (2015), [The Fixed Charge Transportation
  Problem](https://doi.org/10.1287/mnsc.2014.1947): activation costs on covers and
  discharge-port incidence. We do not reproduce their branch-and-price solver.
- Hooker and Ottosson (2003), [Logic-based Benders
  decomposition](https://doi.org/10.1007/s10107-003-0375-9): justified final-state
  necessary-rule cuts. A replay or single-ballast rejection is not a global cut.
- Land and Doig (1960), [An Automatic Method of Solving Discrete Programming
  Problems](https://doi.org/10.2307/1910129): incumbent versus certified solver
  bound, and integral cutoff infeasibility.

## Predeclared measurements

Validation maps are used for **proof counts only**, not algorithm development
or tuning. The thirteen unresolved C3 identities are fixed from its recorded
certificates before current map results. Use the unchanged C3 global bound,
seed zero, **120 seconds per map**, with the cheapest existing checked upper
from copied C3 evidence or the leader's engine-0.10.0 benchmark. A single later
**300-second** certificate continuation is allowed for any remaining gap using
the same code and seed, not per-map heuristics or constraint changes. New
constructor code is developed/tested only on quick/synthetic inputs, then its
hash and uniform settings are frozen before validation proof-count evaluation.
Unknown, a failed candidate, or missing upper evidence never means infeasible.

Constructor evaluation records the first raw assignment, the first returned
replay-legal candidate, independent full legality, eventual full legality,
construction/finishing/checking time, cut counts and load. Cold construction
does not read a known cargo assignment or ballast. An explicitly separate warm
ballast lane may retain incumbent ballast; it cannot substantiate a cold
under-one-minute replacement claim. Initial quick-small setting: 30 seconds
construction and 20 seconds finishing. Any generic rescue is separately
labelled, not substituted for first-try rates. Restricted load-once model bounds
are never reported as global proofs.

Compare lifts only with a **fresh 0.9.0 + E9** arm, under the same process limit
and recorded load. Stored rows may supply proof upper plans but not a fair
speed/lift comparator. E9-enabled passes are bug-fix gains, not solver gains.
Run the full `quick` admission at the end; medium results on V6-selected
passable maps retain that selection caveat. Medium savings are projected
later-port lifts under the no-later-loads model.

Any aggregate fixed-cohort job exceeding twenty minutes is retained to avoid
dropping difficult maps after seeing results: the thirteen-map 120-second
certificate rung, a predeclared continuation, or a fresh paired comparison can
cross that threshold. Per-map budgets remain bounded and all overruns are
recorded. No cold runtime speedup will be claimed from warm reference admission.

## Cooperative CPU scheduling during the fresh comparison

The alternating ten-map comparison is retained as a fixed cohort. Its initial
baseline adapter is copied in `references/baseline_method.initial.py`. A later
adapter revision adds only an idle gate **before** starting 0.9.0's timed work,
without changing its search, finisher, seed or budgets. Each row's plug-in hash
records the version actually imported. The gate acknowledges its own PID and
sleeps while `PAIRED-PAUSE` exists, so other team experiments may use the sole
CPU token. Only after the acknowledgment is observed is another numerical job
started. No process is killed or selected by a process-name pattern.

`method_metadata.control_idle_s` records this scheduling delay. Corrected
comparison wall times subtract it; unadjusted score rows retain it for audit.
Load is recorded again after the gate at the actual start of baseline work.
The original exact constructor and its freeze are unchanged during this run.

The planned ten-map fresh control was subsequently **stopped after its first
two complete maps**, at the acknowledged idle boundary, to prioritize the
requested proof closures and constructor gate. The other eight control maps
are unmeasured, not failures. No ten-map fresh-baseline lift/speed comparison
is claimed. The two controls passed at 22 lifts each, taking 293.22 and 202.38
seconds. Their cold exact counterparts returned no candidate and a checked
22-lift plan respectively. This limited sample cannot establish a general
speedup. All ten cold exact maps will still be measured separately.

Stopping used only the owned exec session18274 (PID63787 recorded by its own
idle acknowledgment). Direct OS signaling was denied by the sandbox; Ctrl-C
through that owned session returned exit130. No process-name match or other
session's process was used. `results/PAIRED_STOP.json` preserves the details.

## Intermediate bounded results

The unchanged global bound's first13-map rung adds **five certificates** in
618.474 seconds of bound work (620.715 evaluator elapsed), moving validation
from32/45 to37/45 after independent admission. All thirteen updated upper plans
pass the fresh full checker. Their E9-enabled passes are mask-bug fixes, not
new solver pass gains. The three medium reference plans also pass freshly at
71/70/55. Their safe bounds remain65/70/51: one inherited equality, no new
medium certificate, under this directory's stated D15 scope.

Generic admission of the cached global relaxation witnesses gives three
complete replay-legal stows but zero full-check passes; a fourth assignment
requires an actual current ship shift and fails the load-once replay. A new
bounded realization helper, developed solely on synthetic/quick data, supports
temporary slots and repeated `Place` shifts. Its work limits and source hashes
are frozen in `REPAIR_FREEZE.json` before validation use. Warm then60-second
cold finishing are separately admitted; failed repair or finishing remains
unknown. This is candidate construction, never an extra global proof rule.

The first-feasible constructor variant was rejected on quick pilots before
validation use: it produced replay34 versus22 on random40100011, and17 versus
the original constructor's8 on40100025. Original minimizing construction at
60seconds with inherited upper ballast is the frozen validation policy,
recorded in `VALIDATION_CONSTRUCTION_FREEZE.json`. No validation outcome chose
these settings or modified the eight global-bound modules.

The medium exact-order runs used120seconds each on the two gapped TRAIN maps.
They added no proof in240.033seconds: the high map had no incumbent at timeout;
the low map excluded two all-ballast-securing-impossible stows, then timed out.
The optional source-pinned weather-KG row was independently proved redundant
even without packing on all three TRAIN maps, so no validation trial is made.
See `medium_NOTES.md` and `medium_physics_review.md` for the numeric qualification,
source guards and negative result.

Verification so far:56 inherited global-bound tests,6 original-constructor,
3 fast-constructor,4 repair and8 medium/physics tests passed (**77**). The first
global suite run had55passes and one missing copied JSON fixture; copying the
two unchanged C3 fixtures and rerunning that test passed. No model code changed
to repair the test harness. `COPIED_TEST_FIXTURES.json` records their hashes.

## Target-guided construction and the final proof loop

The separate `constructor_target.py` computes a cheap independent packing lower
bound from cold input, then seeks a first-feasible stow at that cost. A guarded
assumption literal enforces the target. Only proven infeasibility of this
restricted construction target permits increasing it by one; it never raises
the independent global bound. UNKNOWN stops. Nine additional tests passed,
bringing the total to **86**. Synthetic/quick development and source hashes are
recorded in `constructor_target_NOTES.md` and `TARGET_FREEZE.json`; the eight
global-bound modules and engine-facing API remain unchanged.

Quick pilots produced22 and8 replay lifts in4.207 and6.613seconds respectively.
Formal cold finishing subsequently remains unsuccessful, so cheap objective
attainment alone does not establish a legal plan or a cold speedup. The target
policy is applied once at uniform60seconds, with inherited ballast, to the
seven remaining validation identities for proof counts only. The list and
policy were frozen before that run in `TARGET_VALIDATION_EVALUATION.json`.

The final uniform300-second cutoff rung uses only the unchanged bound. Any new
fully checked upper from target construction that improves this rung's frozen
upper but leaves a positive gap triggers exactly one further300-second cutoff
test, also unchanged and seedzero (`reprove.py`). This is the usual incumbent
tightening step, not a per-map parameter or constraint change. Already closed
maps are skipped. The full-check gate, source/input hashes and scope remain
mandatory; no failed search is an infeasibility claim.

Target construction's first new validation success is a fresh full-checker pass
at **6 lifts on sim.small.40100038**, equal to its unchanged global lower bound,
in **22.36 seconds** including admission with inherited upper ballast. This
raises the certificate count to39/45. It is a different, load-once stow, not
the earlier abstract seven-lift witness requiring a ship shift. The target
policy was source-frozen before validation; no map-specific constraint or
setting was introduced. Together with the earlier checked8 on41100001, these
are genuine new upper plans, while the engine references close other cases.

## Final results and open cases

All equality statements retain the scope at the top of this document: **fewest
lifts among plans the checker passes, counting later ports by our D15 projection
(fixed stow, no later loads)**. Current restows are unrestricted in the global
proof family; empty decision arrival, no discharge/forecast, immutable disjoint
background, finite nonnegative objective weights and recorded legacy rules
remain the guards. Construction restrictions never raise a global bound.

Final certificate counts are tiny **8/8**, quick small **10/10**, additional
validation small **39/45** (up from32, closing **7 of the13** Cycle3 gaps), and
the V6-selected passable medium cohort **1/3**, inherited. The120-second rung
used618.474seconds and the300-second rung1254.729seconds: **1873.203seconds,
31.22minutes of new bound work**, in addition to construction/admission work.
No further cutoff test was needed: both new checked validation improvements
already meet their bounds (`results/proofs-refined/SUMMARY.json`).

| Closed validation identity | Checked upper | Lower | What closed it |
| --- | ---: | ---: | --- |
| sim.small.40100001 | 8 | 8 | Updated engine upper meets the C3 bound |
| sim.small.40100002 | 9 | 9 | New integral cutoff proof |
| sim.small.40100021 | 10 | 10 | New integral cutoff proof |
| sim.small.40100038 | **6** | **6** | New checked target-model plan, inherited ballast |
| sim.small.41100001 | **8** | **8** | New checked minimizing-model plan, inherited ballast |
| sim.small.41100003 | 8 | 8 | Updated engine upper meets the C3 bound |
| sim.small.41100013 | 8 | 8 | Updated engine upper meets the C3 bound |

The requested thirteen-map closure is **not complete**. All six remaining
cases were retained and attempted under the fixed policies:

| Open identity | Checked upper | Lower | Gap |
| --- | ---: | ---: | ---: |
| sim.small.40100026 | 4 | 0 | 4 |
| sim.small.40100029 | 8 | 6 | 2 |
| sim.small.40100045 | 6 | 4 | 2 |
| sim.small.41100000 | 13 | 6 | 7 |
| sim.small.41100009 | 10 | 8 | 2 |
| sim.small.41100011 | 5 | 4 | 1 |

Four remain CP-limited at300seconds; two admit cheaper abstract static/securing
screened witnesses that did not finish successfully under either inherited
ballast or the60-second cold ballast attempt. Neither outcome proves those
maps infeasible, nor proves that the current upper is minimal.

### Does exact construction replace small-map search?

**No.** The original minimizing constructor,30seconds construction plus20seconds
cold ballast finishing, gives **8/10 complete replay-legal candidates but2/10
full-check passes**, both with attached equality certificates. Mean total wall
is **32.986seconds per selected map**, including failures. All ten first returned
raw CP witnesses replay, but **0/10** pass the separate20-second cold-finishing
gate. This is a budgeted finishing failure rate, not proof that every raw stow
is impossible under every ballast.

Using the frozen incumbent ballast instead of cold finishing raises the original
constructor to **4/10** full checks. Cached-stow admission alone takes75.543seconds
summed; this excludes original CP construction and cannot substantiate a cold
speedup. The target variant gives6/10 replay candidates and **0/10 cold or warm
full checks** on quick small, despite its successful validation6-lift plan.
First-feasible and target construction therefore remain experiments, not a
drop-in replacement. The C3 checked8-lift plan for40100025 remains valid and
is freshly rechecked in the final portfolio; the newly regenerated8-lift stows
here did not pass the tested ballast lanes.

The two completed fresh0.9.0+E9 controls both pass at22lifts; there is one mutually
passing map,22versus22. No cohort-level lift saving or relative runtime is
claimed. Cold constructor load snapshots range from **10.58 to27.73** on the
one-minute POSIX load average. Other sessions and parked cores make these
wall-budget outcomes load-dependent. The benchmark marker disappeared before
the later two-process measurements.

### Final gate, verification, and verdict

The final `lab/score.py --set quick` uses a frozen portfolio of newly checked
C4-generated plans and carried upper-plan fallbacks. It produces **20/21 complete
replay-legal plans and20/21 full-check passes**: tiny8/8, small10/10, medium2/3.
Total admission wall is148.199seconds, with no search. Small mean lifts across
all ten are11.4; tiny mean is6.375. The runner's printed stored-row comparisons
are preserved for audit and are **not** used as fair C4 lift/speed comparisons.
The additional E9-enabled historical passes are a mask bug fix, not solver gains.
Medium71/55/70 references remain preselected as passable; their differences and
all savings remain subject to the stated no-later-loads D15 projection.

**86 tests passed**, all48 top-level Python files parse, and `git diff --check`
is clean. `results/VERIFICATION.json` verifies **143 complete C4 admission rows**,
all **66 certificate input/upper identities**, unchanged checker/source hashes,
all constructor freezes, and the eight byte-identical global-bound modules.
The bound signature remains `(instance, *, budget_s=15.0, seed=0, tables=None,
upper_bound=None)`; DG widening remains intact. There are no owned numerical
jobs left running. Every source, copy, cache, report and result lives in this
directory; no engine/shared source, sealed data or other session was modified.

**Keep the bound; keep construction experimental.** The exact model supplies
new checked stows and seven additional certificates but does not replace cold
search under a minute. The medium formulation stalls near15000variables and
142000constraints and needs stronger physics/order structure. Next: derive
coupled stability/securing necessary cuts and physically informed constructive
constraints on a separately declared TRAIN set. Do not tune on this validation
cohort. Main entries remain `certified.py:lower_bound`, `constructor.py:construct`
and `constructor_target.py:construct`; full per-map evidence is in
`results/TABLE.md`, `results/CERTIFICATES.json` and `results/SUMMARY.json`.
