# Aggregate lower bound on medium lifts

`bound.py:lower_bound` is a new, optimistic fixed-charge port–cover design
model. It is **not** the scout page LP: that LP restricts the pattern family to
pure stacks and uses fitted securing weights, so its objective is not a valid
whole-map lower bound. The aggregate model uses only necessary conditions.

## Selection, fixed before solving

`bound_maps.txt` contains the two D192 inputs from `quick_medium`, followed by
the first two other full-checker-certified TRAIN inputs in lexicographic V6
summary-path order. Those are feeder_500/f60-high/seed-192200002 and
feeder_500/f60-low/seed-192200000. The D185 third `quick_medium` input belongs to
the separate full runner gate; this bound measurement is four D192 maps.
Certification requires `checker_passed == true`,
`full_checker_unmodified == true` and `job.split == train` in V6 metadata.
No sealed input or sealed list was opened. The shared catalog's allowlist gate
validates the custom list before the command-line tool reads instances.

## Why every legal stow fits this model

A region consists of all stacks attached to one cover on either the hold or
deck side. Each real slot maps to its footprint signature: the number of cells
it occupies in each region. A long box can touch two regions; its signature
retains both, rather than assuming long boxes never cross covers. Explicit
guards require footprints of one or two cells and consistent deck-cover
membership between stack records and the cover's deck-stack list.

Boxes are grouped by first future discharge call, footprint size, mass rounded
down to whole tonnes, height rounded down to millimetres, and identical sets of
allowed footprint signatures. A group can be divided among signatures with
integer counts. The production static eligibility table supplies allowed
signatures: no legal actual slot is removed by this aggregation. Boxes of a
group need not share any exact stack placement, which makes the relaxation
more permissive.

For each region, total occupied cells cannot exceed its real cell count.
Summed box mass shares cannot exceed the sum of the largest applicable stack
ratings; taking the largest of mixed, 20-foot and long-box ratings only relaxes
the rule. Mass shares are down-rounded, capacities up-rounded per stack, and
all constraints use integers. Similarly, total height counted once per
occupied supporting stack cannot exceed summed stack height envelopes. Every
legal physical stack satisfies these summed inequalities. A long box's mass
is shared over its two supports; its height counts on both. The model drops
individual stack capacity, support/no-gap, E1/E2 order, E6 completion, DG pair,
OOG pair, securing, visibility, balance, draught, trim and all other whole-ship
physics. These omissions can admit impossible stows; they cannot remove a
legal one.

For each cover, a used hold region forces an opening/closure pair now. Each
(cover, future call) containing hold cargo forces a pair at that call. These
are the projection's exact hatch identities for the supported empty-arrival,
initially-closed maps. Nonnegative down-rounded objective coefficients charge
those pairs. A later-discharge deck box is charged the configured future
restow lifts whenever any cover it touches has earlier-discharge hold cargo.
A box spanning two covers is charged once per earlier call, using the union
of triggered edges. This is a subset of the projection's access closure:
other in-column and transitive overstows are free. Thus the aggregate
hatch-plus-restow objective never exceeds the corresponding actual plan's
hatch-plus-restow contribution.

CP-SAT's `BestObjectiveBound` is a lower bound on the minimum of this
relaxation; an incumbent is never substituted for it. Coefficients are rounded
down to 0.001 weighted lifts and the returned solver bound is rounded down.
The assignment-independent yard bound is the number of distinct non-load
boxes lying above a load box, exactly as counted in `c1/fx-fvs/certificates.py`.
The code computes this inventory set independently and asserts agreement with
`stowage.bounds.lower_bound`. Each such box must relocate at least once, for
any ship layout or load order, in the ship-call domain without simulator gate
moves. Relocations, hatch lifts and future restows are disjoint cost types, so
we add their bounds. This proves a whole-map lift lower bound, not an optimal
stow and not an infeasibility certificate.

## Measured lower bounds

All four aggregate solves reached `optimal` in below 0.3 s each (model-building
and solve time; instance parsing is outside this timing). This proves the
relaxation optimum only. It does not prove that an operational plan attains
that bound.

| D192 map suffix | Forced relocations | Hatch + restow bound | Total bound | V6 checked lifts |
| --- | ---: | ---: | ---: | ---: |
| feeder_500-f60-high-s192200000 | 47 | 4 | 51 | 131 |
| feeder_800-f60-low-s192200101 | 84 | 6 | 90 | 214 |
| feeder_500-f60-high-s192200002 | 46 | 6 | 52 | 128 |
| feeder_500-f60-low-s192200000 | 47 | 4 | 51 | 127 |

All optimal relaxation incumbents have zero restows. The bound is loose
because a few special hold-only boxes force cover usage, while free physical
and securing constraints allow most remaining boxes onto the deck. The first
and fourth maps can share one hold cover and one future port edge; the second
and third need both future port edges, even within one cover.

As a separate comparison, the unchanged `c1/fx-exact/bound.py:lower_bound`
ran with 20 s per map: 51 optimal in 9.63 s, 84 unknown in 20.02 s, 52 optimal in
10.89 s, and 51 optimal in 3.92 s. Its exact-slot packing relaxation did not
strengthen the aggregate bounds; the 162-box solve found neither an incumbent
nor a positive hatch bound by its limit. Details remain separate in
`bound_exact_results.json`. We do not mistake that timeout for infeasibility.

## Verification and scope

`test_bound.py` replays all four V6 finished plans and matches their stored
weighted counts. For each actual final assignment it checks static-mask
eligibility, the aggregate capacity inequalities, and that model cover/edge
and union-counted deck-restow charges are no larger than replay's projected
charges. It then solves the aggregate bound and checks it lies below each
certified plan. A second test checks outward rounding at floating-point
boundaries. Both tests passed. These are model-mapping tests, not a substitute
for the fresh full checker gate used for newly proposed plans by `score.py`.

Every CPU run set `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1`, CP-SAT uses one worker, and this subtask used only one
CPU-busy process at a time. `nice -n 5` was attempted on each run; the sandbox's
`setpriority` permission was refused, so the Python process ran at inherited
priority. No process was stopped and no run took over 20 minutes.

No source file was copied for this bound. Its fixed-charge interpretation and
optimistic-slot proof follow the cycle 1 notes and fx-exact model; this bound's
aggregation and union-restow formulation are project derivations. The
literature and search method remain documented in the enclosing `NOTES.md`.
