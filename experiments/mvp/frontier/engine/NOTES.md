# Frontier Engine 0.13.0

Final compilation: the leader ended measurement early and requested exactly
three final additions. No quick was run after that stop. Only engine/ was edited. No checker/src/parameter changes, commits, cloud or GPU.
Sealed maps, the entire valsplit tree and lab/c6/c6-maps remain closed.
[0.12 notes](NOTES-0.12.0.md) preserve the historical run; the corrections below
supersede its wording. Engine NAME stays frontier.engine; VERSION is 0.13.0.

## Pipeline and objective

L0 means a pass through the unchanged full checker, with nothing peeled.
Tiny/small routing keeps the Frontier portfolio with its original budget floor;
the assignment LP is explicitly inactive there. Review fixes, a C4 target
constructor and stronger proof work are additive. The identity regression toggles
the LP switch on tiny/small rows and checks identical candidates, winner, lifts
and proof outcome, with no LP/model construction.

Before any solver search, an exhaustive native unary certificate may identify
an unpeeled booking that no supported cell can satisfy. The supported tiny
case random.tiny.20000040.0.2 has 192/192 supported departures violating
expert.e8.away_from_heat for QZXU0000035. This is an L0/T3-booking certificate,
not physical map infeasibility; it never blocks later peeling of that booking.
Unsupported/partial enumeration is Unknown and search continues.

Above small size, empty-arrival maps run two cold lanes in order: assignment
the frozen C6 assignment LP first (300 s proposal, 180 s finishing per try,
two tries), then the page portfolio. A held native-checked pass survives every
later optional exception. Pages still run; the cheapest native-checked pass
wins and winner_lane is recorded. LP size routing is explicit in
assignment/routing.py: tiny/small metadata always disables it; absent metadata
falls back to more than 60 loads. Arrival-prefix maps keep their separate route.
The LP's continuous moment/securing rows guide search; only native checking
admits a result. This invocation's LP pass may seed C4 polishing after the
unchanged independent page portfolio, never shorten that portfolio's work.

The page lane retains fixed-work pure/mixed/closed-cover families. Uniform
preferred four-tier decks and a capacity-derived minimum replace the >120 box
threshold. A three-tier family is also tried whenever capacity fits, independent
of box count. Closed-hold capacity is subtracted before screening. These are
necessary screens of specified constructor families, never map rejection.
C4 adds moment pricing, soft moment recovery, fixed-charge LNS and exact
failed-plan/native-report diagnostics after the earlier families.

Candidates publish complete ordered actions immediately, preserving the exact
checked ballast recipe. Arrival has no five-candidate truncation. Finishing
uses full checking, exact static/fixed-stow exclusions, local ballast guidance,
cold real-area search, inherited V4 and unchanged native V4. Native static hard
violations (including hard E6) and a cost below the sound map bound skip tank
work for that fixed stow, while its cargo/actions remain in the archive for
other search and tier inspection. A direct full pass always wins over an
inconsistent diagnostic. Soft/tolerated violations never trigger this skip.

Failed certified witnesses are first tried by a cheap full check, then queued
until ordinary constructors have a chance to hold a pass. They are never
dropped. If no pass exists, every still-viable candidate retains its full
finishing opportunity. A checked pass also permits C4 projected-restow exchange
within the same invocation; a new full checker pass is mandatory for improvement.
No historical assignment, ballast recipe or saved plan is a runtime start.
The frozen narrow C6 lift polisher then runs after a medium full pass, with
a 30-second search allowance. A fresh full checker pass and strictly fewer
lifts are required; any error retains the exact held plan and ballast.

Arrival plans are supported by the public engine planner and CLI. The frozen
lab scorecard accepts only Place candidates, so the shared lab adapter is not
a general arrival-prefix interface: a plan needing explicit arrival restows
will be rejected there. Quick has no arrival cargo. The uncapped-arrival and
archive regressions exercise the public planner; they do not establish arrival
coverage through that scorecard. Use the planner API for these instances.

## Proof work and scope

