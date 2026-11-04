# Cycle 3: necessary cover capacity and E6 inequalities

`bound.py` copies the Cycle 2 `c2/c2-layout/bound.py`, retaining its independent
forced-yard-relocation certificate, fixed hatch and port-cover charges, and
union-counted deck restows. It strengthens the packing relaxation in three ways.
It is not the constructive page LP, whose restricted patterns and fitted
securing coefficients cannot establish a whole-map bound.

## Capacity when covers remain shut

Let `z[h]` say that hold cover `h` contains any final load cargo, `C[h]` its
number of physical 20-foot cells, and `D` all deck cells. Each closed unused
cover makes its complete hold capacity unavailable. Every actual stow therefore
satisfies `hold_cells <= sum(C[h] z[h])`. Independently, at least
`R = max(cells of boxes with no eligible deck slot, total demanded cells - D)`
cells must be in the hold. Hence `sum(C[h] z[h]) >= R` is necessary.
If the largest `k-1` cover capacities sum to less than `R`, at least `k`
covers must be used. `minimum_covers` computes this cardinality consequence.
These are capacity inequalities, without any assumed trim, list or securing
policy. They are intentionally optimistic because all geometric deck cells are
counted, including cells unusable by some cargo.

The new signatures retain each physical supporting stack rather than only its
cover/hold-or-deck region. Tier identity and support are still relaxed. Each
box maps to its actual supporting-stack tuple; integer group counts retain the
port, footprint size, length class, identical allowed tuples, mass rounded down
to kg, height rounded down to mm, and identical E6 witness-cover set. Physical
cell count, stack mass and stack height bounds now apply per stack. Largest
length-dependent stack rating and outward rounding enlarge the feasible set.
The unchanged full securing checker rejects any mixed 20-foot/long hold stack,
independently of GM and ballast (`lashing._load_graph`); a stack binary imposes
that necessary condition. No fitted lashing or motion coefficients are used.

## A safe part of legacy E6

Legacy E6 permits a heavy deck box only if no compatible empty hold opportunity
remains. It does **not** require every heavy box to be in the hold. For an
ordinary non-DG heavy box, take a bottom-tier hold slot wholly within a single
cover and validate it in the empty ship with the production
`placement_violations`. If that cover is unused, all supporting hold stacks are
empty. Bottom support, mass, weight order and height consequently remain exactly
as in that validated empty witness. The ordinary candidate has no DG, OOG,
booking or heat request; other non-OOG cargo outside its empty stacks cannot
introduce a placement interaction. Thus that slot remains an E6 witness and
the box cannot be on deck. Algebraically, `deck_count[g] <= n[g] z[h]` for
each validated witness cover `h` and ordinary-heavy group `g`.

Lateral OOG elsewhere could block a witness through its expanded envelope, so
**all E6 cuts are disabled when any instance cargo has lateral OOG**. This
conservative restriction follows the Cycle 2 hatch worker's proof gate.
They are also disabled under activated rule-freeze-v1, where E6 is soft, and
unless the declared fallback is exactly true with a known heavy threshold.
Boxes with DG, OOG or any booking request receive no E6 cut. Every member in a
group has the same separately validated witness-cover set, so aggregation
does not transfer a witness from a lighter or differently booked member.

Let `B` contain the ordinary-heavy boxes receiving at least one witness, and
`K` be the intersection of their witness-cover sets. Either one member of `B`
is on deck, in which case all `|K|` covers must be used, or all of `B` plus
hold-only cargo is below deck. In the latter case their combined cell demand
forces `k` covers by the capacity argument above. Consequently every legal
stow uses at least `min(|K|, k)` covers. Every used cover has at least one
future discharge edge, so this count also lower-bounds the future cover pairs.
The implementation makes `used[h]` equivalent to positive hold occupancy;
unoccupied covers cannot be activated merely to suppress a witness.

## Objective and validity

An operational stow maps to one allowed stack tuple per box, respecting each
retained constraint and E6 implication. Used covers charge two current hatch
lifts; used cover/discharge-call edges charge two later lifts; only the subset
of future deck blockers directly above an earlier hold cover is charged.
In-column and transitive restows remain free. The minimum of this enlarged
feasible set is no larger than the operational hatch-plus-restow minimum.
The solver contribution uses only CP-SAT's `BestObjectiveBound`, rounded down;
it is maximised with the independent analytic cover-cardinality certificate.
No feasible relaxation incumbent is used as a certificate. Add the disjoint forced yard
relocations exactly as in Cycle 2. Empty closed arrival, no forecast/future loads,
one/two-cell footprints and nonnegative objective weights remain guarded.

Support/no-gap, E1/E2 ordering, DG/OOG pairs, most E6 conditions, securing loads,
visibility, trim/list, ballast, stability and other whole-ship physics remain
relaxed. Results certify only a lower bound, not relaxation-witness legality.
The capacity calculation can be loose when special cargo or physics limits
deck use; no empirical constraint is inserted to hide that gap.

