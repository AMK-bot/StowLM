# Chain/flow assignment: implementation rules and certificate limits

Read-only source audit, 2026-11-01. This note copies no implementation. Sources are the repository files named below; no sealed map or evaluation-only cargo data was opened. This task ran no solver and consumed no CPU worker allowance.

## What the order graph does and does not establish

A vertex is a required movable container. A lower-to-upper edge can encode admissible weight order and the optional preference that the lower box leaves at the same or a later port. For a homogeneous physical column with zero tolerance, its transitive closure is a partial order, after breaking equivalent-box ties by ID. A minimum path/chain cover follows from bipartite maximum matching. That unconstrained minimum is only a lower bound on required columns: actual columns have locations, static domains, lengths, mass, heights and shared supports.

**Later port lower is an objective restriction, not a hard checker rule.** Overstowing causes future restows, which are charged by `Problem.score`; the checker can accept such a stow. An infeasible port-monotone chain packing does not establish infeasibility of the map. Relax this restriction before abandoning a map.

**Nonzero weight tolerance breaks transitivity.** In both mass and class modes the upper box is compared with all lower boxes, not merely the immediately preceding box. Adjacent inequalities can accumulate tolerance illegally. Use every lower/upper pair, or a running minimum of lower mass/rank, in construction. With positive tolerance the raw pair-compatibility graph is not a poset. A zero-tolerance suborder is a sufficient construction restriction only; its lower bounds do not prove impossibility under the actual tolerance.

## Reuse the active runtime's domains

`alns.Problem(instance)` owns a native engine constructed with `FeasibilityConfig(extra={'global_screen': 0})`. Thus this work's ordinary mask replay disables C2 physics and must say so. L1/L2, C1 and D55 remain active. Use `problem.engine.T` rather than reconstructing coarse slot eligibility from `Problem.slots_by_length`: that helper only builds 20/40 geometry and does not enumerate a separate 45 domain.

`src/stowage/feasibility/tables.py:build_tables` provides exact static eligibility in `T.static[box_index, slot_index]`, and arrays for real cell footprints, supporting stacks, per-stack ordinal, mass shares, heights, class ranks and limits. Rows include only loads plus nondischarged movable onboard boxes. Static domains include valid VGM, resolvable future POD, physical length acceptance on every footprint cell, reefer plug on **any** footprint cell, on/under-deck requests, crane reach, mapped hatch covers, envelope height, DG stowage category and Document of Compliance. Dynamic segregation and specials remain for native replay. In the activated regime DG static geometry is deliberately optimistic over support heights.

The active catalogue context must remain unchanged for an engine's lifetime (`feasibility/engine.py`). Inspect `rule_freeze_v1_enabled()` or `T.extra['rule_freeze_v1']`; do not silently switch regimes:

| Rule | Legacy D25 | Rule freeze v1 |
|---|---|---|
| E2 weight order | upper class rank <= minimum lower rank + `T.e2_tol` | upper gross kg <= minimum lower gross kg + resolved `weight_order_tolerance_kg` |
| E1 | no threshold-heavy above any nonheavy | same gross-mass order as E2 |
| E6 heavy deck cargo | hard conditional departure check | soft preference |
| Additional active checks | legacy local/completion rules | handling refusals, direct delivery, physical crossing/self-blocking, fixed geometry, OOG killed cells |

All ordering uses whole-box gross mass, including spanning long boxes, never mass/2. Missing or invalid ordering parameters must not be given invented defaults. Sources: `_engine.py:_slot_features`, `tables.py:class_params`, `checker/rules_weight.py:gross_weight_order_violations`.

## Assignment constraints required before replay

1. Assign every required box exactly once; allow no overlapping **physical cells**, not just no duplicate reported slot strings. A 20 occupies one odd-bay cell. A 40 or 45 occupies two neighboring odd-bay cells at an even-bay reported address. A 45 consumes two physical cells, although nominal TEU accounting is 2.25. Both cells must accept the box's own length.
2. Use each stack's actual `stack.cell_ids` bottom-to-top order. Occupancy is a prefix without gaps. A long box requires both target cells to be the next free cells of their respective stacks, so independent chain assignments cannot disagree about occupied tier ordinals.
3. Add mass/1 for twenties, mass/2 for longs to **each** supporting stack, and add the whole box height (plus OOG over-height) to **each**. Bound cumulative height by `T.stack_hmax` and the absolute D55 roof cap `T.extra['stack_visibility_top']` where finite. D55 is necessary even if `completion_screens=0`; actual visibility remains a later full-checker condition.
4. Respect both native and checker mass envelopes. Native `_slot_features` uses 20-only/40-only/mixed ratings from the actual prefix composition. `checker/rules.py` instead selects the 20 rating whenever a twenty exists, otherwise the 40 rating for all-40/45. Therefore mixed stacks may need the minimum of two distinct final ratings; also every intermediate native prefix must pass. `src/stowage/solver/construct.py:_build_assignment_model` documents separate `checker_stack_mass` and `mask_stack_mass` rows.
5. Never put 20 above 40/45. The full securing checker rejects **any mixed 20/long hold column**, even 40 above 20 that the local placement mask can admit. Deck columns can have twenties below longs if other checks pass. A top-stowage request or over-height box closes each support stack to additional boxes.
6. Each long box needs matching **actual support roof height** on its two physical supports, within 1e-6 m. Merely matching tier numbers is insufficient when standard and high-cube boxes differ. Use base elevations plus actual lower heights; prefer homogeneous paired long columns or explicitly enforce roof equations. The checker propagates a long box's common roof to both columns in `(tier, slot)` order. See `solver/securing_geometry.py` and `checker/lashing.py:_load_graph`. `securing_state_violations(instance, expander)` cheaply detects definite completed-stow geometry failures before an expensive finisher call.
7. Keep reefers, DG and special bookings in their true domains. DG pair segregation is location dependent and tested against all DG aboard. OOG reserves neighboring space and can poison capacity elsewhere; heat clearance depends on actual vertical geometry. These cannot be represented by a universal box-only chain edge. Let native replay separate them, or add sound lazy pair/assignment cuts after a specific violation.
8. Legacy E6 is not 'all heavy must be under deck' and not 'all hold cells must be filled'. For every heavy box on deck, there must be no compatible empty hold opportunity at departure under the checker's full static placement assessment. Blocking can result from occupancy, weight order, mass, height, length, specials or DG. Query `checker.rules_weight.e6_departure_violations`; forcing full holds is a sufficient restriction only and can make a feasible map appear impossible.

