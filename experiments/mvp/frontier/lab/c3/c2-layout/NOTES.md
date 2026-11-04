# Cycle 3: closed covers, page neighbourhoods, and necessary capacity

Read the full lab contract including the Cycle 2 and Cycle 3 updates, and the
lab README. All experiment code, result and vessel-cache paths are scoped to
this directory. No sealed input or sealed list was opened. `engine/` is not
imported: it is changing during this cycle. The shared checker is unchanged.

## Model and sources

Use the Cycle 2 stack-page/port-cover graph. A used hold cover costs two hatch
lifts now; every discharge-port edge costs two later. Closing one pure-port
cover therefore saves four lifts. Deck space above a shut hold remains usable.
Within-page and hatch-mediated future blockers are charged once per call.
The small 18-box/two-cover graph and medium 88-box/six-cover graph are the
concrete examples in our Cycle 2 NOTES. Forced relocations are independent.

Papers: Gilmore and Gomory (1961),
https://doi.org/10.1287/opre.9.6.849 (page column generation); Roberti,
Bartolini and Mingozzi (2015), https://doi.org/10.1287/mnsc.2014.1947
(fixed-charge transportation); Pisinger and Ropke (2010), *Large Neighborhood
Search*, https://backend.orbit.dtu.dk/ws/portalfiles/portal/5293785/Pisinger.pdf.
Publisher abstract/source metadata and the LNS chapter's introduction were
read in this cycle. The chapter describes destroying part of a solution and
repairing it; here a few whole hatch regions are freed and the others fixed.
No downloaded implementation was run.

## What was copied and implemented

`pages_base.py` copies `c2/c2-cgscale/pages.py`: exact pricing/master caches,
diversified legal seeds and equivalent E8-only initializer. `pages.py` adds a
closed-hold static mask, leaving its deck usable, and preserves Cycle 2's
asymmetric deck-port permission. It gives heavy hold cargo a stronger packing
reward when a cover closes. This is a constructor heuristic, not an inequality.
`repair.py` copies our Cycle 2 OOG repair. The existing scout securing oracle
and authorized leader E9 mask wrapper are imported unchanged. The final
scorer is real replay, and every pass requires a fresh full checker.

`lns.py` seeds an incumbent's pure pages, freezes pages outside four selected
covers, adds transferred/dual-priced alternatives and solves a restricted
integer page master. Each used cover and port edge has its fixed charge.
Deck boxes blocked by both their own page and a cover count only once.
Optional heel/LCG rows limit drift. Closing a target hold keeps deck capacity.
OOG repair is rejected if it changes any fixed outside assignment.
The neighbourhood model's bound is only a restricted-pool bound, never a
whole-map lower bound. Warm known-pass columns may bypass the fitted oracle
while seeding, but every new candidate undergoes real replay and full check.

`method.py` is explicitly a warm improvement search on the four previous
TRAIN plans and the remaining V6-certified f60 plan. Unsolved ladder maps
start cold with a plain page packing and a checker-guided physics ladder.
Reference ballast is retained as a candidate condition; the plugin checks it
again and may invoke the normal finisher. No stored passed flag substitutes
for a fresh check. Whole-map closed-cover designs complement local search.

## Valid necessary inequalities

See [bound_notes.md](bound_notes.md) for the full proof. `bound.py` strengthens
our Cycle 2 relaxation by retaining physical supporting stacks and their
necessary cell/mass/height limits, plus the checker's necessary prohibition
on mixed 20/long hold stacks. It adds conditional capacity cardinality:
if required hold demand exceeds the largest k-1 cover capacities, at least
k covers must open. E6 bottom witnesses give a second disjunction: either
all ordinary heavy cargo is in the hold, requiring enough covers for those
boxes plus hold-only cargo, or some ordinary heavy is on deck and every
common validated empty-hold witness cover must open. Lateral OOG disables
these E6 cuts conservatively. No fitted securing coefficient is a bound.

