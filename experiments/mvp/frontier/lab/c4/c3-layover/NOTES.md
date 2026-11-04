# Cycle 4: work-bounded layover construction and ballast alternatives

Scope: only `lab/c4/c3-layover/` is writable. The full CONTRACT, including Cycle
4, was read. No sealed data, production source, checker, catalogue, parameter,
prior-cycle result, or shared engine file is changed. The public full checker
remains the only pass gate. Complete replay and full-checker acceptance are
reported separately; a local LP failure is never physical infeasibility.

## Predeclared experiment

72 synthetic TRAIN maps: twelve per (tiny/small/medium) × (30%/50% arrival
occupancy) cell. Fresh seeds 331401000–331401071 in that size/occupancy order;
all 72 are fixed before solver outcomes. No old six-map outcome is counted as
a fresh result. The C3 wrapper retains native no-DG standard-dry20 masses on
small/medium and native empty cargo on tiny; lighter boxes become bottom-closed
arrival prefixes and one current discharge lies under retained late-port cargo.
This deliberately exposes inherited-prefix restow choices. It is a structural
Stage B cohort, not a representative mixed-cargo or prior-voyage simulation.
Actual occupancy and all generation failures/conditioning must be recorded;
no solver failure may remove a map from the denominator.

Primary arms: fresh unmodified 0.9.0 plan_and_check, a fresh run of the frozen
engine copy, and C4 arrival pages. Native 0.9 uses historical search allocation;
the engine gets budget_s=None (true 120/375-second extra defaults), rather than
the C3 zero-extra diagnostic. The requested native finishing budget is 60
seconds; the copied engine retains its >=300-second small-route native floor.
This unavoidable effective-budget difference is reported, not called equal
work. The new method uses explicit deterministic construction/ballast work
limits, with native V4 fallback separately labelled if used. Every returned
plan receives independent real replay and stowage.checker.check.

`engine_copy/MANIFEST.json` pins 98 Python files copied at admission. It is
engine 0.11.0, before fx-engine's forthcoming prefix integration; none changed
during the copy. The copy and frozen Frontier dependencies are local. Do not
claim comparison against the yet-unmeasured integrated engine version.

## Model and intended changes

Stack pages keep fixed arrival prefixes; patterns consume residual tiers,
mass and height. Buying a prefix removal changes the available page family
and incurs the original move replay's off/on or ship-shift lifts. A cover
vertex couples hold access to all deck prefixes already blocking that cover.
Tank-mass variables couple selected pages through ship mass and longitudinal,
transverse and vertical moments, then nonlinear voyage-state stability and
securing. Lifts remain D11: yard relocations, current restows/covers and later
D15 projected restows/covers. D15 fixes departure stow and has no later loads.
Concrete fresh small/medium graphs, masses, residual cells and blocked-cover
memberships are written out in [GRAPH.md](GRAPH.md).

C3 larger candidates reached real replay but timed out in native ballast.
C4 tries inherited tanks, then a local ballast LP using the actual native
righting-area/securing oracle, and uses moment/height feedback in pricing.
GM alone is inadequate: the C3 benders result's binding criterion was total
positive GZ area to flooding/first vanishing angle, with securing opposing
higher GM. Linear guidance is heuristic; only exact checking establishes a
pass. Construction stops by counts (pricing, LP iterations, patterns, branch,
dive and integer-search work), never a shared-machine wall-clock race.

The primary settings are fixed in `PROFILE.json` before the primary three-arm
measurement. One fresh-cohort tiny map is also a full-checker regression fixture;
these maps are TRAIN development data, with no outcome-based seed selection.
The profile allows 8,000 pricing calls, 12,000 patterns, 200 master LP calls,
200,000 total master iterations and 10,000 iterations per LP; other construction
counts remain the documented defaults. CP-SAT uses 0.5 deterministic work
units and 100,000 conflicts per solve with one worker. Finishing permits
28 exact native oracle calls, two local iterations, a 0.15 initial trust
radius and 2,000 iterations per local LP. Native backtracking verifies proposed
steps at scales 1, 1/2 and 1/4. The optional native V4 fallback is disabled.
`run.py` forwards that profile to the direct-checker `runner.py`; no custom
path is smuggled into `lab/score.py`'s approved-input allowlist.

