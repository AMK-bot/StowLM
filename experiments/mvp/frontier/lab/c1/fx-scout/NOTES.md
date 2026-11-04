# fx-scout: the graph, the ranking, and one unconventional build

Scope: only this folder is written. Source, checker and parameters are imported unchanged. Read [GRAPH-MODEL.md](GRAPH-MODEL.md) first.

**Data disclosure.** Early on, a `head` over `experiments/mvp/valsplit/*.txt` printed the first five path lines of the sealed `ablation_small15.txt`. I opened no map from it, ran nothing on it, and tuned nothing on it. The five seeds shown (041100006, 012, 016, 020, 019) aren't used in this folder. The leader should know.

## Model in one sentence

A stack is a page: a pattern is what one page holds, a stow is a choice of patterns that covers each box once and uses each stack once, and the port-cover graph prices the hatch lifts.

## Ranking of methods, by likely effect on the two barriers

Barriers: (1) legal stows at 80 % fill, (2) fewer lifts. Measured facts behind the ranking:

- Stored small plans: 12.14 mean lifts, of which 3.92 are forced strangers. On 32 of 51, lifts equal forced strangers plus 8.
- Relocated boxes in stored plans: 200 strangers, 21 load boxes.
- At 80 % fill the tight resource is **mass per stack**, not cells: 1,906 t of cargo on 2,032 t of 40-ft stack ratings on the first D185 map.
- Lashing in the checker is computed chain by chain, with no wind shelter, so it's a per-column function.

| Rank | Method | Barrier | Where it fails or goes trivial on our maps | Verdict |
|---:|---|---|---|---|
| 1 | **Column generation with stacks as pages** (Gilmore and Gomory): LP master, exact knapsack pricing, diving | 1, some 2 | Pure stacks only, so far. Pricing needs small tiers, which we have | **Built** |
| 2 | Fixed-charge transportation on port × cover (Roberti, Bartolini and Mingozzi) | 2 | fx-cuts and fx-exact already hold this model; with 2 ports and 2 covers it's a small enumeration | Stopped my copy, stay with theirs |
| 3 | Submodular or matroid selection of hold boxes (Nemhauser, Wolsey and Fisher; Edmonds) | 2 | Hold choice has few boxes; a CP-SAT solve is already fast | Skip |
| 4 | Sinkhorn optimal transport (Cuturi; Altschuler, Weed and Rigollet) for fast relaxations | 1 | Marginals capture cells and mass, not stacks; an LP on patterns now takes seconds, so speed is no longer the shortage | Skip for now |
| 5 | Register allocation and Belady spilling (Hack, Grund and Goos) | 2 | Spills are load-box relocations, 21 of 221. The other 200 can't be avoided. With 2 ports "furthest next use" is just port blocking | Trivial here. Worth a rerun on 3+ port rotations |
| 6 | Circle graphs, book embeddings, maximum independent sets (Avriel, Penn and Shpirer; Bernhart and Kainen; Heath and Rosenberg; Gavril; Valiente) | 2 | With one load port and free load order, a stack sorted by port has no crossing. Page number is 1 | Trivial at 2 ports. Becomes real with cargo aboard (stage B) or multi-load-port rotations |
| 7 | Perfect graphs, Dilworth | 1 | The order-conflict graph is bipartite, so it's perfect and two chains suffice without capacity. fx-chains reached the same result | Trivial |
| 8 | Pebble motion, MAPF, conflict-based search (Kornhauser, Miller and Spirakis; Sharon et al.) | 2 | Relocation here is a lift to a clean stack, not a move through a congested graph. Yard treewidth is at most 4 and stranger moves are forced | Skip |
| 9 | Network design on the yard | 2 | Forced strangers leave almost no choice; only 0.41 relocations per map are chosen | Skip |
| 10 | Tropical methods | none found | No matching structure found in the objective: it's a count of edges, not a min-plus schedule | Skip |

