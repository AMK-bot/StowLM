# Cycle 4: whole fill ladder, moment pricing and necessary physics

Read the full lab contract through the Cycle 4 update and corrections, lab
README, and C3 Benders NOTES/BINDINGS. Only this executive folder is written.
No sealed data or mutable `engine/` imports; no checker/rule source changes.
All maps are development TRAIN data. The prior four primary maps were chosen
from V6-passing maps and are **preselected as passable**. V6 is a constructor
built to pass, **not a lift benchmark**. Its lift totals are not a comparator
in this report. Stored 0.9.0 rows are not a fair runtime/lift arm; no solver
advantage is inferred from them. The authorized E9 wrapper is a bug correction,
not a solver gain. Medium later-port lift counts are the D15 fixed-stow,
no-later-loads projection. A matching lower/upper bound means fewest lifts
among plans the checker passes under that projection, never an unrestricted
voyage optimum. Relocation minima are used only for the specific guarded
maps where their independent certificate applies, not all validation data.

## Model, sources and reused code

The graph retains physical stack pages and concrete cargo vertices, with
port-cover edges. A used hold cover costs two hatch lifts now; each used
port-cover edge costs two later. Internal and hatch blockers are union-counted.
The concrete small 18-box/two-cover and medium88-box/six-cover examples remain
in the C2 model notes. Exact yard replay measures all five lift components.

Gilmore and Gomory (1961), https://doi.org/10.1287/opre.9.6.849, motivates
page column generation. Pisinger and Ropke (2010), *Large Neighborhood Search*,
https://backend.orbit.dtu.dk/ws/portalfiles/portal/5293785/Pisinger.pdf,
motivates whole-hatch neighbourhoods. Griffith and Stewart (1961),
https://doi.org/10.1287/mnsc.7.4.379, and Broyden (1965),
https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/,
motivate the inherited local linearization of exact physical margins.
These sources and interpretations were reviewed in prior cycles; no downloaded
implementation is run. The proof enclosure source is recorded in
[physics_bound_notes.md](physics_bound_notes.md).

`pages_base.py` copies C3 layout pages. `pages.py` adds actual mass-moment
rows and pricing duals, with geometry-refined pricing types. It distinguishes
intact-stability nominal body/support geometry from expanded OOG-envelope
geometry used by visibility/securing; the former is default. Targets are soft
in the LP to generate useful duals, with explicit coverage and target slacks.
Tail/dive guards prevent old first-fit shortcuts bypassing requested targets.
These are construction restrictions, never necessary whole-map inequalities.

`constructor.py` copies C3 Benders construction/descent; its model now uses
these local pages, geometric moment duals and a restricted CP master with
soft moment targets. The whole-map CP remains able to emit a complete stow
when its target cannot be attained. Coverage slack and moment slack are
reported separately. `exchange.py` retains separately copied C3 descent helpers.
`repair.py` copies C3 OOG repair. `lns.py` copies C3 fixed-charge LP/IP LNS,
adds every new moment row to both masters and saves/restores fixed outside
columns for correct partial pricing RHS. Its pool objectives are never map
bounds. Exact sequencing, complete box coverage and native move replay remain
mandatory. Every emitted plan is full-checked independently by score.py.

`physics.py` copies C3 Benders native PhysicsEvaluator unchanged. `ballast_opt.py`
copies its trust-region LP, reducing direction count to DB-F, DB-M, FWD-DEEP,
HEEL and a HEEL split so a short run can evaluate an actual local step. Native
area-to-flood and securing margins, including phase-specific thresholds, drive
the search. Scalar GM is not used as a substitute. The search Jacobian is
heuristic, and a local failure never proves physical infeasibility. Every
accepted ballast recipe requires the unchanged native full checker.

## Fixed cohorts and diagnosis

[MANIFEST.json](MANIFEST.json) fixes all 48 approved input identities and hashes
before new outcomes: f60/f70/f80 each12 from **all** TRAIN V6 metadata at those
rungs, regardless of V6 outcome, and the public medium12 development list.
f80 exists and is included. The 36-map ladder is not filtered to V6 passes.
V6 constructor pass context is5/0/0, not a lift benchmark. Full selection and
source hashes are in [diagnostics_notes.md](diagnostics_notes.md).

