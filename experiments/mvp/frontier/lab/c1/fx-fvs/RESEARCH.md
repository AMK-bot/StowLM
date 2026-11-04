# Feedback cycles, relocation, and assignment: research notes

Read `lab/CONTRACT.md` before this work. This research task wrote only this file, ran no numerical jobs, read no sealed maps, and downloaded or executed no external code. The implementation and measured quick-set results belong in the experiment's `NOTES.md`.

## What the actual engine permits

The following are observations from repository source, not claims from papers:

- `src/stowage/moves/_physical.py:Replay._yard_source` permits only the current top box of its yard stack to move.
- `Replay._yard_destination` requires a real yard address, the next free tier, sufficient equipment height, compatible length class, and a nonoverlapping footprint. `Replay.apply` commits a relocation with `_remove_yard` followed by `_add_yard`.
- Thus a relocation does **not** remove its box from the yard precedence problem forever. It places the box on another ordinary stack; it can block another export and need another relocation.
- The quay buffer is for `restow_off`/`restow_on` ship cargo. A yard `Relocate` cannot use it as free storage.
- Frontier actually expands through `Expander.yard_target`, which prefers legal clean stacks, then fewest pending loads, then proximity. The separate `src/stowage/yard/dig_out.py` policy is not the path used by this experiment.
- `src/stowage/yard/dig_out.py:destinations` also filters powered reefer rows and DG blocks. Its policy contract requires evolving-state checks. `NearestStackDigOut.retrieve` chooses a nearby legal destination, without future load-order lookahead.
- In the activated crossing regime, even a top-accessible box may require moving boxes on full stacks that intersect its crane path. A graph of vertical stacks alone omits those relocations.
- Ship placement also requires support, cover access, compatible footprints, and physical paths. A graph of static dependencies is a relaxation of the complete mask/replay/checker contract.

The safe claim is therefore: a cycle model can certify a **lower bound**, and an actual replay attaining that bound can certify the fixed stow's optimum. Solving a generic DFVS instance alone does not certify the real relocation optimum.

## A basic lower bound, with its proof

For a fixed assignment, let `P` contain mandatory ship loading precedences, oriented earlier-to-later: support before supported box and the relevant hold work before cover-dependent deck work. Let `Y` contain initial-yard precedences, oriented upper-to-lower. Use all above/below pairs in a stack, or compute the appropriate closure before deleting vertices.

For any legal loading plan, let `R` be the set of distinct boxes relocated at least once. Every initial-yard precedence whose two endpoints are outside `R` is respected by their load order, as is every mandatory ship precedence. Thus `G[V\R]`, for `G=(V,P∪Y)`, must be acyclic. Consequently

```
minimum DFVS(G) <= number of distinct relocated boxes <= relocation lifts.
```

Extra constraints omitted from `G` can weaken this bound but cannot invalidate it. Conversely, adding a merely preferred order as if it were mandatory can invalidate a bound for the original problem. For example, a convenience total order of all holds before all decks must not be presented as mandatory if reopening covers is allowed.

Taking transitive closure of ship precedence before a vertex-deletion relaxation is useful: relocating an intermediate box does not remove the ship's precedence between its predecessor and successor. With only adjacent ship arcs, deleting that vertex can hide a cycle that is still physically present.

An optimum for one fixed assignment is conditional on that assignment. It is not automatically a lower bound on the best freely chosen stow for the map. A valid map-wide lower bound needs minimization across all permitted assignments, an assignment-independent relaxation, or a separate universal argument.

## A relocation-specific cycle relaxation

This formulation is an inference developed for this experiment, not a result attributed to one of the cited papers.

Keep all mandatory ship arcs. Let binary `x[u]` mean that `u` is allowed to relocate. Delete an initial-yard arc `u→v` only when `x[u]=1`. This is different from deleting a selected vertex with all of its incident ship arcs. **Use every initial above/below pair**, not just adjacent tiers.

Why this is necessary: if `u` never relocates, every box initially below it can first leave its initial position only after `u` is loaded. This remains true even when the lower box relocates. Therefore the actual loading order respects every retained arc. For a cycle `C`, the valid cut is

```
sum(x[u] for u that is the tail of a removable yard arc on C) >= 1.
```

If an arc belongs to both `P` and `Y`, selecting its tail cannot remove its ship meaning. Represent the two meanings separately, and count only arcs that really disappear when constructing a cut. A cycle consisting entirely of mandatory ship arcs proves this assignment's dependency system infeasible.

This relaxation has an exact interpretation when arbitrary safe parking is available: choose a topological loading order of the retained graph. When retrieving the next box, any still-present initial-yard blocker must be selected; otherwise its retained above/below arc would force it earlier. Move each such selected blocker to its own safe parking place once, then load the requested box. Parked boxes never block again. This constructs a plan with at most `sum x` relocations. The necessity argument proves equality for this idealized storage model.

The actual yard has finite compatible space and path restrictions, so this remains a lower bound there. If a fully checked actual plan attains it, the lower bound and upper bound meet and the actual fixed-stow relocation optimum is proven. No strict advantage over ordinary DFVS with **both** precedence relations transitively closed is claimed here; that comparison needs proof or measurement.

