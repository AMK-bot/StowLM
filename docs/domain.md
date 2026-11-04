# Domain primer — stowage planning and dangerous goods

Items marked **(verify)** come from general knowledge and must be checked against the primary source before the
checker relies on them. When verified, remove the mark and cite the source.

## Slot addressing (BAPLIE / ISO 9711 convention)

- Position = `BBBRRTT`: bay (3 digits), row (2), tier (2).
- Bays: odd numbers = 20ft positions; even numbers = a 40ft spanning two adjacent odd bays (e.g. 02 spans 01+03).
- Rows: numbered out from the centreline; odd to starboard, even to port, `00` = centre row when the row count is odd.
- Tiers: below deck from 02 upward in steps of 2; on deck from 82 (sometimes 80) upward. **(verify exact conventions)**
- BAPLIE = SMDG's UN/EDIFACT bay-plan message (current major version 3.x). We use its fields as our data schema.
- Container IDs: ISO 6346 (owner code + category + serial + check digit). Size-type: ISO 6346 codes (e.g. 22G1, 42G1, 45R1).

## Constraint catalogue (what the checker enforces)

| Group | Constraint | Notes |
|---|---|---|
| Physical | One container per slot; 20ft vs 40ft slot compatibility; 20s not on top of 40s **(verify)** | |
| Physical | Stack weight limits per stack; tier height limits (incl. high cubes) | Limits come from the synthetic vessel's loading manual |
| Physical | Reefers only in slots with plugs | |
| Rotation | Earlier-port discharges must be physically accessible, directly or through explicitly planned, legal and counted restows | Includes hatch-cover access: clear on-deck blockers before opening a cover for below-deck work. Unplanned or infeasible clearance fails; a feasible accounted restow is allowed (D11; catalogue `expert.rotation.no_overstow` and `expert.rotation.hatch_overstow`). |
| DG | IMDG segregation between classes (4 levels: "away from", "separated from", "separated by a complete compartment or hold from", "separated longitudinally by an intervening complete compartment or hold from") | IMDG 42-24 table 7.2.4 + container-ship rules 7.4.3 (distances in container spaces, open/closed, on/under deck) — free as IMO resolution MSC.556(108) (D10, `docs/data-sources.md` §4) |
| DG | Stowage categories and special provisions (e.g. on deck only, away from living quarters, protected from heat) | **(verify)** |
| Stability | Displacement/draft within limits; GM ≥ minimum after free-surface correction | |
| Stability | IMO 2008 Intact Stability Code GZ-curve criteria: area to 30° ≥ 0.055 m·rad, to 40° ≥ 0.09, 30–40° ≥ 0.03; GZ ≥ 0.20 m at ≥ 30°; max GZ at ≥ 25°; initial GM ≥ 0.15 m **(verify)** | Needs large-angle stability (cross curves from hull geometry) |
| Stability | Trim and list within limits | |
| Strength | Shear force and bending moment along the hull within limits at each station | Integrate (weight − buoyancy) along length |
| Visibility | SOLAS V/22: view of the sea surface from the conning position not obscured by more than 2 ship lengths or 500 m, whichever is less **(verify)** | Limits on-deck stack heights forward of the bridge |
| Lashing | Lashing forces | **Approximation** — class-society rules not public (see decisions D2) |
| Expert | Heavy containers under deck, for stability (E6) | "Heavy" threshold and what to do when under-deck is full: TBD |
| Expert | Weight order in a stack: heavy at the bottom, light on top; the stack total within its stacking-weight limit (E1, E2, E7) | Strict kg or weight classes, tolerance TBD |
| Expert | Special stowage requests per box: under deck, on deck, away from heat, top stowage (E8) | Booking attribute; heat sources come from the vessel (engine room, heated tanks) |
| Expert | Out-of-gauge (OOG) cargo (E9) | Over-height: top of stack only. Over-width/length: blocks neighbouring slots **(verify)** |
| Port | Height limit per port, e.g. only 2 or 3 high (E3) | Yard equipment and/or quay crane: TBD; a port parameter |
| Moves | Physical access: only the top box is reachable; below deck needs the hatch cover clear; ship stacks fill bottom-up | Move engine (plan 2.3) |

## Moves: the objective (D11)

