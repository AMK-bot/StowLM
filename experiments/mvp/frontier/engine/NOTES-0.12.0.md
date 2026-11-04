# Frontier Engine 0.12.0

The implementation, its copied tests and this measurement live entirely under
`experiments/mvp/frontier/engine/`. VERSION is `0.12.0`; NAME remains
`frontier.engine`. The frozen 0.10/0.11 copies and stored rows were not edited.
Previous integration notes remain in [NOTES-0.11.0.md](NOTES-0.11.0.md).
No sealed data, cloud, GPU, commit or full benchmark was used.

## Completed quick and comparison

The required `lab/score.py --set quick` run through `engine/lab_method.py`,
seed 0, two workers, `--budget-s 900 --finalize-s 600 --tries 3`, completed
all 21 development maps in **5,443.196 s
(90.72 minutes)**. Every map constructed an internal replay-legal complete
candidate; **20/21 passed the unchanged full checker**. Only D185 failed.
The distinction matters: replay completeness does not establish a full pass.

| Development group | Full passes | Mean lifts on passing maps | Stored 0.11.0 | Stored 0.10.0 | Fresh 0.9.0 + E9 |
|---|---:|---:|---|---|---|
| Tiny | 8/8 | 6.375 | 8/8, 6.375 | 8/8, 6.375 | 8/8, 6.375 |
| Small | 10/10 | 11.4 | 10/10, 11.5 | 10/10, 11.5 | 10/10, 13.1 |
| Medium | 2/3 | 87.5 (71, 104) | 2/3 (71, 116) | 2/3 (79, 184) | Unknown; not run |

All 18 tiny/small passes equal a named certified map lower bound, with the
strict proof scope below. No medium optimality claim is made. Both D192 maps
were preselected as passable by V6, our two-step-based feasibility constructor;
V6 is not a lift benchmark. These and all quick/validation/TRAIN observations
are development results, never unseen or sealed-test evidence.

The fresh control used the unchanged
`lab/c1/leader/method_e9_val.py`: original val-0.9 search budgets, 300-second V4,
three attempts, seed 0, two workers. Its fixed pre-result selection was all
eight quick tiny plus all ten quick small maps, with no outcome exclusions.
It completed 18/18 in **1,567.923 s (26.13 minutes)**. The engine quick
uses 900 seconds of additive work on small maps too (the API default there
is 120); the fresh adapter restores its original budgets independently of
the shared scorecard budget argument. The engine and control
launched concurrently under the four-worker ceiling, but individual map jobs
were not synchronized and engine effort is larger. Their elapsed times overlap
and must not be added. This is a fresh same-period control, not proof of
identical per-map machine load. Historical rows have unequal effort/load.

| Small paired comparator | Mutual passes | Engine / comparator mean | Mean delta; paired bootstrap 95% CI | W/T/L |
|---|---:|---:|---|---|
| Fresh 0.9.0 + E9 | 10 | 11.4 / 13.1 | -1.7; [-5.1, 0] | 1/9/0 |
| Stored 0.11.0 | 10 | 11.4 / 11.5 | -0.1; [-0.3, 0] | 1/9/0 |
| Stored 0.10.0 | 10 | 11.4 / 11.5 | -0.1; [-0.3, 0] | 1/9/0 |
| Stored 0.9.0 | 9 | 11.5556 / 13.8889 | -2.3333; [-6.3333, -0.1111] | 3/6/0 |

Tiny pairs all tie. Medium's two mutual-pass means are 87.5 versus 93.5 in
0.11.0 (delta -6, CI [-12, 0], W/T/L 1/1/0), and 87.5 versus 131.5 in
0.10.0 and C1 scout quick-3 (delta -44, CI [-80, -8], W/T/L 2/0/0).
Unknown medium 0.9.0 evidence remains unknown, never zero or a failure.
These two-map intervals and one-seed small differences do not establish a
population gain or causal stage contribution. No percentage headline is inferred.

The sole small difference against the fresh arm is `sim.small.40100025`,
8 versus 25; all nine other small pairs tie. Against stored 0.11 it is 8 versus
9. The new 8 comprises 0 relocations + 4 current hatch + 4 projected future
hatch lifts. The stored 9 has one current relocation; the fresh 25 comprises
3 relocations + 4 current hatch + 6 future hatch + 12 future restow lifts.
Relocations are not generally forced. `sim.small.40100034` passes at 10 in
both engine and fresh E9 control: its extra pass over shipped 0.9.0 is the E9
mask bug fix, not a solver gain.

