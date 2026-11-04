# Cycle 2: column-generation scaling

Scope: all new files and results are confined to this directory. `pages.py` is copied from `lab/c1/fx-scout/pages.py`; `method.py` adapts that experiment's ladder. The checker, source engine and shared scorecard are imported unchanged. No sealed data is read. The process-local Cycle 2 E9 mask correction is installed.

## Model and assumptions

A ship stack is a page, each column is an ordered legal pure-length stack pattern, and the master covers cargo while reserving each physical stack at most once. Yard relocations are measured by real move replay; hatch and restow lifts are measured by the unchanged full checker. The real small and medium examples and graph interpretation are those documented in `c1/fx-scout/GRAPH-MODEL.md` (18 and 121 load boxes respectively).

The intended pattern family, rounding resolution and per-column legality tests are unchanged. Exact aggregation requires equal pattern-relevant attributes, including mass, height, discharge port and static eligibility. Similar boxes are not assumed interchangeable. An LP pricing stop remains a statement about the fitted pure-stack family, never a whole-map infeasibility proof.

## Compute and measurement plan

At most two CPU-busy processes across this team; each uses `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`, `PYTHONDONTWRITEBYTECODE=1`, and long jobs use `nice -n 5`. Benchmarks and the scorecard use one worker each, concurrently only after implementation tests release the second slot. We stop only our recorded process/session IDs.

Vessel-cache writes are directed to `results/vessel-cache` by `STOWAGE_VESSEL_CACHE_DIR` in the reproduction script and current score command. Earlier commands used existing default-cache hits before this implicit library-cache setting was noticed; a modification-time audit found no recent writes to that outside-scope cache. The score was resumed with the scoped setting and its already-complete row was reused.

The paired `medium12` benchmark gives both implementations 60 seconds for the phase-1 root and 60 seconds for phase-3 pricing plus diving, with seed 0. It records LP objective/uncovered, LP seconds, dive seconds, all generated-pattern legality, and complete replay legality. An aggregate run can exceed 20 minutes because it measures all twelve predeclared maps against both implementations; no individual map receives 20 minutes. A separate unchanged `lab/score.py --set medium12` run measures full-checker passes and reports those separately from replay-complete stows. Budgets and command lines will be recorded with the final results.

The original LP call itself has no time limit, so a call begun before the deadline can overrun it. The paired benchmark retains that behavior in both implementations. It also retains the original phase-3 polish allocation except when an integer incumbent already exists. The score entry point caps polish at 20% of the remaining first-stow time, gives a 428-box first stow 85% of the construction budget, and tries up to eight additional seeded best-fit ties within two seconds before diving. These are separately identified wrapper heuristics; benchmark timing is not silently substituted for scorecard timing.

After the raw original initializer on seed 185100210 was interrupted after **293.34 seconds**, the remaining original LP/dive measurements use `--static-adapter pages.py`: only its E8 initializer is replaced by the verified equivalent special-rule path. Original pricing, LP, and dive code remain unchanged. Adapter identity is recorded per row. Do not interpret adapted initialization or total wall time as the original end-to-end time. The first two raw 428-box baseline rows have no adapter.

## Final integrated RMP benchmark

The final `rmp_method.Model` completes **12/12 replay-legal stows**, including **all four 428-box maps**, against **8/12 and 0/4** for the original. Same predeclared `medium12` cohort, seed 0, 60-second root plus shared 60-second phase-3/dive allowance. All 121/229-box coverage objectives and uncovered counts remain **exactly zero**. All **114,951 final RMP patterns** and **320,487 original patterns** pass the original per-column legality and same securing checks.

| Measure | Original | Final integrated RMP | Speed-up |
| --- | ---: | ---: | ---: |
| Root LP stages, total seconds | 286.350 | 20.342 | **14.08×** |
| Dives alone, total seconds | 160.866 | 37.777 | **4.26×** |
| Phase 3 + dives, total seconds | 503.572 | 62.569 | **8.05×** |
| Four 428-box root stages, mean seconds | 61.943 | 0.779 | **79.47×** |

