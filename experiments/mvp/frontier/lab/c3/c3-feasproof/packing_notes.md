# Whole-map outward packing model

`packing.py:build_model` builds a continuous LP. It does not enumerate pages,
pure stacks, cargo permutations or a restricted pool. A legal complete stow
maps into its feasible set, as follows.

Each load box belongs to a class with its **exact** mass, ordinary height,
height including OOG excess, length, reefer state and explicit on/under-deck
request. Usually each class contains one box. Each real physical slot maps to
a footprint: a list of `(odd bay, deck flag, ordinal within stack, cell count)`
entries. A 40/45-foot box has two physical cells and shares its mass equally
between them, exactly as `checker/rules.py` does. Slots of different rows can
have the same footprint; that row choice is forgotten. Allocation variables
give how many class boxes take each footprint. They are continuous, so allowing
fractions only enlarges the feasible set.

All class counts must be allocated. Cell use at each bay/deck/ordinal is at
most the raw physical cell count. Summed occupancy of ordinal k cannot exceed
that of ordinal k-1 in the same bay/deck: every supported real placement has
a box in each preceding cell of its supporting physical stack. The mass cap
in a bay/deck is the sum of the **largest** mixed/20/40 mass rating of each
stack; a legal stack's applicable rating cannot exceed that maximum. The
height cap is the sum of its stack height limits, charging each footprint cell
the box's full physical height. These are aggregate necessary conditions,
not a claim that a real row/stack assignment exists.

Unary exclusions retain only physical length acceptance, at least one reefer
plug per footprint, explicit on/under-deck requests, box physical height no
larger than the whole supporting stack, and the larger of the legacy/frozen
port crane limits. Thus either move-replay activation is covered. E6, DG,
booking interactions, OOG horizontal clearance, slot blocking and top-stow
requests are relaxed. Optional externally certified per-stack sight caps can
be supplied; the default does not invent a sight rule. A slot is excluded by
such a cap only if its minimal possible physical top already exceeds it.

For cargo moment, let h be the smallest **ordinary** load-box height. A real
box in ordinal k has k supporting boxes in each footprint stack, so its
support plane is at least `stack.base_z_m + k*h`. Among actual slots with a
given footprint, use the smallest average of these lower support planes.
Add half the class's ordinary height and multiply by its exact mass. This
matches `checker/stability_loading.py`, which uses ordinary support heights
and half-box cargo CG. The result is a lower bound on cargo vertical moment,
not an achieved KG. The securing CG needs its own `.45*height` (or declared CG)
instead; `support_z_lower_m` is exposed separately for that purpose.

The supported scope is empty arrival, all current load-list boxes, closed
covers, and no future forecasts/plan calls. Other states raise an exception
and must be reported unknown. Fixed background is not rearranged: its mass
and moment belong in the separate ship-physics provider.

The numerical interface uses SciPy `milp` with zero integrality, HiGHS one
thread, sparse constraints and a time limit. `add_variable`/`add_row` attach
tank and outward physics inequalities. The default objective minimizes the
cargo-moment lower bound. `witness` serializes class allocations, raw-region
capacities, representative real slots, lower moments and deck targets split
equally between the two supporting odd bays of a long box. A feasible LP is
only an outward-model witness. A solver's numerical infeasible status alone
is not a formally checked physical proof; the independent audit owns that
verification. Only the unchanged full checker can establish a legal stow.

This implementation derives the aggregate necessary-condition idea from the
read-only C2 `c2-layout/bound.py`, but copies no source or stateful engine code.
All required contract/README and historical notes were read. No sealed data,
source, parameters, engine code or other experiment outputs were edited.

Smoke verification, with the sole team compute token: the known 88-box and
162-box V6 maps both remain LP feasible/optimal. They contain respectively
2,366 variables / 180 rows and 5,288 / 276. Minimum cargo moment lower bounds
are 11,367,303.990410183 and 13,254,769.458505988 kg m. Build plus solve took
0.132 and 0.219 seconds on the shared machine. The separate raw-number
`audit.verify_witness` verified both primal witnesses; maximum aggregate row
residual was 6.4e-12. These are packing-only LP witnesses, not new full-checker
passes. The audit agent separately fully checked the existing passing plans.

`attach_rows` adds the physics agent's necessary conditions. Every tank gets
an independent mass variable between zero and full nominal capacity. Its VCG
is bounded below by the raw tank geometry's minimum z, which contains every
tilted-liquid centroid; fixed-load moment is exact. The weather applicability
condition `KGvirtual <= 1.5*draft` and nonnegative free-surface correction imply
the physical KG has the same upper bound. Inserting the **lower** cargo/tank
moments into that upper-KG inequality enlarges the feasible set. The departure
displacement interval is retained; no sampled hull tangent is used.

For each allocation, the physics provider supplies own-box securing-ratio
floors and a transverse force floor. If every member of a class has a ratio
floor above one, that allocation is impossible. The per-bay force sum is at
most the source fitting capacity times the number of physical deck columns
in that bay. Long-box force is divided by its footprint size, with an
additional downward factor for asymmetric OOG end shares. Each class uses
the smallest member coefficient, so differing OOG offsets never strengthen
a cut accidentally. Lashing CG uses the separate support lower bound plus
0.45 of physical box height. Lashing GM bins strengthen these floors only;
no identity with the stability checker's GM is assumed. The interval's upper
endpoint remains metadata, since omitting that constraint is outward-safe.
Finite-angle GZ, containership area, equilibrium displacement/draught and tank
transfer effects remain relaxed unless a separately certified provider adds
their rows. Thus even a feasible physics-augmented LP is still only guidance.
