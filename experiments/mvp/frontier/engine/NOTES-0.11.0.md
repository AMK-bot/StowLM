# Frontier Engine 0.11.0

This integrates the kept Cycle 2 work into the cold-start engine. The original
0.10.0 notes are retained in `NOTES-0.10.0.md`; its quick rows remain unchanged.
Only `engine/` is writable. The public full checker remains the only pass gate.
No sealed data, cloud, GPU, source/parameter edits or full benchmark is used.

## Compute and measurement rules

While `lab/c1/leader/BENCH-RUNNING` exists, the team runs at most one CPU-busy
process. After it disappears, the authorized cap is four. Every numerical
process uses one thread, long jobs request nice 5, and the vessel cache is scoped
to `engine/results/vessel-cache`. The mandatory quick run waits for the marker
to disappear. It may exceed twenty minutes because it retains all 21 fixed maps,
the 0.9.0 small-map construction/search floor, additive work and unchanged full
finishing/checking. No map is dropped for speed. No unrelated process is stopped.

## Pipeline and switches

All switches are fields of the frozen `Config`; legacy stage switches remain.
`budget_s` continues to buy additive work on <=60 boxes, preserving historical
`min(120,max(20,6*n_loads))` ALNS seconds and at least 300 native V4 seconds.

| New switch | Role | Proof/acceptance scope |
| --- | --- | --- |
| `restow_bound` | Strongest certified old hatch / new restow-closure bound within one allocation | Map lower bound only; exact full-pass equality required |
| `witness_start` | Repair the optimistic assignment into a legal stow; restricted chain candidate fallback | Heuristic proposal, real replay and full gate |
| `layout_search` | Normal >60 route searches port/cover designs | Generated page family only |
| `asymmetric_deck` | Early deck cargo may lie above later hold cargo | Construction rule, unchanged checker |
| `oog_repair` | Validate both swapped boxes and affected columns | Proposal repair only |
| `unary_cache` | Reuse exact state-free eligibility in one Problem | Copy ownership, no unlike-state reuse |
| `medium_bound` | Aggregate region/cover/port allocation bound | Map relaxation, outward rounding |
| `fast_pricing` | Cached pricing and incremental sparse master | Concrete identities and original per-column gates |
| `aggregation` | Equal-weight tier batching in pricing | No mass rounding or merging unlike boxes |
| `restricted_master` | Integer feasibility solve over generated legal columns | Restricted-pool feasibility only |
| `cached_mask` | Prefix cache and singleton owner-engine probes | Guarded legacy domain; native fallback elsewhere |
| `diagnostic_screen` | Partial exact learning flags, full fallback on possible passes | Never returns an unchecked pass |
| `securing_cuts` | Forbid complete pure-page choices preserving a securing contradiction | Exact fixed-instance certificate; no map infeasibility |
| `ballast_lp` | Local hydro/tank LP proposes a ballast recipe before native V4 | Guidance only, full checker on every candidate |

The 0.10 page ladder remains available with `layout_search=False`. Layout models
have a `page_model_factory` seam and separate verdict/feedback hooks for the
forthcoming mixed-pattern/per-bay-sight work. No unfinished scout code is used.
The LP is a tangent approximation: infeasibility and Farkas diagnostics are
unknown physically. Native V4 keeps its entire budget after failed extra lanes.

## Provenance

Every copied Python file starts with its original path. `bounds/hatch_restow.py`
and witness repair come from `c2/c2-hatch`, with the copied chain dependency
from `c1/fx-chains`. `scout/layout.py`, layout permissions, OOG repair, cache and
aggregate bound come from `c2/c2-layout`; fast pages and restricted master come
from `c2/c2-cgscale`. `gates/` copies `c2/c2-gates` mask and diagnostic code.
`benders/` copies the local LP and securing-cut machinery from `c2/c2-benders`.
Their tests are retained with engine-local imports. No runtime imports depend
on mutable experiment implementations. Prior-cycle helper provenance remains
in `NOTES-0.10.0.md` and each copied file.

Exact identical-box class aggregation, smoothing and separation were not
implemented by c2-cgscale and are not invented here. The `aggregation` switch
names its implemented exact equal-weight batching. `c2-discrep` is left out as
ordered. Pure-page securing cuts reject mixed, holed, duplicated, overlapping
or incompletely covered selections, and refuse a cut if OOG/balance repair has
changed the certified assignment. This can lose a cut opportunity safely.