[FAILURES-C3.md](FAILURES-C3.md) identifies every prior13 failure and exact
rule IDs. Three feeder500 f70 low maps reach securing-only near misses; one
feeder800 f60 low map reaches E9-only. The other nine combine securing with
visibility, draft/trim or E9. All had complete legal replay trials, but C3
returned no candidate after discarding failed trials. No map was proved
infeasible. C4 saves each concrete failed stow and its full report for repair.
The three-map pilot deliberately targets one securing-only, one E9-only and
one combined-failure development map. The whole cohort remains fixed.

## Necessary lower-bound experiment

[physics_bound_notes.md](physics_bound_notes.md) gives the complete proof and
source guards. The new rows use outward-rounded displacement/tank ranges,
weather virtual-KG implications, mass-times-support-height lower moments,
and minimum-motion securing floors, without identifying stability and securing
GM. A second model retains physical cell/tier occupancy, no-gap prefixes and
necessary tier support-height floors. Every legal plan maps to these enlarged
feasible sets; only BestObjectiveBound or an independent analytic certificate
is used, never a relaxation incumbent or restricted page objective.

Both four-map experiments retain bounds **65/90/70/51** after maximising with
the hash-matched C3 port-Hall certificate. The first physics model gives raw
63/90/70/51; the larger cell/tier model gives63/88/70/51 within15-second caps.
The low500 cell/tier relaxation solves to shipping cost4, so its remaining
operational gap4 is not closed by these safe rows. This is a negative result.
The actual binding finite-angle area criterion, exact signed trim/list and
most securing interactions remain relaxed. A sampled righting curve or local
Jacobian would be an invalid universal bound; none is used as one.

## Compute, validation and reporting

BENCH-RUNNING existed at admission. Until it disappears the entire team uses
one CPU-busy process, one numerical thread. Proof/coding agents do not run
numerical work without the explicit exclusive token. Bounds, then pricing
tests, then stronger bounds, then root tests/pilot run serially. All numerical
calls cap OMP/OPENBLAS/VECLIB (and auxiliary numerical pools) to one and use
only the scoped vessel cache, disabled bytecode and disabled pytest cache.
`nice -n5` is requested; sandbox priority adjustment is denied. No process is
killed. `run_score.py` records the owned PID, start/end time, load averages and
arguments. Per-map metadata records load too. Budgets remain load-dependent.
The constructor has count limits of4 attempts,32 coverage pricing rounds,
12 improvement rounds,2 pool variants,4 local exchange passes and8 full
checks, plus a wall cap. These are not deterministic work guarantees: pricing
and native checker costs vary by map. Short numerical search cannot establish
infeasibility.

A whole48-map sweep can exceed20minutes because it covers every predeclared
rung and medium12, with real replay/full checks; no individual map is allotted
20minutes. The pilot uses90s proposal,60s finalization,2 tries,1 worker. Final
cohort budgets and load will be recorded with their results below.

Validation so far:8 moment tests,11 inherited LNS tests,5 LP/IP tests (including
new moment-row integration), and6 necessary-physics/tier tests: **30 distinct
tests passed** in scoped batches. One real88-box checked incumbent's intact
cargo moments match the native full-minus-empty loading condition to1.46e-11
tonne-metres; [moment-regression.json](moment-regression.json). Necessary-row
and tier tests independently map all four prior full-checked incumbents.
AST/source hashes and fresh final-plan audits will be added after measurement.

The report covers all 48 selected maps, separately stating complete replay and
full-check counts, and lists actual failing rules on **every** failed map.
Warm inherited C3 passes are labelled, including the Benders medium12 witness;
retaining one is not a new constructor pass. No runtime claim compares a warm
incumbent with a cold constructor.


## Pilot, integration review and frozen cohort arm