| Medium map | Full checked lifts and parts | Named map lower bound | Gap | Outcome |
|---|---|---|---:|---|
| D192 feeder_500 f60-high, 88 boxes | 71 = 47 relocations + 12 current hatch + 12 future hatch | 65, `two_port_hall` | 6 | Full pass; pure anchor supplies 71 |
| D192 feeder_800 f60-low, 162 boxes | 104 = 84 relocations + 10 current hatch + 10 future hatch | 90, `aggregate` | 14 | Full pass; unused-cover design supplies 104 |
| D185 random.medium.185100000.0.2, 121 boxes | null: no full pass | 32, `physical_stack_e6` | Unknown | One internal replay-legal 114-lift candidate fails full checking |

No medium restows are charged in the two passing new plans. Relative to
0.11, the 162-box improvement is 6 current hatch and 6 projected future hatch
lifts, with unchanged relocations; it is not solely a later-port gain. Every
future-port number uses the **D15 projection with a fixed stow and no later
loads**; it is not measured future-voyage work. The older medium gains were
predominantly projected later-port work. Part-by-part paired tables are in
[comparison-0.12.0.log](results/comparison-0.12.0.log), with machine-readable
[comparison-0.12.0.json](results/comparison-0.12.0.json).

## Pipeline, switches and provenance

1. Install the process-local E9 correction and guarded cached native-equivalent
   mask. The unchanged full checker is the sole pass gate.
2. Combine old sound map bounds with guarded C3 certified packing/first-aboard
   cuts, or arrival bounds for onboard cargo. Each number names its producer.
   Bounds report gaps and strict equality; they never reject a candidate plan.
3. Try the exact witness repair and arrival discharge/restow prefixes. At most
   60 boxes retain frozen Frontier constructors and original minimum budgets:
   search min(120, max(20, 6*n)), its 2/3 and 1/3 split and native finishing
   floor. Extra stages add work; a strict scoped proof can stop earlier.
4. Above 60 boxes run the deterministic pure-page anchor before mixed pages,
   per-stack sight roofs, OOG reservations, unused-cover designs and fixed-charge
   LP/LNS. Default medium budget is **900 s**, not a whole-pipeline time cap.
5. Replay every proposal by real Problem.score and archive complete ordered
   actions, including arrival actions. Optional fusion/classes remain available;
   the default medium work route supersedes their legacy wall-clock search.
   Turning `work_count_search` off restores the legacy medium route.
6. Finish cheapest candidates first: sound fixed-stow securing screen, direct
   full check, local ballast LP guidance, cold area/securing and permutation
   where eligible, inherited-tank V4, then unchanged full native V4. Continue
   until a pass and retain the cheapest checked pass within available work.
7. Warm polish uses only this invocation's checked finished plan and its actual
   departure ballast, never a stored recipe or result row.

All 15 new boolean switches are frozen Config fields and default true; the
contribution table below names each. Existing switches and ablations remain.
Work controls default to 40 root LP rounds, 8 polish rounds, 80 dive nodes,
12 pure-anchor rungs and 6 LNS iterations. Restricted CP uses one worker,
10,000 conflicts and one deterministic-time unit. The generous page safety
stop is 4 * max(900, budget_s); work counts define search schedules. Native V4
and the inherited small ALNS floor remain wall-clock limited. Arrival maps
bypass search/fusion/classes families that cannot preserve arrival prefixes.

| Engine files | Provenance / integration |
|---|---|
| `bounds/exact/*.py`, `bounds/certified.py` | c3/c2-hatch certified relaxation, first-aboard order, heat/E6/OOG/securing cuts and replay/mask witness repair |
| `bounds/physical_capacity.py`, `port_capacity.py`, `capacity.py` | c3/c2-layout physical/E6 and port Hall bounds; optimistic DG eligibility, private bound-keyed E6 reuse |
| `scout/mixed_pages.py`, `mixed_oracle.py`, `physics.py` | c2/fx-scout mixed 20-under-40 pages, per-stack sight roofs, OOG reservations |
| `scout/closed_pages*.py`, `lns*.py` | c3/c2-layout unused covers, capacity screens, fixed-charge CP/LP masters, warm polish |
| `scout/advanced_pages.py`, `layout_work.py` | Local fixed-work glue, additive pure anchor, all-candidate retention and checked warm polish |
| `benders/optimize.py`, `permute.py`, `physics.py`, `seed.py`, `area_securing.py` | c3/c2-benders real area/securing and cargo assignment search, made cold and engine-local |
| `layover/*.py` | c3/c3-layover page prefixes and arrival bound, exact arrival-action retention and honest certificate labels |
| Other retained files | C1/C2 copies and their tests retained; source comments and NOTES-0.11.0.md record provenance |