Bounds in `train4.txt` order improve **51/90/52/51 to 63/90/70/51**.
For the 87-box high map, ordinary-heavy plus hold-only demand is 64 cells,
but the entire hold has only 60. Some ordinary heavy must be on deck.
Validated bottom witnesses under all six covers force all six open, costing
24 hatch lifts; 46 forced relocations give **70**, attained by a checked
plan. This map is proved optimal. On the 88-box high map, the conditional
hold demand is 51 cells and the three largest covers hold only 44, proving
at least four covers and bound 47+16=63. The low maps have no heavy boxes;
the strengthened physical relaxation does not improve their bounds.

## Fixed measurement set and compute

`MANIFEST.json` fixes all 24 TRAIN summaries from V6 metadata with stage f60
or f70, before outcomes. `f60.txt` and `f70.txt` contain 12 maps each; V6 full
passes are 5/12 and 0/12. `train4.txt` is the unchanged Cycle 2 primary set.
All lists use the shared catalog's approved TRAIN gate. No evaluation-only
or sealed input is accessed.

While `c1/leader/BENCH-RUNNING` exists, the whole team uses **one** CPU-busy
process. Coding/proof work was delegated while numeric jobs used an explicit
single shared token. Every run sets OMP/OPENBLAS/VECLIB threads to one,
PYTHONDONTWRITEBYTECODE, and STOWAGE_VESSEL_CACHE_DIR under this directory.
`nice -n 5` is requested; the sandbox refuses setpriority and still runs the
process. No other session's process is stopped. Either twelve-map rung of
the required 24-map ladder may exceed 20 minutes as one aggregate run under
the one-process limit; the reason is the full predeclared cohort, not a
20-minute allowance per map.

Hygiene disclosure: the LNS agent's first 0.79-second synthetic pytest run
omitted disabling pytest's cache provider and may have updated the existing
repository-root `.pytest_cache`. It edited no external source. Shared cache
files were not deleted/restored because other sessions use them. Subsequent
tests explicitly use `-p no:cacheprovider` or a scoped cache directory.

## Results in progress

Initial low-500 probe (`results/probe-low-1`): a four-cover page neighbourhood
closed two hold covers and passed the full checker at **63** versus 71,
47 relocations + 8 hatch now + 8 hatch later + zero restows. Total wall 168.01s.
The measured main-source snapshot is retained as `method_probe1.py`.
Later neighbourhoods reached CP-SAT UNKNOWN at their short limit; this is
not infeasibility. Final cohort results and independent audits follow below.

## Fixed-charge LP variant and measurement versions

The original `method.py` first generates a coverage-LP pattern pool and then
solves the fixed-charge integer master. It is the frozen entry for f60.
`lns_lp.py` adds a literal fixed-charge LP relaxation over the same pool before
CP-SAT: fractional page choices activate fractional cover/port-edge variables;
linear product envelopes account for deck restows. It is limited to two seconds
and one HiGHS thread. Near-integral LP choices provide integer hints. Its LP
objective is only a bound on that restricted pool, never a whole-map bound.
Four synthetic LP tests and one ordinary-heavy mask test passed, bringing
the checked test total to **22** (including 17 original layout/bound tests).
The later independent port-capacity tests bring the final total to **26**.

`method_lp.py` is the frozen f70 entry and the primary-repeat entry. In addition
to the fixed-charge LP, `pages_lp.py` restricts ordinary heavy cargo to hold
when a cover is closed, which avoids the repeated E6 failures seen in f60's
cheaper high-map candidates. It sizes the pure hold partition by mandatory
hold demand, searches every number of shut covers, applies an inexpensive
page-family cell-capacity screen, and gives both local and whole-map designs
time. If a first-fit seed violates the fore-aft target it uses the moment-aware
page LP/dive instead. Checker-derived E9 conflicts exclude only exact pairs
of selected patterns before retrying. This is a search restriction, not a
checker change or a mathematical global cut. V6 reference tier profiles are
retained so their legal incumbents can be seeded.

