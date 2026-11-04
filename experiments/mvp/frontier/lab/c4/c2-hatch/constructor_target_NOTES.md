# Independent mathematical targets for cold construction

`constructor_target.py:construct` is a separate experiment; original/fast
constructors, frozen adapters, and all global bound modules remain unchanged.
It spends at most min(4 seconds,15% total budget) solving the independent plain
packing relaxation from current input/tables. Its valid lower bound, rounded
conservatively to an integral objective, supplies the first construction target.
No stored plan, historical upper, incumbent cargo or ballast is read.

The joint load-once model receives a cap on **total** weighted lifts. The cap
subtracts the forced non-export yard relocation constant before constraining
joint terms plus the cover constant. All objective weights must be finite,
nonnegative, small enough, and integral; unsupported weights do not enter this
lane. CP-SAT stops at the first feasible witness and receives the whole remaining
budget, preserving first raw witness and first returned replay witness metrics.
Static/securing/replay cuts remain the same source logic as the frozen original.
Cold yard hints are soft and inherited unchanged from the fast experiment.

Only a proved **restricted construction-target** INFEASIBLE permits target+1.
Assumption literals retire the old target while retaining necessary cuts.
UNKNOWN ends the search; no repeated time-slice restart occurs. A higher
construction target never increases `independent_lower_bound`: that number
remains precisely the separate plain model's certified value. No global
`lower_bound` field or full-check pass claim is exported. Subsequent equality
certificates must use unchanged `certified.lower_bound`, scoped as **fewest lifts
among plans the checker passes, counting later ports by our D15 projection
(fixed stow, no later loads)**.

Tests cover forced-yard/cover constant accounting, retiring a proved infeasible
target, invalid budgets, fractional-weight guards, and the separation between
construction infeasibility and the independent bound. Development uses only
synthetic fixtures and approved quick maps; validation outcomes were not read.
All **nine tests passed** under root's explicitly granted second CPU token.
The benchmark marker was gone, one root proof process occupied the other slot,
and numerical threads were one. No finishing or validation run was performed.

## Frozen fixed-budget quick pilots

Both maps used seed zero and a 30-second total construction budget, without
historical upper plans or ballast. Their completed diagnostics, raw first
witnesses and returned actions are isolated in
`results/constructor-target-pilots/`.

| Quick map | Independent LB | First target | Returned replay lifts | Seconds | Witnesses |
|---|---:|---:|---:|---:|---:|
| `random.small.40100011.0.8` | 22 | 22 | 22 | 4.207 | 1 |
| `sim.small.40100025` | 8 | 8 | 8 | 6.613 | 2 |

Neither run increased its target. Both raw first witnesses were departure-
static and replay legal; the first quick25 witness failed the ballast-independent
securing screen, causing a valid cut before the returned second witness. These
are candidate-generation results, **not full-check passes**. Root must still
finish ballast and admit plans through the unchanged independent scorecard.
The source was frozen after tests and was not changed after pilot outcomes.
All numerical processes exited and the CPU token was explicitly released.