Each copied source has a one-line provenance comment; its tests are kept.
Further details: [bounds notes](bounds/NOTES-0.12.md),
[Benders notes](benders/NOTES.md), [arrival notes](layover/NOTES.md).
The entry points are `planner.plan_and_check`, `lab_method.propose/finish`, and
`python -m experiments.mvp.frontier.engine.planner <instance.json>` (see --help).
The adapter reuses the engine's finished plan; the scorecard independently checks it.

## Review fixes and regressions

| Review item | Disposition and regression evidence |
|---|---|
| P1 constructor/search exceptions lose a pass | Closed: optional stages record errors and preserve the checked incumbent; partial StartBank and final pipeline guards recover retained candidates. Callback-then-error and search-then-error regressions. |
| P1 securing filtering / two-hopeful cap | Closed: all replay-complete ordered candidates survive both legacy and modern routes, including securing failures and a sole viable third hopeful. Native replay/checking remains required. |
| P2 alternate load orders deduplicated | Closed: archive and portfolio keys include complete ordered actions rather than final placements. A regression makes one order fail intermediate stability and its alternative pass at the same final stow/cost. |
| P2 0.1 s authorizes 20 s | Closed: absolute deadline is captured before model construction and each legacy rung clamps to actual remaining time. Tests cover expired, 0.1 s, below-20 s and larger remainders. |
| F2 load-dependent medium search | Implementation closed: fixed LP/node/rung/LNS work, generous outer stop, default >=900 s. Full requested stress qualification remains open because ps is denied; exact-repeat evidence and limitation below. |
| F7 DG unary filter underestimates eligibility | Closed: preserve optimistic DG footprint widening in restow, certified and aggregate/capacity bounds before reading T.static; private relaxed tables leave native tables untouched. Guard/superset regressions cover these paths. |
| F11/F13 proof scope and unnamed bounds | Closed: strict equality only after a full checker pass; `proved_optimal` is accompanied by a string `proof_scope` naming the producer and D15 fixed-stow/no-later-loads scope. Counting-only fallbacks are labelled honestly. |
| Arrival archive drops actions | Closed: load, discharge and temporary-removal actions are validated separately and included in ordered keys; arrival-prefix/archive regressions. |

The exact proof wording is: **fewest weighted lifts among plans the unchanged
checker passes; later ports use D15 projection with a fixed stow and no later
loads**. Exact-model guards include supported rule mode/parameters, no arrival
for empty-ship bounds, valid discharge/forecast structure, finite nonnegative
weights and supported geometry. Unsupported inputs fall back to sound weaker
bounds; model incumbents, partial enumerations and generated-pool objectives
are never substituted for certified lower bounds.

Review regressions are in `test_review_fixes.py`; work caps, additive anchor,
guidance seed preservation and real departure tanks in
`scout/test_advanced_pages.py`; exact/DG/Hall guards in `bounds/`; arrival scope
in `layover/test_bound.py`; load evidence and exact-order comparisons in
`bench/test_reproduce.py`; fixed fresh-control selection/admission in
`bench/test_quick012.py`. `test_e9fix.py` keeps checker/_parameter isolation
and corrected-mask/placement_violations comparison on empty and partially
loaded ships over three OOG maps, including sim.small.40100034. There is no
remaining disagreement reported by those differentials.

Logic review F1/F5 is addressed by the fresh control and unequal-effort caveats;
F3/F4 by distinguishing E9 fixes, complete stows and full passes; F6 by lift
parts and D15 scope; F8/F10 by V6 selection/feasibility labels; F9 by development
labels; F12 by removing the forced-relocation claim. No historical headline or
sealed-test adoption claim is made. The requested external-load part of F2 is
the one open verification item, not silently marked closed.

## Observed contribution table

All switches were enabled on all 21 quick maps. Activity/candidate/pass counts
are execution evidence, **not isolated ablations**. The nine page features
share observations and overlap; do not add their pass counts. An archive item
is a distinct ordered replay sequence, not necessarily a distinct final stow.

