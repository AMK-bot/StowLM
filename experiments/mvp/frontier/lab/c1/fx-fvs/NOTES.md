# Yard feedback, class assignment, and what replay actually proves

Read `../../CONTRACT.md` first. Scope: this folder only; no source, checker, parameter, environment, or sealed-data changes. All inputs are the shared frozen validation/TRAIN `quick` set. No downloaded code was executed.

## Model and assumptions

A vertex is an export box. A ship arc points from a supporting lower box to a supported upper box, or from a hold load to a deck load over its cover. A yard arc points from an initially higher export to every initially lower export in the same yard stack. Every distinct non-export box above an export is an unavoidable relocation: productive loading of non-load-list inventory is rejected by the move engine. These non-export blockers matter substantially here and are not represented by a graph only on exports.

The graph is directed because load-before is asymmetric. A cycle asks for mutually contradictory orders. Relocating one box can break its outgoing yard dependencies, but does not remove the box's ship dependencies. `exact.py` therefore keeps all ship arcs and switches off outgoing yard arcs by a binary decision for their upper endpoint. An integer rank increasing along retained arcs certifies that no cycle remains. CP-SAT minimizes the number of switched-off upper boxes plus mandatory non-export blockers. This is an exact minimum for a yard with unlimited safe parking, and a lower bound for real loading of the fixed stow with one load per box and no ship restows. A fully checked real plan attaining it proves its actual minimum within that family.

Real relocations return to ordinary finite yard stacks, selected by the unmodified deterministic `Expander.yard_target`. They can block again; crossing clearance and handling rules can require other relocations. The quay is not a free export-parking area. We do not implement generic DFVS reductions that could erase mandatory ship arcs. We use the rank formulation rather than a lazy cycle master because these graphs are small.

`search_replay` is a bounded exact search of real yard states over all ship-ready Place actions and the current default relocation policy. Its memo includes loaded set, full yard contents and lengths/footprints, covers and quay state. It prunes by a valid forced-blocker bound and accepts complete orders only after real replay. Exhaustion proves a policy minimum; a timeout leaves a gap. It does not optimize arbitrary explicit relocation destinations, proactive moves, or ship restow alternatives.

A fixed-stow bound is not generally a bound for the freely chosen stow. The mandatory non-export count is assignment independent and **is** a map-wide yard-relocation lower bound under this productive-load contract. Minimizing yard relocations alone over arbitrary ship staging is different from minimizing D11, which also charges staging/restow and hatch lifts.

## Assignment and combination

Weight classes do not make boxes physically identical: actual masses change weight ordering, strength and stability. `assignment.py` first groups by all Container fields except identity and the untrusted category tag; its broader proposal pass drops mass facts only within a non-null weight class while retaining reefer, DG, OOG, size, discharge and booking facts. Every proposal still requires finishing and the full checker.

The Hungarian subproblem assigns a class to its existing occupied slots while pricing its interactions with other classes. Pairwise interactions inside a class make the full inversion objective nonlinear; coordinate descent and exact improving pair swaps are heuristics, not a proof of global assignment optimality. Crossings and inversion pairs are surrogates: one relocation can clear many pairs.

The joint CP-SAT model assigns class-compatible identities to occupied positions, chooses ship-position ranks satisfying support/hatch arcs, and charges each yard upper box whose assigned rank violates an initial yard dependency. This combines assignment with the cycle relaxation directly. Its bound is conditional on those occupied slots and exchange classes. Real replay and the checker decide whether its candidate is useful.

## Implemented entry points

- `method.py:propose_order`: fixed stored assignment, real ordering and relocation bound.
- `method.py:propose_exact_classes`: add conservative attribute-identical exchanges.
- `method.py:propose`: add broader same-weight-class mass exchanges, Hungarian proposals and joint CP-SAT.
- `method.py:order`: standalone load-order plug-in.
- `finish.py:finish`: optional cached-ballast adapter. It tries the stored ballast on the actual new moves and runs the full checker; on rejection it falls back to the unchanged V4 finisher. The runner independently checks again. Reusing an existing ballast solution is reported explicitly and is not evidence of faster cold construction.