Prior-cycle TRAIN pilots, excluded from the fresh 72-map comparison, exposed
construction regressions before measurement: the 1,000-per-LP cap stopped
models despite unused global work, physical pricing groups prevented ordinary
cross-side balancing, and guided greedy placement displaced the earlier
room-first completion. These were corrected with the larger per-call cap,
separate exact-legal geometry balancing groups, and an admitted room-first
fallback. Coverage-only phase 1 shares exact geometry/prefix types; phases
with positional cost retain physical pricing groups. Failed CP calls report
status, branches and conflicts rather than disappearing.

The old medium-30% TRAIN pilot at 14 ballast calls and a 0.03 radius produced
one complete 350-lift stow in 143.77 seconds of construction, but failed the
full checker after 536.99 seconds total. The local lane improved arrival
righting-area spare from -0.13662 to -0.12753 m·rad; visibility still failed.
Those are diagnostic lifts and local improvements, not a passed upper bound
or a physical impossibility certificate. `results/pilots/` retains failures.
The final old small-50% pilot restored one 117-lift complete stow in 3.75
seconds; it failed native area/draught/trim after 23 oracle evaluations
(466.97 seconds total). No passing objective is claimed. The medium pilot
also exposed a missing local guide for visibility: primary local separation
therefore includes the ordinary native visibility provider at all three D57
states, under the same 28-call cap. Its full-checker acceptance gate is unchanged.

## Sources and provenance

Local copies/adaptations: C3 layover pages/base/model/cohort/runner; inherited
ballast-first scheduling from `c1/fx-finish`; real-area/securing physics and
successive local LP ideas from `c3/c2-benders`. Exact copy provenance and
limitations are documented in the associated implementation notes.