The original three-map pilot completed1/3 full passes: new500/f70/low/000
passes at135 lifts (53 relocations,12 current+18 projected later hatch lifts,
52 projected restows). It is a cold new pass relative to the C3 development
outcome, with the same authorized E9 bug fix in both. The other pilots clear
loading/sight in some candidates but remain rejected. The pilot averaged149.18
wall seconds/map at proposal cap90/finalization60; first-map cold native setup
contributes atomic checker time beyond the proposal cap. Source-role snapshots
are `method_pilot.py`, `constructor_pilot.py` and `PILOT-SOURCES.json`.

A later single-map cold smoke on500/f70/low/001 failedS/A/V at77.01s. Saved
native reports show cargo CG13.6--14.2m and roof24.575m, versus9.1m and20.764m
in the passing pilot. The failing arrival area.26286 is below.33130 required.
The failure is not explained by an excessive-GM claim. Detailed JSON-only
native-margin extraction is in [physical_diagnosis.md](physical_diagnosis.md).

The frozen cohort arm therefore keeps every oracle-free attempt capped,
prioritises the passing pilot's(3,5,5) profile and physical vertical pricing,
and adds a soft cargo-moment target at hull depth+0.5m. This is a search
preference, never a necessary physics inequality. Complete hints missing
optional moment targets may enter the soft whole-map CP; native checking
still decides. A separate cheap C3 fast-page coverage seed provides candidate
reference motions and a concrete fallback. It has at most3 greedy retries,
16 coverage rounds and2 dive retreats. The last source change was made before
cohort measurement; hashes are in `COHORT-SOURCES.json`.

Code review caught and corrected a warm-reference error: move replay does not
apply `plan.ballast`. The fitted securing oracle now uses the native public
`departure_condition(instance,plan)`, including the actual checked tank recipe.
No physical provider is patched. Fixed-column moment RHS and both fixed-charge
master integrations were reviewed too; see [review.md](review.md).

The original arm A measured f60/f70 at120s proposal and45s finalization.
After discovering its fallback HiGHS setup defect, the corrected dense sweep
measures fixed cohorts of24 f60/f70 maps,12 f80 maps and12 medium12 maps.
Ladder proposal caps are120s and medium12 uses180s; all use45s finalization,
2tries and1worker. At most two processes are admitted after BENCH clears. Already-passing nonprimary maps receive bounded
warm LNS rather than whole-map reconstruction; all remaining maps receive
cold reconstruction. C3 inherited passes and the one accepted pilot plan are
explicit references, freshly checked. Retaining them is not another new pass.
D185's114-lift Benders witness is inherited and labelled similarly.


Diagnostic preservation: the first prototype used map/trial filenames without
a run tag. Later measurements of the same map can replace those convenience
paths. Before overlapping maps were remeasured, existing plans/reports were
copied by content SHA256 into `diagnostic-archive`; `DIAGNOSTIC-ARCHIVE.json`
records the original path and immutable archived bytes. Failed-trial violations
are also embedded in each immutable score row. Final passing plans are stored
in each score row and independently audited from that concrete JSON. Reporter
and audit utilities may be improved after runtime-source freeze; constructor,
pricing, LNS and method source hashes remain unchanged during each arm.
The portfolio has no single runtime: only latest-run per-rung times and total
all-attempt effort are reported, without a cherry-picked portfolio speed claim.

## Corrected single-thread dense sweep

The first dense f80 smoke exposed a real integration defect: the plain fallback
LP omitted HiGHS `threads=1`, which initializes a default multi-thread scheduler
and makes a later explicitly single-threaded moment LP return status4/NotSet.
A tiny two-LP reproduction confirmed the native scheduler message (9 threads).
The smoke and any earlier affected fallback measurements are retained as
software-affected evidence, not a clean physics search or resource-compliant arm.
Passing concrete plans still require fresh native audits. `pages_fast_dense.py`
is a private copy with explicit single-thread options; two regressions passed.
All dense solver paths were reviewed for explicit one-thread configuration.