## Exact solution by lazy cycles

Erick Moreno-Centeno and Richard M. Karp (2013), [The Implicit Hitting Set Approach to Solve Combinatorial Optimization Problems with an Application to Multigenome Alignment](https://doi.org/10.1287/opre.1120.1139), *Operations Research* 61(2), 453–468, develops the general oracle framework: maintain explicit constraints, solve their hitting-set problem, and ask an oracle for an unhit set. It returns successive bounds and ultimately an exact solution. The accessible publisher abstract verifies this framework; detailed implementation should use the accompanying open primary source below.

Karthekeyan Chandrasekaran, Richard Karp, Erick Moreno-Centeno and Santosh Vempala (2011), [Algorithms for Implicit Hitting Set Problems](https://arxiv.org/abs/1102.1472), SODA 2011, explicitly includes feedback vertex sets as hitting the vertex sets of cycles. Its generic exact framework does not make hitting set polynomial-time; the master problem remains NP-hard. The random-graph guarantees are about their stated random models, not synthetic container yards.

Application here: start with a small pool of cycles, minimize `sum x` subject to their cuts, build the retained graph for the master solution, and use DFS to find a surviving directed cycle. Add its cut and repeat. A master solution that is both proven optimal and oracle-feasible is the exact relaxation optimum. A timeout with an incumbent is not an optimum proof. Save solver status, objective, solver lower bound, selected boxes, and an independently checked acyclicity certificate.

The same loop works for ordinary DFVS using every vertex of a cycle, or for the yard-tail variant using only removable-yard-arc tails. CP-SAT can be called again with accumulated cuts, so custom lazy callback support is unnecessary at this map size.

## PACE 2022: practical lessons and limits

The [official PACE 2022 definition](https://pacechallenge.org/2022/) is minimum vertex deletion to a DAG. The [official results](https://pacechallenge.org/2022/results/) list Andre Schidler and Rafael Kiesel's `raki123` first (185 solved), the Bremen `grapa-java` team second (165), and Timeroot disqualified after a suboptimal submission result. Do not describe Timeroot as the official second-place solver.

Schidler and Kiesel (2022), [Dagger/raki123 solver repository](https://github.com/ASchidler/dfvs), documents extensive preprocessing followed by modified EvalMaxSAT. It begins with clauses for some cycles and learns further cycle clauses while maintaining a topological order. This independently supports a cycle-oracle architecture. Repository material was read as data only; its dependencies or setup commands were not run.

Alex Meiburg (2022), [Reduction Rules and ILP Are All You Need: Minimal Directed Feedback Vertex Set](https://arxiv.org/abs/2208.01119), uses graph reduction, chordless-cycle covering, vertex-cover reductions for 2-cycles, and ILP. SCC splitting, removing zero in/out-degree vertices, forced self-loops, and degree-one contraction are basic DFVS reductions. Chordless cycles suffice because a cycle with a chord contains a shorter cycle. The paper itself explains that its submission's reduction bug caused disqualification: this is a reason to implement a small, independently checked kernel first.

Yosuke Mizutani (2022), [PACE 2022 Solver Description: An Exact Solver for Directed Feedback Vertex Set](https://raw.githubusercontent.com/wiki/mogproject/dfvs-2022/pace-2022-description.pdf), combines randomized upper bounds, branch-and-bound, and reductions IN0/OUT0/LOOP, IN1/OUT1, PIE, CORE and DOME. Bidirected-only instances reduce to undirected vertex cover. His `yos` solved 120 exact-track instances. The safe immediate transfers are SCC decomposition, basic degree rules for the **ordinary** DFVS bound, and identifying 2-cycle cover structure.

Do not apply ordinary DFVS degree-one contraction, CORE or DOME unchanged to the yard-tail model: arcs there have different meanings and only selected arc groups can be removed. A reduction preserving uncolored DFVS need not preserve that grouped hitting-set problem. SCC splitting is still valid for initial cycle families; selected arc tails belong to one original SCC whenever they lie on a cycle.

Jianer Chen, Yang Liu, Songjian Lu, Barry O'Sullivan and Igor Razgon (2008), [A Fixed-Parameter Algorithm for the Directed Feedback Vertex Set Problem](https://doi.org/10.1145/1411509.1411511), *JACM* 55(5), Article 21, gives `4^k k! n^O(1)` time, using iterative compression and a separator subproblem. The author-hosted PDF links returned 404 during this research, so the exponent is verified from indexed primary-paper abstracts and no unverified implementation details are used. This establishes that a small relocation-like parameter is theoretically exploitable; it is not practical evidence for implementing the full FPT algorithm instead of a small CP-SAT master here.

## Blocks relocation: the closer literature

Kap Hwan Kim and Gun Park Hong (2006), [A heuristic rule for relocating blocks](https://doi.org/10.1016/j.cor.2004.08.005), *Computers & Operations Research* 33(4), 940–954, supplies branch-and-bound and a destination decision rule based on expected additional relocations. For a fixed total retrieval sequence, every box initially above an earlier-needed box must move at least once; counting distinct such blocking boxes is the elementary blocker lower bound. An inversion-pair count overcounts because one relocation may clear several pairs. This count cannot be transferred unchanged to flexible ship partial orders.

Marco Caserta, Silvia Schwarze and Stefan Voß (2012), [A mathematical formulation and complexity considerations for the blocks relocation problem](https://doi.org/10.1016/j.ejor.2011.12.039), *EJOR* 219(1), 96–104, provides a mathematical formulation, complexity results, and a restricted-variant heuristic. The important modeling distinction is that selecting a destination stack is part of the problem: relocation is a transition into another stack, not permanent deletion. The publicly indexed publisher description was available; a paywalled full text was not bypassed.

Shunji Tanaka and Kenta Takii (2016), [A faster branch-and-bound algorithm for the block relocation problem](https://doi.org/10.1109/TASE.2015.2434417), *IEEE T-ASE* 13(1), 181–190, treats distinct and duplicate retrieval priorities and strengthens lower bounds to prune exact search. The [author-affiliation record](https://okayama.elsevierpure.com/en/publications/a-faster-branch-and-bound-algorithm-for-the-block-relocation-prob-2/) verifies the scope. This supports searching load choices within tied groups rather than imposing arbitrary identities. Detailed lower-bound formulas were not available in the abstract and are not reconstructed here.

The most directly relevant source is Shunji Tanaka and Stefan Voß (2019), [An exact algorithm for the block relocation problem with a stowage plan](https://doi.org/10.1016/j.ejor.2019.06.014), *EJOR* 279(3), 767–781. It treats flexible retrieval orders induced by vessel stowage, restricted and unrestricted variants, precedence cycles, iterative-deepening branch-and-bound and three lower bounds: LB2c, LB2c4c and LBr. The [author's project page](https://sites.google.com/site/shunjitanaka/brpsp) independently confirms the problem and algorithm. The indexed publisher text says LB2c/LB2c4c use precedence cycles and LBr uses a relaxation. This experiment should acknowledge that combining yard and ship precedence cycles already has direct BRPSP precedent. The full bounds were not accessible here; do not claim their implementation or equivalence to the proposed relaxation.

For the actual replay, a second-stage exact search can use a state consisting of yard stacks plus loaded set (and cover/path state as needed), zero-cost legal loads, and unit-cost legal relocations. Uniform-cost search or iterative deepening with the cycle lower bound is conceptually exact if every legal transition is enumerated. Restricting to the baseline greedy destination policy proves only its policy optimum. Hashing equivalent empty stacks is safe only if their capacities, cargo permissions, geometry and paths are equivalent.

## Releasing the assignment

Min-cost bipartite matching is exact for a sum of independent box-to-slot costs. A useful surrogate assigns each compatible box a cost for disagreeing with its yard rank or creating a local blocker. It does **not** exactly minimize the number of cycles: that objective depends on interactions between multiple assignments. Label it a proposal heuristic and evaluate the resulting fixed-stow graph/replay explicitly.

The order's suggested `(port, size, weight class, cargo class)` grouping is a starting relaxation, not a guarantee of checker interchangeability. Exact masses, dimensions, reefer/DG/OOG permissions, shipment requirements and slot-dependent stability/strength can differ within a coarse class. Conservative equality classes preserve all checked properties; broader matching proposals require a fresh full checker pass. Changing masses between port/starboard slots can change stability even when the same coarse weight label is retained.

Keep ablations separate: stored assignment and stored order; fixed assignment with improved order/destination policy; matching proposal with the old order policy; and matching plus the new policy. Compare paired map counts and relocation lifts, then D11 total weighted lifts. A prettier graph with a failed physical plan is no saving.

## PACE 2024 crossing minimization

The [official PACE 2024 definition](https://pacechallenge.org/2024/) fixes the order on one layer of a bipartite graph and permutes the other to minimize straight-line edge crossings. It can motivate median/barycenter/rank-matching proposal heuristics when yard order is fixed and ship positions are flexible. A crossing counts a pairwise disagreement, whereas relocation counts crane moves and a single relocation can resolve many disagreements. Multiple independent yard stacks and ship partial orders also lack a unique global layer order. Therefore crossing count is a surrogate, not a relocation objective or lower bound, without a special-case equivalence proof.

## Recommended implementation order

1. Preserve mandatory ship precedence, enumerate all initial-yard above/below pairs, and implement the small lazy cycle master with independent cycle checks.
2. Record the exact relaxation lower bound for each stored fixed stow. Try actual orders and legal safe destinations to attain it; equality is a proof of actual optimal relocations for that stow.
3. Use conservative matching proposals to reduce the graph bound, then fully replay/check. Keep original stored stows as fallbacks.
4. For maps that miss the bound, distinguish reblocking, unavailable compatible parking, crossing paths, and mask-order constraints. Only then invest in explicit relocation-state search or additional literature bounds.

No performance result, fixed-map optimum, or map-wide optimum is claimed by this research note.