**Why the page model is the one to build.** At the time of choosing, the other constructors I read were a slot-level CP-SAT (no incumbent at 120 s on the first two D185 maps) and sampled patterns with no duals. Dual pricing was the missing piece. It also gives a coverage reading from the LP in seconds and a place to put per-column physics.

## The mathematics in plain words

**Column generation.** You can't list every possible stack; there are billions. So keep a small list, solve the LP, and read the dual prices it gives: how much one more unit of each box's coverage would be worth, and what a stack's slot costs. A new stack is worth adding only if the prices of its boxes beat the price of its stack. Finding the best such stack is a small optimisation of its own, the **pricing problem**. Repeat until none helps. Gilmore and Gomory invented this for cutting paper rolls in 1961. Here the roll is a stack, the cuts are boxes.

**The knapsack in pricing.** A stack has limited tiers and limited mass. Choose boxes to maximise total price under those limits: a knapsack. Since heavy boxes must go low, sort boxes once and then each pattern is a subsequence, which dynamic programming solves exactly in a table of tiers × high cubes × mass used.

**Diving.** The LP gives fractions, like 0.6 of a stack. Fix the strongest fraction as real, solve again on what's left, repeat. It's the standard way to turn a pattern LP into a plan. It's a heuristic: it can fail near the end, so the code backs up a few steps and tries again.

**Number partitioning for heel.** Swapping two whole patterns between same-shaped stacks changes no per-stack rule, only the balance. Choosing swaps to cancel heel is the classic problem of splitting numbers into two near-equal piles.

**A per-column securing oracle.** The checker sums forces up each chain of boxes. Nothing about one chain depends on another except the ship's motion, which comes from GM and draught. So once a reference stow fixes GM and draught, a pattern's lashing ratio is a function of the pattern alone. That turns a whole-ship check into a column filter, and only a column model can use it that way.

## Sources