## Verification and measurements

`test_bound.py` tests cardinality at zero, boundary and insufficient capacity;
outward rounding; soft-E6 disabling; and mapping of all four certified V6
finished plans to per-stack mass/height/cells, mixed-length and E6 implications.
Each replay's lift count bounds the returned solver certificate from above.
The fixed input list is the unchanged Cycle 2 `train4.txt`, with provenance
checked by the shared catalogue before the command-line program reads a map.
Measurement rows and exact solve status are stored in `bound_results.json`.

Final four TRAIN bounds, in Cycle 2 list order, are **63, 90, 70, 51**, against
the previous **51, 90, 52, 51**. At the Cycle 2 checked upper bounds
71/116/70/71 the gaps are **8/26/0/20**. The third map is now globally D11
optimal at 70 lifts under the declared current checker/projection model.
The source/hash-matched Cycle 2 fully checked plan supplies the upper bound;
new candidate construction still needs the unchanged full checker.

For the first high map, hold cell capacities are 18,18,8,8,4,4. All 33 ordinary
heavy boxes together need 51 cells; all hold-only cargo is already among that
set. The largest three covers provide 44 cells, so the all-heavy-in-hold
branch requires four covers. The alternative branch uses all six compatible
witness covers. Hence at least four covers and four future edges cost 16
hatch lifts, plus 47 forced relocations: **63**. The physical-stack relaxation
times out at 15 seconds with a 24-hatch-lift incumbent; that incumbent is not
substituted for the certified 16-lift lower bound.

For the second high map (third list row), ordinary-heavy plus hold-only demand
is 64 cells against only 60 total hold cells. The all-heavy-in-hold branch is
impossible. At least one ordinary-heavy box must be on deck, all six covers
have independently validated bottom witnesses for every such box, and E6
forces all six to open. Six current and six future pairs cost 24 hatch lifts;
46 forced relocations yield **70**, matching the checked upper bound.

The two low-distribution maps contain no heavy boxes under the declared
20,000 kg E6 threshold. Their necessary cover counts remain one; the stronger
per-stack capacity relaxation solves to the old 6/4 shipping-lift bounds.
This is a measured negative result for capacity-only bound closure, not
evidence that their actual physics permits leaving all other covers shut.
The second map has lateral OOG, independently disabling E6 witness cuts.
Solve/build times for the four rows are 15.00, 2.74, 14.00 and 0.54 seconds.

The four mapping tests passed. Final read review widened the hold-activation
big-M from `n` to `2n`, because a long box appears in both supporting stacks.
This avoids excluding legal generic tiny inputs. It leaves the four measured
feasible sets unchanged: every cover has at most 24 physical cells, less than
`n >= 87`, so even the previous `n` activation upper bound was redundant on
these inputs. Returning the independent analytic certificate on solver
startup timeout likewise leaves every reported numeric result unchanged.

## Negative supporting-stack refinement

We inspected whether heavy short boxes consume whole hold stacks, which could
force more covers than the cell-capacity argument. The first high map contains
15 ordinary-heavy 20-foot boxes (20,166--28,791 kg) and 18 ordinary-heavy
40-foot boxes (20,209--29,145 kg). Crucially, its declared pure-20 stack rating
is **68,000 kg**, while the pure-long rating is **34,000 kg per supporting
stack**, sharing each long box's mass equally over its two supports. The
production checker explicitly selects these respective ratings. Using 34,000
kg for an all-20 stack would invent an invalid restriction.

For a pure-20 stack, the number of ordinary-heavy short boxes is at most
`A = min(physical tiers, floor(68000 / minimum_short_mass))`. For a pure-long
stack, heavy long supports number at most
`B = min(physical tiers, floor(2*34000 / minimum_long_mass))`. Both floors are
three on this map. Thus the safe global supporting-stack inequality
`n_short/3 + 2*n_long/3 <= used_hold_supporting_stacks` needs only 17 supporting
stacks and is weaker than the retained 51-cell/four-cover bound. Tier-specific
versions retain the existing physical-cell capacity because the count limits
match the physical tier counts. No strengthened bound follows from this idea.

As a consistency check from the JSON cargo facts, the twelve lightest heavy
short boxes total 269,730 kg and the twelve lightest heavy long boxes total
269,986 kg. Each sum is below four pairs' or columns' combined 272,000 kg
rating. This is not a feasibility witness, but it rules out the proposed
simple claim that even the lightest three-box groups must exceed their ratings.
No numerical solver ran for this inspection; no measured bound or source
coefficient was changed. More cover closure on this map still requires actual
eligibility/packing inference or rigorously necessary physics constraints.

All numerical runs use one process and one thread after receiving the root's
sole CPU token while `BENCH-RUNNING` exists. `nice -n 5` was requested; the
sandbox reports that changing priority is not permitted. No process is killed.
No sealed data, live shared engine imports or source edits are used.
