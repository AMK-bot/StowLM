# The stowage map as one graph

Plain-language model of a Frontier map. Every number is measured by the scripts in this folder on real maps. Every rule was checked against `stowage.moves` and stored 0.9.0 plans (`verify_claims.py`), not recalled.

**The short version.** A map is three graphs glued together: a yard forest, a ship forest, and a small two-sided port-cover graph. The cost of a plan is a count of edges in those graphs. On our maps the yard forest has almost no choice left in it, so most of the cost is fixed before planning starts. The freedom that remains sits in the port-cover graph.

## Two real maps

| | Small: `random.small.40100003.0.2` | Medium: `random.medium.185100000.0.2` |
|---|---|---|
| Ship | `feeder_800` slice | `feeder_500` slice |
| Load boxes | 18 (13 for SGSIN, 5 for MYPKG) | 121 (77 for SGSIN, 44 for MYPKG) |
| Lengths | 13 × 40 ft, 5 × 20 ft | 72 × 40 ft, 49 × 20 ft |
| Ship cells (20-ft cells) | 92 | 240 |
| Fill (TEU / cells) | 34 % | 80 % |
| Ship stacks | 20 (16 deck, 4 hold) | 64 (36 deck, 28 hold) |
| Hatch covers | 2 | 6 |
| Yard stacks holding a load box | 18 | 121 |
| Other boxes in the way | 4 | 24 |
| Special boxes | 1 DG | 8 reefers, 4 DG, 1 OOG |

## The graph

**Vertices**
- One vertex per **box** on the load list, plus one per **stranger**: a yard box that isn't loading.
- One vertex per **ship cell** (a 20-ft position). A 40-ft box covers two cells at the same row and tier, in neighbouring bays.
- One vertex per **discharge port** and one per **hatch cover**.

**Edges and weights**

| Edge | Means | Cost |
|---|---|---|
| Yard edge: box *a* directly on box *b* | *a* must be out of the way before *b* lifts | 1 if *a* lifts only so that *b* can be reached |
| Assignment edge: box to cell | The stow itself, the thing we choose | 0 |
| Ship edge: cell directly on cell | Lower cell fills first | 0 while cargo below leaves no earlier than cargo above |
| Port-cover edge (*p*, *c*) | A hold box under cover *c* goes to port *p* | 2 per edge (cover off and on at *p*) |
| Used cover | Some hold box is under *c* at departure | 2 per cover (off and on here) |

So there are two kinds of stacks and one small two-sided graph:
- **A stack** is a path in the ship graph (a column of cells) or in the yard graph. Two boxes in a stack are ordered: only the top lifts. Each path is one "page" in the book-embedding sense below.
- **A block** is everything under one hatch cover in one region: `HC-002-P/hold` or `HC-002-P/deck`. Blocks are what the hatch costs are charged on. A block is not a graph component; it's a set of ship paths that share a cover vertex.

## What each lift is in that picture

All three were checked on the 51 stored plans that pass.

- **Relocation.** A yard edge (*a* on *b*) where *a* lifts to reach *b* and *a* isn't going to the ship yet. The expander moves *a* to another yard stack and the edge disappears. In 221 stored relocations, 200 are strangers and 21 are load boxes.
- **Restow.** Two boxes in one ship path where the lower one leaves first. The upper one must come off and go back, which the projection counts as 2 lifts per box (`future_restow_lifts_per_box`, assumption D15, expert Q1 pending). A deck box above a hold box under the same cover that leaves earlier is the same thing through the cover: this is a two-step path, hold box → cover → deck box. On empty-ship maps no restow happens this call: all 51 plans read `restow_now = 0`.
- **Hatch lift.** One end of a cover's off-and-on pair. Measured identity, exact on all 51 plans: `hatch_now = 2 × used covers` and `hatch_later = 2 × port-cover edges`.

So the objective on an empty ship is:

```
lifts = relocations
      + 2 × (used covers) + 2 × (port-cover edges)
      + 2 × (boxes above an earlier-leaving box)
```

## Structure, measured

Treewidth says how "tree-like" a graph is; small treewidth lets exact dynamic programs run. A bound in brackets gives lower and upper limits: the lower is the degeneracy and the upper comes from a min-degree elimination order.

