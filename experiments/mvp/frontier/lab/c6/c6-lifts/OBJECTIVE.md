# Optional fixed-charge assignment objective

`objective.py:Assignment` subclasses this folder's copied c5 `gap.Assignment`.
It does not replace `method.py`'s initial cold constructor. Changing an LP's
objective changes its chosen vertex, dive and eventual feasible stow; that can
lose a pass despite leaving the original rows in place. Keep the c5 incumbent
and independently check every alternative before accepting an improvement.

## The model in plain words

There are two small graphs on top of the existing box-to-slot assignment LP.
The first joins discharge ports to hatch covers. Using a hold cover incurs its
current opening and closing once; using that cover for cargo at a later port
incurs another opening and closing once. These are fixed charges, like opening
a warehouse before assigning any customer to it. The old objective charged
every box placed away from a guessed cover layout; the new objective chooses
which cover-port edges to open and charges each edge once.

For every box and cover, all candidate slots under that cover are grouped in
one link to the cover variable. This is stronger than linking each candidate
slot separately: one box split half-and-half between two tiers still activates
its cover fully. A capacity row also links assigned TEU to the physical number
of cells under the cover, the same capacity-cut idea used by c4/c2-layout.

The second graph joins a tier to each earlier discharge port that would require
lifting its box. A box is a blocker if it stays past that port and either a
lower box leaves there or its underlying hold cover opens there. One Boolean
variable charges that tier once per port even if both causes occur. Looking at
all lower tiers catches `[port 1, port 2, port 2, port 3]`: all three upper boxes
move at port 1 and the top box moves at port 2. An adjacent-transition penalty
misses some of this work. Only planned cargo and pure pages are represented;
background cargo, yard relocations and extra operational hatch closures remain
with replay and the full checker. This is a search objective, never a certified
map lower bound.

The existing vertical objective and moment rows are inherited. First-aboard
order is deliberately left to the existing cold `Sequencer` and actual replay:
a guessed fixed birth order can trade hatch gains for yard relocations, and a
free birth-order model needs the full current-operation precedence constraints
from c4/c2-hatch/bound_joint.py. No claim to optimize first-aboard order is made.

## LP and integer tail

The LP relaxes cover, edge and blocker variables into `[0, 1]`. The integer tail
keeps those variables Boolean and includes fixed-only activation rows. Reusing
c5's tail unchanged would be wrong here: that tail interprets every auxiliary
as independent soft slack and skips rows without free assignment variables.
It could forget that a previously fixed hold box opens a cover and underprice a
new deck blocker. A synthetic test covers that exact case.

The copied c5 securing and moment slacks remain soft by default for optional
cold construction. `hard_securing=True, hard_moments=True` sets their bounds to
zero in both LP and tail for an incumbent neighbourhood. Integer coefficients
round conservatively, and every returned integer-tail assignment is checked
against the original float rows. The full checker must still decide actual
securing at true heights and actual ballast; fixed-motion rows do not certify it.
No source file outside c6/c6-lifts was changed.

## Papers and relation to this implementation

