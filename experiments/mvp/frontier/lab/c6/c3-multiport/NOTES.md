# Cold multi-port securing LP, Cycle 6

Status: fixed cohort generated; development smoke and runtime verification in
progress. No frozen comparison is claimed yet. This folder is the only write
target. Sealed data and `lab/c6/c6-maps/` remain closed.

## Question and model

Does pricing port order directly in a securing-aware box-to-slot LP improve
cold medium plans as discharge ports and fill increase? Each box chooses one
eligible physical footprint. The copied LP retains securing, support, weight,
booking, moment, height and cell-capacity rows. New Boolean variables represent
a required slot at each call and an opened hatch cover. Their least closure
charges a surviving blocker once per call, including a long box connecting two
stacks. Port-pure hatch labels and adjacent-tier restow proxies are replaced.
The order produced by the LP is preserved when securing permits it.

The independent top/hatch closure is exact for the cohort's unchanged legacy
activation and fixed-coordinate D15 scenario. With modern crane-path closure
enabled it is a lower bound until those dependencies are added. No activation
is changed to obtain a result. Current hatch use is one opening per used cover;
actual replay remains authoritative for extra current handling and yard work.
The LP also includes preferences and soft physical overages: its objective is
not a global lift bound. See `PORT-MATH.md` for rows, proofs and tests.

Circle conflicts come from native E2 rank/tolerance constraints that force
opposing port order. A clique placed on one physical stack requires blocker
events. These are valid conditional strengthening rows while E2 is enforced,
not bounds after peeling T4. A freely reordered single loading call still has
a zero port-sequence-only global restow bound. No positive bound is invented.

## Frozen inputs and development split

`cohort-spec.json` fixed seeds 263360000–263360031 and all specifications before
any planner result. There are 24 ship×ports×fill cells: feeder_500/feeder_800,
3/4/5/6 discharge ports, 60/70/80% fill; the eight ship×ports 80% cells have a
second replicate, for 32 maps. Six development maps were selected in advance,
covering both ships, all fills and all port counts. The other 26 are frozen
TRAIN measurement data, not sealed or unseen data. Development and nondevelopment
results will be reported separately. No seed is removed after a solver failure.

`cohort.py` wraps the native generator without editing `src/`. Its native cargo
mix, admission gates and background screening remain, including DG, reefers,
40/45-foot cargo, high cubes, OOG and special bookings. A natural draw can have
no DG. POD coverage and geometric fill in [target−0.02,target] are generation
conditions only. Actual fill and equipment counts are in the manifest. These
maps have empty arrival and one loading call; no multi-load claim follows.

## Copies, coldness and comparison

The securing implementation and experimental dependencies are copied under
`frozen_securing/`, original Frontier helpers under `frozen_frontier/`, and an
existing archived Engine 0.12.0 under `frozen_engine12/`. Its original Frontier
backend is restored locally, not the peel planner that surrounded the archive.
Tier classification and the leader's unmodified table/aggregation are copied
under `frozen_reporting/`. See `source-manifest.json` and `snapshot-notes.md`.
No live experimental folder is imported. Native checker/rules/parameters are
unchanged. Both arms are cold: no stored plan, recipe, assignment or ballast
from any earlier run is supplied. Reading a result for reporting is not a
construction input.

Both arms receive 240 proposal seconds, 120 native finishing seconds per
attempt, and a 600-second whole-arm/map cap. The port LP uses five GM rounds
and at most two finishing candidates; Engine 0.12 retains its frozen default
portfolio and internal candidate limits. Stage bounds are advisory around
atomic numerical calls. Times describe these configured pipelines and machine
load, not an isolated speedup. Every worker and native descendant is recorded;
only exact owned PIDs can be interrupted. Cleanup can outlast the 600-second
measurement cap. Two numerical workers maximum, one while the leader sentinel
exists, one thread per numerical library. Commands request nice 5; this macOS
sandbox can deny the priority adjustment and keep inherited priority.

A complete 64-arm/map sweep plus development and verification exceeds twenty
minutes because all predeclared failures are retained and every accepted plan
is fully checked. No individual numerical solve is intentionally unbounded.

## Tier completion gate

`frozen_checks.py` delegates to the unchanged public checker and returns its
report untouched. It observes full checks in copied solver code and checkpoints
concrete plans, reports and every hard breach. A valid checked incumbent can be
retained when the outer time limit interrupts the public engine call. Private
native finalizer checks are not intercepted; any returned plan is independently
checked on the original input. Trial instances cannot supply a pass against a
different input. Plans must place every requested box and have no shut-outs to
count as completed.

Full-enforcement work is attempted first. Only after that fixed work fails to
find a full pass are checked candidates classified at the smallest peeled
level. There is no lower-tier construction search in this iteration and no
peeling to reduce lifts. Inside a level, cargo retention precedes number of
broken rules, breach and lifts, using the frozen leader classifier. Unknown
hard rules are T0 and fail closed. Relaxed plans retain exact rule IDs,
measurements, limits and breach evidence; they are never called passes.
Safe partials, if observed, are separate and include percent aboard.

## Sources

The assignment/securing solver is the copied Cycle 5 implementation described
in its NOTES. This iteration implements its new port rows directly; no paper
code was downloaded or run.

- Avriel, Penn and Shpirer (2000), [circle graphs and stowage](https://doi.org/10.1016/S0166-218X(99)00245-0).
- Tanaka and Matsui (2021), [capacitated circle-graph stack formulation](https://arxiv.org/abs/2102.00691).
- Gavril (1973), [circle-graph independent sets](https://doi.org/10.1002/net.3230030305).
- Lenstra, Shmoys and Tardos (1990), [unrelated-machine assignment LP](https://doi.org/10.1007/BF01585745), and Shmoys and Tardos (1993), [generalized assignment](https://doi.org/10.1007/BF01585178): inherited design motivation, without their approximation guarantees under these many side rows.
- Danna, Rothberg and Le Pape (2005), [relaxation-induced neighbourhoods](https://doi.org/10.1007/s10107-004-0518-7), and Shaw (1998), [related-item exact repair](https://doi.org/10.1007/3-540-49481-2_30): inherited repair design.

## Results

Development change, based only on predeclared seed 263360000: the initial
priced LP returned a T4 completion at 53 lifts (17 yard, 10 current hatch,
10 later hatch, 16 later restow) in 264 seconds. All five checked attempts
failed only legacy E6; ten heavy deck boxes had compatible empty hold
opportunities. The tuned wrapper adds a soft cost of 25 per heavy deck box in
the next round only after an E6-only hard miss, using the actual E6 threshold.
It removes that guidance when another hard rule appears. It neither bans heavy
deck cargo nor treats an E6 witness as a unary constraint. Earlier higher-tier
checked candidates remain available. This is development evidence, not a frozen
all-map result. The first smoke predates completion of the cohort manifest;
its input is unchanged, but its source/cohort fingerprint is not reused in the
final comparison.

Pending development, freeze, all-map comparison, and fresh full-check audits.
