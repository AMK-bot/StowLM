# Frontier Engine 0.10.0

This folder combines cycle 1's kept algorithms into a cold-start planner. The
unchanged public checker is the only pass authority. A replay-legal complete
stow is recorded separately from a full pass. Source, parameters, frozen
Frontier and lab files are imported, never edited. No sealed inputs are used.

## Pipeline and budgets

The frozen `Config` has an enabled-by-default switch for E9 lookup correction,
bounds, starts, ALNS, fusion, conservative classes, and finishing. Each stage
records diagnostics and contributions. `budget_s` buys extra work: on maps up
to 60 boxes the constructor budgets and search floor remain present even if
that extra budget is zero. The historical `runs/val-0.9` rows use
`min(120, max(20, 6*n_loads))` seconds of search and 300 seconds of V4 finishing.
The engine retains that search budget and at least 300 seconds of native V4,
after the additive direct/inherited finishing lanes. Explicit switches waive
the corresponding floor for ablations. CP-SAT uses one worker rather than the
historical two, to obey the process/thread cap; time-bounded trajectories may
therefore differ. Finishing and indivisible checker calls are separate from
construction/search budgets.

1. Install the process-local E9 mask lookup wrapper. The original parameter
   function is restored on normal and exceptional return from every batch.
2. Compute the hatch-packing map bound and mandatory-inventory relocation bound.
   Fixed-stow relocation relaxations are diagnostic only. Only a full pass
   equalling the map-wide weighted bound is `proved_optimal` and stops work.
3. Copy the frozen constructor portfolio: two-step, port-blocked, yard-first,
   master. Keep checked fallbacks. Add the scout page/column-generation ladder.
   Above 60 boxes pages run first with 80% of the extra budget; if none pass and
   at least 300 seconds remain, try the frozen large planner with one CP worker.
4. Run unchanged ALNS with its original two starts and 2/3–1/3 split. Additional
   starts receive extra search time. Keep distinct stows with real replay costs.
5. Run short sampled cyclic exchanges on the guarded fast scorer, then lazy
   exact component fusion over the good archive and distinct constructor starts.
   Every returned candidate is replayed by `Problem.score`.
6. Run only attribute-identical exchange proposals, on the best current stow,
   followed by real replay.
7. Try candidates cheapest first until a pass exists, then continue with any
   cheaper candidates. Necessary securing contradiction, default-tank full check,
   inherited-tank lane, native V4, and unchanged full gate. Failures do not impose
   a fixed try count that could conceal the next viable candidate.

## Provenance

Every copied Python file begins with its original path. `portfolio.py` copies
frozen Frontier `planner.py`'s complete small portfolio body and imports its
helpers. `frozen.py` loads that original planner under a unique module name.
`scout/{pages,lash_oracle,method,lash_test}.py` comes from fx-scout. `vlsn/` keeps
fx-vlsn's search, fast scorer, eager component helpers, lazy fusion and tests.
`classes/` keeps fx-fvs's assignment/order machinery and tests with conservative
exchange selected. `bounds/hatch.py` and its tests come from fx-exact's bound;
`bounds/relocation.py` and its tests come from fx-fvs's exact relaxation.
`finishing/` copies fx-finish's early bound, inherited finisher and dependencies;
optional shaped/rectangular acceleration keeps the source's default settings.
`e9fix.py` copies the leader's wrapper. `lab_method.py` adapts the frozen-reference
result-reuse pattern. New orchestration and routing/budget tests live alongside
these files. No downloaded or external code is used.

## Bugs fixed during integration

- Unqualified `planner`, `search`, `method` and sibling imports could resolve a
  different package after the lab loader prepended the engine directory. All
  copied package dependencies are relative and the frozen planner is explicit.
- The inherited finisher's worker import by file stem broke spawned package
  workers. It now imports its stable full package name and restores dispatch.
- A default-tank passing plan has `ballast=None`; the copied start collector now
  uses an empty tank override rather than dereferencing it.
- The historical scout lashing test executed CLI arguments at import. Its CLI
  is guarded so pytest can collect it without opening arbitrary input paths.
- Adding page starts before baseline ALNS could displace an original search
  start. The full original split now runs first; extra-start search is additive.
- Truncating the good archive could omit distinct constructor parents. Fusion
  explicitly includes those starts as well.
- The first black-box scorecard launch exposed the finishing callback's keyword
  label collision. The callback now matches the frozen signature, with a direct
  regression. That launch is debug evidence, excluded from the final comparison.
- Approximate floating equality could falsely certify optimality for arbitrarily
  small fractional weights. Certificates now require exact equality.
- Legacy freeze-OFF DG unary tables can exclude supported upper-tier placements
  during empty-ship geometry probes. These maps now use the native counting
  bound; the same conservative guard applies to caller-supplied tables.
- Optional heuristic/accelerated-finisher errors now retain checked incumbents
  and the unchanged native finishing lane. Error details stay in the run record.
