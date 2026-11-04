# Rounding implementation

`rounding.py` takes the root LP dictionary and the already generated column pool.
It reads physical coefficients exclusively through `model.features(column)`;
`model.feature_names` supplies their labels. There are no invented hydrostatic
coefficients in this module. It does not read any map itself.

The mathematical object is an exact-cover hypergraph: each page pattern touches
its boxes and occupied physical stacks. Structural rows require every box once
and every stack at most once. Side rows are the linear physical quantities of
the fractional stow. Preserving them means that rounding tries to keep the LP's
ship rather than independently selecting its strongest fractions.

`round_lp(model, root, deadline=..., seed=..., variant=...)` returns chosen column
indices (or `None`) and diagnostics. The variants are:

- `iterated`: start with the LP's positive coordinates and a nonnegative slack
  coordinate for each unused stack fraction. Exact cover and stack-plus-slack
  equations are never released. Compute the kernel of those equations and the
  protected physical rows; move to a boundary of the unit box. If there is no
  direction, release the physical row touching the fewest remaining fractional
  coordinates and try again. Deterministic endpoints prefer smaller maximum
  normalized feature drift. This transfers the floating-variable and row-release
  ideas of Beck and Fiala and iterative relaxation.
- `walk`: use the same exact structural equations and row-release mechanism, but
  project independent Gaussian coefficients into the current kernel. Choose
  between the two feasible endpoints with probabilities that give a mean-zero
  step. Boundary coordinates freeze. This transfers the correlated movement and
  partial colouring ideas of Bansal and Lovett and Meka.
- `cp`: omit the walk, providing an ablation of the pooled completion alone.

The walk may meet a fractional vertex even after releasing every physical row:
pattern exact cover is not integral. All variants therefore use single-worker
CP-SAT for pooled completion. It enforces exact box cover and stack capacity,
uses a complete feasible witness as its hint when supplied, otherwise the
walked coordinates, and minimizes `1000 * maximum deviation +
sum of deviations`, with an inferior tie term preferring the walked coordinates.
A row's coefficients are scaled to 1,000 units per largest absolute column
coefficient and rounded to integers. Reported final drifts always use the
original floating-point coefficients. Quantization and the finite time limit
can matter. Side rows are optimized, rather than promised as hard final bounds.

A first 121-box pilot used roughly 10,000 pooled columns and exhausted its
31-second completion allowance without an incumbent. Completion therefore now
retains every positive root column plus every supplied witness column, then
fills a cap of 2,000 columns with alternatives ranked by their box affinity to
the root on the same page, phase-3 cost, and seeded random ties. The cap expands
if the mandatory support itself is larger. The root and witness never disappear
because of this cap. `model.rounding_pool_cap` optionally changes the cap.

`model.rounding_witness`, when present, is a list of known complete column
indices, produced before reoptimizing the shared root. Structural validation
requires all boxes exactly once and all stacks at most once. CP-SAT receives
its full binary hint and exact deviation and maximum-deviation auxiliary hints;
presolve iterations are limited to three. If optimization finds no incumbent
or time is already exhausted, the valid witness is returned with
`completion.fallback_witness=True`. Such a row records no rounding improvement.
Pool size, original pool size, root-support size and witness size are recorded.
The controlled experiment must give all arms the same enriched root and witness.

No discrepancy theorem guarantee is claimed: physical moments are weighted,
the structural exact-cover constraints can have a nonintegral vertex, this walk
uses full endpoint steps rather than the papers' prescribed increments, and no
semidefinite program is solved. The walk gets at most 12 seconds or 20% of its
budget; more than 900 floating coordinates sends it directly to completion.
Unlike the original `Model.dive`, pooled completion does not reprice. A failure
can therefore mean the fixed pool has no integer exact cover; it is not an
infeasibility certificate for the map. This asymmetry must accompany any
comparison with the original dive.

`balance_kk(model, chosen, deadline=..., seed=...)` returns the selected indices,
heel, and diagnostics. It considers disjoint occupied deck-page mirror pairs
with the same model type, same bay and equal transverse distance. Both exchanged
patterns must pass the existing per-page legality and lashing checks. Their
combined feature vector must preserve every nonheel row within `1e-6`. For each
pair the old/new heel contributions are written as a fixed centre plus or minus
one number. Largest differencing repeatedly replaces the two largest numbers
by their difference and reconstructs their opposite signs. The fixed ship heel
is included as an anchored number. Swaps are applied only when this partition
reduces absolute heel. This stronger invariance restriction intentionally
excludes many swaps available to the original greedy `balance`.

## Literature checked

- József Beck and Tibor Fiala (1981), [“Integer-making” theorems](https://doi.org/10.1016/0166-218X(81)90022-6).
  Author institution's [record and abstract](https://www.researchwithrutgers.org/en/publications/integer-making-theorems/)
  checked; no transfer of its discrepancy bound is asserted.
- Nikhil Bansal (2010), [Constructive Algorithms for Discrepancy Minimization](https://arxiv.org/abs/1002.2259).
  Abstract checked: correlated random movement uses an SDP in the paper; our
  kernel projection is an explicitly different practical heuristic.
- Shachar Lovett and Raghu Meka (2012), [Constructive Discrepancy Minimization by Walking on the Edges](https://arxiv.org/abs/1203.5747).
  Abstract checked: restricted random walk and basic linear algebra motivate
  the projected partial-colouring arm.
- Narendra Karmarkar and Richard M. Karp, [The Differencing Method of Set Partitioning](https://www2.eecs.berkeley.edu/Pubs/TechRpts/1983/CSD-83-113.pdf).
  The order names the 1982 work; the university's available archive labels its
  report [1983, UCB/CSD-83-113](https://www2.eecs.berkeley.edu/Pubs/TechRpts/1983/6353.html).
  Differencing and sign recovery are implemented directly, without downloaded code.

## Checks performed

Single-thread synthetic exact-cover test with two boxes and four patterns,
root coordinates all `0.5`: all three variants returned an exact integer cover
on two distinct stacks; vertical moment stayed exactly unchanged. The two walk
variants released the conflicting heel equation, took one step, and ended at
an integral point. Their final heel error was one tonne-metre, which is the
integer obstruction in that toy. Pure completion selected the opposite,
equally good orientation. CP-SAT returned `OPTIMAL` in every arm. Largest
differencing on `[8, 7, 6, 5]` recovered a partition of zero residue. Python AST
parse passed. Follow-up tests supplied a witness to all three arms, checked
that a cap below the mandatory support retained all root columns, verified
expired-deadline fallback, and rejected a witness reusing the same stack. No
real-map improvement is claimed by these unit-scale checks.