The corrected dense method remeasures the whole f60/f70/f80 ladder, including
all previous failures, plus medium12. It may reuse the best full-checked C4
or C3 plan, with the exact warm source recorded; this retains the new62-lift
plan. These are warm development measurements, not a fresh0.9 comparator.
The dense f60/f70 and f80 runs use120s proposal/45s finalization, medium12
uses180s/45s; all use2 tries/1 worker. Runs beyond20minutes are necessary
for the predeclared full cohorts and their atomic native physics checks.
BENCH-RUNNING was absent before admitting the corrected two-process sweep.
`DENSE-CORRECTED-SOURCES.json` freezes source hashes before measurement.

## Proof review status

[PROOF-AUDIT.md](PROOF-AUDIT.md) separates the necessary weather, minimum-motion
and tier rows from the ineligible trim diagnostic. The latter passed7 analytic/
safety tests and evaluated high88 in4.987s over40active-tableboxes, but never
entered a reported certificate: direct floating clipping and active liquid
interpolant enclosures remain unproved. All new-bound maxima stay independently
backed by the earlier hash-matched Hall/E6 certificates. The binding finite-angle
area test remains relaxed; no scalar-GM or sampled-GZ surrogate is called a proof.

The corrected dense run reaches securing-only complete alternatives on both
remaining500/f70/low maps. A separately copied adaptive method refreshes the
next attempt's motion oracle from native departure_condition of the actual
candidate, instead of retaining only the initial coverage hint's motions.
It also seeds the next attempt with that candidate, preserving any accepted
warm anchor. Current attempt caches are not changed. This is heuristic search
feedback, not a necessary inequality or a full-check acceptance change.
`adaptive-targets.txt` fixes the three historical near-miss cases before running
that follow-up; the whole48-map dense cohorts remain reported separately.

## Combined verification

The complete local suite passed **56 tests**, exit0, with cacheprovider disabled,
one numerical thread and the scoped vessel cache. It ran only after f80 exited,
with the f60/f70 worker occupying the other slot. `TEST-RESULT.json` records
the command and time. This combines moment/native-mapping, LNS LP/IP, balance,
sight, necessary-bound rounding/image tests, trim analytic/safety gates and
the two same-process HiGHS thread regressions. Trim tests do not certify the
uncompleted global numerical enclosure.

## Final fixed-cohort results

The corrected dense sweep measured every predeclared map through `lab/score.py` with list files: **48/48 complete replay-legal stows, 13/48 full passes**. The whole ladder was selected independently of V6 outcomes. The four primary maps remain preselected as passable; their warm references and the inherited D185 Benders witness are labelled in each score row. Retention is not a new constructor pass.

| Set | Measured | Complete replay | Full pass | Mean lifts of passing maps | Mean wall seconds, all maps |
|---|---:|---:|---:|---:|---:|
| f60 | 12 | 12 | 8 | 92.25 | 69.17 |
| f70 | 12 | 12 | 4 | 112.50 | 70.41 |
| f80 | 12 | 12 | 0 | — | 92.21 |
| medium12 | 12 | 12 | 1 | 114.00 | 82.36 |

Fixed-sweep per-map starting one-minute load average ranged from 11.00 to 16.34. Ladder budgets were 120 s proposal/45s finalization; medium12 used 180 s/45s, all 2 tries/1 worker. These are descriptive wall times, not deterministic work or a speed comparison. Proposal/native atomic setup can exceed a wall cap. Passing-only means use different subsets and must not be interpreted as a paired lift comparison.

**Fresh 0.9.0 paired arm: not run; paired lift/pass/time comparison is N/A.** No stored-row or V6 lift comparison substitutes for it. V6 is a constructor built to pass, not a lift benchmark. The same authorized E9 bug correction was already present in C3, so the one new 135-lift f70 pass is not credited to adding that fix. It is a new development witness relative to historical C3, not a demonstrated advantage over fresh 0.9.0.

