# Cycle 3: global small-map certificates and an eight-lift plan

All code, copies, tests and results in this component remain in `lab/c3/c2-hatch/`.
No sealed or frozen validation input was opened during development. Only `quick`
was used for tuning. The root executive owns the subsequent frozen evaluation,
fresh admission runs, per-map tables and final contract report.

## Scope and copied code

Copied privately from Cycle 2: `bound.py`, `bound_joint.py`, `bound_tests.py` and
`bound_joint_tests.py`. Copied `c1/fx-finish/early_bound.py` as `bound_securing.py`,
retaining its reviewed securing-source guard, independent positive-compliance
cross-check, numerical safety guards and unknown fallback. The local copy adds
recorded hashes for visibility and the hull-geometry implementation. No live
`engine/` implementation is imported. The root copied engine result rows only as
upper-bound evidence, because `engine/` is being edited during this cycle.

Public entry point: `certified.py:lower_bound(instance, budget_s=15, seed=0,
tables=None, upper_bound=None)`. It returns a dataclass, not a plan. The
`proof_family` explicitly says **global arbitrary current moves, final-stow
relaxation**. Current ship shifts and off/on restows remain allowed. No
load-once restriction is imposed. Empty decision arrival, no discharge and no
joint forecast are the packing scope; unsupported cases retain the inherited
counting bound. Fixed background cargo remains present and immutable. The
validated vessel catalogs keep fixed cells/stacks disjoint from decision
cells/stacks; background need not be empty.

## Model and objective

Each load box chooses one statically eligible footprint. Physical cells exclude
overlap and occupied cells form bottom-up prefixes. Stack mass/height demands
round downward and limits round outward. The selected length class chooses the
checker's actual 20-foot or long-box stack rating; the old largest-rating
relaxation is no longer necessary. Full-checker securing rejects mixed 20/long
hold columns, so a column-type Boolean enforces that necessary condition.

A cover costs its unavoidable current opening/closure, and a hold discharge
cover costs two later lifts at each first matching later POD call. A unique
required-box Boolean per future call takes the upward/deck-clearance closure,
including transmission through both columns of spanning boxes. It charges
projected restow once per blocker, rather than once per conflict edge. Frozen
crane-crossing requirements remain relaxed. This is the checker's fixed-stow
projection, not a minimum over arbitrary future ship policies.

The optional joint model gives each box its first-aboard rank. An initial yard
upper export either boards before a lower export or incurs a yard relocation.
A final upper/deck box either first boards after the relevant final lower/hold
box or incurs at least one current ship restow. The latter is necessary because
an un-restowed box retains its first-aboard position. Each box is charged once,
not once per precedence arc. One shift costs one lift; off/on costs at least
that much. Export-yard flags, current ship-restow flags and the mandatory
non-export-yard blocker bound represent disjoint move types and add safely.

## New necessary constraints

### Supported heat domains

At tier k, each supporting column has exactly k lower boxes. The minimum and
maximum regular container heights give an outward interval for the actual
floor, with the finite stack envelope as another necessary upper limit. The
checker's special geometry sums **regular height**, excluding OOG overheight
below cargo. A long box's floor is the maximum of its two column floors.

For fixed horizontal coordinates, distance to a rectangular heat source is
convex as the vertical floor varies; its maximum on an interval is at an
endpoint. If both endpoints violate clearance, every supported placement
violates it and the slot can be removed. Interior-overlap violations at zero
clearance are handled separately. This removed ten placements of the
away-from-heat box on `sim.small.40100034`, raising its initial bound 6 to 8.

### Conservative OOG-aware bottom E6 witnesses

E6 is a **final departure condition**, not a condition that staging cargo can
evade. The unchanged full static checker calls `e6_departure_violations`.

For an ordinary heavy box b (no DG, OOG or bookings), use the production
placement oracle to verify a compatible empty bottom hold footprint. Empty
bottom cells imply those entire columns are empty, so support, local class,
mass, height and booking effects are fixed. Other ordinary remote cargo cannot
invalidate this witness. Lateral OOG can, so credit every selected OOG position
that **might** overlap the hypothetical hold placement:

`b_on_deck <= occupied_witness_cells + possible_OOG_blockers`.

A possible blocker needs the same horizontal proximity used by the checker and
an optimistic vertical overlap interval. Its maximum floor uses k times the
maximum regular box height. Its minimum uses k times the minimum height in a
hold, and `max(k-1,0)` times that height on deck: hypothetically moving b away
can remove one deck support. This deliberately credits all such possible floor
drops, even when b was elsewhere. A 1e-7 m outward allowance exceeds the
checker's 1e-9 m geometric tolerance. Unknown OOG policy supplies no such cut.
The rule is disabled under freeze-v1, where legacy E6 is soft.

Six such bottom cuts on 40100034 raised the cheap packing bound to **10**.
They are global final-state consequences, independent of current move order.
The read-only logic audit independently checked the hypothetical-removal floor
bound, nominal-height convention, empty-column argument and frozen guard.

### Optimistic DG unary rebuilding

The legacy production table may assess an unsupported upper DG position on an
empty ship and reject unknown support geometry. Rebuild DG rows from necessary
physical eligibility, reefer, booking, crane, POD, gross-mass and applicable
legacy E6 constraints. Then call the checker's documented necessary
`rules_imdg._stowage(..., ship=None)` screen: it retains category/DoC/fixed facts
while deferring unknown upper SW1/SW2 support geometry. Never AND the rebuilt row
with the potentially restrictive original DG row. A synthetic regression
exercises an actual supported legal upper SW2 placement rejected by the old row.

### Lazy final-state and securing cuts