The full **per-map original/final LP and dive table** is [benchmark-rmp-timing.md](results/benchmark-rmp-timing.md), with machine-readable [benchmark-rmp-summary.json](results/benchmark-rmp-summary.json). Final root time **includes** restricted-master construction and CP-SAT, a total of 12.309 seconds over the cohort. Solver statuses: six `UNKNOWN` calls fall back to normal diving, one root pool is `INFEASIBLE` and is subsequently completed through more pricing/diving, one is feasibility-`OPTIMAL`; four maps already have checked first-fit incumbents and need no RMP call.

The root speed ratios compare bounded stages: the original four large roots still leave 19–42 uncovered boxes, while all final roots have zero. They are not claims that both versions reached the same cost-polished optimum. Zero-cost feasible roots and retained integer incumbents deliberately avoid unnecessary pricing/polishing. All timings are on the shared machine with the prescribed two-process and single-thread limits, not isolated hardware.

## Paired base-model benchmark results (before restricted master)

`medium12`, seed 0, all twelve maps measured. The complete per-map **LP, phase-3, and dive timing table** is [benchmark-timing.md](results/benchmark-timing.md); machine-readable results and per-map source hashes are in [benchmark-summary.json](results/benchmark-summary.json) and `results/benchmark-{original,optimized}/`.

| Measure | Original | Optimized | Speed-up |
| --- | ---: | ---: | ---: |
| All twelve root LPs, total seconds | 286.35 | 19.18 | **14.93×** |
| All twelve dives alone, total seconds | 160.87 | 86.14 | **1.87×** |
| All twelve phase-3 + dive stages, total seconds | 503.57 | 202.16 | **2.49×** |
| Four 428-box LPs, mean seconds | 61.94 | 1.87 | **33.10×** |
| Four 428-box phase-3 + dive stages, mean seconds | 67.14 | 16.82 | **3.99×** |
| Complete replay-legal stows | 8/12 | 11/12 | separate gate |
| Complete replay-legal 428-box stows | 0/4 | 3/4 | separate gate |

All eight 121/229-box maps have **exactly zero uncovered boxes and zero phase-1 objective** in both versions, passing the requested 1% regression with a numerical tolerance of `1e-6` at zero. Every generated pattern was rechecked with the original unmodified `Model.legal` and the same securing gate: **320,487 original and 134,994 optimized patterns, zero illegal**. This audit includes patterns added by balancing.

These are bounded construction-stage timings, not a comparison of optimal phase-3 objectives. A known integer incumbent deliberately skips cost polishing; the giant individual ratios for those near-zero dive times in the detailed table represent retrieving that checked incumbent. The cohort sums above are the useful timing claim.

The remaining 428-box benchmark failure, `sim.medium.185100210`, reached zero LP shortfall after 7.20 seconds. Its phase-3 polish then took 61.60 seconds, leaving no dive budget; the dive returned `left=428, reason=time`. That is a budget-allocation failure, **not** evidence of infeasibility. The restricted-master entry closes this gap below.

## Scorecard and independent replay audit

Both `lab/score.py --set medium12` runs and both fresh-process audits completed. Detailed per-map timings, RMP statuses, failure rules and source hashes are in [score-timing.md](results/score-timing.md) and [score-summary.json](results/score-summary.json).

| Entry | Construction budget | Complete replay-legal | 428-box replay-legal | Full-checker passes | Mean total wall/map |
| --- | ---: | ---: | ---: | ---: | ---: |
| `method.py` | 120 s | 11/12 | 3/4 | **0/12** | 105.160 s |
| `rmp_method.py` | 60 s | 11/12 | **4/4** | **0/12** | 53.756 s |