- P. C. Gilmore and R. E. Gomory (1961), [A Linear Programming Approach to the Cutting-Stock Problem](https://pubsonline.informs.org/doi/10.1287/opre.9.6.849). Core method.
- R. Roberti, E. Bartolini and A. Mingozzi (2015), [The Fixed Charge Transportation Problem: An Exact Algorithm Based on a New Integer Programming Formulation](https://doi.org/10.1287/mnsc.2014.1947), Management Science 61(6). Solves up to 70 × 70 by their abstract; read as abstract only.
- M. Avriel, M. Penn and N. Shpirer (2000), [Container ship stowage problem: complexity and connection to the coloring of circle graphs](https://dblp.uni-trier.de/rec/journals/dam/AvrielPS00.html), Discrete Applied Mathematics 103. Abstract only.
- T. Tanaka and T. Matsui (2021), [New Formulation for Coloring Circle Graphs and its Application to Capacitated Stowage Stack Minimization](https://arxiv.org/pdf/2102.00691). Found in search, not read in full.
- F. Bernhart and P. C. Kainen (1979), The book thickness of a graph, J. Combin. Theory B 27(3), 320–331. Citation confirmed; not read.
- L. S. Heath and A. L. Rosenberg (1992), [Laying out graphs using queues](https://courses.cs.vt.edu/cs5974/Fall2022/Resources/HeathPapers/HeathRosenberg_SIAMJComput_1992.pdf), SIAM J. Comput. 21(5).
- F. Gavril (1973), [Algorithms for a maximum clique and a maximum independent set of a circle graph](https://doi.org/10.1002/net.3230030305), Networks 3(3).
- G. Valiente (2003), maximum weight independent set on circle graphs: named in the order; **my search did not confirm this citation**, so treat it as unverified.
- S. Hack, D. Grund and G. Goos, [Register Allocation for Programs in SSA Form](https://publikationen.bibliothek.kit.edu/1000007166). Chordal interference graphs. Belady's rule wasn't confirmed by my search; it's cited from the order.
- D. Kornhauser, G. Miller and P. Spirakis (1984), Coordinating pebble motion on graphs, FOCS, 241–250. Citation only.
- G. Sharon, R. Stern, A. Felner and N. Sturtevant (2015), [Conflict-based search for optimal multi-agent pathfinding](https://people.engr.tamu.edu/guni/pistar/Papers/CBS-AIJ15.pdf), Artificial Intelligence 219.
- J. Altschuler, J. Weed and P. Rigollet (2017), [Near-linear time approximation algorithms for optimal transport via Sinkhorn iteration](https://arxiv.org/abs/1705.09634), which also discusses Cuturi (2013).
- G. Nemhauser, L. Wolsey and M. Fisher (1978): greedy bound for submodular maximisation, via the survey [Submodular Optimization Problems and Greedy Strategies](https://arxiv.org/pdf/1905.03308). Edmonds on matroid intersection wasn't confirmed by my search.

## What I copied

`lash_oracle.py:Fast` copies the deck branch of the chain loop from `src/stowage/checker/lashing.py`. `lash_oracle.py:Oracle` calls `_check_lashing` with `visibility._loading_condition` replaced inside one call and restored. Nothing under `src/` changes.

## Measured so far (D185 map `random.medium.185100000.0.2`, 121 boxes, 80 % fill)

These are the steps that shaped the method. Final numbers are under Results.

- LP over pure-stack patterns covers all 121 boxes after 21 rounds, 3.5 s.
- The LP's fewest-stacks reading: 53.6 of 64 stacks, so packing alone has room.
- Dive plus first-fit gives a complete stow in about 33 s. Replay is legal at 92–114 lifts, on a map where nothing had reached replay. This was **not** yet a pass.
- `order_legal` stops on the out-of-gauge box because of the known E9 mask gap, so the stow goes through `alns.Sequencer` and real replay instead. The checker still decides.
- Full check with default tanks, tall deck: racking 1.03, sight 310 m against 232.8 m, GM second check 0.233 against 0.280, list, draught, trim.
- 4-tier deck: sight and list pass. Racking 1.165 and draught and trim remain; GM second check 0.27 against 0.293.
- Global mass derate: LP covers at 0.85, leaves 3.2 boxes at 0.75, 7.9 at 0.65.
- Exact lashing oracle equals the full check on all 13 failing stacks, to 3 decimals.
- Fast form: same values on every failing stack, 0.24 ms each; low on passing ones where another chain limit binds (mean −0.07).

## What the checker taught the method

Each fix came from reading a failed full check, not from theory:

| Failed rule | Change |
|---|---|
| E9, every horizontal OOG refused by the mask (known gap, `scale/E9-PARAMETER-MISMATCH.md`) | Order with `alns.Sequencer` and real replay, not the mask. `clear_oog` swaps the box to a clear position, tested exactly |
| E8 away from heat | Slots filtered per box. On maps where the empty-ship probe refuses every slot the filter is dropped, so this can still fail at the checker |
| E6 heavy on deck | Larger hold reward for heavy boxes, hold patterns fixed first in a dive |
| SOLAS V/22 sight | Deck tier cap, 4 then 3 |
| Lashing | Per-column knapsack weights fitted from the oracle, and a check on every priced pattern |
| Trim and draught | Soft fore-aft row in the master, stepped by what the check reports |
| List | Whole-pattern swaps between same-type deck stacks |
| Hatch lifts | One port per cover in the hold, or in hold and deck, tried once a stow passes |

## Results

Code hashes: `results/source.sha256`. Set `quick`, seed 0, budget 300 s, 2 workers, one numerical thread each. Runs: `results/quick-3`, `results/medium12-1`. Every pass went through the shared runner's fresh full checker. The two medium passes were checked again in a separate process (`audit_passes.py`, `results/audit.json`): passed, 0 violations, all boxes loaded.

| Set | Passes | 0.9.0 passes | Mean lifts, paired | Wall time, mean per map |
|---|---:|---:|---|---:|
| Tiny, 8 maps | 8 | 8 | 6.88 vs 6.38, change +0.50 (95 % 0 to +1.25), 0 better, 6 equal, 2 worse | 11 s |
| Small, 10 maps | 9 | 9 | 14.00 vs 13.89, change +0.11 (95 % −3.33 to +2.67), 1 better, 4 equal, 4 worse | 52 s |
| Medium, 3 maps | 2 | no stored row | none paired with 0.9.0 | 275 s |
| `medium12` (D185) | 0 of 12 | no stored row | none | 193 s |

**Medium against V6, the only stored certificates on these maps (not 0.9.0):**

| Map | Boxes | Fill rung | V6 lifts | Page model lifts | Page model parts |
|---|---:|---|---:|---:|---|
| `D192.medium-feeder_500-f60-high-s192200000` | 88 | f60 | 131 | **79** | 47 relocations, 12 + 12 hatch, 8 later restow |
| `D192.medium-feeder_800-f60-low-s192200101` | 162 | f60 | 214 | **184** | 84 relocations, 16 + 28 hatch, 56 later restow |

Both passes finish without ballast search: the plan passes at the ship's default tanks, so the finish stage is only the check, under 3 s here. Total time per map is 207 s and 298 s, most of it repeated 30–60 s full checks inside the ladder. V6 took 82 s to finish the 88-box stow alone.

On the 88-box map, relocations are 47 in both plans. The whole saving is hatch and restow lifts: 36 + 48 in V6, 24 + 8 here.

On the 162-box map, a one-port-per-cover stow reached 116 lifts with E9 as its only failing rule, in every run including the final one. It didn't pass, so 184 is the number.

**`medium12`.** 0 of 12 pass. 8 of 12 reached a complete stow that replays legally with all boxes loaded. That isn't the same gate as the V6 carrier, which also requires the mask, so don't read it as 8 of 12 against 0 of 12. Rules still failing on each map's closest stow, over those 8: securing 8, trim 7, sight 7, draught 6, GM second check 4, E9 3. The closest is `random.medium.185100000.0.2`: 98 lifts, failing securing, GM, draught and trim.

The four 428-box maps stopped earlier. Two ran out of time in the first dive. On the other two, the LP was still at 428 uncovered when its time ran out, which says nothing about feasibility.

**No proof claim.** The LP's "proved" flag means pricing found nothing more within the fitted knapsack weights. Those weights are an approximation, so a shortfall is not an infeasibility proof of any map. With exact lashing inside the knapsack this could become a bound on pure-stack patterns; it isn't one today.

**What's weak.**
- Only pure stacks are generated. 20-ft boxes under long boxes on deck would add capacity, and that's where the D185 maps are short.
- The small-map lift gap: losses are mostly maps where 0.9.0 keeps hatch lifts at 4 or 6 (`sim.small.40100010`, 8 vs 4; `sim.small.41100007`, 15 vs 9). Nothing here searches hatch edges beyond one layout rule.
- GM in the securing check comes from one reference stow, not the final one.
- `Model.integer` is unused and predates the final model. The measured code is kept as hashed, so it stays.

**Verdict: keep.** It meets the leader's rules on the 88-box and 162-box maps only by comparison with V6, since 0.9.0 has no row there; the leader should run 0.9.0 on them if a paired number is wanted. It doesn't meet "passes a map 0.9.0 fails" on the D185 cohort. On small maps it doesn't meet the 3× rule strictly, because lifts are 4 worse and 1 better; as a fast start for search it looks strong.

**Next.**
1. Mixed 20-under-40 patterns in pricing.
2. Feed page-model stows to Frontier's search as starts. Small maps would then cost about a tenth of the time.
3. Replace the fitted weights with a piecewise-linear securing form, for a real bound.
4. Run the full check less often: cheap gates for sight, trim and lashing first.

## Long-run notes

`quick` ran past no 20-minute limit in the final run; `medium12` took about 25 minutes, needed for the full 12-map cohort.

## Entry points

- `method.py:propose` and `method.py:finish`, run through `lab/score.py`.
- `pages.py`: pages, pricing, master LP, dive, repairs. `lash_oracle.py`: securing checks.
- Measurements: `measure_graph.py`, `breakdown.py`, `verify_claims.py`, `hold_census.py`. `hatch_bound.py` was stopped before it finished, because fx-exact covers it, so it has no results.