`build_model` and `solve_model` expose a reusable CP model. An illegal final
assignment can receive a full assignment no-good because `check_static_rules`
is independent of ballast and current move history. Ordinary-heavy E6 cuts can
fix the full target/OOG column contexts, including every empty cell, closing
through spanning boxes. Heat and OOG geometric rows instead use outward-rounded
floor expressions at 1e-6 m resolution. OOG requires vertical non-overlap when
horizontal overlap/proximity applies; it does **not** add a fictitious vertical
clearance margin. Grouping identical height/length/overhang envelopes reduces
identity-specific OOG rows.

A reviewed securing proof can exclude a whole final stow for every ballast.
Only a source-pinned positive rod-chain proof can be lifted to a smaller
component: fix every identity/slot and every empty cell on the entire disjoint
pure-long support columns. Then parent frames, floor, CG, heights, winds, end
shares and gear remain identical. Outside cargo changes only the loading
condition, already covered by the uniform motion floor. Unknown securing is
never rejection. These analytic floating-point exclusions retain the inherited
strict guards; they are not a formal interval-arithmetic kernel proof. Saved
securing certificates include exact box slots, empty cells and source hashes.

## Integer cutoff proof and search budget

With finite nonnegative integer objective weights and integer restow
multiplicity, any plan costing below a small integer cutoff has an exactly
represented integer objective. The cutoff magnitude is restricted below
2**53. An upper bound U permits a model constraint `cost <= U-1`; the outside
mandatory-yard contribution is subtracted with exact Python integer arithmetic.
Only CP-SAT **INFEASIBLE** proves the next lower bound U. UNKNOWN never does.
Equality with a freshly full-checked U supplies optimality; an upper input alone
does not establish a pass.

The inexpensive packing model runs first. If its certified lower bound already
exceeds the cutoff, return it immediately without constructing the joint or
lazy models. A regression forbids joint construction and verifies this shortcut.
Otherwise CP-SAT receives the genuine remaining time. The early prototype
restarted the same UNKNOWN model every five seconds; this discarded learned
search state and was corrected before freezing.

## Quick development outcomes

| Remaining Cycle 2 map | New best checked upper | New global lower | Explanation |
| --- | ---: | ---: | --- |
| sim.small.40100025 | **8** | **8** | Solver's first-aboard order yields a legal eight-lift plan |
| sim.small.40100034 | 10 | **10** | Heat domains plus conservative OOG-aware bottom E6 |
| sim.small.40100046 | 9 | **9** | Exact E6 final-state cuts plus joint order relaxation |

The first eight-lift 40100025 witness passed replay/static screening but failed
full securing. The source-pinned rod lower bound proved that particular chain
at least 1.222403 times its limit for every ballast. Excluding that complete
column pattern produced a different eight-lift witness. Its first-aboard order
was essential; canonical sequencing could add a relocation.

`results/pilot25b/sim.small.40100025.json` records a **fresh full-checker pass at
8 lifts**, through `lab/score.py`, in 11.35 seconds, using inherited engine
ballast. This improves the engine's nine and stored 0.9.0's twenty-six. The new
CP proposal run took 6.24 seconds after the earlier witness; this is warm
experimental evidence, not a cold solver speed comparison. The passing plan
matches a global eight-lift bound, so the sixteen stored projected restow lifts
were avoidable, not forced.

For 40100034, earlier unrestricted lazy trials and a sustained 300-second
cutoff trial retained only an eight-lift bound. After the six conservative
bottom E6 cuts, the 300-second-budget run finished in **7.49 seconds** with
cutoff 9 infeasible; its initial packing bound was already 10. See
`results/threshold34-final.json`. The final early-return optimization removes
that redundant subsequent joint search. All ten `quick_small` maps now have
matching bounds and full-checked uppers in the root's final measurement; the
root owns the frozen reproduction and complete per-map table.

## Verification and measurements owned elsewhere

The copied 32 packing/oracle tests and four first-aboard-order tests remain.
New tests cover exhaustive legal heat/E6 assignments, both exact E9 grouping and
lazy pair rows, outward floor arithmetic, real supported DG geometry,
integrality/invalid weights, cutoff scope, the early-return shortcut, the
128-case independent rod-compliance check, source guard fallback, and a real
bad 25 rod cut preserving the freshly checked eight-lift stow.

Use one numerical thread and requested `nice -n 5`. While `BENCH-RUNNING`
existed the team used one CPU-busy process; after its removal the root resumed
the usual second slot. No process was killed. The longest individual numerical
runs were five minutes, below the twenty-minute threshold. Frozen validation
is evaluation only and was not opened during this development.

Reproduce tests from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c3/c2-hatch \
nice -n 5 .venv/bin/python -m pytest \
-o cache_dir=experiments/mvp/frontier/lab/c3/c2-hatch/.pytest_cache \
experiments/mvp/frontier/lab/c3/c2-hatch/bound_tests.py \
experiments/mvp/frontier/lab/c3/c2-hatch/bound_joint_tests.py \
experiments/mvp/frontier/lab/c3/c2-hatch/strengthening_tests.py -q
```

The model is a direct derivation from local checker semantics. The inherited
certificate distinction follows Land and Doig (1960),
[An Automatic Method of Solving Discrete Programming Problems](https://doi.org/10.2307/1910129),
and Bergman, Cire, van Hoeve and Hooker (2016),
[Discrete Optimization with Decision Diagrams](https://pubsonline.informs.org/doi/abs/10.1287/ijoc.2015.0648).
The source-pinned spring-chain argument was inherited from Cycle 1's reviewed
`early_bound.py`, including its explicit positive compliance derivation; its
matrix context is Fiedler and Pták (1962),
[On matrices with non-positive off-diagonal elements and positive principal minors](https://dml.cz/handle/10338.dmlcz/100526).
No downloaded implementation or new literature theorem is claimed.