The map bound and C4 target constructor use CP-SAT deterministic work, one
worker. Default map proof work is 10 deterministic seconds shared across
legacy (25%) and exact (75%) solves; 900 seconds is a generous outer safety
stop, not the ordinary work schedule. The ten-unit limit is per bound request,
including each fresh-incumbent cutoff request; it is not a total across all
incumbents of a map. The targeted constructor gets two work units. Every improved *fresh fully checked* small-map incumbent supplies
its upper bound to the integer-cutoff path; no saved upper is used. A restricted
constructor failure never becomes a global lower bound.

Each number names its actual contributing family. Equal older bounds keep
their older producer. Exact records distinguish independent plain packing
from joint first-aboard/necessary-cut strength. Equality is strict, after the
full checker. Scope: **fewest weighted lifts among checker-passing L0 plans
under the current rule set, rule freeze off in this run, including provisional
expert rules; later ports counted by D15 at a fixed stow with no later loads**.
This includes provisional E6, default zero-metre E9 clearance and synthetic
securing gear. L0 bounds/proofs do not apply after relevant tiers are peeled.

Different bound families have different strength. The old 88-box 65 is
`two_port_hall`; the 162-box 90 is `aggregate`, weakened by lateral OOG disabling
E6/Hall witnesses. Their gaps must not be averaged or ranked as like quantities.

## Frozen tier table and no-pass ladder

`tiers.toml` is byte-identical to lab/c5/leader/tiers.toml, SHA256
`a1ec216cb996a43decc6e902cee3aea8778fe1c998b4eae2e2dbc85a16e65cc9`.
Unknown rule IDs count as T0; T0 is never peeled. The completion summary code
retains the leader's D241/D242 definitions.

`peel.no_pass(instance, everything_found, ...)` is called only when no full
pass is held. On the existing medium route it invokes the frozen C5 assignment
ladder. Its eight assignment files and required local dependencies keep their
own measured LP, separate from the new C6 normal lane. Original hashes were
verified against assignment-frozen-hashes.json before provenance headers were
added. The package records both donor and copied hashes.

The ladder receives this invocation's archive and finishing history and does
not rerun normal L0 planning or read stored plans. Defaults are 240 s proposal,
120 s finishing per relaxed level and two finishing candidates. All reported
breaches come from the native full checker. Relaxed outcomes retain completed,
best_safe_partial, rules_broken and level, with passed=false; they never add
proofs or replace a held full pass. The relaxed plan is stored separately from
the adapter's L0 replay fallback. T0 violations cannot qualify as completion.
A safe partial reports boxes left ashore and percentage aboard separately.
L4 witnesses require human review; L5/T0-only witnesses are not safe to sail.

Tiny/small routing is unchanged: no assignment LP, lift polish or new ladder.
The old existing-witness classifier remains available there and when the new
ladder switch is off. `assignment_lp`, `lift_polish`, `peel_ladder` are separate
stage switches; the existing `peel_seam` also disables the whole no-pass hook.

## Review dispositions

| Finding | Change / regression |
|---|---|
| Code P1 legacy pass lost after exception | Immediate caller-owned publication in legacy/public page routes and root recovery; public fault-injection regression keeps first checked pass after later factory/LP failure. Cleanup errors cannot erase it. |
| Code P1 arrival top five | All distinct complete orders returned; incremental publication and exception recovery. Public regression makes only candidate six finish. |
| Code P2 HiGHS threads | Explicit threads=1 for legacy pages, mixed pages and arrival master; option/AST tests. SciPy warns that the option is forwarded verbatim to HiGHS. |
| F2 proof cutoff/work | Fresh checked incumbent cutoffs, cumulative deterministic work and explicit outer-stop metadata; no 22.5-second proof-budget claim. |
| F6 arbitrary deck cap | Removed size threshold; capacity floor and size-independent three/four-tier families; 193-demand/168-capacity regression. |
| F7 closed-cover capacity | Closed hold cells excluded; all-closed regression. Earlier 15 D185 rejections shared an insufficient three-tier cap and did not establish a closure effect. |
| F8/F9 wasted finishing | Native static and bound exclusions skip hopeless fixed-stow tank work; soft/Unknown cases remain eligible. Unchecked certified witnesses deferred, never dropped; no first-pass budget cutoff introduced. |
| F11 big fallback | Original plan_big remains explicitly wall-limited: up to four 120-second column attempts (480 seconds), admission depending on remaining wall time. Native V4/small ALNS remain wall-limited too. |
| F14 contradictory no_candidates | Adapter returns a replay-complete failed candidate when held, allowing the unchanged scorecard to report failed/full-check breaches rather than no_candidates. No replay candidate still means no_candidates. |