- A 30-second remainder cannot accommodate the frozen large constructor's
  240-second two-step lane. Large fallback now requires at least 300 seconds and
  allocates the remainder across four column attempts and search.

## OOG regression

The corrected E9 batch is compared against `placement_violations` on all
supported, unoccupied, length-compatible horizontal-OOG placements, both empty
and after three accepted ordinary loads. Inputs are `sim.small.40100034`, the
quick V6 TRAIN feeder_800 f60-low map and the quick D185 feeder_500 map. All three
have width or length OOG. No disagreement was found. The test isolates E9 rather
than counting other rules' mask rejections as E9 mismatches. The parameter and
public checker identities/source are checked outside normal and exceptional
batch calls.

## Benchmark tooling

`bench/MANIFEST.json` records outcome-independent selection rules and hashes.
There are 45 non-quick validation small maps with stored rows, 40 non-quick tiny
maps, and 8 medium maps: quick medium, remaining certified V6 TRAIN maps (up to
five additional), and each ship class's smallest D185 case, seed breaking ties.
All paths pass the shared allowlist. `bench/run.py` launches at most two scorer
processes with two workers each; numerical threads are one. `bench/compare.py`
keeps sizes apart and unknown baseline evidence null, reports paired lifts,
bootstrap intervals, wins/ties/losses, wall times and lift parts. It can compare
medium rows to scout's quick-3 evidence. No full benchmark is run here.

## Deliberately omitted and limits

Mass-swapping class paths are not enabled: copied generic assignment helpers
retain their unused broader option, but defaults and the engine entry point use
only exact attribute classes. No exact restricted-repair search, whole-map
separator/treewidth solver, global cut constructor, chain-cover constructor or
order-only repair is enabled: cycle 1 demonstrated no independent improvement for
these beyond the kept conservative exchange/bounds. Optional shaped numerical
kernels remain off. Sampled cycles are a diversity source, not an exact VLSN
solver. Fusion certificates cover a restricted parent family and canonical
order, never global optimality. Page fitted securing/packing coefficients are
heuristics, not infeasibility certificates. Bounds relax restows and may be loose.
Medium fallback reserves the frozen 240-second two-step lane before allocating four column attempts and search. Its remaining construction budget is advisory: its atomic frozen
calls can overrun, and finishing has an independent budget. Shared-machine
wall time is descriptive.

## Verification and quick run

The final required complete suite passes **99 tests** in 22.94 seconds with
`PYTHONPATH` unset; see `test-results.log`. Engine-local `conftest.py` makes
the plain requested pytest command import frozen helpers without external setup. This includes copied tests, fractional proof-gap and
callback-signature regressions, three-map empty/partial OOG comparisons, full
checker restoration, default-tank fallback and native finishing-budget routing.
The corrected full quick run is complete; results follow below. The mandatory fixed 21-map quick run
is allowed to exceed 20 minutes: it preserves the complete historical portfolio
and search budgets, adds the retained methods, and performs unchanged full
checking including the scorer's independent check. No map is removed for speed.
All long runs request nice 5 and single numerical threads; no unrelated process
is stopped. No cloud or GPU is used.

Before invoking the lab runner directly after any Python change, run
`PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
.venv/bin/python -m engine.provenance` (as one shell command). The unchanged
scorecard hashes its plug-in file, so this stamps helper-source changes into the
adapter's comment and invalidates stale caches. `bench/run.py` does this
before launching shards. `source-manifest.json` records the measured engine
files, and `native-source-manifest.json` binds 257 native/parameter/frozen files
for post-run comparison. All 51 measured Python files stayed unchanged throughout the corrected run;
`measured-source-audit.json` records this, and all 21 map records have the same
engine source digest. The final manifest is described below.

The top-level `replay_legal_stows` is the number of retained archive stows.
Page-ladder records also show complete replay-legal stows filtered out by
full-check rule failures; the construction gate in the quick report includes
those ladder records, even when no candidate is returned for finishing.


## Final quick results

Run: fixed `quick`, seed 0, two workers, one numerical thread each,
`--budget-s 375 --finalize-s 600 --tries 3`, explicit engine propose/finish plug-ins.
Outputs: `results/quick-0.10.0/`. Actual elapsed **2262.58 s (37.71 min)**;
sum of map times **4489.47 s**. The sandbox refused nice's priority change;
the command continued at inherited priority. Timings describe this shared machine.

| Size | Complete replay-legal maps | Full passes | Stored 0.9.0 passes | Paired lifts: engine / 0.9.0 | Mean wall s/map |
| --- | ---: | ---: | ---: | --- | ---: |
| Tiny | 8/8 | 8/8 | 8/8 | 6.375 / 6.375, 8 pairs | 90.28 |
| Small | 10/10 | **10/10** | 9/10 | **11.6667 / 13.8889**, 9 pairs | 289.60 |
| Medium | 3/3 | 2/3 | unknown, 0 measured | no pairs | 290.43 |