Construction budgets differ, so these wall times are **not a controlled speed-up comparison**. Both metadata claims match independent replay audits exactly: [base audit](results/score-base-audit.json), [RMP audit](results/score-rmp-audit.json). The final entry's four 428-box initial complete stows took **13.14, 1.76, 12.10, and 1.78 seconds**, excluding model initialization and subsequent full-checker/ladder work.

The final 60-second run misses `random.medium.185100100.0.2`: its zero-shortfall root's 4,658-column integer pool is infeasible (proved only for that restricted pool), then the first-stow budget leaves 22.17 seconds for diving; it stops on time with 160 boxes left. The 120-second base entry completes it, and the equal-budget RMP model benchmark also completes it after further pricing/diving. This is a first-stow budget allocation limitation, not evidence of a changed legality rule or whole-map infeasibility.

The full checker rejects every complete initial stow. Final-entry failed rules, by map: securing **11**, visibility **11**, draught **9**, E9 **5**, trim **5**, GM second check **4**, E6 **1**. Thus **no full-checker pass and no paired lift improvement are claimed**. There are no stored measured 0.9.0 rows on `medium12`, no mutual passes, and no paired mean lifts. Replay lift counts in the certificates are not substituted for passing-plan lift results.

The plain entry `method.py` is measured with construction budget 120 s, finalization budget 120 s, one try and one worker in `results/score-medium12/`. A separate, final entry `rmp_method.py` adds the bounded restricted integer master below and is measured with construction budget **60 s**, otherwise the same settings, in `results/score-medium12-rmp/`. Both use the unchanged `lab/score.py --set medium12`. Each returned passing candidate must still clear the runner's independent full checker. Replay certificates are saved even when physics rejects every plan; `audit_score.py` reloads those action sequences in a fresh process, checks real replay and confirms every load-list box is aboard, and independently rechecks any saved full-checker pass.

**Verdict: keep part.** Keep the pricing/master caches, equivalent E8 initializer, diversified legal seeds and restricted-master feasibility incumbent. They close the 428-box construction barrier without changing the pattern family or per-column gates. They do not solve whole-ship securing, sight, stability or draught. Next, give the remaining 229-box dive the unused construction time before abandoning the first stow, then hand the complete columns to physics-aware master/finisher work. Exact identical-box aggregation is deferred because this cohort keeps 80–90% of its rows even with exact classes; no smoothing algorithm is claimed.

## Closing the fourth 428-box map

`target210.py` first reproduced the plain entry's failure: root LP zero uncovered in **5.09 s**, followed by a 120-second first stow that left 303 boxes unfixed. The exact restricted master over the root's **11,152 legal patterns** then found a complete integer selection in **0.111 s**, with **zero** rejected columns under the original legality/securing gates. Balancing, clearance repair, ordering and replay took **13.36 s**. Fresh replay confirmed all **428 boxes** loaded, **111 patterns**, **499 replay lifts**. The actions, full plan and assignment are in [target210.json](results/target210.json). This establishes a construction on the fourth map; it is **not a full-checker pass**, and CP-SAT `OPTIMAL` here means feasibility objective zero, not optimal lifts.

The final `rmp_method.py` integrates that feasibility solve immediately after a zero-shortfall root LP when first-fit supplied no incumbent. It chooses only existing legal columns, covers each concrete box exactly once, reserves each physical stack at most once, hints near-integral root columns, and uses one CP-SAT worker with a **two-second total limit** including model construction. The original dive rechecks the resulting incumbent before real replay. This is the restricted-master heuristic motivated by Sadykov et al. (2019); the full published diving algorithm is not claimed. Four synthetic regressions verify stack conflicts, fractional-only infeasibility, banned columns/deadlines, and root-only invocation.

## Verification and entry points

