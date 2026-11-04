# Frontier Engine 0.13.0

Only engine/ was edited. No checker/src/parameter changes, commits, cloud or GPU.
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
LP first, then the page portfolio. A held native-checked pass survives every
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

## Frozen tier table and peel seam

`tiers.toml` and `completion.py` are copied from lab/c5/leader with provenance.
The tier table is unchanged; unknown IDs count as T0, and T0 is never peeled.
`peel.no_pass(instance, everything_found, ladder=None)` is called only if no
full pass was held. It receives this invocation's complete archive, stage
records, raw partial assignment diagnostics and finishing history. The future
frozen LP peel ladder plugs into this function. No unfinished lab implementation
is imported and no wait for it is needed.

Until that ladder arrives, the seam **only classifies existing L0 search
witnesses**, with exact native breaches/measurements/limits and per-part margins.
It does not claim to have searched the intermediate relaxed levels. A completed
witness has every requested/surviving arrival box aboard; a partial is separately
reported with percentage aboard and is never counted as completed. Full passes
are T5/L0 only; relaxed completion is never added to passes or proof counts.
A full pass suppresses peeling, regardless of potential lift savings. A future
ladder result is independently replayed/checked and cannot override T0.

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
| assignment/{method,gap,stow,rows,chainlab}.py | Frozen C5 securing assignment LP. All five donor hashes matched FROZEN-SOURCES.txt before adaptation; local provenance manifest retained. `assignment_lp`, assignment budget 240s. |
| assignment/pages*.py and local helpers | Needed C4 geometric/sight/balance/pricing dependencies copied locally; no lab imports, stored recipes or donor result writers. |
| bounds/exact/*.py | C4 c2-hatch certified/target constructors; existing DG/oracle guards retained. `certified_bound`, `proof_cutoff`, `targeted_constructor`, work limits. |
| scout/moment_pages.py, moment_lns.py, dense_master.py, dense.py, highs.py | C4 c2-layout moment pricing, fixed-charge LP/IP/LNS, thread config and diagnostics. `moment_pricing`, `dense_layout`, `dense_fixed_charge_lns`, `layout_diagnostics`, `capacity_deck_cap`. |
| benders/permute.py, post_pass.py | C4 c2-benders projected-restow objective and checked-plan exchange, merged with engine safety/None guards. `post_pass_exchange`; 60s extra, current-run incumbent only. |
| finishing/chain.py | Retained source-pinned securing certificates used by C4; added native static and map-bound tank-work skips. `fixed_stow_screen`, `candidate_triage`. |
| unary.py | Local exhaustive native supported-stow E8 certificate; `unary_certificate`. |
| tiers.toml, completion.py, peel.py | Frozen leader D241/D242 assignments/summary plus local seam; `peel_seam`. |
| safe_catalog.py, approved-maps.json, bench/safe_score.py | Frozen positive public provenance from lab manifest and stored validation rows; avoids forbidden valsplit reads while running unchanged lab/score.py. |

Each copied file/test has a provenance comment. Earlier cycle features/tests
remain; detailed notes in assignment/NOTES.md, scout/NOTES.md and
bounds/NOTES-0.13.md. C4 warm disk references are omitted: cold engine never
reads them. The unmeasured cold C4 Benders profiles, stopped feasibility-proof
line and discrepancy rounding remain out. c5-peel, c6-lifts and next securing
code are pending frozen follow-ups; their seams are present, not claimed as
implemented. No frozen external source is imported as a mutable solver module.

## Compute and measurement protocol

At most two CPU-busy processes, one when BENCH-RUNNING exists, with one
numerical thread. nice 5 is requested; macOS denies setpriority in this sandbox,
so effective lowered priority is not claimed. No other session's process is
inspected or managed. Long runs beyond 20 minutes are required to complete the
fixed 21-map quick and its full fresh arm at the user's generous budgets;
there is no full benchmark or outcome-based map exclusion.

`bench/quick013.py` launches the engine and fresh 0.9+E9 scorecard arms, two
workers each, sharing a two-slot file-lock admission gate. Workers waiting for
a token do no numerical search. Queue time is outside each map's solver budget.
Engine arguments: budget 900, finalize 600, tries 3, seed 0, workers 2. The fresh
method_e9_val policy restores original val-0.9 search and 300s V4 internally.
All 21 quick maps are in both arms. Budget 900 is the search allowance, not
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

## Verification and contribution results

The latest full engine+lab suite passed **548 tests, one forbidden-list skip**,
in **177.99 s** (`test-0.13.0-final.log`). A final E9-ablation fix is covered by
**88 targeted tests in 2.69 s** (`test-freeze013.log`). One real spawned
scorecard smoke passed at eight lifts in 81.44 s.

The measured source is frozen: **196 Python files plus tiers.toml and
approved-maps.json**, normalized tree digest
`9f33817a89d0e4a252902c4a6af8e1a6bef4ebd9f5e51cbeefdceb01fb61f023`.
The combined engine/native/parameters/input binding digest is
`da17127d6d637d9ccc04153f9b177113bf968393eaa3a9d9102c7b5c8ef49372`.
All 257 previously pinned native/frozen Python files matched before launch.

The required full quick/fresh measurements are running. Final paired results,
per-map times, exact bound names and the observed contribution table follow.
A post-measurement suite will verify the measured source again.
Counts will describe execution, not isolated ablations. Medium exact repeats
are optional only if cheap; full finishing repeats of the old 47-minute map
would not qualify as cheap.

Optional repeat rule, fixed before any medium result: repeat a medium map twice
only if its complete engine invocation takes at most 120 seconds, with the same
seed/settings, after the main arms finish. Longer full-pipeline repeats are not
cheap under the two-process ceiling and will be omitted explicitly. Repeats
cannot establish seed robustness or external-load independence. L4 witnesses
require human review; L5/T0-only witnesses are not safe-to-sail plans.