F1: 0.12's 18 tight tiny/small bounds included 14 proofs already in 0.11;
17 were supported by plain packing. First-aboard plus E6 mattered on one map,
40100046. Model contents and cut counts are not a causal proof count.
F3: on the same 162-box map, 0.11 reached 112–116, versus 0.12's 104:
8–12 lifts, one seed each. The 88-box result stayed 71. These two TRAIN maps
were preselected as passable by V6, a two-step feasibility constructor, not a
lift benchmark. Medium future-port work is a D15 projection, not measured
future terminal operations. Some new reductions include current hatch work;
part tables keep current and projected costs separate.
F4: 0.12's own small improvement was one lift on one map (9 to 8 versus
0.11); 16 of the 17-lift difference from the fresh 25 were already in 0.10.
No tiny-sample confidence interval or percentage gain is quoted.
F5: the 15-of-18 eligible certified-start winners (eight tiny) reflected first
queue position and early proof stopping. They do not establish stage skill or
causal improvement; no such ablation was run.
F10: three same-seed 71/104 runs on two maps at similar load showed repeatability
there, not heavy-load independence or seed robustness. The old stress test was
unqualified because ps was denied; no stronger claim is carried forward.
F12/F13: scoped proofs and separate named gaps above. F15: historical 0.9 big
arms exist (129 on 88 boxes, no pass on 162), with E9 and unequal-load caveats;
they are distinct from the fresh full-quick arm below. Relocations are not
assumed forced, E9-only extra passes are bug fixes, and all data here are
public development data, not sealed/unseen evidence.

Additional integration fixes: the LP now honours the process-local E9 ablation
flag rather than always installing the patch; its regression forbids install
when the switch is off. The tier inspector reads native CheckReport.parts as
a dictionary of part reports, retaining exact measurements and errors. The
source bundle now includes tiers.toml and approved-maps.json as runtime inputs,
so changing either invalidates the adapter fingerprint and measured identity.

## Provenance and ablations