- `results/verify-model.json`: **5,784 exact pricing pattern/value comparisons**, **8 identical-pool LP objective/uncovered comparisons**, and **10 exact heat-violation dictionary comparisons**, all passed.
- **41 tests passed** across the 25 shared scorecard tests, 3 wrapper tests, 5 benchmark-summary tests, 4 RMP tests and 4 score-summary gate tests. Shared tests include a real full-checker rejection of an illegal finished plan.
- Both twelve-map scorecard runs completed, both fresh-process replay audits matched all claims, and no plan was counted as a pass without the full-checker gate.
- Final entry: `rmp_method.py:propose`, with `rmp_method.py:finish`. `run.sh` records the scoped cache, single-thread environment, paired-stage budgets and final score command. `pages.py` and `method.py` remain independently usable for the ablation.

## Implemented ideas

The cold LP originally starts with only uncovered-box slacks. All those boxes have the same price, so pricing repeatedly chooses the same identities. Seed it instead with the original first-fit heuristic's fully checked patterns, retaining useful partial patterns even when that attempt fails. When first-fit supplies a whole legal packing, the phase-1 objective is already zero: its exact lower bound. Keep that integer incumbent for diving rather than rediscovering it through fractional columns. This changes the search path, not the allowed patterns. Ship replay and the full checker remain separate gates.

Pricing also caches immutable item weights/eligibility, batches equal-weight tiers, and removes the high-cube count dimension only when even an all-high-cube column fits the original height limit. The sparse master incidence matrix is extended for new columns instead of rebuilt from every old pattern. The ladder reuses the identical E8 static table within a Problem, transfers old columns only after the new rung's legality/securing checks, and caches a full-check result only for the exact same complete plan including ballast. The shared runner still independently checks any claimed pass.

The simulated-yard 428-box maps exposed another wall before the LP: the inherited E8 initializer invoked all placement rules for every heat-box/slot pair but retained only `away_from_heat` violations. In the current shared source, those unrelated rules repeatedly load and hash vessel source while checking background capacity. The new initializer calls the same checker's `special_violations` with the same geometry and usable-candidate preparation; only that function emits the retained rule. Nonempty/invalid-geometry cases keep the original fallback. The source checker and its rules are unchanged. Initialization is reported separately from LP and dive time.

Exact class aggregation was inspected rather than implemented: the first 428-box map retains 345 exact classes, because most cargo masses differ. Rounding unlike masses together would change the admissible patterns. Explicit dual smoothing, box penalties, in-out separation and extra limited-discrepancy search were not enabled; the implemented cold-start incumbent removed the initial equal-dual problem directly. These alternatives have not been experimentally compared. More implementation detail is in [MODEL-NOTES.md](MODEL-NOTES.md).

## Papers

- P. C. Gilmore and R. E. Gomory (1961), [A Linear Programming Approach to the Cutting-Stock Problem](https://pubsonline.informs.org/doi/10.1287/opre.9.6.849): the inherited pattern master and knapsack pricing.
- R. Sadykov, F. Vanderbeck, A. Pessoa, I. Tahiri and E. Uchoa (2019), [Primal Heuristics for Branch and Price: The Assets of Diving Methods](https://pubsonline.informs.org/doi/10.1287/ijoc.2018.0822): retaining primal incumbents and reoptimizing restricted masters; this implementation uses the existing dive plus a checked first-fit incumbent, not their complete algorithm.
- P. Wentges (1997), [Weighted Dantzig–Wolfe decomposition for linear mixed-integer programming](https://doi.org/10.1111/j.1475-3995.1997.tb00071.x): considered weighted dual smoothing; not enabled.
- O. du Merle, D. Villeneuve, J. Desrosiers and P. Hansen (1999), [Stabilized column generation](https://doi.org/10.1016/S0012-365X(98)00213-1): abstract consulted; no claim to implementing its penalty method.
- W. Ben-Ameur and J. Neto (2007), [Acceleration of cutting-plane and column generation algorithms: Applications to network design](https://onlinelibrary.wiley.com/doi/10.1002/net.20137): considered in-out separation; not enabled.

`nice -n 5` was requested for long jobs; this environment printed `setpriority: Operation not permitted` but ran the command. No permission escalation or external processes were used.