- Cornuéjols, Nemhauser and Wolsey (1983), *The Uncapacitated Facility Location
  Problem*, Cornell technical report, later chapter in *Discrete Location Theory*
  (1990), pp. 119–171. [Cornell archive](https://ecommons.cornell.edu/entities/publication/0f45f770-313e-4ce4-87a7-102fc9e4b3f1).
  The opening-cost plus customer-assignment formulation motivates the
  disaggregated cover activation links. This implementation's TEU capacity
  cuts and cargo side rows are additional structure; no facility-location
  approximation theorem is claimed.
- Avriel, Penn and Shpirer (2000), *Container ship stowage problem: Complexity
  and connection to the coloring of circle graphs*, *Discrete Applied
  Mathematics* 103, 271–279, [DOI](https://doi.org/10.1016/S0166-218X(99)00245-0),
  [authors' university record](https://cris.technion.ac.il/en/publications/container-ship-stowage-problem-complexity-and-connection-to-the-c/).
  Their top-access conflict view motivates treating each earlier-port blocker
  as a graph conflict. This code uses explicit blocker witnesses rather than
  claiming to implement their circle-graph coloring algorithm.

## Verification and recommendation

Five synthetic tests in `test_objective.py` cover nonadjacent blockers,
cover/internal de-duplication, hold blockers, grouped fractional links and
activation of previously fixed cargo in the integer tail. These are meaningful
model checks, not a measured improvement on maps. The root executive owns CPU
slots and runs them; no numerical run was started by this subagent.

Recommendation: retain as an optional post-incumbent arm. Do not replace the
first cold c5 attempt without measured pass retention. It may provide useful
neighbourhood guidance, but the larger matrix and new vertex selection need
measurement on the six development maps before any frozen held-back run.

## Alternatives considered and initial frozen choice

The initial frozen choice is local branching among comparable boxes in
`polish.py`: keep the occupied positions, physical heights, heavy status and
weight ranks, then let at most 32 concrete boxes change position within one
class. This leaves support and height relationships intact, while hard securing,
stack-mass and moment guards limit physical changes. Every accepted move still
needs cheaper real replay and a fresh full-checker pass. The current-process
incumbent is retained on failure. This is an adaptation of Fischetti and Lodi
(2003), *Local branching*, not their entire branching strategy:
[primary publisher paper](https://doi.org/10.1007/s10107-003-0395-5).

The alternatives below were considered, rather than measured as independent
arms. Their selection rationale is an engineering assumption, not an experimental
ranking of the published methods.

- **Proximity search:** demand a lift improvement, then minimize the number of
  changes from the passing stow. This reverses local branching's emphasis:
  distance becomes the objective instead of a radius constraint. It could be a
  later arm, but a strict improvement cutoff is awkward when the compact graph
  objective omits some real replay work. Fischetti and Monaci (2014), *Proximity
  search for 0-1 mixed-integer convex programming*, *Journal of Heuristics* 20,
  709–731: [primary publisher paper](https://doi.org/10.1007/s10732-014-9266-x).
- **RINS:** solve the LP, fix assignment decisions on which the LP and incumbent
  agree, and optimize the remaining decisions. Agreement can identify a useful
  neighbourhood without choosing classes by hand. Here a new LP and uncertain
  agreement pattern add work; the smaller comparable-class model is the initial
  choice. RINS is not implemented in this frozen arm. Danna, Rothberg and Le Pape
  (2005), *Exploring relaxation induced neighborhoods to improve MIP solutions*,
  *Mathematical Programming* 102, 71–90:
  [primary publisher paper](https://doi.org/10.1007/s10107-004-0518-7).
- **Fixed-charge LNS:** remove several related cover/port groups, then rebuild
  them together so that an entire expensive graph edge can close. This is more
  powerful than exchanging ports among existing positions because it can change
  which covers are used now. Reconstructing stack heights also disturbs securing
  and side moments, so it needs a larger hard-row neighbourhood. The concrete
  precursor is `c4/c2-layout/lns.py`; Pisinger and Røpke (2010), *Large
  Neighborhood Search*, *Handbook of Metaheuristics*, pp. 399–420, supplies the
  general remove-and-repair framework, not a cargo fixed-charge theorem:
  [authors' DTU record and manuscript](https://orbit.dtu.dk/en/publications/large-neighborhood-search-2/).
- **Exact exchange from c4:** `c4/c2-benders/permutation.py` reassigns concrete
  boxes to existing positions with static and capacity guards; `warm_restows.py`
  also considers column moves. We reuse the fixed-position idea and add a port
  graph objective plus a local-branching radius. Exact refers to the discrete
  subproblem model; a time-limited solve does not certify the whole map optimal.

The optional objective in this document is consequently not the default: it
changes the initial LP vertex and rounding path, while the frozen choice starts
with a checked incumbent and admits only checked improvements. Root's combined
verification passed all 23 tests, including this module's five synthetic tests.
No numerical process was started for this documentation addition.
