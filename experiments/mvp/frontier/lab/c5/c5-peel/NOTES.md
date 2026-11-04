# Assignment LP peel ladder (current iteration)

The leader stopped the pages constructor sweep by killing its two recorded runners,87680 and90375. Its five rows are preserved unchanged in `results/pages-ladder-stopped5of36` and labelled **pages ladder, stopped at5of36**. They are never mixed into the assignment experiment. Earlier source lives in `pages_source`; its manifest and the historical AS_IS table remain evidence for that stopped iteration.

Current source is a local copy of the securing assignment LP and its lab dependencies, with original per-file hashes in `assignment-start-hashes.json`. No live engine or another team's runtime folder is imported. The copied ordinary `method.py` remains byte-for-byte unchanged; added row metadata preserves all ordinary LP matrices exactly. A process-local adapter observes existing checks without changing their verdict. L0 uses ordinary propose and finish, two tries,240s proposal and120s finishing per candidate as in the securing frozen run. If it passes, the exact result is returned and no tier is peeled.

For tiny/small (at most60 load-list boxes), use the original frozen engine rather than dense assignment targets. This follows the securing study's explicit medium-only verdict and size-routing recommendation; its unguarded dense method lost all10 small maps. The new quick comparison tests the ordinary method selected by this size guard through the ladder in the same run.

Relaxed levels each allow240s proposal and120s total real finishing across at most2 candidates, with original5-round GM feedback, one numerical thread, and exact elastic/integer work counts recorded at freeze below. L3 shares its proposal work between complete and partial searches. L4/L5 forbid ashore slack for the completed search. No page-constructor fallback is used in relaxed proposals. Complete candidates already produced and rejected by this invocation's ordinary assignment method remain available, never read from a prior run. The full checker always determines the minimum justified level. D242 cargo-first ranking and a separate best safe L3 partial are retained.

Every benchmark map runs in a fresh subprocess with a PID-specific empty cache. The fixed list remains `benchmark36.txt`, seed0; the same six development inputs remain declared in `development6.txt`. The queue requests nice5 (the sandbox may reject the priority change), runs at most2 busy workers, and uses only recorded child PIDs for pausing if BENCH-RUNNING requires one. A stopped pages row, warm reference, stored plan, assignment, ballast or recipe is never a runtime input.

Development check: ordinary copied assignment method passed `D192.medium-feeder_500-f70-high-s192200000` through the full checker in129.094s. Six new controller tests and six row/mask tests pass. These are development observations, not final frozen benchmark rows.

The previous filename-inventory disclosure below still applies; it is not erased by the rebuild. All new access uses explicit approved folders and input paths.

## Goal programming and diagnostic slacks (current assignment iteration)