| New switch | Activation and candidate evidence in these 21 rows | Full-check outcome / attribution limit |
|---|---|---|
| `certified_bound` | Executed on all 18 tiny/small maps; 22 logged lazy cuts: 9 E6 column, 11 pinned pure-long rod-column, 2 pinned whole-stow securing cuts. | All 18 final passes equal their bound with the recorded scope. Bounds themselves never accept plans. Scope: fewest weighted lifts among checker-passing plans, later ports under D15 fixed stow and no later loads. |
| `certified_start` | Witness expansion ran on 18 maps: 28 expansion attempts, 27 replay-legal candidates on 16 maps; two maps had no usable complete relaxation witness. | 15 final winners came from `certified-0` (8 tiny, 7 small). Three small winners came from ranked fallback. Candidate generation is not acceptance; all winners passed the full checker. |
| `arrival_pages` | No arrival-page activation or candidates: all 21 rows log zero arrival replay stows. | Not exercised on this quick snapshot; no pass contribution established. Separate layover tests are not quick evidence. |
| `arrival_bound` | Enabled but no arrival-bound path exercised in these 21 empty-arrival maps. | Not exercised; do not turn the absence of activation into a failed bound or zero baseline. |
| `mixed_pages` | Three medium portfolios schedule six mixed-model design trials. Four D192 trials yielded legal replay/four checks; the two D185 designs were capacity-screened before construction. The constructed D192 models exposed 36/62 mixed pages respectively. | One checked candidate pass (154 lifts on the 162-box map), no final mixed-family winner attribution. Available mixed pages do **not** establish that a selected stow actually used a mixed stack; that usage was not logged. |
| `sight_roofs` | Enabled in all three modern medium portfolios; 46 modern design trials use the reference/roof path (15 + 16 + 15). Fifteen D185 trials are capacity-screened; the D192 trials produce 13 checked candidates. | Seven candidate passes within those trials, overlapping mixed/closed-cover families. No roof-only ablation or independent roof gain is measured. |
| `oog_reservations` | Enabled in three medium portfolios. The 88-box construction rows reserve zero boxes; 16 construction rows on the 162-box map report two reserved boxes each (the same two boxes across models). D185 designs are screened before construction telemetry records reservation counts. | Six passing candidate checks occur in the reservation-enabled 162-box modern design family. This does not isolate a reservation effect or mean 32 distinct boxes were reserved. |
| `unused_covers` | 48 logged nonempty-`closed_hold` trial records: 34 direct closed-design trials and 14 closure-LNS records. Eleven D185 closed designs are capacity-screened. Six legal replay/check events, all on the 162-box map. | Three candidate passes from direct closed designs; closure-LNS itself has zero passes. These records overlap `fixed_charge_lns`, roofs and reservations. |
| `fixed_charge_lns` | Executed on two of three medium maps: 14 trial/repair records (6 on 88 boxes, 8 on 162); three replay-legal candidates/checks, all on 162 boxes. D185 has no checked incumbent, so starts no LNS trial. | Zero checked passes from this cold LNS lane. Restricted-pool failure/timeout is not map infeasibility. |
| `capacity_cuts` | Enabled in all three medium design queues; 46 selected-family design screens, 15 logged rejections, all on D185. | The 15 rejected D185 designs explicitly log `certified_map_rejection=false`; they reject selected page families only. D192 has zero screen rejections. The map-bound route independently reports 65 from `two_port_hall`, 90 from `aggregate`, and 32 from `physical_stack_e6`; these are lower bounds, never rejection gates. No whole-map infeasibility or causal pass gain follows. |
| `fixed_charge_lp` | Executed on the two D192 maps; 50 logged LP records across cold LNS/polish and post-finish polish: 40 OPTIMAL, 10 INFEASIBLE. 32 records are before final finishing, 18 after. | The associated neighborhood records contain 37 candidate-check events and eight successful checker events, all in warm-polish lanes; none is a stand-alone LP acceptance or causal LP gain. Generated-pool LP objectives/bounds are not global lift certificates. |
| `warm_polish` | Cold portfolio: 18 logged warm-polish trial/repair records, 17 replay candidates/checks, four pass events. Post-finish: 18 more trial/repair records, 17 checked candidates, four pass events. | D185 starts no warm-polish work because it has no checked incumbent. The engine-owned finished-plan reference is used, never a stored recipe. Best checked lifts remain 71 and 104 after post-finish polish, so this snapshot shows no further lift improvement from that lane. Counts exclude unseparated construction-internal polishing work. |
| `work_count_search` | Activated on all three medium maps: 36 pure-anchor rungs, 46 modern designs, plus reference/LNS/polish work. Main ledgers contain 117 trial/repair records (40 + 49 + 28). All three main records and both applicable post-finish records say safety deadline not hit. | Work caps: LP rounds 40, polish rounds 8, dive nodes 80, CP conflicts 10,000, deterministic CP work 1, layout rungs 12, LNS iterations 6. Repair records can exceed the neighborhood count. D192 full passes are 71 and 104; D185 has no pass. Exact F2 repeat evidence is separate, and external-load qualification remains pending under ps EPERM. |
| `area_securing` | Nine **executed cold** route calls on seven small maps; eight additional eligibility records were skipped, including the D185 fixed stow. Nine real-area ballast searches ran. | One full-check lane pass: `random.small.40100011.0.8`, certified candidate, 22 lifts. It is an actual route success, not proof that an ablated/native lane would fail. Eight other calls stayed unknown/rejected and fallback continued. None of the three medium maps executed this route; D185 also has draft/trim/visibility failures and a fixed-stow securing certificate, detailed below. |
| `area_permutation` | Eight actual permutation API attempts on six small maps, nested inside the area route. Seven reached an assignment solver; one rejected a seed outside the pure-page family. Two attempts on `sim.small.41100007` produced legal 25-lift candidate stows, each moving 18 boxes. | Both candidates failed their full check; zero permutation passes. The two candidates are events, not an asserted count of distinct assignments. Five other solver attempts retained no incumbent. The top-level `area_permutation` record only says enabled, so these counts come from nested route metadata. |


