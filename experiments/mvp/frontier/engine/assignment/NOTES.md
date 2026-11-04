# Cycle 6 cold medium assignment integration

This replaces the earlier Cycle 5 assignment lane with the frozen sources from
`lab/c6/c5-securing/`. All seven upstream `FROZEN-SOURCES.txt` hashes matched
before copying. The manifest here records the upstream files, not hashes of the
adapted engine copies. `SOURCE-PROVENANCE.json` records both upstream provenance
and the final adapted source identities.

`capacity.py` was verified only for provenance. It is deliberately not copied,
imported or used: no C6 aggregate-capacity filter, bound or GM ceiling enters
this lane. Existing engine bounds outside this directory are untouched.

## API and defaults

`method.iter_candidates(instance, *, seed=0, budget_s=None, context=None)` remains
compatible with the engine. A missing budget resolves to
`config.assignment_budget_s`, default 300 seconds. Local finishing uses
`config.assignment_finalize_s`, default 180 seconds, and
`config.assignment_tries`, default 2. Global `finalize_s=600` and `tries=3` in
context do not replace these assignment settings. The public `finish` adapter
also caps an explicit finishing budget by the assignment setting.

Explicit tiny/small metadata disables the lane before numerical imports,
regardless of load count. Explicit medium/large/full enables it; missing or
unknown size falls back to more than 60 loads. An existing onboard stow stays
with the separate arrival route. `config.assignment_lp=False` disables the
lane, and `config.e9_fix=False` prevents this module from reinstalling the E9
patch.

The cold proposal now uses six rounds, the donor's 7.5%-of-beam initial GM,
80 sampled pairs per box above 300 boxes, rolling dive, heavy-deck cost,
`fill_hold`, OOG-aware exchange and the large assignment sweep. It retains the
C6 integer-tail fix that keeps tonne-metre moment slack from rounding to zero.
`shaping.py` starts from this plan's own inherited tanks, drains double bottoms
in eighths, levels with the heel pair and chooses a securing/draught/GM-guided
ballast proposal. GM targets and floors are search guidance, never certified
physical bounds.

Each complete native replay is published before optional physics. A shaped
plan is also published with its exact ballast. All candidates remain available;
there is no donor top-k truncation of the replay stream. Distinct ordered cargo
and exact ballast combinations are tracked separately, so a resumed cargo
assignment can be checked again after its ballast changes. Partial dive subsets
remain raw, unreplayed and unchecked in `assignment_partial_assignments`; they
are not counted as completed or safe partial plans.

After proposal, at most two selected failed candidates enter the C6 local
ballast finisher, with one slot reserved for an already held passing proposal.
Candidates no cheaper than a held pass need no further finish. The copied
`ballast_opt.py` searches the donor's wider tank groups using the copied exact
native-physics evaluator. It runs in memory by default, with 30 physics
evaluations and four iterations. It does not read or write recipes. Optional
explicit debug output must remain inside engine/.

Every acceptance uses the unchanged full checker. Direct and local-finisher
passes enter `checked_passes`, `assignment_reports` and `_candidate_plans`
with their exact ballast before callbacks or later telemetry. The native local
optimizer has an immediate checked-pass callback before report serialization.
Caller control exceptions propagate by identity; later ordinary errors leave
held passes intact. The public finish adapter can return an already checked
pass after a later optimizer error. Physics-only feasibility, restricted-LP
failure and a search timeout never establish map feasibility or infeasibility.
No checker rule, tier, native V4 fallback or polisher is changed here.

## Provenance

Every copied Python file begins with its source location.

| Files | Upstream source and adaptations |
|---|---|
| `method.py` | C6 `c5-securing/method.py`; medium guard, streaming retention, exact-ballast keys, caller-control propagation, assignment-specific limits, local finish integration, no capacity import or ceiling. |
| `shaping.py` | C6 method's `level` and `shape` functions; engine-local imports. |
| `gap.py` | Frozen C6 assignment LP, corrected integer tail, sweep, E6 fill and OOG exchange; local rows import and deepest-partial retention. |
| `stow.py` | Frozen C6 conversion and rolling dive; removed lab/sys.path setup and made imports local. |
| `rows.py`, `chainlab.py` | Frozen C6 securing rows and exact-chain diagnostics. |
| `ballast_opt.py` | Frozen C6 local ballast LP; local physics import, optional in-memory output, no saved-plan CLI, immediate native-check callbacks, one-thread LP, JSON-safe unknown merit. |
| `physics.py` | C4 `c2-layout/physics.py`; native exact separation helper, copied locally. |
| `pages*.py`, `repair.py` | Existing engine-local C4 dependencies retained; the reachable base HiGHS call remains pinned to one thread. |
| `test_gap.py`, `test_rows.py` | C6 tests with package imports, plus the tonne-metre slack regression. |
| `test_sight.py`, `test_moments.py`, `test_balance.py`, `test_highs_threads.py` | Earlier copied C4 tests retained. |
| `routing.py`, `test_method.py`, `__init__.py` | Engine routing and integration tests. |

## Fixes and verification

The integration preserves the donor's soft-moment rounding fix. It also avoids
losing a complete cargo replay when shaping fails, avoids losing a native pass
after a later finisher error, updates cached sight margin only after model
construction succeeds, honors the engine E9 ablation, and permits checking the
same cargo again with different exact ballast. No runtime lab import or saved
recipe read remains. All reachable HiGHS solves explicitly use one numerical
thread; CP-SAT uses one worker.

Assignment-only pytest passed 60 tests after correcting unconditional donor E9
installation. This includes routing identity, streaming before diagnostics,
held exact ballast after finisher errors, caller-control propagation, C6
300/180/2 defaults, no capacity/lab imports, and a real one-worker integer-tail
regression whose soft tonne-metre target requires nonzero slack. One subsequent
pure public-finisher recovery regression is included for the root's integrated
suite. No quick benchmark was run or restarted for this replacement.

The sandbox rejected the requested `nice -n 5` with `setpriority: Operation not
permitted`; tests nevertheless completed with all numerical-thread environment
variables set to one, without escalation. Root owns the full integrated suite.

## Limits

Proposal and native-physics deadlines are cooperative. The donor rolling dive
and integer tail may overrun a sub-deadline, and a native checker call may finish
after the local budget. Fixed-motion rows, nominal heights, sampled pairs,
rank restrictions and ballast derivatives are search restrictions or guidance.
The donor reports narrow securing margins and load-dependent wall-bounded
results. Its reported 36/48 TRAIN passes are upstream measurements, not an
engine integration benchmark. No new pass-rate, lift-parity, reproducibility,
capacity proof or optimality claim is made here.