The default scorer finisher remains available by omitting `--finish`. Complete ordered Place tuples are returned and the original stored order is retained as an admission fallback. At most two strict alternatives precede it, so `--tries 3` can always reach it.

All production algorithms are newly written in this folder. Imported unchanged interfaces: `alns.Problem`, `alns.Sequencer`, `precedence_sets`, `Expander`, real `apply_plan`, full `check`, and Frontier `_finish`. No existing implementation was copied or modified.

## Literature

See [RESEARCH.md](RESEARCH.md) for authors, years, primary links and access limitations. Principal influences: Tanaka & Voß (2019), *An exact algorithm for the block relocation problem with a stowage plan*; Caserta, Schwarze & Voß (2012); Kim & Hong (2006); Tanaka & Takii (2016); Moreno-Centeno & Karp (2013) and Chandrasekaran et al. (2011) on implicit hitting sets; Schidler & Kiesel (2022), Meiburg (2022), Mizutani (2022), and official PACE 2022 results; Chen et al. (2008); official PACE 2024 crossing-minimization problem. The practical BRPSP precedent is especially close. We do not claim to reimplement unavailable paper lower-bound formulas.

## Measurement protocol

Shared `lab/README.md` was read as soon as it existed. `lab/score.py` is the pass gate for all measured results. `quick` has 8 tiny, 10 small and 3 medium maps. Stored 0.9.0 evidence exists only for tiny/small, and one small stored row failed. Medium unknowns must not be described as 0.9.0 failures. Order and assignment entry points require an existing complete stow; they deliberately return no candidates where none is stored, rather than quietly running another constructor.

One numerical thread per process; no more than two busy processes in this team. Long runs invoke `nice -n 5`; this managed environment prints `setpriority: Operation not permitted`, then runs the command. No single planned run exceeds twenty minutes. No other process is stopped.

Measured results and verdict follow below.

Primary references used (access limitations and precise applicability in RESEARCH.md):

