# A* exact yardstick (task 4.6)

The operational planner now uses **A* branch-and-bound over the shared engine's
legal actions**. Its certificate concerns weighted nonproductive lifts within
the configured engine constraint model, including D49's empirically qualified
necessary-direction linear screen and stopping when the manifest is complete.
APPROXIMATION: empirical screening and the public-engine action domain can
exclude physical optima. It does not claim the optimum over
every physically possible ship plan, or tertiary margin optimality. It now
uses the **public feasibility API**, with C2 and its measured-error margins
enabled by default. An explicit optional feasibility configuration supports
permitted C2-off diagnostics, as labelled below. Accepted production candidates
require own ballast, exact checking and default public-engine replay for G1;
configured diagnostic replay is reported separately.
D51 accepts this admissible A* method; the full D11 margin tie-break remains
unproved. Current fix-wave evidence and exact limitations are recorded in
`../review/fixes/fix-t4-exact.md` and `fix-current-epoch/`.

Plain terms: each branch's paid lifts plus a bound on work still forced cannot
exceed the cost of its best continuation. Once every remaining branch's bound
is at least the best checked plan's cost, no cheaper plan remains. Root and
residual counting bounds are combined by maximum to avoid double-counting.
Cheaper paths reopen canonical states; fully identical cargo can be renamed
only when semantics and original manifest/inherited-slot roles agree. The
public engine's LP reference can depend on action history, so dominance retains
canonical semantic prefixes too; matching physical worlds alone is insufficient.
A bounded greedy rollout supplies incumbents, and locked hold cargo adds a
proved v2-specific lower bound on unavoidable future hatch lifts. Unknown
ballast terminals keep their bounds, so a failed numerical search cannot create
a false certificate. Source: [Hart, Nilsson and Raphael (1968)](https://ai.stanford.edu/~nilsson/OnlinePubs-Nils/PublishedPapers/astar.pdf).

## Current feasibility-2.3 paired groundwork

`current_compare.py` freezes train/validation specifications before generation,
then regenerates them with public `instances_v3.generate` and current full hulls,
checker **1.3** and original cargo/static limits. The frozen selection contains
six small cases (three train, three validation; 20–34 boxes) and three train tiny
cases (six boxes each). Every pair uses identical saved bytes. Only train and
validation specifications are opened. Input hashes, physical source hashes,
current source epochs and versions are recorded.

Repeated upstream fixed-load changes interrupted `paired-current/` (14 rows)
and `paired-final/` generation. These outputs remain historical: payload audits
confirmed that the regenerated vessels' actual fixed loads changed, so their
measurements cannot be pooled with the latest inputs. The latest attempted pilot
is `paired-quiet-current/`: first frozen tiny and first frozen small, regenerated
under physics SHA `2172f7db692f8263758c9511828a9c6f58d5923e39a3ce7fd01960d1d71bafac`.
Generation took 53.483 s for tiny and 3.000 s for small. The latter also passed a
300 s outer generation watchdog (`bounded_generation.py`, 3.380 s total).

Both exact and scalable planners receive the explicit public configuration
`FeasibilityConfig(extra={'global_screen': 0})`. This is **C2 off**, as permitted
for this experiment; terminal full checking remains active. Configured-mask
replay is recorded separately from production G1. No C2-off reference plan is
called approved gold. The exact API now accepts this optional configuration;
its default still uses the production C2-on engine.

Each planner receives 120 s. Scalable has an explicit additional 2 s return
grace so a completed public-solver incumbent is not discarded during serialization;
actual overruns are recorded and excluded from claims of finishing within 120 s.
Finalization/check/replay have a separate 60 s hard wall cap, including a maximum
30 s ballast budget. Times describe the contended shared machine.

| Pinned prior-rule pilot | Observed / planned cases | Exact OPTIMAL | Exact checker / candidates | Scalable checker / complete plans | Qualified lift gap |
|---|---:|---|---|---|---|
| Tiny, train, six boxes | 1 / 3 | 1 prior-epoch C2-off engine optimum; 6 lifts, engine LB 6 | 1 / 1 | 1 / 1; 12 lifts | (12−6)/12 = 50%, prior-epoch C2-off engine reference only |
| Small, train, 20 boxes | 1 / 6 | 0 (120 s limit) | Undefined: no candidate | 1 / 1; 40 lifts | Undefined: no exact proof |

The tiny exact search took 11.640 s plus 2.714 s validation; scalable took
0.916 s plus 5.549 s validation. Both loaded every box, passed the full checker
and configured replay, and retained an unchanged runtime epoch with all 76
original physical source hashes matching. The exact certificate concerns the
configured engine domain; the 50% gap has that scope and is not a current-epoch
physical quality gap. The separate fresh-tiny 16.7% excess claim has not been
independently verified here and must retain its own recorded epoch/domain
qualification. Neither plan has a tertiary
margin certificate. Raw checkpoints and checked plans are in
`paired-quiet-current/`. The independent physical oracle hit its 10 s hard limit
both before exact and with a checked incumbent afterward: no physical optimality
or brute-force agreement was established. A separately admitted 120 s, two-million-node
attempt also reached its hard wall (120.033 s, LB 0, no completed brute optimum
or checker witness). Its peak RSS was not measured. Overrides remain unresolved.
During the expanded campaign, upstream `rules/catalogue.toml` and
`checker/rules_special.py` changed again. Strict guards stopped new cases;
affected rows are tagged with physical-source mismatches and do not qualify
for pooled current-epoch gaps. No further regeneration was started. All numeric jobs ended by 20:59:52 UTC.
The attempted pilot contains ten raw planner rows: four stable prior-rule rows
(two complete pairs) and six quarantined mixed-epoch rows. The planned six-small/
three-tiny cohort is incomplete; observed proof shares are 1/1 tiny and 0/1 small,
not fractions using unrun cases. Four oracle calls (three first-tiny attempts
and one second-tiny attempt) all returned LIMIT. `report.json` retains raw
metrics separately from stable-source metrics, and `jobs-ended.json` records
worker completion. Reference plans remain diagnostic, not production gold. The first
tiny and small pairs above completed before that change and describe their
recorded prior rule epoch, rather than the newly edited checker implementation.
The first 20-box scalable plan passed checking/replay with zero shutouts in
120.212 s search plus 5.501 s validation; its 0.212 s search overrun is explicit.
Exact reached its limit in 120.048 s without a checked candidate.

Per-planner CPU/RSS attribution is unavailable when bounded worker cleanup defers
OS child accounting; raw zero counters are not evidence of zero resource use.
The independent tiny physical oracle is distinct from
the public action domain. The three frozen tiny specifications have no yard blockers,
so they do not challenge the default relocation-target rule. An optimal physical
witness without overrides proves an optimum exists without them for that case;
an override in one witness, or a gap to an engine optimum, does not prove overrides
are necessary. Further override stress cases require separate predeclared admission.

The historical tables below are not measurements of this fresh campaign. Current
verification reported by the parent passed 227 exact-related tests; whole-suite
acceptance and 50 independent full-physics agreements remain unestablished.

Historical verification before the hard-deadline revision passed **198 tests**
(197 unit plus one slow integration). The hard-deadline revision passes **213
fast tests**, including 16 new deadline regressions, with
`uv run pytest -q tests/solver/test_exact*` (exit 0). Slow integration was not
rerun during the ship-design measurement freeze.
These include 50 actual public-v2 local/D25 comparisons, 50 isolated A*/oracle
comparisons, 50 legacy relaxed comparisons, and residual-bound/dominance/oracle
certificate regressions. They do **not** establish 50 full-physics canonical
agreements: the public local-rule tests are hull-free and isolate terminal physics.
The historical whole-suite attempt hit its 480 s cap before completion. Current canonical
results and their C2 setup failures are below; commands are in `validation.md`.

The first Measurements table and `tiny.jsonl`/`rungs*.jsonl` are
**historical pre-A* Dijkstra measurements**,
pinned to their recorded source digest. They are retained for comparison and
are not presented as measurements of the new operational A* implementation.
The explicitly relaxed `check_physics=False` oracle retains that legacy search.

The deadline revision bounds the entire `solve_exact` call in a dedicated
POSIX worker, including engine construction, bounds, search, ballast, checker
calls and G1 replay. Startup and result delivery count against the same budget;
process cleanup has a bounded 20 ms join allowance. The parent stops only its
recorded worker. Expiry returns raw `TIME_LIMIT` and planner `timeout`, retaining
the best fully checked and replayed incumbent, if any. Each call starts a fresh
worker, so cold model calibration also counts against its limit.

The independent oracle uses the same guard. On expiry it retains a previously
completed checker witness as `FEASIBLE`, otherwise returns `LIMIT`; it never
claims brute-force equality or optimality from an interrupted computation.
Synthetic regressions cover blocked construction, bounds, actions, checking,
ballast and G1 replay, background-thread callers, and incumbent preservation.
Python 3.12 warns about fork in the deliberate background-thread test; the
parent watchdog still bounds that call. **Real-ship measurements
remain frozen by the leader's explicit instruction**; the tables below are
historical observations, not reruns of this revision.

## Reproduce

All commands use one worker, one BLAS/OpenMP thread, no GPU. The scripts append
one completed instance to JSONL and skip matching keys on restart. Use a new
output path after code or parameter changes.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/exact-solver/benchmark.py --count 50 --time-limit 2
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/exact-solver/rungs.py --time-limit 1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/exact-solver/rungs.py --physics --time-limit 1 --out experiments/exact-solver/rungs-physics.jsonl
```

## What is being measured

`benchmark.py:tiny_instance` builds two loading boxes and one background box
from the model fixture. Seeds change yard stacking, PODs, masses, relocation
weights and whether one or two ship stacks are available. Covers are absent.
Expert thresholds are explicit synthetic policy inputs (heavy 30,000 kg;
stack weight-order tolerance 10,000 kg). This is a small, deliberately restricted
family, not 50 production-rung instances. Every relaxed optimum is compared
with `stowage.bounds.bruteforce.brute_force_optimum(check_static_rules=True)`
using `solve_exact(check_physics=False, check_static_rules=True)`
and `stowage.bounds.lower_bound`. The brute-force oracle enumerates physical
moves independently of the action expander.

APPROXIMATION: the tiny study retains the fixture's placeholder hull; it omits
ballast/stability/strength/visibility/lashing by using the explicitly relaxed
solver API. No relaxed certificate applies to the joint physical problem.

`rungs.py` uses the existing full-physics random-yard shards, one each on tiny
(6 loads), small (36) and medium (150). Free relocation actions and the default
rule are solved separately. Only two proved optima can establish that default
relocations lose objective value. Finding overrides in an incumbent alone says
nothing about whether an optimal plan needs them. A timeout without an
incumbent has no measurable primal gap; it is reported as unavailable.

## Measurements

Final rerun on 2026-10-27 after progress-first equal-cost tie breaking; raw rows are in `tiny.jsonl`, `rungs.jsonl` and
`rungs-physics.jsonl`. The 50 tiny cases all proved optimal and matched the
independent brute-force optimum **exactly**, including nonzero objectives.
The counting lower bound was valid in 50/50 and tight in 49/50. Mean exact
solve time was 0.0356 s, median 0.0077 s, maximum 1.3949 s (cold static-rule
initialisation); total exact search 1.782 s, total oracle search 0.358 s.
Every row records the exact module SHA-256
`ff69d5aa4668878de63820f14b676b8f52b1bd7459bcb2df505e55c0363dbee1`.

| Family / mode | Cases | Limit per solve | Proved optimal | Mean / max solve s | Incumbent / gap |
|---|---:|---:|---:|---:|---|
| Restricted tiny, relaxed full actions | 50 | 2 s | 50/50 | 0.036 / 1.395 | All; solver certified gap 0 |
| Saved tiny (6 loads), relaxed full / default | 1 each | 1 s | 0/1 each | 1.000 / 1.000 | None / unavailable |
| Saved small (36), relaxed full / default | 1 each | 1 s | 0/1 each | 1.001 / 1.001 | None / unavailable |
| Saved medium (150), relaxed full / default | 1 each | 1 s | 0/1 each | 1.009 / 1.016 | None / unavailable |
| Saved tiny (6), physics full / default | 1 each | 1 s | 0/1 each | 1.000 / 1.000 | None / unavailable |
| Saved small (36), physics full / default | 1 each | 1 s | 0/1 each | 1.002 / 1.002 | None / unavailable |
| Saved medium (150), physics full / default | 1 each | 1 s | 0/1 each | 1.005 / 1.006 | None / unavailable |

For an available plan the requested counting-bound gap is `(objective-LB)/objective`
(with zero when both are zero); raw rows keep the independent LB as well as the
solver's certified bound/gap. These are different quantities: a search can prove
an optimum even when a counting lower bound is loose.

**Acceptance remains incomplete:** the small-rung proved share is 0/1 at this
short limit; there is no CP-SAT/MIP model or tertiary margin certificate. All
12 larger-rung probes timed out before a complete candidate, so there were no
ballast attempts or exact-check rejection cuts. This does not measure the
linear-feasible rejection rate. The question whether the default relocation rule
loses optimality on bigger rungs is **unresolved**; two timeout results prove
nothing about their optima.

Saved generation shards also carry empty expert-rule overrides. The static
checker requires explicit heavy/weight-order thresholds when applicable. These
probes preserve those inputs; no feasible operational departure is inferred.

The shared feasibility engine landed during validation. Its default mode uses
a bounded completion witness, so an action can be masked even when an exact
departure check passes. The slow real-hull integration test constructs such a
one-box candidate with fleet hull geometry, synthetic background cargo and
explicit synthetic strength envelopes. The candidate passes the full checker;
the planner withholds it if the shared mask rejects it. Mask rejection remains
an unresolved search bound and is never a physical-infeasibility proof.
The real-hull test does not establish an approved joint optimum.

## Compute

Current paired campaign: the parent reserves up to three one-core workers,
3 GB total, no GPU, through 21:15 UTC (8 GB RAM reservation after the physical oracle was admitted). Native numeric libraries are pinned to
one thread. The revised estimate for the remaining five small and two tiny
pairs was about 15 minutes with two or three workers, based on the measured
120 s searches plus up to 60 s checking and the initial completed tiny pair.
Each public generation has its own 300 s watchdog. The first current tiny and
small generations took 53.483 s and 3.000 s; the first tiny pair took 20.819 s
combined. Two initial independent oracle attempts each reached 10.012 s, so a
separately admitted 120 s / two-million-node oracle attempt is recorded too.
The current attempt ended early after rule changes: ten raw planner pipelines
summed to 694.819 s of work (concurrent, not elapsed wall time), while all numeric
jobs ended at 20:59:52 UTC. Four oracle attempts summed to about 150 s.
The parent maintains the shared fleet ledger; per-planner CPU/RSS values that
cannot be attributed after deferred child reaping are explicitly unavailable.

Historical relaxed experiments below were short and needed no heavy-job
reservation. Oracle pilot: estimated one core, <1 GB, no GPU, seconds; actual 1.830 s search
plus interpreter startup, RSS not measured. Initial pre-final measurements took
5.39 s for tiny agreement and 6.36/6.45 s for the relaxed/physics rung shards.
Final rerun estimates remained <10 s/6 s/6 s: actual 2.63 s for all 50 tiny
comparisons (2.153 s recorded per-case work), 6.21 s relaxed rungs and 6.24 s
physics rungs. Every solve had `num_workers=1`; BLAS/OpenMP/Accelerate were
pinned to one thread. No GPU, network, paid calls or heavy jobs were used;
RSS was not measured. No heavy-job ledger entry was needed for these short
single-core shards; this task's scope excludes the fleet ledger.

## Full-hull A* follow-up

`full_hull.py` prepares instances under this folder and runs the operational A*
solver against the shared feasibility model. Controlled cases use two tiny or
thirty small ordinary 2,200 kg empty dry twenty-foot boxes with ground-accessible
yard positions and the same POD. This isolates attainable low objectives; it is
not representative random cargo. Random mode retains the generator's original
six/thirty-six target mix. Hull geometry, tanks, windage, strength limits and the
fleet slice's existing background loads are retained; no broad test envelopes
or rebalanced background loads are introduced. Tiny restricts the decision cells
to the original two one-tier deck stacks. Cache storage is redirected to this
folder without changing numerical models.

The initial full-hull input preflight was blocked by the former analytic hull
parameters. Canonical KCS fleet parameters and the v2 release subsequently landed;
that old failure remains recorded in `full-hull-preflight.json` as historical
context. Current evaluation uses canonical artifacts directly instead of
regenerating or conditioning cargo.

## Canonical v2 release evaluation

`canonical.py --root experiments/instances-v2` accepts explicit generators,
profiles, train/validation splits, offsets and shard counts. It verifies the
release-pinned manifest SHA-256, filters split metadata **before opening any
instance**, verifies each selected shard's bytes, checks clean provenance and
rechecks the canonical split specification. Test instances are never opened.
Outputs retain release hashes, solver source hashes, D11 hard-pass/shut-out/lift
levels, counting-LB gap, solver certificate gap, margins and separate
solve/finalize/check timings. A weighted-lift certificate does not claim tertiary
margin optimality.

The first public-v2 pilot selected `random_yard/tiny/seed-00000000.json` (train).
The nominal solve limit was 10 s; cold public-engine construction did not return
before the **45 s outer wall cap**, and the wrapper stopped its precisely
recorded child PID after **45.0208 s**. No incumbent, solve result, finalization
or checker report was produced. Solved and OPTIMAL shares for this one attempted
pilot are both **0/1**; lift objective and gap are unavailable. This is a censored
setup measurement, not a physical-infeasibility proof. The initial private-cache
attempt was interrupted after more than 180 observed seconds inside the standard
192-condition linear-error calibration; its exact total was not instrumented.
The final pilot reused standard shared, content-addressed numerical caches as
directed; no equations, margins, error sample counts or safety checks changed.

`canonical_bounded.py` runs sequential uv-managed Python children, records each
PID and enforces a wall cap around setup as well as solving. It terminates only
its own recorded child. Checkpoints are `public-v2-pilot.jsonl`,
`public-v2-pilot-0000.log` and `canonical-pilot-status.json`. All compute has stopped
pending further admission. The root executive owns the fleet-ledger reservation;
this pilot used one core, a 2 GB reservation, no GPU; actual CPU/RSS were unavailable
when the child was terminated. Fifty equally capped tiny attempts would consume
at least 37.5 CPU minutes if all hit the cap; no completed-case timing supports
a useful full campaign estimate yet.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/exact-solver/canonical_bounded.py --count 1 --offset 0 --wall-cap 45 --name public-v2-pilot --time-limit 10
```


### Admitted public-v2 cold/warm follow-up

After compute admission, the predeclared first train tiny shard and first three
eligible train/validation small shards were attempted. Metadata filtering chose
tiny seed 0 and small seeds 100001, 100002 and 100004; all are train. No test shard
was opened. A public default-engine prewarm ran in the **same process** before
a nominal 30 s warm search. Setup and complete process wall time were counted;
these are not 30 s end-to-end claims. No C2 screen, margin or calibration sample
count was reduced. The first small failure was retried once by solver construction;
later `checker.missing_vessel_data` prewarm failures were recorded directly to
avoid rebuilding the same unavailable model.

| Canonical profile | Attempted | Solved | OPTIMAL | Full checker checks | Mean / max process s | Outcome |
|---|---:|---:|---:|---:|---:|---|
| Random tiny, 6 targets | 1 | 0/1 | 0/1 | No plan to check | 480.052 / 480.052 | Setup outer timeout |
| Random small, 36 targets | 3 | 0/3 | 0/3 | No plan to check | 47.115 / 120.026 | 2 model unavailable; 1 setup timeout |

The tiny public-engine setup did not finish before its 480 s cap. The completed
small setups took 7.950 s and 2.481 s, but both failed calibration: **0 accepted
conditions out of 64 required in 3,200 attempts; no condition reached equilibrium**.
The other small setup hit its 120 s cap. These are model availability/setup
failures, not proofs of physical infeasibility. No candidate reached ballast
finishing or the full checker, so all D11 levels, objective gaps and margin
comparisons remain unavailable. `canonical-campaign-summary.json` includes every
attempt, including killed setup processes, in its denominator.
Checker pass rates are undefined here: zero departure plans reached a checker
call. A missing plan is not recorded as a failed physics check. Medium/full and
the dense realistic profiles were not attempted in this exact-solver campaign.

Independent oracle follow-up: the first three eligible train tiny shards (seeds
0, 1, 2) each received a 5 s physical brute-force budget and a 30 s outer cap.
All three returned `LIMIT` without any incumbent (147, 116 and 151 expanded
nodes); consequently no actual-checker witness or real optimality certificate
was available. Their process walls were 6.580, 6.371 and 6.646 s (19.597 s total).
The oracle uses the original physical move domain, including possible overrides;
it is a reference domain distinct from the public v2 mask. The helper can certify
a full-checker optimum only when an independently proved relaxed lower bound
meets a checker-passing witness; these three runs did not establish that equality.
Override necessity therefore remains unresolved, and no 50-case real-checker
agreement claim is made. Raw oracle rows and wrapper runs are separate in
`oracle-canonical-pilot.jsonl` and `oracle-canonical-pilot.runs.jsonl`.

Compute estimate versus actual: two admitted cold probes reserved one core,
2 GB each, no GPU, at most 480 s per process; actual tiny 480.052 s, small 18.278 s.
The next two small probes reserved 120 s each; actual 120.026 and 3.041 s. Known
CPU time for completed small results totals 12.585 s; their maximum measured RSS
was 127.4 MB. CPU/RSS of killed setups were unavailable. The admitted short oracle
pilot estimated roughly 15 s of search plus startup/checking and used 19.597 s
process wall, one core sequentially. All children were precisely recorded and
only those children were stopped at their caps. No processes remain running.
A 50-case campaign at the 480 s setup cap would reserve 6.67 CPU hours without
guaranteeing any completed model or certificate; it was not launched. The root
executive owns ledger/history updates and further admission.