| Engine source | Copied findings / switches |
|---|---|
| assignment/{method,gap,stow,rows,chainlab,ballast_opt}.py | Frozen C6 c5-securing sources verified against FROZEN-SOURCES.txt, adapted locally. `assignment_lp`; proposal 300 s, finish 180 s, tries 2. Donor capacity.py is excluded as both filter and planner bound. |
| assignment/pages*.py and local helpers | Needed C4 geometric/sight/balance/pricing dependencies copied locally; no lab imports, stored recipes or donor result writers. |
| bounds/exact/*.py | C4 c2-hatch certified/target constructors; existing DG/oracle guards retained. `certified_bound`, `proof_cutoff`, `targeted_constructor`, work limits. |
| scout/moment_pages.py, moment_lns.py, dense_master.py, dense.py, highs.py | C4 c2-layout moment pricing, fixed-charge LP/IP/LNS, thread config and diagnostics. `moment_pricing`, `dense_layout`, `dense_fixed_charge_lns`, `layout_diagnostics`, `capacity_deck_cap`. |
| benders/permute.py, post_pass.py | C4 c2-benders projected-restow objective and checked-plan exchange, merged with engine safety/None guards. `post_pass_exchange`; 60s extra, current-run incumbent only. |
| finishing/chain.py | Retained source-pinned securing certificates used by C4; added native static and map-bound tank-work skips. `fixed_stow_screen`, `candidate_triage`. |
| unary.py | Local exhaustive native supported-stow E8 certificate; `unary_certificate`. |
| lifts/{polish,checked}.py | Frozen C6 narrow polisher plus incumbent-preserving adapter; `lift_polish`, 30 s. No cover LNS. |
| peel_ladder/, peel.py | Eight frozen C5 assignment ladder files plus their own measured local LP dependencies; `peel_ladder`, 240/120 s and two candidates. `peel_seam` retains the outer hook. |
| tiers.toml, completion.py | Unchanged leader rule table and copied D241/D242 summary. |
| safe_catalog.py, approved-maps.json, bench/safe_score.py | Frozen positive public provenance from lab manifest and stored validation rows; avoids forbidden valsplit reads while running unchanged lab/score.py. |

Each copied Python file/test has a provenance comment; the tier TOML is byte-identical, with provenance recorded here. Earlier cycle features/tests
remain; detailed notes in assignment/NOTES.md, scout/NOTES.md and
bounds/NOTES-0.13.md. C4 warm disk references are omitted: cold engine never
reads them. The unmeasured cold C4 Benders profiles, stopped feasibility-proof
line and discrepancy rounding remain out. The older pending peel/lifts/securing
seams are superseded by the three final additions
above. The following remain unfinished and are not included: lab/c6/c6-xl,
lab/c6/c3-layover, lab/c6/c3-multiport and cover_lns.py. They were explicitly
excluded from this final compilation; no XL, new arrival/multiport or wide cover
search is claimed. No frozen external source is imported as a mutable solver module.

## Compute and pre-final measurement protocol

At most two CPU-busy processes, one when BENCH-RUNNING exists, with one
numerical thread. nice 5 is requested; macOS denies setpriority in this sandbox,
so effective lowered priority is not claimed. No other session's process is
inspected or managed. The pre-final run attempted the fixed 21-map quick and
fresh arm at the requested budgets; the leader stopped it after 19 engine rows.
No quick was restarted and no full benchmark was run.

`bench/quick013.py` was the stopped launcher for the engine and fresh 0.9+E9 scorecard arms, two
workers each, sharing a two-slot file-lock admission gate. Workers waiting for
a token do no numerical search. Queue time is outside each map's solver budget.
Engine arguments: budget 900, finalize 600, tries 3, seed 0, workers 2. The fresh
method_e9_val policy restores original val-0.9 search and 300s V4 internally.
Both arms used the same 21-map input list; the engine completed 19 rows. Budget 900 is the search allowance, not
a total invocation cap; finalize 600 is the native V4 allowance per attempted
stow. Inherited, area and local-LP lanes add work before V4. Several distinct
cheaper stows can therefore make a map take much longer than 1,500 seconds.
The gate records overlap/PIDs/intervals;
load averages are proxies only. This is a shared-period two-active-worker
control, **not identical per-map load or equal effort**, and no causal
percentage follows. A newly appearing marker blocks new second-slot work;
existing atomic native calls must drain rather than be killed.

The unchanged scorecard still independently full-checks returned plans. An
engine-local wrapper adds admission/reporting and replaces only input-list
authorization with frozen positive provenance; no checker or solver is patched.
One legacy lab selection test is skipped because it directly opens a now
forbidden valsplit list; public manifest/hash, sealed rejection and all solver
regressions remain active. No forbidden list is reconstructed or read to make
that test pass.

## Verification and frozen benchmark source identity

The frozen benchmark tree's complete engine+lab suite passed **656 tests**, with **one forbidden-list
skip**, zero failures and zero errors, in **128.446 seconds**. Command from the
repository root (no uv):

```
.venv/bin/python -m pytest experiments/mvp/frontier/engine experiments/mvp/frontier/lab/tests -q
```

The run additionally directed cache, temporary files and JUnit XML under engine/,
with one numerical thread and nice 5 requested. See test-final-0.13.0.log and
results/final-tests-0.13.0.xml. The skipped legacy lab selection test directly
opens a now forbidden valsplit list; public selection/hash and solver tests run.
New coverage includes 61 assignment tests, 15 polish tests, 73 ladder tests and 13
final integration cases: held passes survive failing polish, strict full-check
lift improvements only, ladder suppression after a pass, relaxed passed=false,
tiny/small identity, native mask/parameter restoration and source identity.

Final source identity: **243 files** (239 Python files plus the tier table,
approved-map list and two ladder runtime manifests), normalized SHA256:

```
a3e52c7c621b59d004d26006f2b5db48dfa963f80c6f35d3f3480702cefca852
```

source-manifest-0.13.0-final.json records every raw file hash, environment,
provenance checks and test totals. The normalized adapter fingerprint zeroes its
own embedded hash marker. The source did not change during final tests. All 257
pinned native/frozen Python files are unchanged; the tier table is byte-identical;
all 24 frozen peel runtime bodies and both other package provenance manifests
verify. NAME=frontier.engine, VERSION=0.13.0; lab_method.py exposes propose/finish.

**No final quick run:** the leader stopped the earlier run and will benchmark a
frozen copy. No quick or medium repeats were restarted. The earlier 548-test
suite and subsequent targeted checks are historical, superseded by the 656-test
final run above. Source and results from the stopped pre-final run remain
separate from this final source tree.

## Pre-final partial evidence, stopped by leader

The leader stopped controller/process group 7815 after 19 engine rows. Every
preserved row is labelled **pre-final partial, stopped by leader**, with
final_source_comparison_eligible=false. The old source identity remains
`9f33817a89d0e4a252902c4a6af8e1a6bef4ebd9f5e51cbeefdceb01fb61f023`.
Its manifest and controller snapshot are preserved; the final additions are
not measured by those rows. The fresh E9 arm had completed all 21 rows.
The two unfinished engine medium maps are unknown, never failures or zeros.

| Group | Pre-final engine full passes | Fresh E9 full passes | Joint mean lifts engine/fresh | W/T/L |
|---|---:|---:|---:|---:|
| tiny | 8/8 | 8/8 | 6.375/6.375 | 0/8/0 |
| small | 10/10 | 10/10 | 11.4/13.1 | 1/9/0 |
| medium | 1 observed; 2 stopped/unknown | 1/3 | 71/129 on one pair | 1/0/0 |

The small win (40100025:8 vs25) was already in 0.12, not a new final-stage
improvement. The 88-box 71-lift plan is exactly the official 0.12 ordered plan
and assignment, with default ballast in both. Current raw handling on that
pair is identical; the entire 58-point difference is D15 projected hatch/restow
work (fixed stow, no later loads), not measured future terminal operations.
No confidence interval, percentage headline or load-independence claim follows.

### Contribution table

Counts below describe observed activity, not causal ablations. The final three
additions have unit/integration evidence only and no new map benchmark.

| Stage | Proven evidence / limit |
|---|---|
| Existing unary certificate | Native regression:192/192 supported stows for random.tiny.20000040.0.2 violate expert.e8.away_from_heat for QZXU0000035. Quick partial has19 Unknown results; no map rejection. |
| Existing map proofs and live cutoff | Pre-final18 tiny/small strict equalities;15 cutoff uppers matched saved full passes; no proof outer-stop hits.17 plain-packing observations, one genuinely stronger joint+E6 case(40100046). |
| Superseded C5 LP | Pre-final88: one77-lift native pass, assignment-stage19.898 s; exact whole-invocation first-pass timestamp unrecorded. This is not evidence for the final C6 replacement. |
| Existing page route | Pre-final88:27 distinct replay stows,7 distinct native page passes;71-lift winner. |
| C4 dense / moment LNS | Pre-final88:four dense checks,121/125 passes and two E6 failures;two moment-LNS Unknown/no replay. No winning lift gain there. |
| Fixed-stow screen | Pre-final88:three63-lift E6-invalid stows below bound65;14.371 s direct/screen work total, zero ballast-LP/inherited/V4 attempts. Native timeouts elsewhere remain Unknown. |
| Existing post-pass exchange | Pre-final88:one CP solve proposed replay79 versus71; rejected as not cheaper, proposal full-check verdict unrecorded. |
| Final C6 LP |61 package tests plus integration; local frozen source,300/180 s,two tries; no capacity filter or new planner bound. Medium only; no final map measurement. |
| Final C6 lift polish |15 package tests plus integration;30 s narrow stage, strict native full-pass improvement, checked incumbent retained on failure. No final map measurement. |
| Final C5 peel ladder |73 package tests plus integration; medium/no-pass only, own measured frozen LP, native breaches, T0 guard, relaxed passed=false and scoped native-state restoration. No final map measurement. |

Per-map times, parts and bound telemetry from the stopped source remain in
results/paired-quick-0.13.0-draft.md, results/parts-quick-0.13.0-draft.md and
results/audit-quick-0.13.md. The missing engine medium rows are explicitly stopped/unknown.
The stopped OOG small map 40100034 took 2987.12 s versus fresh 179.91 s. Three
cheaper stows exhausted 600 s native V4 each; infeasibility was not proved.
Its held 14-lift pass survived, and a later inherited pass reached bound 10.
The 88-box map took 1096.01 s versus fresh 513.03 s. These times are not final
source measurements and do not isolate a stage or load effect.

### Exact bound names in the preserved partial

| Map | Engine pass/lifts | Fresh pass/lifts | Bound | Producer | Proved | Cutoffs |
|---|---|---|---|---|---|---|
| D192.medium-feeder_500-f60-high-s192200000 | True/71.0 | True/129.0 | 65.0 | two_port_hall | False | 0 |
| random.small.40100011.0.8 | True/22.0 | True/22.0 | 22.0 | hatch_only | True | 1 |
| random.small.40100023.0.8 | True/22.0 | True/22.0 | 22.0 | hatch_restow | True | 1 |
| random.small.40100036.0.2 | True/9.0 | True/9.0 | 9.0 | hatch_restow | True | 2 |
| random.small.41100002.0.2 | True/12.0 | True/12.0 | 12.0 | hatch_restow | True | 1 |
| random.small.41100018.0.2 | True/10.0 | True/10.0 | 10.0 | hatch_only | True | 2 |
| random.tiny.20000006.0.5 | True/8.0 | True/8.0 | 8.0 | hatch_restow | True | 0 |
| random.tiny.20000022.0.2 | True/7.0 | True/7.0 | 7.0 | hatch_restow | True | 0 |
| random.tiny.20000032.0.8 | True/8.0 | True/8.0 | 8.0 | hatch_restow | True | 0 |
| random.tiny.20000042.0.5 | True/6.0 | True/6.0 | 6.0 | hatch_only | True | 0 |
| sim.small.40100010 | True/4.0 | True/4.0 | 4.0 | hatch_restow | True | 2 |
| sim.small.40100025 | True/8.0 | True/25.0 | 8.0 | hatch_only | True | 1 |
| sim.small.40100034 | True/10.0 | True/10.0 | 10.0 | certified_plain_packing | True | 1 |
| sim.small.40100046 | True/9.0 | True/9.0 | 9.0 | certified_hatch_first_aboard_heat_e6_oog_securing | True | 2 |
| sim.small.41100007 | True/8.0 | True/8.0 | 8.0 | hatch_restow | True | 2 |
| sim.tiny.20000007 | True/4.0 | True/4.0 | 4.0 | hatch_only | True | 0 |
| sim.tiny.20000023 | True/4.0 | True/4.0 | 4.0 | hatch_only | True | 0 |
| sim.tiny.20000033 | True/6.0 | True/6.0 | 6.0 | hatch_restow | True | 0 |
| sim.tiny.20000043 | True/8.0 | True/8.0 | 8.0 | certified_plain_packing | True | 0 |


These numbers are source-bound pre-final evidence. Tied older bounds retain
their older producer. The long certified family name on40100046 names the
model; its incremental strengthening was joint final-state plus necessary
lazy/E6 cuts (plain8 to9). The medium bound is two_port_hall65, not a proof
of71. No C6 securing capacity bound is imported, applied or reported.

## Remaining limits

The final three additions have test evidence and frozen upstream provenance,
not a new engine map benchmark. The leader will benchmark a frozen copy.
The scoped legacy imports of the peel ladder are process-local and unsupported
for concurrent threaded calls; use process workers. The adapter restores module
names, SciPy functions, native mask and parameter function identities even after
an import or search exception. Native checker source/functions are unchanged.
Model construction, native checking and wall-limited finishing can exceed a
stage allowance. Tight securing margins and provisional rules/synthetic gear
still need review; a relaxed completion is never an operational full pass.


## fixed after the frozen benchmark tree

Only review findings P1 and P2 were changed after the leader froze its benchmark
copy. Version remains 0.13.0. The frozen source identity above remains
`a3e52c7c621b59d004d26006f2b5db48dfa963f80c6f35d3f3480702cefca852`;
that copy and the stopped 19-row partial run were not changed or restarted.

P1: a plan constructed under relaxed permissions is an ordinary level-0 pass
when the unchanged native full checker passes it. The adapter publishes the exact
plan, ballast and report to caller-owned state immediately, before ranking or
serialization, and stops further peeling. The planner holds that pass immediately
and recovers a raw published pass outside the optional-stage error guard. Callback,
record-conversion and cleanup errors cannot erase it. Such rows have passed=true,
level=0 and no broken rules; genuinely relaxed outcomes still have passed=false.

P2: the dense medium fallback forwards its absolute stage deadline into the
restricted-master LP. Expired solves never start; HiGHS receives threads=1 and
the remaining positive time_limit. Solver timeout is Unknown and yields no fallback
candidate, so independent lanes can continue. The analogous base and moment masters
use the same contract. No-deadline calls retain their original optimal outputs;
live deadlines produce identical outputs when not reached. The separate gap and
ballast LPs were audited as already finite and left unchanged. The frozen ladder's
private dense fallback is not reached by the engine adapter and was not edited.
Model construction and full checking remain atomic overhead, as before.

Changed files and hunks (current line anchors):

| File | Hunk / purpose |
|---|---|
| peel_ladder/__init__.py:42 | Immediate native-pass publication, private unwind, ordinary-pass serialization and error recovery in run; frozen controller bodies unchanged. |
| peel.py:117 | Recover the newly caller-held ordinary pass if the ladder or its cleanup raises. |
| planner.py:645 | Publish/hold callback, caller-owned report and recovery outside additive error handling; skip redundant promotion once held. |
| planner.py:692 | Preserve ladder-ran telemetry for an ordinary pass. |
| planner.py:722 | A held ordinary ladder pass implies complete_replay_legal=true. |
| peel_ladder/test_p1.py:1 | Seven regressions: both L3 review probes, callback/serialization failure, late error recovery, and three public-planner recovery cases. |
| assignment/pages_fast_dense.py:461 | Deadline guards before construction and solving, remaining HiGHS allowance, timeout Unknown; generate:549 forwards deadline. |
| assignment/pages_base.py:446 | Same deadline contract; generate:534 forwards deadline. |
| assignment/pages.py:197 | Same deadline contract for the moment master override. |
| assignment/method.py:65 | Timeout in dense generation/dive returns no fallback candidate. |
| assignment/test_lp_deadline.py:1 | Expired review probe, all three master deadlines, construction expiry, unchanged optimal results, timeout incumbent rejection and no fallback. |
| assignment/SOURCE-PROVENANCE.json:29 | Refresh the four adapted source hashes changed for P2 and add the regression-file hash. |
| lab_method.py:53 | Refresh source-bundle SHA256 marker only. |
| NOTES.md | This post-freeze disposition and changed-hunk list. |
| source-manifest-0.13.0-review-fixes.json | New source/test/native provenance audit; frozen final manifest retained. |

Review-fixed normalized source-tree SHA256 (245 source/runtime files):
`e768ee9e7a2ee2c06717c0192a4b91acc6400c29fb0808deed2c14be3ce9229a`.
The hash covers the same source/runtime policy as the frozen manifest, with the
two new regression files; it normalizes only the adapter's own hash marker.
Verification: **677 passed, one existing forbidden-list skip, zero failures or
errors**, in **227.458 s**, from the repository root with:

```
.venv/bin/python -B -m pytest experiments/mvp/frontier/engine experiments/mvp/frontier/lab/tests -q
```

The full invocation used one numerical thread, one in-port worker, disabled
bytecode writes, and engine-local pytest/cache/log/XML paths. Both mocked review
probes pass. Twenty-one new cases cover P1/P2; the focused runs also passed.
The audit in source-manifest-0.13.0-review-fixes.json passes: all 257 native files
unchanged, all 24 frozen ladder copies exact, donor adapted hashes consistent,
T0 policy byte-identical, propose/finish present, and the source hash unchanged
through the full suite. The 19 pre-final partial rows remain correctly labelled.
Verification artifacts: test-review-0.13.0.log, results/review-tests-0.13.0.xml,
results/review-source-before-tests.json and results/review-audit-013.py.
No quick, benchmark, commit, checker edit or source import from changing lab code
was performed for these fixes. Nice 5 was requested but the sandbox denied
setpriority; numerical thread limits remained one and test processes ran serially.
