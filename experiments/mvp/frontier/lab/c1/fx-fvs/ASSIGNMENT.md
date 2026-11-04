# Assignment diagnosis on three quick-small simulated yards

These are read-only structural observations from `lab/baseline.json` and its
three validation inputs. No numerical solver, replay, finisher or checker was
run for this diagnosis. Container IDs below omit the common `QZXU000` prefix.
All yard lists run bottom to top. A stated bound concerns assignment within
`interchangeable_signature` classes and the fixed occupied ship slots, not
unrestricted stowage. Moving to previously unused slots could evade it.

## 41100007: an exact-safe three-cycle exchange

Stored: 9 lifts, comprising 1 relocation and 8 hatch lifts. Boxes 0020, 0164
and 0190 are identical HKHKG empty 22G1 containers, each exactly 2,350 kg.
The following simultaneous exchange leaves every slot's physical and booking
attributes unchanged:

| Box | Stored slot | Proposed slot |
| --- | --- | --- |
| 0190 | 0010584 | 0030284 |
| 0020 | 0030284 | 0030286 |
| 0164 | 0030286 | 0010584 |

The yard stack is `[0117, 0082, 0020, 0190]`. In the stored stow, hold box
0117 must precede deck box 0190 across a hatch, conflicting with yard 0190
before 0117. Simply swapping 0190 with either 0020 or 0164 leaves a cycle.
The simultaneous exchange above makes the entire combined ship/yard graph
acyclic, verified with a lightweight topological traversal. It puts 0190
below 0020 on their new ship stack, agreeing with their yard retrieval order,
while the independent yard box 0164 moves to the original troublesome deck
position. A full replay and checker still need to establish the saved lift.

This is a useful example of why pairwise local improvement can miss an
improving permutation. The joint CP-SAT assignment/rank model can express it.

## 40100046: one relocation is inherent within these classes

Stored: 9 lifts, comprising 1 relocation and 8 hatch lifts. Yard
`B024-013-01` contains `[0082, 0040, 0117]`. Reefer 0082, mass 22,019 kg,
occupies hold slot 0020202. Empty 0040, mass 3,900 kg, occupies hold slot
0020204 directly above it. Ship support forces 0082 before 0040, while
retrieving 0082 from the yard requires removing 0040 first.

Both boxes are singletons in our broad signatures: the reefer requires its
own attributes, and the empty box has no same-port/type empty partner.
The only broad exchange groups are empty VNSGN 42G1 boxes `[0020, 0056]`
and heavy VNSGN 45G1 boxes `[0077, 0098]`. Neither changes this conflict.
Therefore at least one relocation is necessary over the allowed assignments;
the stored plan attains it. Reversing 0082 and 0040's ship positions would
also put a 22-tonne box above a 3.9-tonne box and is not an admissible
same-class exchange.

## 40100025: two relocations are inherent within these classes

Stored: 26 lifts, comprising 2 relocations, 8 hatch lifts and 16 future
restow lifts. Broad exchange groups are `[0009, 0056, 0143]`, `[0014, 0020]`
and the exact identical empty pair `[0035, 0061]`.

First consider medium VNSGN 45G1 group `{0009, 0056, 0143}`. Their masses
are respectively 17,707, 15,117 and 18,433 kg. Their occupied positions are
hold 0020102, hold 0020206 and deck 0020182. Whichever member receives hold
0020206 must load after fixed boxes 0164 at 0020202 and 0159 at 0020204.
Yard 0009 and 0143 are above 0164, and yard 0056 is above 0159. Thus the
member assigned to 0020206 must be relocated before its supporting cargo
can load. This proves at least one relocation from that medium group.

Independently, empty pair `{0035, 0061}` occupies hold 0020104 and deck
0020186. If 0061 receives the deck position, yard 0061 is above 0122,
which supports it on the ship at 0020184; 0061 must relocate. If 0035
receives the deck position, yard 0035 is above both 0009 and 0143. At
least one of those two medium boxes must occupy either hold 0020102 or
deck 0020182. Both positions must load before deck 0020186: hold 0020102
is under hatch HC-002-S and deck 0020182 directly supports that deck
stack. Consequently yard 0035 must be removed before that medium box
loads; 0035 itself must relocate.

The mandatory medium-box relocation and mandatory empty-box relocation
are distinct. Hence every assignment allowed by our broad signatures needs
at least two relocations; the stored plan attains this bound. The `[0014,
0020]` exchange does not affect any position or yard relation used in the
argument. This is a bound for the occupied-slot/class neighborhood, not a
global map optimum or a reduction of its substantial future restow count.

## Model caution

`weight_class` is a yard-clustering label. Ship weight ordering uses actual
`gross_mass_kg`, so an exchange within that class can affect physical rules.
`category` is documented as untrusted metadata. The exact signature retains
every Container fact except identity and category; the broader signature
additionally releases mass facts inside a nonmissing weight class. Its
candidates need independent full checking. Likewise, CP-SAT's released-yard-
precedence objective is a cycle relaxation until replay verifies whether a
relocated box can block again at its destination.

Parent verification: the exact-safe three-box exchange was subsequently fully
checked through the shared scorer in `results/exact-class-win/`: 8 lifts,
zero relocations, versus the stored 9 lifts and one relocation. The full
combined quick run also attains every occupied-slot/class relaxation bound.
See `NOTES.md` for the measured cohort, attribution and proof scopes.