Small paired mean change is **-2.2222 lifts (-16.0%)**, bootstrap 95% interval
**[-6.0000, -0.1111]**, **3 wins / 6 ties / 0 losses**. No passing baseline map
is lost. The new pass, `sim.small.40100034`, has **10 lifts** (8 hatch + 2 later
restow, no relocations); it is excluded from the paired mean because its stored
row failed. Mean over all ten engine small passes is 11.5; this is not compared
with a different-denominator baseline mean.

Paired small savings, in raw lift parts: **2 relocations + 2 current hatch lifts
+ 16 later restows = 20 lifts** across nine pairs. Later hatch and current restow
counts do not change. Specific improvements:

- `random.small.40100036.0.2`: **11 -> 9**, two fewer current hatch lifts.
- `sim.small.40100025`: **26 -> 9**, one fewer relocation and all 16 later restows removed.
- `sim.small.41100007`: **9 -> 8**, one fewer relocation.

Medium against **fx-scout quick-3**, separately from unknown 0.9.0 evidence:
**2/3 vs 2/3 passes**, **131.5 / 131.5** paired mean; both passing maps tie.
Feeder_500 f60-high has **79** lifts and feeder_800 f60-low **184**. The D185 map
reaches a complete replay-legal stow inside pages but passes no full checker;
its result is `no_candidates`, not a proof of infeasibility. Engine mean wall is
290.43 s/map versus scout's 275.06; no speedup is claimed.

Machine-readable comparisons (10,000 map bootstrap draws, seed 0) and raw parts
are in `results/quick-comparison.json`; detailed stage/ladder records are in
`results/quick-stage-report.json` and every per-map row. The top-level archive
count intentionally omits filtered page stows, so the replay-legal map gate above
also reads page-ladder replay evidence.

## What stages contributed on quick

- **E9 fix:** installed for every map; differential empty/partial regression on
  three horizontal-OOG maps has no disagreements. The formerly failing small
  map now passes. No isolated E9 ablation was run, so this is not a causal lift
  estimate for E9 alone.
- **Bounds:** five exact full-objective equalities stop work: tiny
  `sim.tiny.20000007` (4), `sim.tiny.20000023` (4),
  `random.tiny.20000042.0.5` (6); small `random.small.40100011.0.8` (22) and
  `random.small.41100018.0.2` (10). Other gaps remain diagnostics. Conditional
  relocation bounds never promote a global proof. Legacy DG guard weakens some
  bounds deliberately.
- **Starts:** retain all original families/budgets on small/tiny. Yard-first
  supplies a 10-lift stow on `sim.small.40100034`; pages supplies both passing
  medium stows. Pages also constructs the rejected D185 stow. Original/fresh
  starts reach 14 lifts on the 26-lift stored map before improvement stages.
- **ALNS:** improves fresh best starts 11 -> 9 on `random.small.40100036.0.2`,
  14 -> 13 on `sim.small.40100025`, 14 -> 8 on `sim.small.41100007`, and
  10 -> 9 on `sim.small.40100046`. These are within-run candidate traces, not
  isolated same-start ablations against historical 0.9.0.
- **Cycles/fusion:** cycles improves the 13-lift best on `sim.small.40100025`
  to **9** through three improvements. Lazy fusion adds no strict lift gain in
  this run; some calls time out before an admission. Every returned plan gets
  real replay. The stage remains independently switchable for later ablations.
- **Classes:** exact-attribute exchange adds no strict best improvement after
  these searches. Its entries retain the parent; mass exchange is disabled.
- **Finish:** 15 direct successful lane calls on tiny, 25 inherited successful
  calls on small, two direct calls on medium. A necessary securing proof rejects
  one fixed stow on `sim.small.40100025`; another stow passes. The ordinary native
  fallback lane adds no successful call in this quick observation. The full
  checker inside admission and the independent outer runner both gate passes.
  No optional-stage or accelerated-finisher exceptions occur in final rows.

These contributions overlap; only explicit within-stage improvements are called
savings. One seed on development validation/TRAIN data does not establish
held-out performance, and bootstrap intervals describe only these map pairs.
The leader alone may open sealed test inputs.

## Final review and source binding

Measured engine digest, identical on all 21 rows:
`d2a238c74cafd3179d522b5fa38c241e3fca705b90721ba44d8e76a31a286225`.
`measured-source-manifest.json` retains those 51 Python files' hashes.
After measurement, two changes were made: the pytest-only import bootstrap,
and correct routing of the **disabled** `finish=False` ablation through frozen
`plan_big`'s internal finishing calls, with a new regression. Default-enabled
planner algorithms did not change. The adapter fingerprint is refreshed for
these final files; `source-manifest.json` binds the final reviewed package.
The 99-test final suite runs against this final code. CLI help, benchmark dry-run,
plain collection and bytecode-free compilation are also verified.

The original callback-failed launch is retained under
`results/integration-failed-callback/` and `integration-failed-callback.log`;
none of its rows is part of the final comparisons. No full benchmark, commit,
cloud work or GPU work was performed. Every write stays inside `engine/`.