## Integration fixes and open reviews

- The Cycle 2 standalone Benders master ignored `model.banned`; the engine master
  enforces those bans and verifies every returned selection respects prior cuts.
- A supplied ballast recipe that fails direct checking could prevent native
  finalize from searching. Extra lanes, V4 and the acceleration-exception fallback
  now clear that rejected recipe while retaining the exact same cargo moves.
- Retaining failed guidance initially displaced existing hopeful stows; the
  candidate union now preserves every original hopeful/pass candidate.
- Balancing after a page-cut repair could recreate a prohibited choice. The
  balanced selection is checked against all cuts and otherwise reverts to the
  valid raw replacement.
- Cached masks explicitly fall back on untested nonempty/off-cover arrivals,
  future loads, forecasts, or changed instance/table identities.
- Incomplete optimistic witnesses now return no candidate instead of indexing
  missing box IDs.
- Copied package imports and source-pin roots are engine-local and stable under
  the lab runner's generated plug-in name.
- Initial source collection found an indentation error introduced during merging
  the fast unary cache; it was corrected before numerical execution.

Legacy DG empty-ship unary eligibility can exclude a legal supported upper
placement. New bounds widen every DG row to all enumerated footprints of the
correct length before solving. This drops restrictions and therefore enlarges
the feasible set; it never tightens a bound using the suspect probe. The native
tables remain unchanged. Superset, nonmutation and 20/40/45-foot footprint tests
cover this correction. The old bound keeps its prior conservative guard.
`proved_optimal` still requires exact equality to an applicable map bound.

The layout route can retain up to two distinct replay-legal failures cheaper
than its best pass for LP guidance and native finishing. They never count as
full passes. Without this seam, the page ladder discarded the failed stows that
the extra ballast lane was intended to repair.

## Verification and quick results

Final command: `.venv/bin/python -m pytest experiments/mvp/frontier/engine
experiments/mvp/frontier/lab/tests -q`, with the repository's normal marker
selection, import mode and an engine-local pytest cache. **199 passed in 42.44 s**.
This includes copied suites, scoped E9/checker isolation, native OOG comparisons
on three real maps (empty and partially loaded), and both measured medium
regressions. Every reported map bound is checked against both versions' passing
upper bounds. The two result-based regressions are no longer skipped.

The fresh cached-mask differential completed **2,220 comparisons, zero
disagreements**, including the 88- and 162-box maps. Repeated cases and fallback
assertions are disclosed in `results/mask-differential-0.11.0.json`. The final
source audit finds all 257 native files unchanged and all 84 measured engine
Python files unchanged. No checker/rule changes were made outside scoped E9.

The full fixed `quick` ran after the benchmark marker disappeared, with two
workers, seed 0, proposal budget 375 s, finalize 600 s and three scorecard tries,
matching 0.10.0's settings. Outputs are `results/quick-0.11.0/`; comparison and
per-map stage evidence are `results/quick-comparison-0.11.0.json` and
`results/stage-contributions-0.11.0.json`. Source identities are retained in the
versioned manifests. The adapter bundle digest is
`5052f8fe26476b4c1d15873810ca4e1ead4bd566e60b22d546f4bffceca3b873`.

**21/21 maps obtained a complete replay-legal stow; 20/21 passed the full gate.**
Whole elapsed time was **1625.75 s (27.10 min)**, against 2262.58 s (37.71 min)
for 0.10.0. The 901.32 s spent waiting for the leader is excluded from run time.
No full benchmark was started. Priority adjustment was requested with nice 5;
the sandbox denied that adjustment and continued the jobs normally.

| Size | 0.11.0 full passes | Mean lifts vs 0.10.0, mutual passes | vs stored 0.9.0 |
| --- | --- | --- | --- |
| Tiny | 8/8 | 6.375 vs 6.375, 8 ties | 8/8; 6.375 vs 6.375 |
| Small | 10/10 | 11.50 vs 11.50, 10 ties | 9/10; 11.667 vs 13.889 on 9 mutual passes |
| Medium | 2/3 | 93.50 vs 131.50, 2 wins | No measured 0.9.0 rows; unknown |

