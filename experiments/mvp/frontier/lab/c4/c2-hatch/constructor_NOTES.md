# Exact-model cold construction

`constructor.py:construct` builds the C3 first-aboard joint packing model, retaining
the optimistic DG widening required by review F7. It fixes all ship-restow flags
to zero. Consequently every support and hatch precedence applies to the emitted
first-aboard ranks; the ordinary action expander supplies yard relocations and
hatch moves. This is a restricted load-once construction lane. Its internal
objective bound is **not** a globally admissible bound and is never exported as
one. The unchanged `certified.py:lower_bound` remains the proof interface.

The solver's first raw assignment/order is retained as `first_model_actions`,
even when its final static screen fails. Its replay result, static violations,
and lift count are recorded independently of subsequent cut generation. Root's
adapter can submit this first witness to the unchanged full checker to measure
first-time legality rather than counting a repaired witness as first-time.

The returned `actions` are the first witness surviving the real departure-static
checker, the source-pinned ballast-independent securing screen, and real move
replay. Static E6/heat/OOG cuts and complete-assignment nogoods copy the logic of
C3's `lazy_bound.py`; a replay failure excludes only that assignment **and**
order. Neither ballast nor incumbent cargo is consulted during construction.
Finish with genuinely cold ballast separately from an inherited-ballast lane;
every pass requires `lab/score.py`'s independent full checker.

The only process-local mask patch is the authorized E9 fix. No checker rule or
source is modified. No validation outcome was read to develop this lane. Fixed
development policy: 30 seconds construction, 20 seconds cold finishing, then
report generic 60-second finishing rescue separately. A first solve gets at
most eight seconds; after UNKNOWN the remaining time is offered in one solve,
avoiding repeated identical time-limited restarts. All numerical work uses one
thread and the team's CPU token.

Whenever a checked upper equals the separate global bound, the scope is:
**fewest lifts among plans the checker passes, counting later ports by our D15
projection (fixed stow, no later loads)**. The construction model itself supplies
no such proof. The existing bound additionally guards empty decision arrival,
no discharge or forecasts, immutable background, and recorded rule mode.

Relevant inherited methods: Hooker and Ottosson (2003), logic-based Benders,
<https://doi.org/10.1007/s10107-003-0375-9>; Roberti, Bartolini and Mingozzi
(2015), fixed-charge transportation, <https://doi.org/10.1287/mnsc.2014.1947>,
inherited in C3's notes. No new paper claim
is required for converting a CP-SAT witness to its explicit permutation.

Tests cover forced support/hatch order, unavoidable export relocations,
assignment-and-order replay nogoods, complete witness validation, and the
explicit absence of a global bound or full-check pass claim. All **six tests
passed**, using the explicitly granted sole CPU token, numerical threads one,
and a cache under this C4 directory.

## Frozen quick development pilots

At seed zero and 30 seconds construction, quick `sim.small.40100025` yielded
eight replay lifts in **9.435 seconds**, after two model witnesses. A cold V4
ballast attempt at 20 seconds timed out: this is **not** a full-check pass and
does not prove ballast infeasibility. `results/constructor-pilot25.json` contains
the raw first witness, final replay witness, lazy-cut trace and finish metadata.
The first try used a Python stdin harness that cannot spawn a multiprocessing
worker; it was repeated using `constructor_pilot.py`'s standard main guard. The
constructor itself was unchanged. The numerical priority request was rejected
by macOS (`setpriority: Operation not permitted`); the one-thread, sole busy
process policy still applied.

The first `quick_tiny` map, `random.tiny.20000032.0.2`, yielded eight replay lifts
in **2.068 seconds**, one model witness, with a fixed 10-second construction
budget. No ballast attempt was made in this tiny pilot. The artifact is
`results/constructor-pilot-tiny.json`.

These pilots developed no map-specific constraints or settings. Constructor
source was frozen before root's validation evaluation. The mandatory scorecard
admission and first-time full-check statistics belong to root's subsequent
frozen-policy experiments.