[REPORT-FIXED.json](REPORT-FIXED.json) and [FAILURES-FIXED.md](FAILURES-FIXED.md) preserve the fixed sweep. [REPORT.json](REPORT.json) and [FAILURES.md](FAILURES.md) additionally include the separately measured three-target adaptive follow-up as latest outcomes. Every failed map has its actual rule IDs, first violation example per rule and maximum securing ratio; every saved violation remains in JSON. No timeout, restricted-family failure or discarded candidate proves map infeasibility.

The new full-checked f70 witness is `500/low/000` at 135 lifts: 53 relocations, 12 current plus 18 projected later hatch lifts, 52 later restows. Fixed-charge LNS improves two other accepted starting plans:`500/f60/high/001`: 100→94 and `500/f60/low/001`: 66→62. Those are comparisons of concrete accepted witnesses on the same maps, not engine/V6 benchmark claims.

| Primary map | Checked lifts | Relocations | Hatch now / later | Restows now / later | Certified lower bound | Gap |
|---|---:|---:|---:|---:|---:|---:|
| feeder_500-f60-high-s192200000 | 71 | 47 | 12 / 12 | 0 / 0 | 65 | 6 |
| feeder_800-f60-low-s192200101 | 106 | 84 | 10 / 12 | 0 / 0 | 90 | 16 |
| feeder_500-f60-high-s192200002 | 70 | 46 | 12 / 12 | 0 / 0 | 70 | 0 |
| feeder_500-f60-low-s192200000 | 55 | 47 | 4 / 4 | 0 / 0 | 51 | 4 |

The four checked totals 71/106/70/55 and bounds 65/90/70/51 are unchanged. The 70-lift equality means fewest lifts among plans passing this checker under the D15 fixed-stow, no-later-loads projection. Necessary weather, displacement, minimum-motion and tier rows did not improve the certificates. A 240-second high88 control admits a 65-lift relaxation witness, so more solving of that model cannot close its gap. Trim diagnostics remain certificate-ineligible; native hull/liquid interpolation and direct-clipping error enclosures are incomplete. See [PROOF-AUDIT.md](PROOF-AUDIT.md).

The optional adaptive-motion follow-up returned 0/3 passes, 3/3 complete replay stows, mean 104.58 s. Fourteen oracle refreshes succeeded with zero refresh errors. Keep it as a negative experiment; it adds no accepted lift improvement or pass. `method_dense.py` remains the recommended measured entry point.

Validation: **56 tests passed** in the combined suite; **14 distinct passing plans** independently replayed and full-checked with zero violations, exact load counts and matching inputs/objective. They cover all 27 passing rows across all recorded attempts. Input hashes match all 48 approved maps; 12 frozen dense runtime sources, the adaptive source and target list match their snapshots. All local Python files parse. [AUDIT.json](AUDIT.json), [VERIFICATION.json](VERIFICATION.json), [TEST-RESULT.json](TEST-RESULT.json), [FINAL-SOURCES.json](FINAL-SOURCES.json) record evidence. No sealed data, checker/engine edits, external writes, process kills or ongoing numerical jobs.

Resource precision: the confirmed nine-thread HiGHS initialization and status4 mismatch occurred in the first separate f80 smoke. The earlier arm omitted the same explicit cap, so its resource compliance was not established and it is conservatively excluded from compliant-arm comparisons; no individual A violation is inferred. The corrected private fallback explicitly requests one thread before its first LP, and the whole corrected 48-map sweep records no software errors. All old witnesses were independently audited.

**Verdict: keep part.** Keep moment-aware pricing, fixed-charge LNS, correct thread configuration, saved failure diagnostics and certificate guards. Drop any claim of closing the primary gaps or demonstrating a solver/speed advantage over fresh 0.9.0. Next: a validated finite-angle area envelope covering actual hull and liquid interpolation, and a stronger physical packing/visibility relaxation; keep actual native area-to-flood and securing together in search.

Entry point: `method_dense.py:propose` / `method_dense.py:finish`, measured with `run_score.py` and the fixed `ladder60-70.txt`, `f80.txt`, `medium12.txt` lists. `report.py` rebuilds summaries from result directories; `audit.py` independently rechecks saved passing plans. All writes remain under `experiments/mvp/frontier/lab/c4/c2-layout/`.