Of the 20 final winners, finishing lanes are direct check 10, local ballast LP
4, inherited 4, cold real-area rescue 1, and unchanged V4 1. Counting all
successful attempts before supersession gives 25 events, not 25 distinct maps.
There are 15 certified-start winners and three ranked fallback small winners:
random.small.41100018.0.2 (10), sim.small.40100034 (10), sim.small.41100007 (8).
The two D192 portfolios keep 55 distinct ordered replay sequences, 17 of them
passing a full check. No top-level or pipeline errors are logged in quick.

On 162 boxes the pure anchor reaches 116; unused covers HC002P, HC002S and
HC010P produce 104, alternate designs 106/112. A mixed-family candidate passes
at 154, with selected mixed usage unlogged. Closure-LNS candidates do not pass;
warm polishing passes but does not beat 104. On 88 boxes the pure anchor
reaches 71, extra families do not improve it. Post-finish polish improves
neither final plan. These are concrete observed paths, not ablation effects.

## Why D185 remains a cold failure

D185 takes **550.990 s** with scorecard `passed=false`, `lifts=null` and
`status=no_candidates`. Internally it has one complete replay-legal candidate
with objective 114. That is **not** the warm C3 114-lift passing recipe, and
not a new passing 114-lift result. The adapter returns only finished passes,
which explains the outer status despite an internal complete stow.

The source-pinned securing screen certifies a minimum rod ratio
**1.130735662784186 > 1** for every ballast setting of that fixed stow.
Its real full-check hard failures also include containership second check,
draft, trim and visibility. The new rescue route accepts only actual
area/securing failures, so draft/trim/visibility make this stow ineligible:
**zero executed D185 area or permutation calls**. The fixed-stow securing
certificate skips unchanged-stow LP/inherited/native V4 lanes. It rejects the
arrangement, never the whole map. A broader cargo-plus-ballast search could
still find a different arrangement; cold engine search did not reach the
warm targeted C3 basin. The code remains enabled and demonstrably cold on
engine-owned small stows (one 22-lift area-lane pass).

Twelve anchor rungs keep uncovered fractional cargo around 12.5. Fifteen
modern selected-family designs, including two mixed and eleven closed-cover
designs, are capacity-screened. The reference learns one securing cut; its
bounded repair adds 730 columns and remains UNKNOWN. With no checked
incumbent there is no LNS/warm lane. None of those restricted outcomes proves
map infeasibility. The map lower bound stays 32 (`physical_stack_e6`).

## Integration bugs fixed and limits

