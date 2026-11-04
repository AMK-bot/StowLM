# Negative trial: joint first-aboard order lower bound

`bound_joint.py` is a separate copy of the final `bound.py`; the primary source
and `results/bounds/final-hold/` certificates remain unchanged. This trial adds
current-order consequences while allowing arbitrary current ship restows.
It is not used in the root's main bound table because it improved none of the
three remaining maps tested.

Give each eventual load box a distinct **first-aboard rank**. Any empty-arrival
legal move sequence defines these ranks even when it later restows cargo.
For every original yard pair with export U above export L, either U first
boards before L, or U has made at least one yard relocation. One Boolean per
export U prices this necessity once, even if several lower boxes witness it.

For every final physical-column pair with U above L, either L first boards
before U, or U has undergone at least one current ship restow. The same
implication applies to every final hold box L and every deck box U on its
cover. If U never restows, its first-aboard placement is its final placement;
L therefore cannot first board afterward while U blocks its support or cover.
One Boolean per U charges **one** current restow lift. This remains admissible
with direct ship shifts as well as two-lift off/on policies. No fixed load
order, unchanged-coordinate policy, or prohibition on current restows is
assumed. Box ranks and restow flags are only necessary conditions; their
feasibility does not construct a move sequence.

The new export-yard flags and current ship-restow flags represent disjoint
nonproductive move types. The inherited mandatory non-export-yard blocker
count is also disjoint from these export flags. Their weighted costs can
therefore be added to the original hatch/projected-restow objective. Both
flags price each physical box at most once, never each conflict edge.
All new objective coefficients retain downward rounding. Certificates use
CP-SAT's best objective bound.

Results, one worker and one numerical thread, requested `nice -n 5`:

| Map | Original bound | Joint bound | Status | Seconds |
| --- | ---: | ---: | --- | ---: |
| sim.small.40100025 | 8 | 8 | optimal | 3.262 |
| sim.small.40100046 | 8 | 8 | optimal | 1.319 |
| sim.small.40100034 | 6 | 6 | optimal | 0.398 |

JSON witnesses are `results/bounds/joint-40100025.json`,
`joint-sim.small.40100046.json` and `joint-sim.small.40100034.json`.
All three optimal witnesses have zero new order cost. Thus current-order
consequences alone do not close these gaps in the existing packing relaxation.
In particular this trial supplies no ten-lift certificate for 40100025.
Remaining E6 completion compatibility, cargo pair restrictions and physical
rules can still invalidate optimistic eight-lift witnesses. Delicate new E6
support cuts were deliberately not introduced in this short trial.

Verification: all sixteen inherited brute-force/oracle bound cases passed
when evaluated with this joint model. Four dedicated tests in
`bound_joint_tests.py` passed. They exhaustively enumerate the six first-aboard
orders of a three-box cycle with reversed yard/ship precedence and compare
the minimum unique relocation/restow flags against CP-SAT, including zero and
fractional current weights. The original fourteen fixed-assignment projection
tests intentionally compare **future plus hatch only** against total cost;
substituting the joint module into them adds unavoidable current-order costs
and invalidates those tests' total-equality premise. Their component projection
checks still agree. They are not joint-model tests and the original main
32-test suite remains unchanged.

Reproduce the dedicated tests with the same environment/PYTHONPATH as
`bound_NOTES.md`, substituting `bound_joint_tests.py` in the pytest command.
No new literature algorithm, external code or sealed data was used. This is a
direct necessary-condition derivation from the existing yard/ship move rules.
The root owns fresh checked-plan results. CPU slot was released after testing;
no further numerical run is required for this negative result.