The 88-box map passes at **71 vs 79**, and the 162-box map at **116 vs 184**.
Both also improve on C1 scout quick-3's 79/184. Medium mean savings are 32
projected restows plus 6 later hatch lifts; mean relocations (65.5) and current
hatch lifts (14) are unchanged. On two paired medium passes the bootstrap 95%
interval for candidate-minus-0.10.0 lifts is [-68, -8]. Small versus 0.9.0 is
three wins, six ties, zero losses; paired change -2.222, interval [-6, -0.111].
These are map-bootstrap development intervals, not holdout generalization claims.

D185 has a complete 118-lift stow, but a securing certificate rejects that fixed
stow independently of tanks. It remains a failed map, and does not contribute
118 to any passing-plan mean. This is not whole-map infeasibility.

## Observed stage contributions on quick

Counts describe work and archive provenance in this one default run. No
per-switch causal ablation was run. Origins can overlap; they must not be added
to obtain distinct global stows or credited as independent lift savings.

| Stage / switch | Observed contribution |
| --- | --- |
| `e9_fix` | Scoped installation on all 21 maps; OOG regression `sim.small.40100034` retains its 10-lift full pass |
| `bound`, `restow_bound` | Restow certificate beats hatch-only on 10 maps; all new bounds beat 0.10.0 on 13 maps including medium; 14 exact full-pass optimality proofs vs 5 in 0.10.0 (7 tiny, 7 small) |
| `witness_start` | 34 proposals; 11 distinct archive stows carry witness provenance; no separate lift gain claimed |
| `layout_search` | 15 replay-complete stows and 4 checked passing stows across the 3 medium maps; selected passes 71/116 |
| `asymmetric_deck` | 68 no-restow layout trials including LP-screened trials; the 116-lift passing design uses asymmetric permission |
| `fast_pricing` | Fast implementation selected for all 3 medium maps, 82 recorded reference/layout model rows; no isolated pricing speed claim |
| `aggregation` | Exact equal-weight tier batching enabled in the same fast models; no independent DP batching counter or quality claim |
| `unary_cache` | 12 recorded one-stow cache hits; screened rows omit this counter, so this is a lower count than all model reuses |
| `oog_repair` | Enabled and tested; zero boxes moved by its extra swap repair on quick |
| `medium_bound` | Applicable map bounds 51, 90, 30; passing gaps 20 and 26, no medium optimum claim |
| `restricted_master` | 13 recorded root master calls; feasibility is restricted to generated columns |
| `cached_mask` | 62,821 calls; 37,251 prefix hits, 239,400 actions reused, 24,903 rejected-prefix hits; zero domain fallbacks on this run |
| `diagnostic_screen` | 15 ladder verdicts: 10 partial failures and 5 full fallbacks; possible passes always fully checked |
| `securing_cuts` | 2 certified page no-goods and model-local repairs; one balancing result reverted because it recreated a forbidden choice |
| `ballast_lp` | Zero calls on quick: ordinary lanes pass, or a prior necessary securing proof rejects. No measured LP rescue claimed |
| Legacy `search` | Ran on 9 maps; 46 archive stows carry ALNS provenance |
| Legacy `fusion` | Ran on 12 maps; 131 archive stows carry cycle/fusion provenance |
| Legacy `classes` | Ran on 12 maps; 12 archive stows carry conservative class-exchange provenance |
| `finish` | 40 attempts: 13 direct full passes, 25 inherited-tank full passes, 2 necessary fixed-stow rejections; no separate LP or unchanged V4 fallback stage needed |

## Limits and review focus

This is a single-seed development quick run on a shared machine. Timings do not
isolate individual switches. LP guidance remains covered by unit/integration
checks but produced no quick rescue; local LP infeasibility is never a physical
certificate. The unchanged V4 fallback remains last with its full original budget. The
inherited lane retains its existing accelerated worker, which can itself call
the native V4 solver; the lane counts above describe engine stages rather than
all internal solver calls.
The needed bounds review should examine projected restow closure, outward
rounding and optimistic DG footprint widening. Page cuts remain restricted to
complete pure pages; mixed-pattern support must supply compatible certification
or use the conservative refusal seam. Some layout model setup/physics calls use
cooperative deadlines and can overrun a time allowance.

Exact box-class aggregation, smoothing and separation absent from c2-cgscale are
left out; its implemented exact tier batching is retained. `c2-discrep` is omitted
as ordered. No unfinished fx-scout work is compiled: mixed patterns and per-bay
sight have explicit factory/verdict/feedback seams for the promised follow-up.
Only the leader may run sealed data. No files outside engine were written and
no commits were made.