- A **move** is one lift: by a quay crane between ship and quay, or by yard equipment inside the yard.
- **Productive moves** are the loads and discharges, which are fixed by the lists. What we minimise is the
  **non-productive** moves:
  - **Rehandle** (relocation): in the yard, lifting a box off to reach the one below it.
  - **Restow/shift**: moving a shipboard box to permit access or change its position: **2 crane moves via the quay,
    1 when transferred directly between ship slots**. Keep both `restowed_boxes` and `crane_moves`: a paper's
    “one relocation” need not mean one physical lift. [World Bank/PPIAF, Port Reform Toolkit, module 4](https://www.ppiaf.org/sites/ppiaf.org/files/documents/toolkits/Portoolkit/Toolkit/module4/full.html).
  - **Hatch-cover moves**: clear the affected cover, lift it, perform below-deck work, then replace it. The toolkit
    counts **2 moves per cover removed and replaced**; the project's equal weighting still needs expert confirmation.
    Count each cover opening once, separately from the boxes cleared above it. [Same toolkit](https://www.ppiaf.org/sites/ppiaf.org/files/documents/toolkits/Portoolkit/Toolkit/module4/full.html).
- **Overstow** on the ship = a box sitting above one that leaves at an earlier port → a restow later. The yard has the
  same problem: a box sitting above one that must load first → a rehandle now.
  A no-overstow arrangement avoids those later restows; it is not an unconditional hard rule under D11. Physical
  access, safety and accounting for every required restow remain hard requirements.
- **How yard and ship rules collide:**
  - **Project inference:** within a chosen ship stack, “heavy low” and bottom-up loading put lower heavy boxes before
    upper light boxes. A no-overstow stack similarly puts later-POD boxes below earlier-POD boxes. These are local
    precedence rules, not a requirement to load every heavy/later-POD box before every other box in the vessel.
  - Boxes of the same category (POD, size, weight class, no DG/reefer) are interchangeable on the ship, so a plan can
    take whichever is on top **only if every checker-relevant attribute and booking requirement agrees**. Literature
    calls freely ordered retrieval groups “duplicate priorities”; a fixed ship plan instead induces a partial order
    (some pairs must load in sequence, others need not). [Tanaka & Voß, 2019, introduction](https://doi.org/10.1016/j.ejor.2019.06.014).
    **Open (plan 0.4):** the size of StowLM's savings from interchangeability is unmeasured; the literature establishes
    the model, not a transferable percentage for our POD/size/weight-class categories.

## Yard and port state

- **Layout.** A yard is blocks of bays × rows × tiers. The equipment limits stack height: RTGs, straddle carriers and
  reach stackers differ, and so do ports **(verify typical heights)**. The expert's "max crane height" may mean this.
- **How export boxes arrive.** They gate in over several days before the ship's cut-off, in an order the terminal
  doesn't control. The terminal places them "good enough", clustered by vessel, POD and weight class, because keeping
  a perfect order costs moves itself.
- **Block relocation problem (BRP, or container relocation problem/CRP).** Given stacks and retrieval priorities,
  retrieve every box with the fewest extra stack-to-stack moves. It is **NP-hard**: there is no known algorithm
  guaranteed to find the optimum quickly for every input. [Caserta, Schwarze & Voß, 2012, complexity proof](https://doi.org/10.1016/j.ejor.2011.12.039).
  Each distinct box above a strictly earlier-priority box must relocate at least once; see the assumptions and
  formulas below before applying this bound to a freely chosen loading sequence.
- **Pre-marshalling.** Re-sort the yard before the ship arrives so that loading needs no rehandles. The re-sorting
  costs moves itself, so it is only worth it when it saves more (stage C).
- **Our stage A problem.** Choose the retrieval order and the ship slots together, under all the ship's rules. In
  practice the carrier's planner makes a pre-stow by category and the terminal's planner picks the actual boxes and
  their sequence against the yard **(verify with the expert)**. Our planner does both.

## Relocation and restow literature

### Which problem are we solving?

- **Restricted BRP:** relocate only boxes blocking the next retrieval. **Unrestricted BRP:** allow any legal
  top-of-stack relocation, including preparation for a future retrieval. Restricted solutions remain feasible in
  the unrestricted problem, but their optimum can be worse. A “restricted” optimum is therefore not a lower bound
  for our unrestricted action space. [Tanaka & Mizuno, 2018, problem definition](https://doi.org/10.1016/j.cor.2018.02.019).
- **Grouped priorities:** all boxes in a group must leave before the next group, but identities inside a group can
  be chosen freely. **BRP with a stowage plan (BRPSP):** a given ship stack imposes bottom-before-top loading;
  independent stacks need not share a total retrieval order. Tanaka & Voß solve both restricted and unrestricted
  BRPSP with iterative deepening branch-and-bound and precedence-cycle/relaxation lower bounds. “Branch-and-bound”
  explores candidate plans while pruning branches whose best possible cost cannot beat the best complete plan;
  iterative deepening raises the permitted cost until a solution is proved optimal.
  [Tanaka & Voß, 2019](https://doi.org/10.1016/j.ejor.2019.06.014).
- **Multi-port restow:** decide ship placements over successive calls, including when a through-container is moved
  temporarily before its destination. Avriel et al.'s foundational model minimizes these shifts; its accessible
  abstract explicitly excludes stability and several placement constraints. A low shift count in that model is
  not evidence of seaworthiness. [Avriel, Penn, Shpirer & Witteboon, 1998](https://doi.org/10.1023/A:1018956823693).
- **Integrated yard + stowage:** choose yard retrieval/relocation and ship assignments jointly rather than give
  the yard a fixed list. Junqueira et al.'s binary optimization model spans the voyage and its port yards, but uses
  restricted CRP and does not represent hatch covers or stability; it is a useful move-planning comparator, not
  the full StowLM checker. [Junqueira et al., 2020 preprint, §§2–3](https://arxiv.org/html/2006.06795v1).

An ordinary **stack overstow** is a through-box above a box discharged now. A **hatch overstow** is a through-box
above a cover that must open for loading or discharge below it; the on-deck box need not share the below-deck
box's vertical stack. These must be checked separately. Multi-port formulations also distinguish mandatory restows
from voluntary early moves that may improve later calls. [van Twiller et al., 2023, §§2 and 4.2](https://arxiv.org/html/2307.07573).

### For 3.6: lower bounds ready to implement

A **lower bound** is a cost no feasible plan can beat. A relaxed problem drops restrictions, so its exact optimum
is a lower bound on the more constrained problem. A heuristic plan supplies an **upper** bound, even if it is very
good. Strong BRP bounds formalize mandatory moves using badly/well-placed boxes and different relocation types.
[Lu, Zeng & Liu, §III](https://arxiv.org/html/1904.03347).

Use smaller priority numbers for earlier retrieval; yard stacks are listed bottom to top. Count physical moves,
not ordered pairs of boxes. The grouped/partial-order and hatch formulas below are **project derivations**, with
their short validity arguments given explicitly; they are not claimed as new published bounds.

1. **Yard blocking count (LB1 / counting bound).** For fixed retrieval priorities `p`, use
   `LB_yard = sum_c 1[there exists d below c in its current stack with p(d) < p(c)]`.
   Count each `c` once, even if it blocks several boxes. Valid for restricted and unrestricted individual moves.
   The standard counting argument is in [Borjian et al., 2015, §2.2.1](https://arxiv.org/html/1505.04229v2).
   **Grouped extension:** use strict `<`; equal-category boxes do not block each other. Every counted box must
   leave its stack before a strictly earlier group underneath can be retrieved, which proves validity even when
   identities within a group are interchangeable. This does **not** impose an order between distinct categories
   unless the task actually requires all boxes of one category before all of the other.
2. **Restricted look-ahead counting bound (`S_k`).** For distinct, fixed priorities only: start with `LB1` and a copy
   of the bay; visit targets in priority order (skip targets already discarded), discard the target and all its remaining blockers after processing
   it. Before discarding, let `MM = max_s min_priority(stack_s)` with an empty stack's minimum `+infinity`. Add one
   for each current blocker `r` with `p(r) > MM`; stop after `k` targets or when an empty stack appears. Thus
   `S_k = LB1 + sum_targets sum_current_blockers 1[p(r) > MM]`. Discarding blockers is optimistic; each added term
   certifies an unavoidable relocation that leaves its box blocking again. [Borjian et al., Eq. (3)](https://arxiv.org/html/1505.04229v2).
   **Do not use this bound for unrestricted preparation or freely reordered groups without a new proof.**
3. **Partial-order / interchangeable yard bound.** If a fixed ship plan or booking induces only some precedence
   requirements, count `c` once only when some box `d` below it is guaranteed to be retrieved before `c` in **every**
   admissible assignment/order. Then `LB_PO = |{c: exists such d}|`. Guaranteed precedence forces `c` to relocate;
   this is the same blocking proof with fewer required pairs. A category match alone does not guarantee precedence.
   A stronger safe alternative is `min_{a in A} LB1(a)` over a set `A` containing **all** feasible assignments and
   retrieval orders (a superset is safe); choosing one arbitrary ID order or minimizing over a sampled subset is
   unsafe. If no precedence is guaranteed before choosing ship slots, the safe bound can be zero. The underlying
   partial-order model is [Tanaka & Voß's BRPSP](https://doi.org/10.1016/j.ejor.2019.06.014); this extension is our
   inference, not a claim that their distinct-ID bounds automatically transfer.
4. **Restows forced at the current call.** For a fixed arrival state at port `q`, let `D_q` be boxes due to discharge
   now; let `H_q` be the distinct closed covers that must open for mandatory below-deck work. Let `U_q` be the union
   of through-boxes above any `D_q` in its stack and through-boxes obstructing any cover in `H_q`. Then
   `LB_restow_boxes(q)=|U_q|`; `LB_restow_lifts(q)=|U_q|` if direct ship shifts are allowed, or `2|U_q|` if every
   clearance must go via quay and all through-boxes must be back aboard at departure. The union avoids counting
   one box twice. Each counted box physically obstructs required access. The stack-shift premise follows
   [Avriel et al., 1998](https://doi.org/10.1023/A:1018956823693); hatch obstruction is described by
   [van Twiller et al., §2](https://arxiv.org/html/2307.07573).
5. **Cover lifts forced at the current call.** With the same `H_q`, `LB_cover(q)=2|H_q|` if covers start and must
   end closed, or `|H_q|` if only opening is required. This counts unique physical covers, not below-deck boxes or
   stack pairs. The two-move round-trip convention is from the
   [World Bank/PPIAF toolkit](https://www.ppiaf.org/sites/ppiaf.org/files/documents/toolkits/Portoolkit/Toolkit/module4/full.html).
   **Assumptions:** `H_q` comes from actual cover geometry and mandatory work; already-open covers and optional
   below-deck loads cannot be charged as unavoidable closed-cover openings.
6. **Safe combination.** Add bounds only for disjoint cost components: yard relocation lifts + ship clearance lifts
   + cover lifts, under the same counting convention. Use `max(LB1,S_k,...)`, not their sum, when bounds cover the
   same yard relocations. On a search node, add incurred moves to a bound on **remaining** moves. Future call states
   depend on earlier placement/restow decisions: summing bounds from one assumed future stowage is not a voyage-wide
   bound while that stowage can change. Minimize over admissible continuations, or keep only present unavoidable
   costs. These are direct lower-bound accounting consequences of the preceding proofs.

Do not import every published BRP bound unchanged. Lu et al.'s stronger unrestricted substructure bounds and
Jin–Tanaka's blocking-layer bounds are candidates after matching the exact variant and implementing their proofs;
their validity does not follow merely from the name “BRP”. [Lu et al., §III](https://arxiv.org/html/1904.03347),
[Jin & Tanaka, 2023](https://doi.org/10.1016/j.ejor.2022.04.006).

### Solver and learning baselines for 4.6 / 4.7

These are strong documented reference methods for their stated variants, not a claim of one universally strongest
algorithm. Published times are on the authors' machines and are not predictions for this Mac.

| Problem / method | Verified scale and result | Source |
|---|---|---|
| Unrestricted BRP, distinct and duplicate priorities: Jin–Tanaka iterative deepening branch-and-bound (`Algo-TS`) | With a 1,800 s per-instance limit, solves 754/760 CVS, 8,793/9,000 ZQLZ and 35,729/36,000 TT instances to optimality. All tested TT cases with initial height `T <= 5` solved. A 1 s variant is compared with greedy look-ahead; the authors report competitive small/medium results, not universal optimality in 1 s. | [Jin & Tanaka, 2023, §§7.3, 7.7](https://doi.org/10.1016/j.ejor.2022.04.006); [public GPL-3.0 C99 implementation](https://github.com/jinboszu/ucrp-idbb) |
| Restricted BRP: bounded beam search (retains only promising candidate states) plus an assignment lower bound | Large `lbri` cases include 1,000 stacks, 10 tiers, 9,990–9,999 boxes. Table 12 reports mean 8,109.102 relocations in 1.037 s for this class; heuristic, not certified optimum. One-second search limits can be exceeded slightly by finishing work. | [Bacci, Mattia & Ventura, §8/Table 12](https://arxiv.org/html/2206.12303) |
| Unrestricted BRP: Feillet–Parragh–Tricoire local search | Reoptimizes one box's move sequence using dynamic programming. For 20×20 initial bays and `H_max=H+2`, Table 3 gives 0.49 s mean from SmSEQ-2 starts; at 100×100, 85,194.16 s. “Up to 50% improvement” is versus constructive starts, not versus optimum; this is not always a fast heuristic. | [Feillet et al., 2019, §4/Tables 2–3](https://arxiv.org/html/1809.08201v2) |
| Integrated voyage + restricted yard CRP: binary model, HR1/HR2 heuristics and solver warm starts | Instance 3A has 92 containers over 10 ports: Gurobi proves objective 2 in 3,260.94 s. Instance 4A has 137 containers/15 ports: Gurobi's objective 3 still has 66.67% gap after 432,000.62 s. HR1/HR2 are also tested up to 10,000 boxes; good large heuristic results do not certify an optimum. | [Junqueira et al., Tables 1, 3, 8](https://arxiv.org/html/2006.06795v1) |

For a first reproducible exact yard comparator, prefer the publicly available Jin–Tanaka implementation after
matching its priorities, height limits and permitted moves; keep its GPL licence separate from any project-code
reuse. For integrated planning, use the published binary model as a small-instance reference and compare against
our full checker separately. These are project recommendations based on the table, not reproduced measurements.

Two further integrated baselines deserve reproduction. Zhu (2022) combines ship assignment, individual weights,
yard retrieval and stability indicators using a two-stage rule/genetic heuristic; large cases load 700–1,100 TEU
over four/five-port routes, but only 20-foot general cargo is modeled and hatch shifts are future work.
[Zhu, 2022, §§3–6](https://www.mdpi.com/2071-1050/14/20/13376).
Gao & Zhen (2024) jointly address yard block allocation, ship slots and loading sequence through three models and
**adaptive large neighborhood search (ALNS)**: repeatedly remove/reinsert parts of a plan, adjusting which repair
rules are favored from their performance. **Open:** its precise size/time comparison was not verified from the
accessible publisher preview; do not use it as a quantitative performance claim.
[Gao & Zhen, 2024, publisher abstract and algorithm section](https://www.sciencedirect.com/science/article/pii/S1366554524000103).

Learning to reduce relocations already exists. Liu et al. use tabular **Q-learning** (learn a score for taking an
action in a state from its later reward) for restricted BRP with duplicate priorities. Their reported mean
improvement is 9.82% over a heuristic baseline, with a 0.41% mean gap to their best exact-algorithm results;
some exact runs have a 30-minute cutoff, so the latter is not always a gap to proven optimum.
[Liu et al., 2025, §5](https://link.springer.com/article/10.1007/s10732-024-09545-y).
Tang et al. study a different 3D stochastic CRP, using predicted retrieval dates and deep reinforcement learning;
it also minimizes crane travel, so its objective is not directly comparable to our move-only score.
[Tang et al., 2024, abstract](https://doi.org/10.1016/j.cie.2024.110111).
DQN-LNS also combines a deep Q-network (a neural action-value predictor) with neighborhood search for multi-port
river–sea stowage; learning-guided move optimization is therefore already established beyond isolated yard bays.
[Advancing multi-port container stowage efficiency, 2024, abstract](https://www.sciencedirect.com/science/article/abs/pii/S0950705124007081).
The LLM novelty check and ITU/LMASS transfer details are in [frontier.md](frontier.md); public instances and licences
are in [data-sources.md](data-sources.md#7-public-moveyard-benchmarks).

## Pre-marshalling and yard allocation

**Pre-marshalling** rearranges a yard bay before retrieval starts: move one top container to another non-full stack until every stack can be emptied in priority order without further relocations. Containers stay in the bay during this preparation. BRP instead interleaves relocations with departures. With smaller group numbers meaning earlier departures, the CPMP goal is `g[s,t] >= g[s,t+1]` from bottom to top; equal groups can be retrieved in either order. Minimise the number of stack-to-stack moves, subject to stack height and top-access rules. These are idealised single-bay models; ship stability, DG and crane interference are additional constraints in StowLM. [Bortfeldt–Forster 2012](https://doi.org/10.1016/j.ejor.2011.10.005), [Kimms–Wilschewski 2025, §2](https://doi.org/10.1007/s00291-025-00825-y).

**Yard allocation / stack loading** chooses the initial storage stack when each container arrives. A cheap surrogate objective counts containers blocking earlier departures; optimising that count does not necessarily minimise later moves. An integrated stack-loading + BRP/CPMP model also plans the subsequent relocations. Kimms–Wilschewski provide a grouped-priority mixed-integer formulation (integer decisions plus linear constraints) and public instances/results; this integration concerns yard storage and retrieval, not full vessel stowage physics. [Paper §§2.3, 4–5](https://doi.org/10.1007/s00291-025-00825-y), [dataset v2](https://berd-platform.de/records/xwmmg-dvw42).

**For 3.6 — safe CPMP bound:** define `B = sum_{s,t} 1[g[s,t] > min_{u<t} g[s,u]]`, with the minimum over an empty set treated as infinity. Then `OPT_CPMP >= B`: every initially blocking container must leave its current stack at least once to expose an earlier group below. Count each container once, not each inverted pair. This direct bound remains valid with interchangeable containers within a group: equality creates no blocker. This is a lower bound (a number no feasible solution can beat), not an estimate of the exact optimum. [Tanaka–Tierney 2018, §5](https://doi.org/10.1016/j.ejor.2017.05.046). **Project inference:** this proof applies only when those groups genuinely constrain retrieval; an arbitrary ordering of interchangeable same-category IDs overstates the bound. Use a relaxation of StowLM's allowed category orders if several categories can fill the next ship slot.

Stronger CPMP bounds include Bortfeldt–Forster's bound, the IBF family and the extensions in Tanaka et al. (2019); these account for extra temporary moves that the direct blocker count misses. Do not treat learned cost predictions as certified lower bounds. Implement the direct bound first; port stronger bounds only with the full algorithm and its assumptions. [Tanaka et al. 2019, §5](https://doi.org/10.1016/j.ejor.2019.04.005).

**Solvers and evidence:** iterative deepening branch-and-bound explores move sequences with successively larger move limits; a proven bound rules out branches that cannot beat the incumbent. Tanaka–Tierney (2018) report 161 additional proven optima over the prior methods on two difficult datasets, and solve three other datasets in seconds. Tanaka et al. (2019) extend the method with bounds, dominance rules and remembered partial solutions; they report 8% more optima overall and solve most tested 7-tier, 9–10-stack cases. These are paper-specific comparisons, not a current universal ranking. The author warns that original `pmp-1.0` has a stack-scanning bug and that dominance rules were corrected following Jin–Yu (2021); use corrected v1.02 if adopting that implementation. [2018](https://doi.org/10.1016/j.ejor.2017.05.046), [2019](https://doi.org/10.1016/j.ejor.2019.04.005), [author corrections and code](https://sites.google.com/site/shunjitanaka/pmp).

**Learning already exists:** DLTS learns move probabilities and remaining-cost estimates from near-optimal solutions, then guides heuristic tree search. Its input is a stack/tier matrix of group values; it filters infeasible moves and immediate reversals. The paper reports gaps below 2% on its realistic-size tests. Its learned pruning can discard an optimal branch: use its cost predictor for ranking, not a proof of optimality. [Hottung–Tanaka–Tierney, 2020, §§3–6](https://arxiv.org/html/1709.09972). MuZero CPMP work also explicitly describes action masking and a lower-bound-aware reward. [Choi–Lee–Chae 2021](https://doi.org/10.23178/jlst.2.2.202112.002). For initial stacking, a self-attention DRL policy minimises future blockages rather than executing CPMP retrieval. [Jin et al. 2023](https://doi.org/10.1016/j.engappai.2023.106508).

**LLM novelty qualification:** CEoH (2025) already asks LLMs to generate constructive heuristics for unit-load pre-marshalling; A-CEoH (2026) generates A* guiding heuristics for that problem. These are warehouse UPMP studies, not evidence that an LLM directly decodes a full container-ship move plan under its checker. Thus “first LLM to minimise moves” is not defensible; the proposed difference is direct plan generation for the combined ship/yard task, with explicit feasibility enforcement and independent checking. [CEoH](https://arxiv.org/abs/2503.03350), [A-CEoH](https://arxiv.org/abs/2601.19622). UPMP here uses one-tier, single-direction-access storage, unlike vertical container stacks; see [CEoH §2](https://arxiv.org/html/2503.03350).

Public instance families and licence limitations: see [data-sources.md §7](data-sources.md#7-public-moveyard-benchmarks). No new yard-allocation dataset here reproduces a real terminal's confidential layout or operating policy.

## Expert input (provisional until confirmed)

**Sources:**
- E0–E5: the user's summary of the 27 Oct 2026 call.
- E6–E11: the expert's written "Rule for making stowage plan", forwarded by the user on 27 Oct 2026. Paraphrased:
  1. Collect every container's details and separate them by requirement: destination port, numbers of 20' and 40'
     boxes, special requests (under deck, on deck, away from heat, top stowage), and special cargo (DG and out of
     gauge).
  2. Make a pre-loading plan from those details.
  3. The terminal planner adjusts the pre-loading plan to match where the containers sit in the yard, to cut shifting
     and moves (productivity).
  4. Heavy containers go under deck, for stability.
  5. Within a stack ("row", the vertical position), load by weight: heavy at the bottom, light on top. Every stack
     stays within its stacking-weight limit, to avoid damaging the ship's floor.
  6. DG containers follow the segregation table (IMDG).

| ID | What | Scope | Hard/soft | Status |
|---|---|---|---|---|
| E0 | Objective: fewest container moves | — | — | D11 |
| E1 | Heavy containers at the bottom of a stack | global | hard (layover: may be tolerated if inherited, D11) | provisional |
| E2 | No heavier container on top of a lighter one | global | hard; tolerance TBD | provisional |
| E3 | Max stack height set by crane, e.g. 2 or 3 high, varies by port | port | hard | provisional — which crane? |
| E4 | Yard kept "good enough": clusters by ship and port, soon-to-leave on top, with noise | port state | — | modelled in the simulator (plan 3.4) |
| E5 | Layover: fit the rest of the cargo around what's on board, following the rules "not to an absolute" | stage B | — | interpreted in D11 |
| E6 | Heavy containers under deck, to keep the vessel stable (rule 4) | global | expert says "must"; hard? | provisional — threshold for "heavy"; what if under deck is full? |
| E7 | Weight order within a stack, and each stack within its stacking-weight limit to avoid floor damage (rule 5) | global | hard (the limit); order: tolerance TBD | confirms and refines E1, E2 |
| E8 | Special stowage requests: under deck, on deck, away from heat, top stowage (rule 1) | per box | hard (a booking condition) | provisional — "top stowage" = top tier of its stack? |
| E9 | Out-of-gauge (OOG) cargo (rule 1) | per box | hard | provisional — slot rules to confirm |
| E10 | Two-step practice: the carrier makes a pre-loading plan from the cargo details; the terminal planner then adjusts it to the yard to cut moves (rules 2–3) | process | — | answers question 7; basis of the industry baseline (D14) |
| E11 | DG follows the IMDG segregation table (rule 6) | global | hard | already encoded (D10) |

More rules are expected from the expert. Each goes into the rule catalogue (plan 1.2, X1).

### Written answers to questions 1–14 (28 Oct 2026)

Verbatim (company names removed): `docs/expert/2026-10-28-written-answers.md` (kept private, not in the public repo). What changed (decisions D61–D63):

| ID | What the expert said | Rule now | Was |
|---|---|---|---|
| E0 | Q1: every lift is one move, quay or yard; yard shifting also slows cargo operations | D11 weights all 1: **confirmed** | provisional |
| E1, E2, E7 | Q2, Q11: on the ship, planners order by **actual weight** (heaviest lowest, lighter on top); in the yard, gate-in separates by weight **class** | Ship stacks: gross mass non-increasing upward, in kg (D61). Yard: weight class is a placement cluster key only | D25 weight classes on the ship |
| E3 | Q3: "max crane height" = how many tiers the crane can work; a stack at that height blocks the crane from crossing over it, so its top box must be shifted first; most Laem Chabang terminals: 8 tiers | **Max crane height H per port**, for the yard and the ship: no stack above H; no lift may pass over a stack at height H (D62, user's specification). Laem Chabang-like H = 8 | 5 stored / 6 clearance yard tiers; 7 on-deck quay-crane tiers (synthetic) |
| E4 | Q5: the gate-in clustering is right; congestion mixes boxes and causes more dig-outs; terminals aim to cut moves; yard–quay distance (up to ~5–6 km) costs time, not moves | simulator noise models congestion: **confirmed** | — |
| E6 | Q11: heavy boxes may go on deck or under deck, as long as weight order holds; the ship *prefers* heavy under deck because the master adjusts stability more easily | **Soft preference** (a D11 level-4 tie-break), never a rejection (D61) | hard |
| E8 | Q12: top stowage and away from heat mean what we assumed; no kill slots for special stowage; if no position exists, the carrier asks the shipper to waive it and amends the booking | Binding, as hard. We can't model the shipper's waiver, so an unplaceable request makes the instance infeasible (D61) | hard (unchanged) |
| E9 | Q13: OOG is re-measured; it kills neighbouring slots ("broken space"); it can go on deck or under deck, not only top stow; the planner picks the safest, least-broken-space position | OOG allowed under deck where physically possible; killed slots enforced and reported; fewer killed slots is a tie-break (D61) | top-of-stack only |
| E10 | Q7, Q14: the carrier makes the pre-loading plan (ship only); the terminal planner adjusts it to the yard to cut shifting, and the carrier's central planning approves the final stow | two-step baseline **confirmed**; StowLM's plan is a proposal for carrier approval | — |
| E12 (new) | Q9: Laem Chabang: Class 7 is not handled at the port; Class 1 is picked up / delivered along ship side (not stored in the yard); OOG is handled per customer requirement | Port DG rules (D61): refused classes; direct-delivery classes | — |
| E13 (new) | Q6: stability must be watched from the start of cargo operations until departure | Stability during cargo operations is checked (D61) | departure + voyage only |
| E14 (new) | Q4: international rules first, then each port's own rules strictly; the loading port must check the discharge and transshipment ports' rules (cargo that is general at one port may be DG at another) | **Deferred** to after G1, checker + mask only (D63) | — |
| — | Q8: "yes you are correct" on our business-case numbers | impact inputs: confirmed in general (no new figures) | — |
| — | Q10: asked what we mean | follow-up question 22, to send | — |


## Questions for the expert (plan 0.3)

1. **Moves.** What counts: yard rehandles, quay-crane restows (off and back on = 2?), shifts within the ship,
   hatch-cover lifts? Do they count equally, or is a quay-crane move worth more than a yard move?
2. **Weight rules (E1, E2).** Strict by kg, or by weight class (light/medium/heavy) with a tolerance? Does weight
   order matter in the yard too?
3. **Height limit (E3).** "Max crane height": is that the yard equipment's stack height, the quay crane's reach over
   on-deck tiers, or both? Typical numbers at Laem Chabang, Singapore, and a small feeder port?
4. **Hard vs soft.** Which rules are never broken, and which are avoided but may be broken to save moves? In a
   layover, which rules apply to boxes already on board?
5. **Yard.** How are export boxes placed at gate-in (by vessel, POD, size, weight class)? How far off that is a real
   yard? How many boxes typically need digging out per call?
6. **Stability during loading.** Is it planned for intermediate states, or only for the departure condition?
7. **Who would use our tool?** Partly answered by E10: the carrier makes the pre-loading plan, and the terminal
   planner adjusts it to the yard. Still open: would the carrier planner, the terminal planner, or both use a tool
   that does both steps at once?
8. **Numbers for the impact estimate.** A typical feeder call at Laem Chabang: boxes loaded and discharged,
   rehandles and restows per call, crane moves per hour, cost of one extra move.
9. **Other port-specific rules?** For example DG areas in the yard, reefer racks, out-of-gauge cargo.
10. **Review.** Would you or a planner review some of our plans? What would "industry level" mean in numbers?

**Follow-ups to the written rules (E6–E10):**

11. **"Heavy" (E6).** What weight counts as heavy: a fixed kg threshold, or relative to the rest of the cargo? When
    under deck is full, may heavy boxes go on deck (low tiers)?
12. **Special requests (E8).** Does "top stowage" mean the top tier of the stack? Does "away from heat" mean away from
    the engine room, heated fuel tanks, or both, and by how many slots? Can a request be refused, or is it binding?
13. **Out of gauge (E9).** Over-height boxes go on top of a stack only? Do over-width boxes block the neighbouring
    slots? On deck only? Any per-port rules?
14. **The terminal adjustment (E10).** How much may the terminal change the pre-loading plan: only which actual box
    fills a slot of the same type, or also slot positions? Does the carrier approve the changes?

**Added by the build (27 Oct), from modelling choices that need confirming:**

15. **Weight classes (D25).** We use light < 10 t ≤ medium < 20 t ≤ heavy, no heavier class above a lighter one in a
    stack, and "heavy" = 20 t+ for under-deck preference. Are these the classes and thresholds planners use?
16. **Mixed 20/40 stacks.** For a 40' box sitting on two 20' stacks, do you check each 20' stack against its 20' stack
    weight limit (what we now do), or against half the 40' limit?
17. **Ballast-only voyages.** Our checker fails a ballast-only leg on the IS Code weather-table range (B/d < 3.5). Do
    feeders in ballast use model-test roll angles (MSC.1/Circ.1200) in practice?
18. **Correction to the layover question — to send (user action).** Our earlier message said inherited breaches
    are tolerated. We mean only preference rules specifically marked that way, such as heavy-low weight order.
    Stability, strength, stack-weight limits, DG segregation and lashing safety must still hold on departure;
    new placements must follow every applicable rule. Which preferences may stay as inherited, and which must
    be corrected before departure?
19. **Trim and displacement operating range — to send (user action).** For a ~500–1,700 TEU feeder, what trim range do you
    plan within, by the stern and by the head? Is any trim by the head acceptable on departure? Is there a minimum
    displacement or forward draught you keep to, apart from the load line? We currently assume trim within ±1 % of the
    ship's length, a forward draught of at least 35 % of the design draught, and displacement between lightship and
    the summer load line (D40).
20. **Hold ventilators and the flooding angle — to send (user action).** On a ~500 TEU feeder, are the cargo-hold
    ventilators treated as openings that can be closed weathertight in the stability booklet, or do they set the flooding
    angle φf? At what coaming height are they fitted? We declare them not closable, which ICLL Reg. 19(3) allows only
    for coamings of **more than** 4.5 m, so we now place them 7.5 m above deck, like our other feeders (D67). That sets φf
    and makes the IS Code B 2.3 area-to-φf criterion bind on the 500 (D58).
    **20b.** Are steel pontoon hatch covers credited as weathertight buoyant volume in a feeder's booklet (to the cover top
    or to the coaming top)? We credit the coaming volume but not the covers (D20).
21. **Heavy boxes and securing — to send (user action).** How do feeder planners stow and secure containers over 30.5 t gross
    (e.g. 32–34 t 40' or reefer boxes)? Under deck only, a lashing-software check, or not carried? Our checker follows the
    IRS guideline's 30.5 t container-weight range and fails heavier boxes, so our generator doesn't create them (D60).
22. **"Industry level", clarified — to send (user action).** Your answer to question 10 asked for detail. We mean: if our tool
    planned a Laem Chabang feeder call, what result would make you trust it? For example: every plan passes all rules,
    and it needs at most X% more moves than an expert planner's plan, or saves Y moves per call. Would you or a colleague
    look at 5–10 of our plans and tell us whether a planner would accept them?
23. **Stability during loading — to send (user action).** You said stability must be watched from the start of cargo
    operations. During loading, what limits do planners keep to: a maximum list (e.g. 2–3° for the cranes), a minimum GM,
    and harbour strength limits? Is ballast adjusted during loading? How much trim does a light feeder arrive with, and
    what trim do the cranes and cell guides accept? Is an anti-heeling tank pair assumed, and how full is it normally kept
    (we assume 50 % each side, D64)?
24. **Weight order tolerance — to send (user action).** With actual weights, may a slightly heavier box sit on a lighter
    one (e.g. within 0.5 t or 1 t), or must the order be strict? For example: may a 14.6 t box sit on a 14.4 t box? We
    currently allow no tolerance (0 kg).
25. **Yard stacking height at Laem Chabang — to send (user action).** The narrow question: do Laem Chabang yards stack laden
    boxes 8 high, or is 8 the quay crane's reach? You said most Laem Chabang quay cranes can work 8 high.
    How high are laden export boxes stacked in the yard there (e.g. RTG 1-over-5 or 1-over-6)? Does a full-height yard
    stack block the yard crane the same way a full-height row blocks the quay crane? Today we use 8 for both (D62).
26. **Class 1 direct delivery: which divisions — to send (user action).** Does direct delivery at Laem Chabang cover every
    class-1 division, including 1.4S, or is 1.4S stored in the yard like general cargo? Does it apply when class 1 is only
    a subsidiary risk? We currently apply it to primary class 1, all divisions (D66).
28. **Mixed yard stacks under congestion — to send (user action).** You said congested terminals mix containers together,
    which causes more digging. Roughly what share of export stacks at Laem Chabang have other boxes (imports, other ships'
    exports) on top of them on a busy day? We assume 25 % (D72).
27. **Class 7 on board in transit — to send (user action).** May a class-7 box stay on board while the ship works cargo at
    Laem Chabang, if it isn't loaded or discharged there? Today we refuse only loading, discharging and restowing it
    there (D66).
29. **How far the terminal may adjust the pre-loading plan — to send (user action).** You said the terminal planner may
    adjust the carrier's pre-loading plan to the yard, and the carrier's central planning approves (Q14). May the terminal
    swap in any box of the same port, size/type and weight class, and move boxes to other slots within the same bay? Is
    there a limit before a new approval round is needed (e.g. a number of changes, or no changes to DG or reefer slots)?
    We currently allow same-category swaps and slot changes within the carrier's pattern, each checked against the rules
    (D73).
30. **Crane sequence and list during loading — to send (user action).** Does the terminal order the crane lifts to keep the
    ship's list small during loading (for example alternating port and starboard rows), or does the chief officer correct
    list with ballast and the anti-heeling tanks? Roughly how fast can a 500–1,700 TEU feeder pump ballast at Laem Chabang
    (m³/h), and how many cranes usually work it? We currently re-order lifts to keep list within limits (D75) and assume
    pump rates of 300–600 m³/h with 2–3 cranes at 30 moves/h each (D74).

## Safety limits vs operational time (29 Oct, D74)

Two kinds of constraint look alike and are treated differently. A **safety limit** (GM, list, trim, strength, lashing,
stability at every in-port state) must hold in every state the plan passes through: a plan that breaks one fails. An
**operational time limit** (how fast ballast can be pumped, how fast the cranes work) only makes the call slower: the cranes
pause or the ship sails later. Following the user's decision on D74, slow ballast is reported as a delay in hours, while
the HEEL-rate and pumping-window checks, which protect the intermediate states, stay hard.

## Stability glossary

- **Δ (displacement)**: total mass of ship + cargo + ballast + consumables = mass of displaced water.
- **KG**: height of the centre of gravity above the keel = Σ(wᵢ·zᵢ)/Δ. Linear in container assignments for fixed Δ.
- **KB**: height of the centre of buoyancy. **BMₜ**: transverse metacentric radius (= I_waterplane / volume).
  ⚠ Not the same as BM = bending moment; use `BM_t` vs `bending_moment` in code.
- **KM = KB + BMₜ**; **GM = KM − KG** (initial stability; larger = stiffer).
- **Free-surface correction**: partially filled tanks reduce effective GM.
- **GZ curve**: righting lever vs heel angle; large-angle stability.
- **Trim** ≈ Δ·(LCG − LCB) / MCT1cm (moment to change trim 1 cm). **List**: tan θ ≈ Δ·TCG / (Δ·GM) at small angles.
- **Shear force / bending moment**: integrals of the net load (weight − buoyancy) along the ship's length.

## Planning practice

- Real stowage is often solved by decomposition (on the ship side only; v3 adds the yard, see Moves and Yard sections): **master planning** (assign container groups — by port, size, weight class,
  DG/reefer — to bay sections; usually a MIP) then **slot planning** (exact slots within each bay; CP or heuristics).
- Planners plan per port call with a load list and the current on-board plan; last-minute changes are common.

## Dangerous goods

- **Stowage codes vs handling codes (IMDG 42-24):** stowage codes (SW1, SW2, SW19, …; table 7.1.5) say *where* a box may
  go: away from heat, clear of living quarters, battery conditions. Handling codes (H1, H2, …; table 7.1.6) say *how* it is
  handled: keep as dry as reasonably practicable, keep cool. We model stowage codes from slot geometry. Handling codes are
  modelled from declared container facts plus a labelled placement policy, and their operational precautions go to the
  terminal and crew as instructions (D59).

- UN Model Regulations Dangerous Goods List: UN numbers, proper shipping names, 9 classes (with divisions), packing
  groups I/II/III. Freely published by UNECE — primary source for our DG dataset.
- Classic hidden/misdeclared DG **(verify UN numbers)**: calcium hypochlorite (class 5.1; UN 1748 / 2208 / 2880) as
  "bleaching powder"/"chlorine"; lithium batteries (class 9; UN 3480 / 3481 / 3090 / 3091) inside "electronics";
  charcoal (class 4.2; UN 1361); perfume/fragrances (class 3; UN 1266); paint (class 3; UN 1263); fireworks (class 1).
- Carriers run keyword screening on cargo descriptions today; our model targets what keyword lists miss.

## Thai context

- Main gateway: Laem Chabang. Typical intra-Asia feeder rotations: Laem Chabang – Singapore – Port Klang – Ho Chi Minh – Hong Kong.
- Use UN/LOCODEs for ports (e.g. THLCH Laem Chabang, SGSIN Singapore, MYPKG Port Klang) **(verify codes)**.

## References

- Pacino, Delgado, Jensen, Bebbington (2011), "Fast generation of near-optimal plans for eco-efficient stowage of large
  container vessels", ICCL. — master + slot decomposition. **(verify citation)**
- Delgado, Jensen, Janstrup, Rose, Andersen (2012), "A constraint programming model for fast optimal stowage of
  container vessel bays", European Journal of Operational Research. **(verify citation)**
- IMO 2008 Intact Stability Code; SOLAS Chapter V Reg. 22; IMDG Code Amendment 42-24 (Ch. 3.2, 7.2, 7.4); IACS UR S11A, S1, L5, Rec. 48. URLs and what each encodes: `docs/data-sources.md` §4.
- SMDG BAPLIE 3.x message implementation guide (smdg.org).
- Google OR-Tools CP-SAT documentation.