- Shunji Tanaka and Stefan Voß (2019), [An exact algorithm for the block relocation problem with a stowage plan](https://doi.org/10.1016/j.ejor.2019.06.014).
- Marco Caserta, Silvia Schwarze and Stefan Voß (2012), [A mathematical formulation and complexity considerations for the blocks relocation problem](https://doi.org/10.1016/j.ejor.2011.12.039).
- Kap Hwan Kim and Gun Park Hong (2006), [A heuristic rule for relocating blocks](https://doi.org/10.1016/j.cor.2004.08.005).
- Shunji Tanaka and Kenta Takii (2016), [A faster branch-and-bound algorithm for the block relocation problem](https://doi.org/10.1109/TASE.2015.2434417).
- Erick Moreno-Centeno and Richard M. Karp (2013), [The Implicit Hitting Set Approach to Solve Combinatorial Optimization Problems](https://doi.org/10.1287/opre.1120.1139).
- Karthekeyan Chandrasekaran, Richard Karp, Erick Moreno-Centeno and Santosh Vempala (2011), [Algorithms for Implicit Hitting Set Problems](https://arxiv.org/abs/1102.1472).
- Andre Schidler and Rafael Kiesel (2022), [Dagger/raki123 DFVS solver](https://github.com/ASchidler/dfvs).
- Alex Meiburg (2022), [Reduction Rules and ILP Are All You Need: Minimal Directed Feedback Vertex Set](https://arxiv.org/abs/2208.01119).
- Yosuke Mizutani (2022), [PACE solver description](https://raw.githubusercontent.com/wiki/mogproject/dfvs-2022/pace-2022-description.pdf).
- Jianer Chen, Yang Liu, Songjian Lu, Barry O'Sullivan and Igor Razgon (2008), [A Fixed-Parameter Algorithm for the Directed Feedback Vertex Set Problem](https://doi.org/10.1145/1411509.1411511).
- PACE organizers (2022), [official DFVS problem](https://pacechallenge.org/2022/) and [official results](https://pacechallenge.org/2022/results/).
- PACE organizers (2024), [official one-sided crossing minimization problem](https://pacechallenge.org/2024/).

## Checked order-only result

`results/order-quick/summary.json`, shared `score.py`, seed 0, 8-second order budget, two workers, explicit `finish.py:finish` cached-ballast/recheck adapter:

| Frozen subset | Passes vs stored 0.9.0 | Paired mean lifts, method vs stored | Wins/ties/losses | Mean wall/map |
| --- | --- | --- | --- | --- |
| quick_tiny | 8/8 vs 8/8 | 6.375 vs 6.375 | 0/8/0 | 71.324 s |
| quick_small | 9/10 vs 9/10 | 13.888889 vs 13.888889 (9 pairs) | 0/9/0 | 65.763 s |
| quick_medium | 0/3; stored baseline unmeasured on all 3 | no pairs | — | 0.547 s |

Every admitted plan was checked afresh, including the original stored fallback. No pass was lost. All 17 admitted stored stows attain their exact relocation-relaxation bound, so their monotone fixed-stow minimum relocations are proven; no exact search tree was necessary. Fourteen also attain the universal mandatory-inventory bound, proving global minimum yard relocations under the operational move contract. In particular the five random-yard small plans have 14,14,3,4,4 unavoidable relocations and no export-to-export yard arcs. The three nonzero simulated-yard fixed-stow minima are 2,1,1. The other simulated small has zero.

`results/order-certificates.json` records the map-by-map counts and proof scopes, including lower bounds 47,84,24 on the three medium maps even though these methods did not construct/finish their missing stored stows. `MODEL.md` separately shows real V6 medium replay attaining the first bound of 47, without claiming a fresh medium full-checker result.

**Negative result:** replacing the stored load order saves zero on this frozen set because all of its passing stows already attain the relocation bound. Arbitrarily deleting ship vertices in ordinary DFVS would not correctly model the operational decision, and ignoring non-export inventory would miss most costs here.

The run's helper source snapshot is in `results/order-quick/source/`. The final code additionally guards non-Place stored actions, makes uncertified metadata wording explicit, and validates each legacy-Expander prefix before memoization. These are robustness fixes. The first-run source already rejected complete illegal replays; every measured bound certificate was obtained by equality, not search exhaustion.

Root verification after these fixes: 5 graph tests pass (including 35 random graphs checked by exhaustive permutation enumeration), plus 3 real replay/joint-assignment tests pass. These test zero-relocation order improvement, a fixed-stow one-relocation exhaustive optimum, and a joint identity exchange removing that conflict.

## Checked combined result and attribution

`results/combined-quick/summary.json`, same frozen `quick`, seed 0, 20-second method budget, two workers, explicit `finish_cached.py:finish` adapter. This adapter offers the existing ballast and the shared scorer performs the full checker once. It does not run a new ballast search. Both runs retain all original passing plans as fallback candidates.

| Frozen subset | Passes vs stored 0.9.0 | Paired mean lifts, method vs stored | Wins/ties/losses | Mean wall/map |
| --- | --- | --- | --- | --- |
| quick_tiny | 8/8 vs 8/8 | 6.375 vs 6.375 | 0/8/0 | 43.648 s |
| quick_small | 9/10 vs 9/10 | **13.777778 vs 13.888889**, 9 pairs | **1/8/0** | 40.226 s |
| quick_medium | 0/3; stored baseline unmeasured on all 3 | no pairs | — | 0.671 s |

Small paired improvement is **0.111111 lifts/map (0.8%)**; map-bootstrap 95% interval for method minus baseline is **[-0.333333, 0]**. Total yard relocations on the nine passing small maps fall **43→42**. No hatch or restow count changed. The new full-checker pass at `sim.small.41100007` has zero yard relocations and eight hatch lifts, versus one relocation and eight hatch lifts in the stored plan. All other passing maps tie.

Part attribution:

- Fixed-stow order optimization: **0 saved** across quick; all existing passing stows were already relocation optimal in the stated monotone family.
- Joint class assignment plus ordering: **1 saved** across the nine paired small maps; no pass lost. The full combined winner uses two swaps, one of which exchanges different masses inside one weight class. This plan passes the full checker.
- A separate full-checker ablation on the winning map, `results/exact-class-win/`, allows only attribute-identical exchanges and also reaches **8 vs 9** lifts. It finds precisely the three-box empty-container rotation in `ASSIGNMENT.md`. Thus **the same one lift can be saved without mass exchanges**; no additional mass-exchange saving is observed or possible within the measured occupied-slot/class relaxation, whose bound is attained on every passing map.
- Hungarian matching and joint CP-SAT are both candidate generators in the combined method; no full-cohort standalone Hungarian ablation was run. We do not attribute an isolated numerical gain to Hungarian alone.

`results/decomposition.json` records both actually checked winning assignments, their changes, and the exact-attribute audit. This corrects the initial expectation that the combined winner itself would be the three-way rotation: it was a different, broader-class plan; the separate ablation validated the conservative rotation afterward.

`results/combined-certificates.json` reports **17/17 chosen stows at their fixed-stow relaxation bound**, **17/17 at the joint occupied-slot/class relaxation bound**, and **15/17 at the assignment-independent mandatory-inventory bound**. These are relocation minima, not unrestricted D11 optima. The remaining small occupied-slot/class minima of two and one relocations also have explicit structural proofs in `ASSIGNMENT.md`. The complete example graphs, initial yard inventory, assignments, bounds and real witness counts are retained in `results/model-small.json` and `results/model-medium.json`.

Timing is warm-start evidence only. Method construction/search totaled 2.068 seconds for the order-only run and 5.848 seconds for the combined run across all 21 inputs; full checking dominates wall time. Sum of per-map wall times was 1229.864 and 753.455 seconds, respectively, with two workers. The first finisher adapter checked twice while the combined adapter checked once, and machine contention varied. **Do not claim a cold-solver wall-time speedup from these figures.**

## Reproduce

Run from repository root, with one numerical thread per process and no other busy jobs in this team:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick --method experiments/mvp/frontier/lab/c1/fx-fvs/method.py:propose \
  --finish experiments/mvp/frontier/lab/c1/fx-fvs/finish_cached.py:finish \
  --budget-s 20 --workers 2 \
  --out experiments/mvp/frontier/lab/c1/fx-fvs/results/combined-quick
```

The order-only command used `:propose_order`, `finish.py:finish`, budget 8 and output `results/order-quick`. Its exact code is preserved in that run's `source/` subfolder. The combined run also has a source snapshot and SHA256 list. Omit `--finish` to evaluate cold V4 finishing; that cold-finisher experiment has not been run here.

The conservative winning-map ablation uses `--set experiments/mvp/frontier/lab/c1/fx-fvs/results/winning-map.txt`, method `:propose_exact_classes`, cached finisher, budget 20, one worker, output `results/exact-class-win`. Its fresh checked result is 8 lifts in 47.557 seconds.

Final verification command:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c1/fx-fvs \
.venv/bin/python -m pytest \
  experiments/mvp/frontier/lab/c1/fx-fvs/exact_tests.py \
  experiments/mvp/frontier/lab/c1/fx-fvs/test_pipeline.py \
  experiments/mvp/frontier/lab/tests -q
```

All tests passed, including the eight new algorithm tests and the shared scorer safety/selection/summary suite. The shared test that invokes the actual checker took 51.1 seconds. `git diff --check` for this folder passed. No commit or push was made.

## Verdict and next experiment

**Keep the bound and the joint assignment component.** They reduce paired quick-small mean lifts without losing a pass and prove the relocation minimum for every returned small/tiny stow, including a useful negative finding that most stored relocation costs are already unavoidable. Prefer the conservative identical-attribute exchange when integrating: it attains the entire observed gain. Keep fixed-stow search as a certificate/diagnostic tool; this cohort gives it no lift saving.

Do not call any result a global D11 optimum. No new stow was built on the failing small map or the three medium inputs. Medium bounds remain useful, including the V6 replay's 47-lift yard optimum, but these plug-ins do not solve construction/finishing failures. The next useful search releases occupied slots or port layouts, especially the 16 future restow lifts on `sim.small.40100025`; further permutations inside the measured classes have no relocation headroom on these passing maps.