Besides the four review counterexamples, fixes include a securing/area circular
import, None sequencing after failed assignment, partial Hall enumeration
mistaken for a certificate, stale diagnostic evidence becoming cuts, and E6
cache reuse without exact instance/table/rule identity. Partial/error outcomes
now remain Unknown; serialized fake evidence cannot populate private caches.
Medium smoke fixes restored the initial bow target, retry after learned-cover
failure, and a pure anchor accidentally inheriting mixed reservations. Warm
polish now uses actual checked departure ballast: native apply_plan alone
leaves arrival tanks. Guidance/LP-rejected complete page seeds are replayed and
retained on private columns/pools without changing main RNG/work counts.
Counting-only fallbacks no longer claim certified/arrival packing provenance.
Each behavior has a regression. Changed diagnostic consumers use full native
fallback rather than blindly refreshing source pins.

Keeping every cheaper candidate is expensive. sim.small.41100007 takes
**1,996 s (33.3 minutes)**; sim.small.40100034 takes **1,210 s** while preserving
a checked incumbent during failed cheaper finishing. The 88-box quick map
takes **2,747 s (45.8 minutes)**. In the first cold repeat, three cheaper
63-lift E6-failing stows spend **2,229.50 s** in inherited/native V4 (three
120-second and three 600-second timeouts), before a 71-lift plan passes in
5.90 s. Those timeouts remain Unknown, not infeasibility. A verified E6-only
fixed-stow finish screen is future work; no untested screen was added here.

`stages.finish.wall_s` measures the first additive block, not total finishing.
Sum `tries[*].engine_finish_s` for aggregate finishing work. Launcher elapsed,
map wall time and overlapping worker sums are different metrics. Some legacy
0.11 contribution keys are absent in the new layout dictionary, making their
top-level zero a missing-key default, not evidence of inactivity. The table
uses raw options, RMP histories, trials and concrete check events instead.
Available mixed-page counts do not prove selected mixed-stack usage.

Mixed pages currently cover standard-height 20-foot bases under a 40-foot
upper stack. Pure fixed-charge masters refuse unsupported mixed families.
Arrival starts/bounds have separate tests but quick has no onboard arrivals,
so this run gives no arrival pass-rate evidence. Outer safety stops and native
finishing wall clocks can still cause load dependence beyond these two medium
maps. Fixed-work reproducibility is not claimed for all cohorts.

Cycle 2 discrepancy rounding remains excluded: no checker pass. C3 D185
feasibility relaxation remains excluded: it certified no map infeasible.
Conservative identical-attribute exchange remains; mass-swapping exchange stays
out as previously directed. Stored recipes are not runtime inputs. Earlier
cycle decisions/provenance remain in NOTES-0.11.0.md.

## Verification, source identity and reproduction

The post-measurement required engine+lab suite passed **397 tests in 153.03 s**,
including the now-active measured quick/bound evidence assertions:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
.venv/bin/python -m pytest experiments/mvp/frontier/engine \
  experiments/mvp/frontier/lab/tests -q