Primary mathematical sources:
- P. C. Gilmore and R. E. Gomory (1961),
  [A Linear Programming Approach to the Cutting-Stock Problem](https://doi.org/10.1287/opre.9.6.849): stack-pattern column generation.
- R. E. Griffith and R. A. Stewart (1961),
  [A Nonlinear Programming Technique for the Optimization of Continuous Processing Systems](https://doi.org/10.1287/mnsc.7.4.379): successive local linearization.
- C. G. Broyden (1965),
  [A Class of Methods for Solving Nonlinear Simultaneous Equations](https://www.ams.org/mcom/1965-19-092/S0025-5718-1965-0198670-6/): consulted through C3; no Broyden or secant updates are implemented here.
No downloaded code is run. These nonconvex search heuristics carry no global
convergence or infeasibility certificate.

## Compute and long-run reason (recorded before measurement)

BENCH-RUNNING exists at admission: at most ONE CPU-busy process across this
whole team while it exists, and at most two afterwards. Read/code agents never
start numerical work without the root's allocation. All numerical processes
set OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
MKL_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 PYTHONDONTWRITEBYTECODE=1;
LP/CP solvers use one worker. Caches and all outputs stay in this folder.
Long commands request nice -n 5; the sandbox may deny setpriority, in which
case inherited priority is stated. Only owned recorded jobs may be stopped.

Generation and the complete 216-row comparison may each exceed twenty minutes:
72 fresh maps retain every cell/seed, three fresh arms keep failures, and the
native baseline search/finisher floors plus full-checker physics are expensive.
Keeping the full predeclared cohort prevents selecting only fast or passing
cases. This is the stated reason for those long jobs; no cloud/GPU is used.
The admitted medium integration pilot may also exceed twenty minutes: it
must finish its predeclared construction/oracle counts to diagnose the
large-map ballast barrier. Native nonlinear physics evaluations are atomic;
their elapsed time is recorded, never used to alter the construction result.
An individual primary method map can likewise exceed twenty minutes when
two candidate stows each consume 28 three-state native oracle evaluations.
Completing the identical predeclared count cap, including failures, avoids
reintroducing a machine-load-dependent wall deadline into this experiment.
The leader's marker cleared after the first native09/engine admission pair.
Those two one-process rows are preserved in `results/admission-comparators/`
and excluded from primary timing/lift tables. All 216 primary jobs are rerun
with two single-thread shards after final verification; map arm order remains
counterbalanced and actual start/end load is recorded. The tail may naturally
have only one active shard. No stored prior-cycle comparator is used.

## Results

Generation completed all 72 predeclared seeds without a failed or replaced
map. Every arrival passed structural support/order/mass/nonoverlap validation;
this does not certify prior-voyage physical feasibility. The frozen cohort
manifest SHA-256 is
`3ad179492d2b7ab255c5e8974aa12eba01b1676abbd36057e29031af8f52f578`.
The initial 53 integrated regressions passed in 22.18 seconds; exact command,
environment, source hashes and output are in `results/verification.json` and
`.log`. After native visibility separation and shard-safe atomic output were
added, the final suite passed all 56 tests; its command and final source hashes
are in `results/verification-final.json`. All 98 frozen engine/dependency copies
rehash correctly. Across all 7,920 cohort containers there are zero OOG or DG
boxes, so no observed pass difference can be attributed to the E9 OOG fix.

All 216 fresh primary rows completed; both original benchmark sessions exited
0. The strict final report (without `--allow-partial`) validated all twelve
recorded seeds per cell, all input bytes, runtime source maps, cell metadata
and passed-plan replay/objective evidence. The independent raw replay audit
also exited 0. Commands, PIDs, logs and exit codes are in
`results/reporting-run.json`; the complete primary table is [TABLE.md](TABLE.md).

| Size | Target occupancy | Fresh 0.9 pass / lifts | Frozen engine pass / lifts | Pages pass / lifts | Pages complete replay |
| --- | --- | --- | --- | --- | --- |
| tiny | 30% | 0/12 / — | 12/12 / 7.833 | 12/12 / 5 | 12/12 |
| tiny | 50% | 0/12 / — | 12/12 / 12.5 | 12/12 / 8 | 12/12 |
| small | 30% | 0/12 / — | 0/12 / — | 0/12 / — | 9/12 |
| small | 50% | 0/12 / — | 0/12 / — | 0/12 / — | 9/12 |
| medium | 30% | 0/12 / — | 0/12 / — | 0/12 / — | 10/12 |
| medium | 50% | 0/12 / — | 0/12 / — | 0/12 / — | 9/12 |

Lifts above are means of full-checker passes only. The two tiny cells give 24
mutual engine/method passes: 10.1667 versus 6.5 mean lifts, a 36.07% reduction,
with no lost pass. Tiny 30% current/later restow lifts are 2/0.5 for the engine
and 1/0 for pages; tiny 50% gives 4/3 and 4/0. All these restows belong to boxes
already aboard. Mean paired tiny wall time is 12.618 versus 10.309 seconds;
this is not a 3x speed gain. There are no mutual full passes against 0.9, hence
no paired lift saving against that arm. Its 24 tiny calls raise the native
`Discharge.slot` AttributeError, while its other 48 calls return no finished
plan. These failures are action-domain/search limitations, not infeasibility
certificates. Comparator replay gates are unavailable when no plan is exposed.

Across all 72 rows per arm, including failures, mean wall seconds are 36.26
(0.9), 53.55 (engine), and 383.43 (pages). Native budgets differ as recorded
above; one-minute system load at starts/ends ranges from 9.19 to 43.55.
No speedup is claimed. The original two single-thread shards ran after the
leader's marker cleared, with a one-shard tail. An attempted STOP of the owned,
recorded parent PID was denied by the OS; no process was stopped, no job was
resharded or duplicated. Both original sessions exited normally.

Pages produce 61/72 complete cargo replays, including 37/48 larger maps, but
none of the 48 larger maps passes the full checker. [REPLAY.md](REPLAY.md)
separately reports raw diagnostic lifts and current/later arrival restows,
with ordinary replay, D11 and future-attribution agreement checked for each
saved witness. These are not feasible-plan upper bounds. No pass or lift gain
is claimed from those witnesses, a local LP result, stored rows, or E9.

The complete 72-row construction audit in `results/work-audit.md` finds zero
profile, source-hash, global-cap, branch-quota or accounting mismatches.
Maxima are 8,000 pricing calls, 12,000 patterns, 200 master LP calls,
95,391/200,000 LP iterations, and 7/8 CP calls; maximum recorded integer
conflicts are 3,754/100,000. No wall-clock construction stop is present.
Actual CP deterministic ticks are not emitted; model setup, legality/replay
and native physics remain finite input-dependent work outside those counters.
There is no end-to-end wall-time guarantee.

The completed tiny-row audit exposes the copied engine's arrival-domain
metadata bug: all 24 engine plans independently pass, yet every native record
says `complete_replay_legal=false`, zero stows and an empty archive. The local
runner's replay/checker gate is authoritative. All 144 future arrival-restow
attribution flags across the 48 tiny passed plans agree with the checker;
current/future arrival-restow sums also equal total restow parts there.
These audits read saved rows, without altering a solver or checker.

The full failure audit in `results/failure-audit.md` separates the 48 larger
method maps as follows. Categories are disjoint at map level; a mixed map
with one E8-skipped candidate and another eligible local candidate is in the
local category.

| Size | Construction, no complete replay | Static E8 only | Eligible local repair, still fails | Full pass |
| --- | ---: | ---: | ---: | ---: |
| small | 6 | 4 | 14 | 0 |
| medium | 5 | 0 | 19 | 0 |

Across 43 eligible attempts on 33 maps, the local lane makes 979 exact oracle
calls and 84 LP calls. Forty-two attempts finish both configured iterations;
one medium attempt stops with unknown native separation before an LP. None
hits the oracle-call cap, and all retain a negative actual righting-area
margin. Small local failures additionally include visibility (22/24 attempts),
trim (19), draught (15) and securing (2). Medium failures include draught
(19/19), visibility (17) and securing (10). These are overlapping rule counts,
not independent map totals or infeasibility certificates. Raising only the
oracle-call ceiling would add no work under this two-iteration profile.

There are seven static E8-skipped candidates on five small maps, including
one mixed map. Native cargo expert attributes were retained; non-DG dry20
does not imply absence of heat constraints. The inherited C2 static probe
has an all-denied heat fallback that can admit checker-rejected patterns.
The unchanged independent full gate prevents a false pass, and the finisher
skips ballast search when a static hard rule fails. Such a candidate is not
evidence that the local ballast LP failed. Raw prefinish replay witnesses are
retained in `replay_evidence`; failed final water plans/full checker reports
are not. This ID-only audit has not attributed the exact E8 cargo/heat geometry.

## Verdict and next experiment

**Keep part.** Fixed arrival prefixes, blocked-cover layouts and bounded
construction produce a measured tiny improvement over the frozen engine,
but this experiment supports no larger-map rescue or speed claim from the
new ballast-aware pricing/local finishing combination. Its 37 larger legal
replays are useful diagnostic inputs, not acceptable stows. This is a
synthetic TRAIN cohort with uncertified prior-voyage physical feasibility;
it does not establish performance on representative or sealed layovers.

First preserve inherited heat eligibility through prefix/restow choices and
remove the permissive heat fallback; retain exact failed-checker geometry
diagnostics. Then add joint cargo guidance/exchanges for actual righting
area, loading and visibility before buying more identical local LP calls.
Re-measure on another predeclared cohort; a later experiment can advance
fully passing empty-ship voyages to obtain certified arrival snapshots.
No primary algorithm or solver setting was changed during measurement.

Entry point: `run.py` (frozen configuration `PROFILE.json`, unchanged full
checker through `runner.py`). Main changed code is `cohort.py`, `method.py`,
`pages.py`, `page_base.py`, `work.py`, `finishing.py`, `ballast_local.py`,
`ballast_physics.py`, `ballast_loading.py`, `runner.py` and `report.py`, with
four test modules, local frozen `engine_copy/`, cohort files and result/docs.
All writes remain under this C4 folder. No shared engine or `src/` edit was made.