The f60/f70 lists were fixed together before measurements, but these two
entries differ. Pass rates by rung are reported descriptively; their difference
must not be attributed solely to fill density or solely to the new LP.
The benchmark marker disappeared at 13:37 UTC. Thereafter f60 and f70 run
concurrently with one numerical process each; no third numerical job overlaps.
Both use budget 180 s, finalize 45 s, two tries, seed 0 and one score worker.

`polish.py` is an optional separate warm follow-up for the six predetermined
feeder_500 f70 maps (`polish-f70-500.txt`). It frees four hatch regions without
forcing a closure, so heavy-cargo maps that must open every cover can still
reduce restows and port-cover edges. It reads only completed checked f70 rows
and preserves the independent full checker and real replay. These follow-up
results do not replace the originally measured rung pass counts.


## Stronger port-capacity certificate

[bound_ports_notes.md](bound_ports_notes.md) proves the independent two-port
Hall-capacity refinement. Enumerate shut/early/late/both masks for every cover.
Necessary capacities adjacent to early, late and both ports must cover the
corresponding mandatory hold cell demands. Take the minimum over the exhaustive
E6 branches (all ordinary heavy hold versus some heavy deck), then add forced
relocations and maximise with the frozen bound. On the first high map the
37/14 port demands need at least five edges on four used covers, proving
18 hatch lifts and **65 total**. Measured bounds are **65/90/70/51**;
`bound_ports_results.json` records input hashes, masks and validation times.
The construction LP remains excluded from every certified whole-map bound.

## Primary results

The four primary TRAIN maps all pass real replay and the full checker. Current
best portfolio (updated after the final audit below):

| Map (ship/fill/distribution/seed suffix) | C2 | V6 | Best | Relocations | Hatch now/later | Restows now/later | Bound | Gap |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 500/f60/high/000 | 71 | 131 | 71 | 47 | 12/12 | 0/0 | 65 | 6 |
| 800/f60/low/101 | 116 | 214 | 106 | 84 | 10/12 | 0/0 | 90 | 16 |
| 500/f60/high/002 | 70 | 128 | 70 | 46 | 12/12 | 0/0 | 70 | 0 |
| 500/f60/low/000 | 71 | 127 | 55 | 47 | 4/4 | 0/0 | 51 | 4 |

Mean is **75.5**, versus C2 **82** and V6 **150**. Mean certified gap falls
from C2's 21 to **6.5 lifts**; the high/002 map is globally D11 optimal.
The 55-lift low map uses two hold covers, leaving four shut, versus C2's six.
Its candidate initially failed list; the normal finisher found ballast in
18.29 seconds and the score runner's independent full check passed it.
The 106-lift large map uses five covers and six port edges. Exact E9 pattern
nogoods recovered a legal candidate that earlier cheap solutions missed.

`results/train4-lp` is a separate 300-second repeat: all four replay and full
pass, lifts 71/108/70/55, mean 175.11 seconds/map. The best 106 comes from the
180-second f60 rung, so the portfolio is not a single cold controlled run.
The high/002 optimum is retained immediately at its proved bound. Warm-start
and aggregate compute costs must be retained when comparing wall time.

The leader's hash-matched 0.9.0 big-planner evidence gives one paired map:
500/f60/high/000 is **71 versus 129 lifts** (0.9.0 wall 1235.24 seconds).
0.9.0 fails the other two quick-medium maps. The lab's old generic baseline
catalogue has no measured medium pairs, so score.py correctly reports 0/0
there; the explicit leader evidence supplies this single comparison. No
speedup is claimed for a warm search against 0.9.0's cold run.

## Reproduction and validation

From the repository root set `PYTHONPATH=src:experiments/mvp/frontier:
experiments/mvp/frontier/lab` (join without whitespace),
`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`,
`PYTHONDONTWRITEBYTECODE=1`, and set `STOWAGE_VESSEL_CACHE_DIR` to this folder's
`vessel-cache`. Request `nice -n 5` for numerical commands. Let `D` below
stand for `experiments/mvp/frontier/lab/c3/c2-layout`.

