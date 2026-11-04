# Two-port cover-capacity certificate

`bound_ports.py` is an independent refinement. The frozen `bound.py` and its
measured four rows remain unchanged. The interface is
`lower_bound(instance, *, budget_s=5., tables=None, base_result=None)`.
Without a supplied base certificate it calls the frozen bound; supplying one
is an explicit caller assertion of trusted certificate provenance. The CLI's
`--base-results` additionally requires matching input SHA256 and instance id.
Inputs still pass through the shared catalogue's approved-list gate.

## Necessary port-capacity inequalities

With two future discharge calls, each cover chooses one of four edge masks:
shut, first port, second port, or both ports. Let `C[h]` be its physical hold
cell capacity and `d[p]` the cell demand known to be below deck in a branch.
Every real assignment satisfies all three two-port Hall capacity inequalities:

* the capacities of covers incident to port one sum to at least `d[1]`;
* those incident to port two sum to at least `d[2]`;
* all used-cover capacities sum to at least `d[1] + d[2]`.

The shared-cover capacity is deliberately counted for both individual-port
inequalities; the third inequality stops it being used twice in total. These
conditions relax individual box units, support, actual slot eligibility,
mass/height, securing and all whole-ship physics. Thus every legal assignment
maps to an enumerated feasible mask. Charge two current hatch lifts for each
nonzero mask and two future hatch lifts for every incident port edge, using
objective coefficients rounded down to 0.001. The minimum mask cost is a valid
lower bound on that branch; future restows remain free.

The familiar Hall subset-capacity condition here is derived directly: cargo
for any port subset can only occupy capacities adjacent to that subset. With
two ports there are precisely the three nonempty subsets above. No downloaded
algorithm or fitted packing model is used; all `4^H` masks are enumerated.

## Exhaustive E6 branch split

Reuse frozen `bound._e6_witness_covers` to validate an ordinary-heavy family
`B`, and let `K` intersect all its witness-cover sets. All the restrictions
and proof in `bound_notes.md` apply: no DG/OOG/booking boxes in `B`, no E6 cuts
with any lateral OOG, and no hard E6 cut when the rule-freeze makes it soft.

Case A puts every member of `B` in the hold. Its per-port demand consists of
`B` union all unary hold-only boxes, counting each box's physical cells once.
Enumerate cover masks against those demands. Case B has some member of `B`
on deck; every cover in `K` must then be used, costing at least one current
and one later opening/closure pair each. If `B` is empty only Case A exists.
If `K` is empty but `B` exists, Case B's optimistic shipping bound is zero.

Every operational plan belongs to one case. Take the **minimum** of their
shipping bounds, add the independently forced yard relocations, then maximise
with the trusted existing whole-map bound. Taking the maximum of branch costs
would be invalid. If Case A has no Hall-feasible mask, that branch is excluded
by necessary capacity alone; it is never called a page-LP infeasibility proof.

## Hand certificate for the first high TRAIN map

All ordinary-heavy plus hold-only cargo has first-port demand **37 cells** and
second-port demand **14 cells**, total 51. Any four used covers must have the
largest capacities **18,18,8,8** because every other four-cover choice totals
less than 51. With only four port edges each cover serves a single port. The
18+18 split provides 36 early cells, too few for 37; 18+18+8 provides 44 early
cells but leaves only 8 late cells, too few for 14. All smaller splits fail.
Thus four covers require at least five port edges: eight current plus ten
future lifts, **18 hatch lifts**. Five or more covers cost at least 20 hatch
lifts. In Case B all six common witness covers open, costing at least 24.
Hence the whole map needs at least **47 + min(18,24) = 65 lifts**, improving
the previous 63. At the checked 71-lift incumbent its remaining gap is six.

The measured four TRAIN certificate totals are **65/90/70/51**. The 87-box high
map retains its 70-lift optimality certificate; low maps have no heavy family
and their mandatory port-capacity demands retain the previous 90/51 bounds.
The scoped run is stored in `bound_ports_results.json`.

## Verification plan and compute

`test_bound_ports.py` checks the 37/14 hand certificate, empty/single-port and
impossible demands, and compares mask enumeration with an independently
enumerated integer-transportation model on tiny capacities. Its map test
validates four approved TRAIN inputs against their stored base certificates.
No numerical job ran without the root's CPU token. Root ran all four tests
successfully, then the CLI with `--list train4.txt --base-results
bound_results.json --out bound_ports_results.json` (paths rooted here).
Measured certificate build times were 9.85, 0.19, 10.91 and 0.05 seconds,
including necessary E6 witness validation, with one numerical thread.
All new artifacts remain in the Cycle 3 executive folder.