Charnes and Cooper, [Goal programming and multiple objective optimizations, Part1 (1977)](https://doi.org/10.1016/S0377-2217(77)81007-2), describe penalising departures from desired values with ordered priorities. In plain words: solve the most important aim first, keep its attained value fixed, then solve the next. This prevents any number of saved lifts from buying an extra box ashore or a worse higher-tier goal. The LP goals guide construction; final candidate ranking uses actual checker cargo, broken-rule count, breach and lifts.

Chinneck and Dravnieks, [Locating Minimal Infeasible Constraint Sets in Linear Programs (1991)](https://doi.org/10.1287/ijoc.3.2.157), add nonnegative deviations so conflicting rows can stretch. The stretched rows identify where the linear model asks for relief. Their elastic filter repeatedly makes stretched rows hard and resolves to narrow a conflict-containing set; isolating an irreducible infeasible subsystem needs a further filter. This ladder records labelled nonzero deviations as diagnostic evidence. It does not claim to produce an IIS, the smallest repair, or a proof that a map has no legal plan.

T0 rows and higher unpeeled tiers stay hard. At Lk the newly tested tier T(6-k) stays in the LP with deviations; less important tiers already peeled are dropped. At L4, securing rows therefore remain a minimised goal instead of disappearing. L3 has a separate omitted-box variable per box and count/TEU/mass priorities. Fractional slack, integer rounding and reference-motion rows are not full-ship checker measurements: their diagnostic fields explicitly identify proxies. Real finishing and the unchanged full checker determine what the returned plan actually breaks.

The primary publisher/author records and indexed primary-paper passages were read; direct PDF fetches failed. Details and access limitations are in [GOAL-PROGRAMMING.md](GOAL-PROGRAMMING.md). No paper-derived approximation or optimality guarantee applies to this bounded mixed-constraint heuristic.

# Earlier D242 pages ladder (superseded by assignment iteration)

This section supersedes the earlier cargo-last ranking, short budgets and preliminary frozen rows below. Those rows were stopped by the leader and archived in `results/superseded-cargo-last`; they are not current completion measurements. The historical AS_IS table remains unchanged:20 L4,8 L5,7T0-blocked. The original quick18 process was expressly left running for its L0 identity proof.

Current ranking at each level is boxes ashore, TEU ashore, kilograms ashore, unique broken rules, total breach, lifts. `objective.all_loaded` derives its breach from exact missing-box IDs; original measured/limit fields are preserved. Completion requires every box aboard. The controller continues through L5 unless a completed stow exists, and separately retains the cargo-best partial satisfying all T1/T2 rules at L3. Records contain `completed` and `best_safe_partial`, with exact ashore IDs, percent of load-list boxes aboard, TEU aboard, lifts, plan and unchanged checker report.

Current work chosen before the replacement freeze:120 seconds proposal and45 seconds real finishing per level; L0 normal engine count settings40 LP rounds,8 polishing rounds,80 dive nodes,12layout rungs,6LNS iterations,45-second area/securing lane. Relaxed proposals use3 page models,40+20 pricing rounds/model, a bounded80-node complete dive,5pricing rounds/node,4retreats,1,000,000 CP conflicts and30deterministic seconds/objective (wall stop120seconds, atomic/minimum integer work can overrun). Finishing examines at most2 cargo-ranked candidates per level, sharing45seconds plus atomic/native overrun; the ordinary ballast LP, area refinement and native V4 are run, and rejected candidates are retained. At most9 additional physical trim recipes and2 coordinate proposals use remaining work. Medium work is intended to fit roughly15minutes/map, but inherited stages and checker calls can exceed a nominal wall stop; all actual times are recorded and no speed claim is made. No short-work failure is an impossibility claim.

L4/L5 construction forbids ashore slack and retains complete stows even when safety checks fail. Complete stows captured from the ordinary L0 engine remain in-memory candidates, are finished and ranked before partial stows, and may seed new columns only through the current gates. No earlier run plan is read. A safe L3 partial never causes the ladder to stop before searching for completion.

The explicit extra tiny20000040 case is included beside the original18quick maps, so completion tables cover19quick inputs while identity assertions refer to the original18ordinary engine runs. The leader's read-only `completion.py` maps completed levelsL0..L5 to columnsT5,T4,T3,T2,T1,T0only; partial stows never enter a completion column.

## Earlier experiment and audit history (superseded where noted)

# Peel ladder, cold experiment

Scope: only this folder. The checker and tier table are unchanged. Engine source was snapshotted at the start of the task, before implementation. `snapshot-hashes.json` logs SHA256 for all 135 engine Python files and the copied Frontier support modules (`../` keys); results/logs/caches were excluded. `runtime.py` refuses a previously imported live engine. Python bytecode writing is disabled. No sealed data is read. Runtime never reads earlier run plans or references.

## Fixed measurement design (before results)

`benchmark36.txt`: all twelve D185 entries from medium12.txt plus all twelve f70 and twelve f80 entries from all48.txt. Seed 0. `development6.txt`: first two D185 entries, first two f70 entries, first two f80 entries, list fixed before results. Only those six are development. Freeze source before the remaining thirty; log hashes and results below.

Budgets fixed before measurements: L0 normal snapshot engine, extra search 8 seconds and finishing 2 seconds, area/securing stage 2 seconds. The engine retains its ordinary historical <=60-box baseline search and >=300-second finishing floor; atomic checks may overrun deadlines. L1 through L5 each receive 4 seconds page search, at most two candidates, and 4 seconds ballast work per candidate with at most two trim recipes plus at most two coordinate proposals at L4/L5. Finite counts bound repeated work; native solver/physics calls are opaque and can overrun a wall deadline. These deliberately short budgets test ladder semantics, not engine peak performance. The engine configuration otherwise uses its defaults. No claim of infeasibility follows exhaustion.

One-thread numerical libraries; HiGHS and CP-SAT explicitly one thread/worker. At most two busy processes for the team, reduced to one whenever BENCH-RUNNING exists. Jobs run at nice 5. No process killing by name or pattern.

## Model and method

Each page is a ship stack (or adjacent stack pair for a long box). Columns assign an ordered subset of boxes to that page. The master chooses mutually compatible columns; weighted lifts count relocations, hatch covers, and projected later-port restows (D15 fixed stow, no later loads). This retains the snapshot's cutting-stock approach (Gilmore and Gomory, 1961, https://doi.org/10.1287/opre.9.6.849). No new paper-derived code was downloaded.

The ladder first invokes the ordinary engine with an observer that only records already-computed reports and preserves the exact returns. A returned L0 plan is reused unchanged. Only failure permits a new level. Within each level candidates are full-checked, and a failed verdict is never relabelled a pass. Rejected finishing candidates are retained process-locally; restoration is in finally. T0 failures and unfinished checker parts always exclude a candidate. Reports preserve every occurrence with measured and limit evidence.

Breach accounting uses the absolute measured-minus-limit gap for a reported violation, handling both upper and lower limits; total breach sums the original units because the fixed policy specifies no unit exchange rates. Unknown numerical gaps stay unknown and rank after measured gaps. This is a stated judgement call, not an invented measurement.

Partial loading is supported by the action expander, scorer, and ordinary checker. The shared scorecard's complete-candidate filter intentionally rejects partial candidates, so `propose`/`finish` expose the requested plug-in API but `run.py` measures relaxed records directly using the unchanged checker. No shared runner is changed. Cold 0.11.0 m0/m1 rows are the historical baseline, not c2-layout's warm solving rows. Budget and engine-version differences preclude a claim of solver improvement from their pass counts.

L1–L3 records say “relaxed plan”; L4 “breaks a class or statutory limit, needs a person's sign-off”; L5 “not safe to sail, shown only to say what would have to change”. Only L0 is a pass. Exhaustion says “no plan found at Lk within this work”.

## Implementation judgements

The mixed-unit breach ranking retains infinity for any missing measured/limit pair. For equal broken-rule count and equal breach key, the explicit partial-load tie break is boxes left, TEU left, mass left, then lifts; it cannot fabricate a measured heat/all-loaded gap. All generated raw plans are checked before selecting up to two for ballast finishing. Search returns bounded best-found candidates and makes no global minimum claim. The page penalty is guidance; exact nonlinear whole-ship physics is ranked only from the full checker.

`nice -n 5` was requested for measurements; macOS denied setpriority in this sandbox, so Python ran at the permitted priority. Early overlapping test jobs briefly exceeded the two-busy-process cap; corrected immediately upon discovering the overlap, and later jobs are explicitly coordinated. This affects timing, which is not used as a solver-speed claim.

Development adjustment after first D185 map: its 4-second page budget produced no integer incumbent at any level. Retain already-computed rejected L0 finish reports without extra checker calls, ranking only after L0 finishes. L0 gate and returned result remain unchanged. Page integer work allocation revised before freeze; earlier development rows are retained as measurements of their actual code revision.

L0 capture also records inputs to the ordinary finishing chain, because the engine's securing certificate can reject a stow before `_checked` runs. These inputs are full-checked only after L0 returns no plan. This adds work after failure, never changes its acceptance test, and makes safety-limit relaxed candidates available at later levels. Runtime vessel caches use a new PID-specific directory, avoiding reads from earlier process runs; the first development process used a shared cache directory and is excluded from frozen measurements.

# Saved stows: as-is tier classification

Historical saved reports only; no solving or new pass claim; not a cold baseline.

The fixed historical portfolio has 35 failed maps: L4: 20, L5: 8, T0/unknown: 7.

| Rung | Failed maps | L4 | L5 | T0 / unknown |
| --- | ---: | ---: | ---: | ---: |
| f60 | 4 | 4 | 0 | 0 |
| f70 | 8 | 7 | 0 | 1 |
| f80 | 12 | 7 | 3 | 2 |
| medium12 | 11 | 2 | 5 | 4 |

| Map | Level reached | Hard rules broken in selected saved report |
| --- | --- | --- |
| D192.medium-feeder_800-f60-high-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f60-high-s192200101 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f60-high-s192200102 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f60-low-s192200100 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_500-f70-low-s192200001 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_500-f70-low-s192200002 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f70-high-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-high-s192200101 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-high-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200100 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200101 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f70-low-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200000 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200001 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-high-s192200002 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200000 | L4 | iacs.member_irs.container_securing, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200001 | L4 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, solas.v22.visibility |
| D192.medium-feeder_500-f80-low-s192200002 | L4 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.trim, solas.v22.visibility |
| D192.medium-feeder_800-f80-high-s192200100 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing |
| D192.medium-feeder_800-f80-high-s192200101 | L4 | iacs.member_irs.container_securing, iscode.loading.list |
| D192.medium-feeder_800-f80-high-s192200102 | L4 | iacs.member_irs.container_securing |
| D192.medium-feeder_800-f80-low-s192200100 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f80-low-s192200101 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, solas.v22.visibility |
| D192.medium-feeder_800-f80-low-s192200102 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| random.medium.185100200.0.2 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| random.medium.185100201.0.5 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100210 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100211 | L5 | iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| random.medium.185100001.0.5 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100010 | L5 | iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100011 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.b2_3.containership_second_check, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| random.medium.185100100.0.2 | L4 | iacs.member_irs.container_securing, solas.v22.visibility |
| random.medium.185100101.0.5 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, solas.v22.visibility |
| sim.medium.185100110 | T0 / unknown | expert.e9.over_width_length, iacs.member_irs.container_securing, iscode.loading.draft, iscode.loading.trim, solas.v22.visibility |
| sim.medium.185100111 | L4 | iacs.member_irs.container_securing |

Every saved full-checker report is included in AS_IS.json, including failures on maps that have another historical L0 witness. The per-map choice first minimises level, then the fixed within-level ranking. This is a retrospective witness table and reads warm historical material; runtime solving reads none of it.

Breach is the sum of absolute measured-minus-limit gaps over hard occurrences, in original units. No missing measured/limit pair becomes zero: its breach remains null and ranks after measured gaps. Each report has its exact content-addressed path and SHA-256, preserving all measured/limit values and locations without duplicating the 389 MB archive. Candidate plan paths are stem associations and can be ambiguous when historical paths were overwritten.

Only hard violations determine level. Soft and tolerated violations remain diagnostics, consistent with the unchanged checker definition of L0. T0, missing replay count, incomplete checker parts or inconsistent checker verdict make a report ineligible at every level.

Latest pre-freeze search count caps: at most three models; phase-one 8 and phase-three 4 pricing rounds/model; 10,000 CP conflicts and 0.5 deterministic seconds/objective; 500 heel swaps. The first model receives 90% of available search time; each started model reserves at least one second for integer search, so atomic pricing and that explicit integer floor can overrun the nominal 4 seconds. Greedy compatible columns supply a concrete partial hint/fallback when presolve exhausts its work. Mass tie breaks use grams in CP-SAT; output keeps original measured kilograms. These are search approximations, with the ordinary checker authoritative.

Cold Engine 0.11.0 m0/m1 evidence: all twelve D185 rows are L0 failures. There are no f70/f80 rows in those two directories, so historical comparison for those rungs is unknown. Frozen C5 uses a start-of-task 0.12.0 snapshot at shorter work; no matched-budget speed or lift comparison is claimed. D185 historical warm c2-layout results are used only for the requested as-is tier classification.

A complete benchmark may exceed 20 minutes in aggregate because all 36 fixed maps and 18 paired quick cases need ordinary physical full checks. Individual model/search work remains count-bounded; the long aggregate run is required by the order, not an expanded tuning budget.

For equal rule count and an unknown total breach, candidate selection compares the sum of the available measured components before ashore/lifts. Unknown components remain null and are never invented or claimed to be minimised. This ensures measured T2/T1 breaches still guide selection when a co-occurring booking rule supplies no numeric pair.

Quick preservation measurement invokes the ordinary engine once per map, then passes that actual result through the ladder control gate using its injected ordinary-call seam. This tests exact plan JSON and object identity, without comparing two wall-budget-dependent engine reruns. It is a same-run gate preservation test, not an independent paired speed benchmark. The returned plan is then checked again by the ordinary checker.

Final L0 count budget fixed before frozen measurements: normal snapshot Config with all stages enabled, lp_rounds=4, polish_rounds=1, dive_nodes=8, layout_rungs=2, lns_iterations=1, area_securing_s=2. The default count-search stage otherwise ignores the small nominal wall budget and made a development f70 attempt take several minutes. These exposed work settings bound search without changing acceptance or the historical small-map floor. The earlier default-count development jobs were stopped only through their own recorded tool sessions (40623 and 10531); their partial rows are not frozen results. All 36 maps are rerun after freeze.

## Source freeze

Start snapshot manifest SHA256: `a813a74b567a7d99b1b9954727e90b7b0a42fbc5aaf01e4626226f064d984585`. Engine planner at start: `0f6aa985b9bb42656fe0e053eff59dd1e0fb1c760cb104435e9ba6bf99d382df`; portfolio: `f1fdbeb0ba3ff2750b06703a8926a05df016758dac71fdf575e341284591caf1`; copied Frontier planner: `788e8b366aca99dd952ebe156e1e9505bedcbdc0c8b39fd570686f6cd2c93185`. The manifest records every copied source separately.

Frozen runtime/test manifest: `frozen-code-hashes.json`, SHA256 `b812eda1cccee3ab36a4a179d6131b031b8b97672bebf7290249919b5954f4d8`. All 36 fixed maps rerun cold under this code; six reserved development maps remain clearly labelled. Later changes to this NOTES file and JSON measurement artifacts do not alter frozen code.

Final pre-holdout audit found snapshot mixed-pages HiGHS aliases without an explicit thread option. Before opening any of the thirty non-development maps, restarted the frozen run and added a process-local SciPy linprog/milp thread-cap wrapper at runtime initialization (threads=1). It changes numerical worker allocation only. Superseded freeze and partial first-map work are excluded; the definitive manifest below supersedes the earlier freeze.

Definitive frozen-code manifest SHA256: `c05a5a29209b6d0ee632de476437573b5b57e8aa5309fe2530987f9b9d8dc0a7`; all 163 Python source hashes recorded there. No further code changes after this freeze.

## Verification completed before results

Full focused suite: 33 tests passed (exit 0), `tests-final.log`. Covers catalogue 62-rule exact coverage, every archive report rule, unknown ID T0, checker-report immutability and lower/upper breach accounting; differential mask monotonicity with higher-tier rule preservation, finally restoration, unchanged inputs and missing E1/heat data staying T0; real partial-load checker evidence; CP ashore lex priorities; soft securing/stack-weight height guard; ordinary L0 result identity; rejected finisher retention and breach-over-lifts ranking. The final numerical thread-cap audit was followed by the frozen actual engine/quick measurements. A source-manifest audit after the first frozen result found no drift.

The first definitive frozen D185 row reaches L4; the first quick tiny engine pass retained both object identity and exact full plan JSON through the control gate. These are interim observations; the final table below supersedes them once complete.

## Limits and next implementation step

This is a bounded best-found ladder, not a global minimum-breach or fewest-ashore proof. The relaxed page family supports empty arrivals only; onboard/discharge search fails closed. L4 securing and L5 stack weight have construction penalties/upper tightening, but exact nonlinear whole-vessel physics is handled only by finite ballast recipes, two coordinate proposals and full-checker ranking. Stronger DG/OOG pattern guards remain voluntarily enforced even at L5, so all theoretically allowed T1 relaxations are not expressible by this family. Freeze is preserved rather than tuning on the thirty non-development outcomes. A next implementation should add safety-guided cargo-removal cuts and a whole-vessel nonlinear breach objective, then repeat a new predeclared experiment.

Successful records expose both `level` (minimum level justified by the unchanged report) and `searched_level` (permission used for construction), with every attempted level in `trials`. Unknown/ineligible reports never receive a relaxed plan level. This distinction allows a later search to discover a checker pass without calling a failed report a pass.

## D242 definitive source freeze

48 focused tests passed (tests-d242.log); the final11 controller/finisher tests passed again after diagnostic-error handling changes (tests-controller-final.log). The complete-only D185 development smoke loaded121/121 at102lifts in122.766seconds, replay-legal; that construction result alone is not a safety or completion-level claim. The new full-checking runs rederive it cold.

Definitive D242 manifest SHA256: `cff7a658ebc43f726fdc0486a72fc112e59fef490e4115b2ba2dce7f841b397a`; 165 Python files. This supersedes every earlier freeze. The six reserved development maps run first, then the other30 with no source edits; every new row logs this manifest digest and D242. Quick18 remains the originally running L0 identity experiment per the leader; the extra tiny case uses D242. New cold completed rows go to results/frozen36.

Final D242 breach-tie audit: cargo order is unchanged; when a qualitative breach leaves the total unknown, the observed numerical breach sum breaks that unknown tie before lifts. Unknown values remain null. Regression demonstrates securing1.03 beats1.50 despite100versus0lifts with the same unknownE6 breach. This final audit occurred only on development code before any new thirty-map result; stopped only own recorded development session1007. Tests-controller-final11passed; tests-breach-tie2passed. Total focused tests now49 (48 plus new regression). Final manifest SHA256 `9ed6409f29b02ec2db3c581cf96416e653c3bb6eb3a9e603e660822184cb6900`; supersedes prior D242 pre-tie manifest.

The frozen30 non-development maps are partitioned, before running them, into10fixed three-map files holdout-01.txt through holdout-10.txt in original benchmark36 order after excluding development6. The next available CPU worker takes the next chunk; no result or earlier plan is supplied to its solver. This preserves the fixed cohort and cold per-process cache policy while balancing the two permitted CPU slots.

First definitive D242 cold row: random.medium.185100000.0.2 completed at L5 with121/121 boxes,114 lifts, source ordinary-ballast-lp; no safe L3 partial found. Weather, securing, containership second check and visibility still fail, so this is not safe to sail. Wall1113.60seconds (18.56minutes); all five relaxed levels were searched and finishing called9times. Six independent120+45second stage caps sum to16.5minutes before atomic/inherited overhead; the per-level work requirement was prioritised over a strict aggregate15-minute watchdog, and actual time is reported rather than claiming a15-minute bound. All49 tests passed across the final checks; the165-file final source manifest audit found no drift on the first row.

The reserved development-six measurement job starts first; subsequent fixed holdout chunks may overlap its later rows once a second CPU slot is free and the first completed record has been validated. All code is already frozen and no later development or holdout outcome will be used to edit it. This scheduling overlaps measurement only; it supplies no earlier plan to a new map/process.

Explicit tiny contract test completed: random.tiny.20000040.0.2, L3,4/4boxes aboard,7lifts, only expert.e8.away_from_heat broken. Reported distances1.960100/1.960763m against2.4m, totalbreach0.879136626m. Safe partial is null because the completed heat-booking alternative was found. Wall686.498seconds includes the ordinary small-map floor. This is a completed relaxed plan, not an L0 pass.

Each fixed three-map chunk can exceed20minutes because it aggregates three maps at the unchanged D242 per-level count caps, real finishing, cold vessel setup and full physical checking. This long run is required for the predeclared cohort, not an extension of per-map tuning or counts. Root worker records its own session/PID separately; worker B records each holdout-NN.pid. No process is stopped by a name or pattern.

Runtime audit during frozen measurement: the snapshotted ordinary engine uses count-based `layout_work` with an inherited safety deadline of `4 * max(900, budget_s)` seconds per layout/polish invocation. Thus the nominal120-second ordinary proposal request is not a strict wall cap; finite40LP/8polish/80dive/12rung/6LNS counts remain the declared work limit. This may exceed15minutes on a map. The frozen ordinary engine is preserved, actual wall time is measured, and no heldout outcome is used to shorten or retune it. Relaxed generation has its separate120-second stop plus atomic work.

Procedural disclosure from the search subagent: early tiny-case discovery ran `rg --files experiments/mvp | rg '20000040.0.2|quick_tiny.txt' | head`. This broad filename inventory was outside the required explicit-path discipline and may have traversed protected tree names; it surfaced no sealed paths and read no sealed file contents. It must not be counted as proof of perfect sealed-tree isolation. The command was not repeated or investigated by opening protected paths; subsequent implementation and measurements use only explicit approved paths, and no sealed data informed them.

## Assignment integration and final pre-freeze corrections

Focused/copy verification now covers90 distinct tests:49 historical pages/tier-policy cases retained under their original controller,10 assignment mask cases,12 elastic LP/CP cases,10 assignment controller/import/reference cases,9 copied securing-row/gap cases. Logs: tests-assignment-full.log, tests-assignment-final-glue.log, tests-assignment-reference.log and the elastic/vendor logs. Later fixes were retested where they affect behavior; no broad repository test work was launched.

The first new full D185 development invocation, before measured-reference seeding, completed L4 at121/121 boxes and94lifts, breaking securing, containership second check and trim; it retained a safe L3 partial109/121 boxes (90.083percent),82lifts, breaking only all_loaded and the heat booking. Wall1093.812s. This is pre-freeze development, not a benchmark row. It exposed that every level reset referenceGM to1.92m despite an ordinary same-invocation complete stow measuring1.485m. The final controller instead obtains measured GM/draught/block coefficient from its cargo-best checked same-invocation candidate and adds the original0.05m GM margin. No earlier-run plan or ballast is read. The corrected cold invocation is measured separately.

The tiny explicit heat case initially had no LP incumbent because the dense arbitrary cargo moment target was incompatible with its sparse cells. Diagnostic row omissions showed only those moment proxies blocked the LP; this was not a physical infeasibility result. Dense moment-target settings are therefore omitted for at-most60-box relaxed constructors, while the full checker still enforces actual T1/T2 limits. After this correction, directL3 construction plus real finishing loads4/4 with7lifts and only the heat booking broken; wall84.90718829201069s, development only. NormalL0 retains its medium-only size guard and unchanged ordinary code.

Partial moment rows use actual loaded mass, not full-load RHS. Integer scaling preserves nonzero slack coefficients even for large moment rows. Partial OOG repair temporarily filters only assigned boxes and restores model state in finally. LP root infeasibility skips integer retries of that same restricted model; rounded integer-model infeasibility skips identical seeds. These exhaustions never prove the map infeasible. The outer loop stops repeating an identical complete-edge LP when its current construction targets are infeasible.

Only an identical full input plan, including its ballast start, is finished once in this invocation; distinct ballast starts remain eligible; existing failed optimizer outputs stay in memory for ranking. L0 optimizer calls/returns are observed but not skipped or altered. The stopped-development request for exactPID99895 received OS operation-not-permitted; one interrupt on its recorded session also had no effect. It was allowed to finish naturally and exited0. No unknown process or PID pattern was signalled.

## Definitive assignment freeze and measurement

Frozen after96 distinct passing tests and the final tiny real-check smoke. `assignment-frozen-hashes.json` SHA256 `dd9abf2aee785239371b48a00459aac284fbd3518e0b35987f7ab8602e32f15d` covers250 Python files, plus monitored checker/policy hashes. Initial `assignment_freeze.py --check` reports zero drift. No Python source changes are allowed after this point; documentation and measured JSON/logs may grow.

Final review retained raw goal-program candidates before exchange/resorting, and passes their original unchanged reports into selection. L0 observation merely appends already-computed reports and postpones ranking until the timed ordinary invocation has ended. Optimizer exceptions do not mark a plan finished; the finishing key includes ballast, so distinct starts can improve. Ordinary per-candidate finisher exceptions continue to the next try, matching score.py. All13 vendor dependencies and native alns are checked against private copied paths. The publicplug-in therefore fails closed in a process that already imported foreign runtime modules; the standalone cold runner supplies the intended environment.

The measured-reference development run (before the final raw-candidate/cache review) completedL4,121/121,94lifts,916.698s; safeL3partial114/121 (94.215percent),56lifts. Per-level times349.26,167.76,123.07,254.84,21.73s. These are development observations only. Finaltiny raw-retention smoke again produced4/4,L3,7lifts,onlyheatbroken. The fixed benchmark starts fresh afterfreeze; no developmentrow is reused.

Benchmark coordinator3781 began at20:40UTC with children3782/3783; exactPIDs/logs/exitcodes are recorded dynamically in assignment36-jobs.json. The parent was tested pausing/resuming its own recorded child3653 successfully (child then exitednormally); unlike a separate sandbox process signal, this allows BENCH-RUNNING to reduce busyworkers toone. No marker was created for that test and no unrelated process was touched.

The exact work counts fixed in the definitive assignment manifest before measurement are:240s proposal per level; relaxed finishing120s total across at most2 candidates; ordinary finishing2 tries with120s each; at most5 relaxed outer rounds,80 LP dive nodes and6 retreats; integer tail at most6 attempts, with15s wall,100000 conflicts and3 deterministic seconds per sequential goal; real ballast finishing at most12 evaluations and2 iterations per input stow. Sequential goals fix an attained LP optimum before the next objective; integer goals are fixed only after an OPTIMAL verdict. Atomic solves/checks and inherited ordinary stages can overrun a wall request. These are the frozen manifest settings, transcribed for readability during the run, with no source or budget change.

Before any replacement quick result, the quick measurement queue is fixed as assignment-quick19.txt: the explicit extra tiny heat case first, then original quick18 order. This keeps both permitted workers available while the tiny case performs ordinary finishing. It changes scheduling only. The same frozen runner uses a fresh subprocess per map; afterward its unchanged row JSON is partitioned into the original18 and the extra case for the existing aggregator. No source or count setting is changed.

Some individual frozen assignment rows exceed20minutes. The stated reason is the unchanged declared work: a map may search L0 plus all five relaxed levels, each allowing240s proposal and120s relaxed finishing; ordinary L0 separately permits two120s finishing tries, as in the copied method. The nominal aggregate allowance is therefore2280s (38minutes) before atomic full checks or inherited ordinary-stage overrun. The observed long rows are not extra tuning or a restart with larger work. All actual per-map and per-level times are reported; no strict15-minute or speed-improvement claim is made, and holdout outcomes do not change these caps.

Frozen construction examples distinguish retention from new solving. random.medium.185100100.0.2 completesL4 at229/229,128lifts,1281.49s; source ordinary-assignment-check is a rejected ordinary L0 candidate retained in this same invocation. Worst securing ratio1.148686432522231 against1.0; total securing breach5.069087241660548 across82 checker occurrences. This is not a demonstrated improvement from L4 proposal. Conversely sim.medium.185100210, the cold baseline's sole no_candidates map, is newly built by L5-elastic-raw-goal-program:428/428,409lifts,1713.17s. Its unchanged checker measures draft9.907334197388511 versus9.52m, list0.041041921224438706 versus0.03490658503988659rad, and trim−1.6360611731673025 versus minimum−1.6m. It breaks booking/reefer, securing, visibility and those hydrostatic limits; this is an unsafe diagnostic completion. It will be included in the five fresh-process plan audits.