```sh
.venv/bin/python experiments/mvp/frontier/lab/score.py --set "$D/f60.txt" --method "$D/method.py:propose" --finish "$D/method.py:finish" --budget-s 180 --finalize-s 45 --tries 2 --workers 1 --out "$D/results/f60-1"
.venv/bin/python experiments/mvp/frontier/lab/score.py --set "$D/f70.txt" --method "$D/method_lp.py:propose" --finish "$D/method_lp.py:finish" --budget-s 180 --finalize-s 45 --tries 2 --workers 1 --out "$D/results/f70-lp"
.venv/bin/python experiments/mvp/frontier/lab/score.py --set "$D/train4.txt" --method "$D/method_lp.py:propose" --finish "$D/method_lp.py:finish" --budget-s 300 --finalize-s 45 --tries 2 --workers 1 --out "$D/results/train4-lp"
.venv/bin/python "$D/bound_ports.py" --list "$D/train4.txt" --base-results "$D/bound_results.json" --out "$D/bound_ports_results.json"
.venv/bin/python -m pytest -q -p no:cacheprovider "$D"/test_*.py
```

Tests passed in scoped batches: 11 original LNS, 4 fixed-charge LP, 3 page mask,
4 physical-stack bound and 4 port-capacity tests: **26 total**. The physical
bound tests map four actual V6 checked plans into every retained necessary
constraint. The port tests independently cross-check tiny Hall enumeration
against integer transportation allocations. The actual score runner tests
all produced plans through replay and the unchanged full checker.


## Follow-up on f70 restows

The fixed six-map feeder500 f70 follow-up (`results/polish-f70-500`) retained
three passes and failed to construct the three previously failing low maps.
For the passing high maps, lifts fall **145/117/121 to 121/103/91**: paired
mean **127.67 to 105.00**. Each still uses all six hold covers, as anticipated
by the E6/capacity argument. Final parts are respectively relocations 53 each,
hatch now 12 each, hatch later 18/20/18, and future restows 38/18/8; current
restows are zero. Thus freeing pages without forcing a closure complements
the unused-cover search. Average wall is 79.93 seconds per searched passing
map (39.99 per selected map including the three immediate skips). This is
an extra warm search after the original rung's cold construction time.

`repeat.py` refreshes the fitted securing reference from the best newly checked
Cycle 3 plan, while importing the frozen `method_lp.py` implementation. The
separate `quick_medium.txt` copies the public lab set exactly, including the
D185 map; its run is labelled warm for the two D192 maps. This measures the
whole required medium quick set without claiming a cold-start improvement.

The analytic-only `bound_ladder.py` uses the production counting bound as its
base and applies the proven port-capacity/E6 certificate to all 24 fixed
TRAIN inputs. It does not run the physical-stack CP relaxation or use a
restricted-pool objective. The stronger physical-stack certificate remains
available for the four primary maps. `LADDER.md` and
`ladder_comparison.json` contain per-map best checked lifts, parts, analytic
bounds and gaps; original rung pass gates remain separate from follow-ups.

## Negative results and remaining gap

Forcing unused covers works on the low-mass maps but not on either primary
high-mass map within this search. Ordinary-heavy hold masks help E6 but may
make a restricted pure-page neighbourhood infeasible; no infeasibility claim
for the physical map follows. More aggressive low-map closure produces
55--59-lift candidates that fail trim, list, sight or second-check stability.
The low500 finisher repairs one 55-lift candidate; other failed candidates
remain excluded.
The large retained V6 f60 plan can exceed this model's pure-page/tier family,
so seeding fails and the known full-checked incumbent is kept.
Cold large f70 maps fail securing, trim/draft, OOG clearance or sight; these
are genuine construction failures under the allotted search, not proofs of
infeasibility. Capacity alone remains loose for low masses because global
physics, securing and OOG interactions limit practical deck usage. Their
necessary whole-map strengthening is the next bound target.


## Final gates, wall time and verdict