| Graph | Small 40100003 | Medium 185100000 (random yard) | Medium `sim.185100010` | Medium `sim.185100210` |
|---|---:|---:|---:|---:|
| Load boxes | 18 | 121 | 121 | 428 |
| Yard: stacks holding a load box | 18 | 121 | 29 | 103 |
| Yard: load boxes in one stack, most | 1 | 1 | 5 | 5 |
| Yard: load-on-load arcs | 0 | 0 | 218 | 754 |
| Yard: arc density | 0 | 0 | 0.030 | 0.008 |
| Yard: components | 18 | 121 | 29 | 103 |
| Yard: treewidth | 0 | 0 | 4 | 4 |
| Yard: largest clique | 1 | 1 | 5 | 5 |
| Yard: strangers above a load box | 4 | 24 | 0 | 0 |
| Order conflicts: edge density | 0.17 | 0.19 | 0.21 | 0.29 |
| Order conflicts: treewidth | [3, 4] | [26, 47] | [24, 46] | [124, 246] |
| Order conflicts: largest clique | 2 | 2 | 2 | 2 |
| Order conflicts: components | 2 | 7 | 15 | 11 |

"Order conflicts" joins two boxes that can't share a ship stack with heavier-below and later-port-below both holding, whichever is on top. It only depends on two numbers per box, port and mass, so it is the complement of a two-dimensional order.

What this says:

1. **The yard graph is a set of small cliques.** Each yard stack is a clique of at most 5 load boxes and nothing joins them. Treewidth is at most 4, so any yard-only question is easy exactly.
2. **On random-yard maps the yard graph is empty.** No two load boxes share a yard stack. Every relocation is a stranger on top of one load box, and no load order can avoid it.
3. **On simulated-yard maps the opposite holds.** There are no strangers on top, but 218 and 754 load-on-load arcs.
4. **The order-conflict graph is bipartite.** All its edges run between the two ports: an early-port box heavier than a late-port box. Inside one port any two boxes chain by mass, so the largest antichain is 2 and, by Dilworth, **two unlimited chains are enough on any 2-port map**. So stack count is never the barrier; tiers, length and mass per stack are.
5. **Graph sizes are not the barrier.** A 428-box map has a yard treewidth of 4. What's large is the assignment between boxes and cells, a two-sided graph with thousands of edges and hard side rules.

## Where well-known models go trivial

Most maps have 2 discharge ports. That breaks several elegant ideas at once.

- **Circle graphs and book embeddings.** Avriel and Penn's picture: a box is an interval from load time to discharge time, two crossing intervals can't share a stack. With one load port and 2 discharge ports, load order is ours to choose. Sort each stack with the later port low and no intervals cross. Page number is 1 for overstows.
- **Belady's rule.** Furthest next use needs several future use times. With 2 ports every box has one of two next-use times, so the rule reduces to "keep the later port low", which is port blocking.
- **Register spilling.** A spill is a relocation of a load box. Only 21 of 221 relocations are load boxes. The other 200 are strangers, which are spilled no matter what.

## What is left to choose

Measured on 51 stored plans (`breakdown.py`):

| Part of the 12.14 mean lifts | Mean | Changed by the stow? |
|---|---:|---|
| Relocations of strangers above a load box | 3.92 | No, forced on every plan |
| Other relocations | 0.41 | Yes |
| Hatch lifts now | 3.61 | Yes, through the port-cover graph |
| Hatch lifts later | 3.84 | Yes, through the port-cover graph |
| Later restows | 0.35 | Yes |

On 41 of 51 plans, relocations equal the forced strangers exactly. On 32 of 51 plans, total lifts equal forced strangers plus 8 hatch lifts. So the only thing left to win on small maps is the port-cover graph: 8 is what a two-cover, two-port plan costs with one port per cover (2 × 2 + 2 × 2). fx-exact's bound reads 6 for the hatch part on the maps I sampled, so that gap is 2 lifts at most on those maps.

## What the medium wall looks like in this picture

At 80 % fill the graph doesn't get harder. What runs out is **mass per stack**: on the 121-box map, 1,906 t of cargo sits on 2,032 t of 40-ft stack ratings (36 × 30 t + 28 × 34 t). A stack is therefore a vector bin with three limits (tiers, mass, height), and stowage at this fill is vector bin packing. That's why `pages.py` treats a stack as a page, chooses whole patterns, and prices them. Medium ship-level rules map onto it this way:

| Checker part | Depends on | Handled by |
|---|---|---|
| Lashing | One column, plus GM and draught | Column check, exact per pattern |
| Sight | Height of stacks, by position | Deck tier cap |
| List | Sum of moments | Swaps of whole patterns between same-type stacks |
| GM | Sum of mass × height | Cost on height in pricing |
| Draught and trim | Whole ship, with ballast | Finisher |

The tank-and-ballast side is a separate graph; fx-finish covers it.
