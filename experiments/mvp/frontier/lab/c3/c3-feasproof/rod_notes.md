# Whole-map aggregate rod inequalities

Only `rod_bound.py` and this note were added by this subtask. The frozen
`physics.py` was not edited. No CPU-busy process was started; the root owns
the current sole compute slot. This is a necessary-condition extension to the
whole-map outward model, not a full securing checker or page constructor.

## A positive scalar response for each physical endchain

The reviewed `c1/fx-finish/early_bound.py` derives the inverse of an unrodded
grounded uniform spring chain: entry `(i,j)` is `min(i+1,j+1)/k`. Adding the
rod at one-based anchor index `a=min(2,n)` gives the same scalar compliance
formula as the unchanged checker's `_end_response`:

`rod = K * sum_j min(j+1,a)*equivalent_joint_force[j]`,

`K = rod_k*cosine / (k + a*rod_k*cosine^2)`.

All coefficients are positive. The equivalent force at joint j is the own
CG fraction times its transverse inertial force plus half its wind, plus the
upper box's complementary inertial fraction and half its wind when present.
Every planned box has CG fraction r approximately .45. Distributing these
terms by boxes gives positive weights:

| Physical ordinal in endchain | Inertial weight | Wind weight |
| --- | ---: | ---: |
| 0 | r0 | .5 |
| 1 | 1+r1 | 1.5 |
| >=2 | 2 | 2 |

The first row also holds for a single-box chain. The implementation encloses
source height addition/subtraction rounding before lowering r; it does not
assume floating subtraction recovers exactly .45 of a box's height.

With `W=2.438`, rod length `l=sqrt(W^2+z^2)` and axial stiffness constant EA,
the response factors for the three independently checked rod components are

`K_resultant = EA*W*l / (k*l^3 + a*EA*W^2)`,

`K_horizontal = EA*W^2 / (k*l^3 + a*EA*W^2)`,

`K_vertical = EA*W*z / (k*l^3 + a*EA*W^2)`.

The exact source axial constants differ for anchors one and two and are
retained. Both source stiffness cases must pass; we retain k=3.7e6, which
produces the larger rod response for every given force/height geometry.

## The minimum over a whole height interval is at an endpoint

Writing `D=a*EA*W^2`, the derivative sign for resultant K as a function of l
is `D-2*k*l^3`: it changes only from positive to negative, so a stationary
point is a maximum. Horizontal K decreases with l. The derivative sign for
vertical K as a function of z is

`D + k*l*(3*W^2-2*l^2)`.

This sign expression decreases for `l>=W` because its derivative is
`k*(3*W^2-6*l^2)<0`. It also has at most one maximum. Therefore the minimum
of each response over a closed positive anchor-height interval occurs at an
endpoint. This is an analytic envelope, not a grid-sampling assertion.

All endpoint computations use exact Fraction arithmetic and lower/upper
square-root enclosures. The numerator is lowered and denominator increased;
a further `1-1e-8` guard accounts conservatively for the ordinary source
floating matrix calculation. The lashing source SHA-256 is pinned. Active
frozen overrides, nonpositive heights, unproved CG-fraction positivity, and
nonempty decision arrival raise unknown instead of contributing a cut.

For an allocation at ordinal zero, the chain can stop after its one box or
continue to box two. We take the smaller coefficient over its own-height
single anchor and its own height plus every globally possible next height.
For ordinal one, anchor height is its own height plus every possible first
height. For higher ordinals, both anchor boxes range over the global load-list
height envelope. These enclosures cover every ordering and every generated
page; no restricted-pool or pure-stack pattern assumption is made.

The tier weights are used only after an explicit geometry guard establishes
that every decision deck cell has `tier = common_start + 2*ordinal`. Then
even a shifted long-box parent is exactly one global tier lower, and
endchain depth equals physical ordinal. If this fails, every allocation
uses the universal smaller own inertial weight r and wind weight .5, and
the union of all possible anchor-height cases.

## Summing root rods gives whole-map inequalities

The source permits at most 212,500 N resultant, 150,000 N horizontal, and
225,000 N vertical force per root endchain rod. A twenty-foot physical column
has at most two root endchains. Summing necessary inequalities over all
decision deck columns gives capacities

`425000 / 300000 / 450000 N * number_of_physical_deck_columns`.

A twenty-foot box contributes to two ends in its one physical column; a long
box contributes one end in each physical column. The allocation coefficient
therefore uses `2/physical_units` times its minimum absolute end share.
For asymmetric OOG this is
`min(abs(.5-dx/L),abs(.5+dx/L))`, with the source's 12.192 m support length
for a 45-foot box. Exact source wind floors, expanded length and lower bounded
actual expanded height are retained. Every forgotten horizontal-OOG class
member contributes its own coefficient, and the minimum across members is
used. This ensures every possible member's real contribution dominates the
class coefficient.

In a mixed 20-under-long deck stack the inner 20-foot endchain can terminate.
Its outer chain continues at the next tier. A shifted long box over two
different lower long boxes can cause the source's end-parent convention to
move a chain between physical bays. For that reason these rod rows sum over
the entire decision deck, never rely on per-bay root conservation. Counting
two available rods for every physical column only overcredits capacity,
particularly on pure-long roots. Omitting fixed
background chains is safe because decision/fixed support catalogs are
disjoint in the admitted maps and omitted nonnegative loads cannot decrease
a retained chain's rod. Nonnegative force and CG-fraction conditions are
essential: negative terms are never assumed away.

Stronger companion rows credit only actual occupied bottom-tier roots.
Every bottom twenty-foot allocation contributes two root rods, and each
bottom long allocation contributes one per physical cell. Subtract its
linear capacity contribution from the total-demand row and require the
result to be at most zero. Every real supportchain starts at an occupied
bottom tier, so this variable RHS is necessary. It removes imaginary rods
in empty columns and the imaginary second rod under a pure-long root.

`attach_rod_rows(model, instance, gm_lo=0.)` adds all three component rows,
each with a constant full-column RHS and a stronger occupied-root RHS,
through the packing model's public append-row API. Coefficients use
the independent lashing-GM motion lower envelope. No stability GM is equated
to it. `evaluate_rod_rows(model, values)` reports each witness's required and
available force, ratio and row identity. A feasible LP remains a relaxation
witness. A numerical infeasible solver status alone remains unknown unless
the root independently verifies an outward contradiction certificate.

## Reuse, references and pending verification

This adapts the positive compliance argument already reviewed in
`c1/fx-finish/early_bound.py` and `REVIEW.md`; no source files were copied or
patched. The checker constant/provider module is imported unchanged.

The broader inverse-positive matrix setting is M. Fiedler and V. Ptak (1962),
[On matrices with non-positive off-diagonal elements and positive principal
minors](https://dml.cz/handle/10338.dmlcz/100526). The specific grounded-chain
formula and endpoint derivative proof above are direct derivations from the
pinned checker, not claims that this paper supplies the whole ship theorem.

Root-owned verification must project the known passing 88/162-box plans into
these rows, compare synthetic chains with `_end_response`, run all twelve
D185 maps, and independently audit any claimed infeasibility. This subtask
has performed source algebra and code review only; map measurements and
full-checker passes are not claimed here.
