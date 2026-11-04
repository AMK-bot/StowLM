# Current-run lift polishing

`polish.py:polish` takes a stow that already passes the unchanged full checker.
It never opens an archived assignment, plan, recipe or ballast. If its search
fails or every candidate fails the checker, it returns the exact incoming
`Scored` object. A candidate carries the incoming plan's concrete ballast.

The graph has occupied positions as vertices, top-access dependencies as
arcs, and a second bipartite graph joining discharge ports to hatch covers.
Opening a port-cover edge pays the cover's fixed charge once, regardless of
how many boxes use it. For each position and port, the model charges a restow
when a later-discharge box occupies that position and either a predecessor
must discharge or its hatch must open. The union prevents double charging a
box blocked both by its stack and by its hatch. The graph is a search
objective: crane-path closure and the actual D15 projection are always
recomputed by native replay, and the ordinary full checker gates acceptance.

Each exact CP-SAT neighborhood permutes boxes within one common length,
height, E2-rank and E1-heavy class, changing at most 32 placements. OOG and DG
boxes stay fixed. Occupied slots and their height/rank categories stay fixed;
static eligibility, mass-share capacities, and concrete securing rows are
hard constraints. The securing guard is no worse than the passing incumbent
at the constructor's fixed reference motion (or 1 if below 1). Numerical
coefficients are rounded conservatively. Hard cargo-moment guards permit
0.10 m longitudinal and 0.02 m transverse movement and forbid an increase in
the constructor's vertical-moment proxy. Exact floating guards repeat these
rows. These reference rows do not certify native stability; the full checker
still decides. Eight groups at most are considered, each with at most eight
seconds of CP search, one worker, and a check reserve.

This is a restricted local-branching neighborhood inspired by Matteo
Fischetti and Andrea Lodi (2003), *Local branching*, Mathematical Programming
98, 23–47, [author publication list](https://www.dei.unipd.it/~fisch/) and
[DOI](https://doi.org/10.1007/s10107-003-0395-5). The Hamming-radius constraint
limits the number of concrete box-slot assignments that change; restricting
comparable classes makes support, heights and weight-order rules invariant.
It is not the paper's full branch-and-bound algorithm.

The stack-order motivation is Mordecai Avriel, Michal Penn and Naomi Shpirer
(2000), *Container ship stowage problem: Complexity and connection to the
coloring of circle graphs*, Discrete Applied Mathematics 103, 271–279,
[Technion publication record](https://cris.technion.ac.il/en/publications/container-ship-stowage-problem-complexity-and-connection-to-the-c/),
[DOI](https://doi.org/10.1016/S0166-218X(99)00245-0). We use its top-access
shift interpretation, not a claim that this constrained, multi-cover model
is solved by circle-graph colouring. No graph-based lower bound is claimed
by this polishing module.

The acceptance pattern and fixed-topology/native-replay idea were read in
`c4/c2-benders/repair.py`, `permutation.py`, and `warm_restows.py`; this module
is freshly implemented and reads no warm driver data. The concrete securing
coefficients come from the copied c5 `Assignment.stack_ratio` cache.