```

The recorded run used importlib collection, the repository's default exclusion
of slow/gpu/net markers, engine-local pytest temp/cache and disabled bytecode
writes. See [test-0.12.0-post.log](test-0.12.0-post.log). Earlier source-ready
run: 395 passed, two measured-evidence skips, 132.44 s. No Python edits followed
the measured source freeze. All **143 engine** and **257 native/frozen** pinned
Python files remain unchanged. The completed quick/fresh controller separately
reports `source_unchanged=true` against its launch snapshot/control bindings.

Every quick row has raw engine source SHA
`b480c9621f46fb907e6a85581c988c908b43c24d63fd7df6eaf28e93d4617c45`
and adapter SHA
`9df9835ac100d78fbc0b2b6e57ae9d10f28e449aaf5ff743e7e3ce0af9f115af`.
Normalized bundle digest is
`d57b7ccff3ab63d0448bbe49feb48798763792e46527083b4ac37eb00c57ab38`;
raw and normalized hashes use different conventions and are not interchangeable.
Source manifests and audits are retained under engine/ and results/.

All CPU work respected the marker ceiling (one while BENCH-RUNNING existed,
then at most four); each numerical process uses one thread. Long jobs requested
nice 5 but managed setpriority was denied, so effective priority is not claimed.
Only recorded owned PIDs are managed. No external load burners were created.

<!-- F2-RESULT-BEGIN -->
The frozen-artifact two-worker arm and its second unverified-load repeat both
produce full passes at **71 lifts (88 boxes) and 104 lifts (162 boxes)**, with
**identical ordered actions and full stow hashes on both maps**. Official quick
independently matches the first arm's same actions/stows. The common frozen
artifact is `2ce31d4c6b6c2c3443b7d969fc2ed81b558e29718b9fb3e19ba018fcae4d61fe`.

| Map boxes | First two-worker arm | Second repeat | Exact action and stow match |
|---|---:|---:|---|
| 88 | 2827.88 s | 2822.44 s | Yes |
| 162 | 1913.34 s | 1869.45 s | Yes |

Both worker pairs exited zero and all frozen-source, native-source, parameter
and input bindings verified unchanged before/after the arms. All medium main
search records avoid their outer safety stop. This exact repeat covers these
two maps only, not a general reproducibility guarantee.

The required **12 other busy processes** stress qualification is **open**.
Launching ps is rejected with EPERM in this managed sandbox. Load-average
proxies cannot establish twelve identified busy processes, and launching twelve
burners would violate the four-process ceiling. The second arm explicitly has
`unverified_load=true`; comparison retains `status=pending`,
`load_requirement_met=false`, `exact_repeat_observed=true`. Harness exit 2
means unqualified load evidence, not divergent stows or a worker failure. This
is the remaining verification limit, not a successful stress test.
<!-- F2-RESULT-END -->

Reproduction freezes source/native/parameter/input identities before both arms
and checks them again afterwards. Its process evidence and final comparison
live in [repro-0.12.0](results/repro-0.12.0/).
[quick-vs-repro-first](results/quick-vs-repro-first-0.12.0.json) records the
independent first-arm comparison.

To close the remaining qualification on a host with process inspection, use a
fresh engine-local output and naturally existing load, without the unverified
flag, for example:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
.venv/bin/python experiments/mvp/frontier/engine/bench/reproduce.py \
  --out experiments/mvp/frontier/engine/results/repro-0.12.0-qualified \
  --wait-for-load-s 3600 --min-external-busy 12
```


Primary artifacts: [quick rows](results/quick-0.12.0/),
[fresh E9 rows](results/fresh-e9-quick-0.12.0/),
[controller](results/quick-0.12.0-controller.json),
[all-stage analysis](results/analysis-0.12.0.json),
[source audit](results/final-source-audit-0.12.0.json),
[final verification](results/final-verification-0.12.0.json).

### Named bounds on every quick map

Every true proof below has exactly the scope stated in the review section;
the producer is part of both its bound record and proof_scope. Medium bounds
are gaps only. A null checked cost remains null.

| Map | Full checked lifts | Lower bound | Producer | Scoped equality proof |
|---|---:|---:|---|---|
| `D192.medium-feeder_500-f60-high-s192200000` | 71 | 65 | `two_port_hall` | No |
| `D192.medium-feeder_800-f60-low-s192200101` | 104 | 90 | `aggregate` | No |
| `random.medium.185100000.0.2` | null | 32 | `physical_stack_e6` | No |
| `random.small.40100011.0.8` | 22 | 22 | `certified` | Yes |
| `random.small.40100023.0.8` | 22 | 22 | `certified` | Yes |
| `random.small.40100036.0.2` | 9 | 9 | `certified` | Yes |
| `random.small.41100002.0.2` | 12 | 12 | `certified` | Yes |
| `random.small.41100018.0.2` | 10 | 10 | `certified` | Yes |
| `random.tiny.20000006.0.5` | 8 | 8 | `certified` | Yes |
| `random.tiny.20000022.0.2` | 7 | 7 | `certified` | Yes |
| `random.tiny.20000032.0.8` | 8 | 8 | `certified` | Yes |
| `random.tiny.20000042.0.5` | 6 | 6 | `certified` | Yes |
| `sim.small.40100010` | 4 | 4 | `certified` | Yes |
| `sim.small.40100025` | 8 | 8 | `certified` | Yes |
| `sim.small.40100034` | 10 | 10 | `certified` | Yes |
| `sim.small.40100046` | 9 | 9 | `certified` | Yes |
| `sim.small.41100007` | 8 | 8 | `certified` | Yes |
| `sim.tiny.20000007` | 4 | 4 | `certified` | Yes |
| `sim.tiny.20000023` | 4 | 4 | `certified` | Yes |
| `sim.tiny.20000033` | 6 | 6 | `certified` | Yes |
| `sim.tiny.20000043` | 8 | 8 | `certified` | Yes |