| Fixed rung | Maps | Complete replay-legal stows | Full checker passes | V6 full passes | Mean wall s/map |
|---|---:|---:|---:|---:|---:|
| f60 (`method.py`) | 12 | 12 | 8 | 5 | 159.59 |
| f70 (`method_lp.py`) | 12 | 12 | 3 | 0 | 168.60 |

The f60 five mutually passing V6 maps average **105.6 versus 164.4 lifts**
(original rung results, before the low500 59-to-55 repeat). All five V6 passes
are retained; three more maps pass. f70 has no mutually passing V6 maps,
so no paired V6 lift mean exists. Its three new cold passes subsequently
improve to 121/103/91 in the separate warm polish. The two rung algorithms
and warm/cold mix differ as documented above; these are descriptive rung
counts, not a controlled claim about fill alone. The aggregate rung wall
sums are 1915.10 and 2023.18 seconds respectively, each exceeding 20 minutes
for the stated full-cohort measurement reason. Actual process runtimes also
include interpreter/startup overhead outside score.py's per-map timers.

Cycle 3 `results/quick-medium-c3`: **3/3 complete replay-legal, 2/3 full passes**,
71 and 106 lifts, mean wall **87.12 seconds/map**; D185 does not pass.
It is a warm 120-second search on the two D192 incumbents and a cold D185
search, with finalize 45 seconds, tries 2, workers 1. The two original quick
medium D192 plans average **88.5**, versus Cycle 2 **93.5**. The public
leader 0.9.0 comparison remains a single full-pass pair, 71 versus 129,
not a two-map or three-map paired mean.

Fresh-process `audit.py` independently loaded every distinct passed plan from
all five final run directories: **16 distinct plans over 11 maps**, all
replay legal, all requested loads aboard, full checker pass, zero violations,
and exactly matching D11 lift counts. Exact duplicate plan JSONs were checked
once. Input SHA256s match score evidence and the approved catalogue gate.
`audit.json` records every independent check. The 24 analytic certificates
also complete; their per-map parts and gaps are in [LADDER.md](LADDER.md).
Failed maps have no upper bound or gap, and are never called infeasible.

Additional reproduction commands, with the same environment above:

```sh
.venv/bin/python experiments/mvp/frontier/lab/score.py --set "$D/polish-f70-500.txt" --method "$D/polish.py:propose" --finish "$D/polish.py:finish" --budget-s 90 --finalize-s 45 --tries 2 --workers 1 --out "$D/results/polish-f70-500"
.venv/bin/python experiments/mvp/frontier/lab/score.py --set "$D/quick_medium.txt" --method "$D/repeat.py:propose" --finish "$D/repeat.py:finish" --budget-s 120 --finalize-s 45 --tries 2 --workers 1 --out "$D/results/quick-medium-c3"
.venv/bin/python "$D/bound_ladder.py"
.venv/bin/python "$D/report.py" "$D/results/f60-1" "$D/results/f70-lp" --primary-runs "$D/results/train4-lp" "$D/results/quick-medium-c3"
.venv/bin/python "$D/report_ladder.py"
.venv/bin/python "$D/audit.py" --runs "$D/results/f60-1" "$D/results/f70-lp" "$D/results/train4-lp" "$D/results/polish-f70-500" "$D/results/quick-medium-c3"
```

All 24 Python files parse and have final SHA256s in `source.sha256.json`.
No experiment process remains running, no shared source was changed, and no
sealed data was used. The earlier possible pytest cache touch is disclosed
above. There was no commit or push.

**Keep:** explicit unused-cover designs, fixed-charge page LP/IP neighbourhoods,
exact E9 column nogoods, inherited checked ballast and the necessary cover/port
capacity certificates. They cut the primary mean by 6.5 lifts, add six full
ladder passes over V6 and prove a medium optimum at 70. **Open:**
primary gaps 6/16/0/4, failed larger f70 maps, and conservative pure-page and
securing restrictions. Next, derive necessary global physics/deck-use cuts for
the low-mass bounds, then price moment-aware columns for the large cold maps.
