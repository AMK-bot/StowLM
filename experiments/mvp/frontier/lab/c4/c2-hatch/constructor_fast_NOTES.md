# Separate practical construction v2

The original `constructor.py`, its frozen comparison adapters, and all bound
modules remain unchanged. `constructor_fast.py:construct` uses the same
restricted load-once model and oracle cuts, but asks CP-SAT to stop at the first
feasible witness. Each solve gets the whole remaining construction budget;
there is no initial eight-second UNKNOWN restart that discards learned clauses.
There can still be fresh solves after genuinely new checker or securing cuts.

The only added guidance is a **soft** first-aboard permutation. It merges each
current yard export chain from top to bottom, selecting a heavier available box
first, then a later first-POD call, then lexical box ID. Non-export blockers are
excluded from the hint. The implied no-export-relocation flag values are also
hinted. These hints neither fix variables nor shrink the construction family;
the solver can violate them to obey support/hatch constraints and pay required
relocations. No stored stow, incumbent ballast, or map result enters the hint.

First feasibility may be more expensive in lifts than the original optimization
search. This is an explicitly separate experiment, designed to measure that
speed/quality tradeoff. The result retains the first raw witness separately
from the first static-screened replay witness. Neither is a full-check pass
until root's unchanged scorecard admits the cold-finished plan.

No global lower bound is exposed. Any subsequent equality certificate must come
from unchanged `certified.lower_bound`, with scope: **fewest lifts among plans
the checker passes, counting later ports by our D15 projection (fixed stow, no
later loads)**.

Development uses synthetic fixtures and approved quick maps only. No validation
results have been read. Three synthetic tests passed using the explicitly
granted sole CPU token, with root's paired process acknowledged idle at an arm
boundary. No numerical work ran concurrently with that paired process.

## Fixed-policy quick pilots

Both runs used seed zero, 30 seconds construction, one numerical thread, and no
ballast or incumbent input. Results are isolated in
`results/constructor-fast-pilots/`.

| Quick map | First raw replay lifts | Raw static screen | Returned replay lifts | Seconds | Model witnesses |
|---|---:|---|---:|---:|---:|
| `random.small.40100011.0.8` | 38 | clean | 34 | 11.655 | 4 |
| `sim.small.40100025` | 19 | E6 failure | 17 | 24.666 | 10 |

These are **replay-legal candidates**, not full-check passes. No finishing was
attempted in these pilots. Compared with the original optimization constructor,
v2 recovered a candidate on the first map where the original timed out, but its
34 lifts exceed the historical checked upper of 22. On quick25 the original
returned eight replay lifts in 9.435 seconds; v2 returned 17 in 24.666 seconds.
The first-feasible strategy therefore sacrifices too much quality to promote.
No constructor source was changed after these pilot results.