## Replay and objective protocol

`alns.Sequencer(problem).order(assignment)` uses real support precedences (long boxes participate in both columns), then puts every assigned hold box before every deck box on the same hatch. Ready boxes are chosen by yard accessibility. This is a sequencing heuristic, not legality proof. Interleaving deck before hold triggers the expander's legal hatch clearing/restow path and can evict boxes that the assignment thought finished.

Replay returned `Place` actions with the native engine: test `is_legal`, `push`, and finally `is_complete`; always `pop` every successful push in `finally` because the context engine is shared. Track assignment occupancy independently: an action into an occupied reported footprint can legally restow an existing box rather than complete a distinct placement. Check the final engine/expander location of every required load, not only action count. Native legality also requires yard blockers to have real legal relocation targets; a perfect static ship packing can still fail this sequencing stage.

`problem.score(actions)` expands and applies the actual moves and returns real weighted nonproductive lifts if replay is legal. It does not add ballast and does not establish a full-checker pass. Lifts are yard relocations plus current/future restows plus current/future hatch-cover lifts, weighted by `instance.objective`; port or tier surrogate cost is not a lift measurement. Any complete mask-legal candidate must still go to the finisher and unchanged `stowage.checker.check`. Report exact later failures (securing, visibility, draft, trim, etc.) for each completion, not just constructor success.

## Hall/flow certificates that are sound

For static geometry, build source -> box arcs of capacity required physical units (1/2), box -> each cell appearing in any allowed slot, cell -> sink capacity 1. Splitting a long box's units and permitting nonsimultaneous/nonadjacent cells makes this a **relaxation**. A max flow below total demand gives a valid necessary capacity certificate, despite its optimism; a full flow proves only the relaxation. Recover a deficient box family and its reachable cell union from the residual min cut. A restricted-family Hall test can expose under-deck, reefer, DG-domain or length pools missed by overall TEU slack.

Do not treat every overlapping even-bay slot as an independent capacity-1 resource: different long slots can share cells. Either use a matching over a set of genuinely disjoint stack pairs, or keep real cell capacity and label the resulting flow a relaxation. Unit box->slot matching ignoring overlap is also optimistic and can certify a shortage, but cannot certify a legal packing.

A prefix certificate says **this committed no-undo prefix cannot finish**. It proves map infeasibility only at the root under domains/capacities that are supersets of every legal full plan. Never use extra port blocking, homogeneous column restrictions, full-hold E6 proposals, conservative securing screens, arbitrary roof choices or a chain-cover heuristic in a global impossibility claim. Timeout/UNKNOWN and bounded search exhaustion are not certificates.

Native C1 already implements static class flow, nested weight/rank Hall for all and twenty-only cargo, long pair/rank bounds, aggregate optimistic stack mass, restricted compatibility classes, legacy E6 blocking, and DG-class availability. New flow machinery should either return an explicit independently auditable cut or coordinate assignments; merely duplicating these necessary counts will not close the discrete packing gap.

## Failure evidence to target

`strategy/MEDIUM-WALL.md` records constructor depth 120/121 on 185100000 and 185100010, 228/229 on 185100101/110, and 426/428 or 427/428 on 185100200/201/210. The defaults on 185100100 and 185100111 did finish cargo but then failed securing/visibility, with draft/trim additionally on 100. None proves whole-map feasibility or impossibility. Long/rank pools of 29 long boxes versus 26 disjoint slots and 43 twenties versus 39 compatible cell units show that lost compatibility, rather than total empty volume, is decisive at some prefixes.

`scale/barrier-ranking.md` attributes 61/64 carrier-arm failures to exhausted 512-backtrack search, with 43 'no box fits' and 19 bounded exhaustion messages. This motivates early domain reservation and joint discrete packing. Both documents explicitly limit capacity witnesses to their relaxations and distinguish completed cargo from full-checker pass.

## Verification of this note

Cross-checked the source definitions above, the public feasibility API documentation, `carrier_v6.py`, `alns.Problem`/`Sequencer`/`Rebuilder`, both requested failure reports and the existing joint-assignment model. `carrier_v6` preserves native replay/terminal/finisher/checker obligations; its extra deck geometry screen is a conservative constructive restriction, explicitly not a certificate. No map run or numerical performance claim is made here. `lab/README.md` was absent during this audit; use the shared scorer when it appears.
