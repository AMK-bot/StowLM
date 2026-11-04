# C2: fast ballast questions and certified page cuts

## Scope and assumptions

Only `lab/c2/c2-benders/` is written. The checker, rules, parameters and C1
experiments are imported unchanged. `c1/leader/e9fix.install()` runs before
constructing the search engine. No sealed data or raw evaluation suite is read.
All numerical jobs use at most two CPU-busy processes across this team, one
numerical thread each. Long launches attempt `nice -n 5`; this sandbox rejects
setpriority, so Python runs with ordinary priority. No other process is stopped.

The initial measurement set is the committed `lab/sets/medium12.txt`, seed 0.
The Benders experiment uses its first four maps, fixed before seeing outcomes.
The hydro LP uses the inherited three-state D57 voyage semantics, with one
shared vector of adjustable ballast masses. No activation/environment switch is
changed to simplify the checker. Every proposed pass must receive the unchanged
full checker. Complete replay-legal cargo stows are a separate gate.

Important archival limitation: `c1/fx-scout/results/medium12-1` describes **11
complete candidates on 8 maps**, but retains neither failed candidate plans nor
assignments. Its other four maps have no complete candidate. Each historical
candidate therefore receives an explicit `unknown` identity/verdict in
`results/audit/historical-census.json`. We reconstruct the plain source/seed and
the three successful later-rung settings, retain the new exact plan bytes, and
compare lift counts. Matching a lift count does not prove historical identity.
No certificate about a reconstructed plan is silently attributed to missing
historical bytes.

## Model in plain words

A stack is a page and an ordered set of boxes on it is a pattern. The master
chooses patterns to cover every box once and occupy each physical stack at most
once. Long boxes occupy paired stacks. Cargo and tank masses contribute to total
mass and longitudinal/transverse/vertical moments; hydrostatics couples pages
through a shared ballast problem. Yard relocations, hatch off/on operations and
future restows define lifts, as counted by the real move replay.

Concrete examples already bound in C1: `random.small.40100003.0.2` has 18 boxes,
20 stacks and two covers; D185 `random.medium.185100000.0.2` has 121 boxes, 64
stacks and six covers. A real small TRAIN operation path in fx-finish has 19
states and 18 tanks; its medium reference has 263 states and 18 tanks. Here the
legacy ballast problem uses three voyage conditions, not all those operation
states. No claim is made about activated per-lift pumping acceleration.

## Two different mathematical tools

**Small local ballast LP.** There are 18 shared tank fill fractions and six
draft variables (aft/forward for each of three voyage states). The checker's
`ExactContext` supplies fixed cargo/consumable mass and moments at each state.
The checker's polygon hull supplies buoyancy and longitudinal moment at a
reference draft, and 0.002 m perturbations supply their local derivatives.
Checker tank soundings at half fill supply arms and a free-surface reserve.
Linear GM/list, mass, trim and draft rows propose a recipe. Exact nonlinear
loading probes recheck every state, and draft/trim residuals correct a later LP.

This is **not an outward relaxation**: hull curvature, tilted liquid arms,
endpoint/slack-tank free surfaces and reference KM can all invalidate a tangent
as a necessary condition. Infeasibility, including a saved residual-checked
Farkas ray, is `unknown` physically. Only the separate exact mass/capacity
interval relaxation can establish a ballast contradiction. Scalar GM success
does not establish the containership second check, GZ/weather, sight, securing,
strength or static/move rules. Full checking is the acceptance gate.

**Logic-based Benders.** The securing oracle needs no tank-equilibrium solve.
It bounds acceleration below independently of tanks, then uses the checker's
grounded spring chain. A positive force cannot reduce rod pull. If the lower
rod ratio already exceeds one, preserving that exact whole page can never
pass under any tank setting. Forbid that pattern (or the complete set of
supporting patterns) in the integer page master, generate alternatives, and
solve again. The saved cut includes physical stacks, box/slot pairs and the
source-bound witness. Unknown local LP results never authorize physical cuts.

Restricted-pool exhaustion proves only exhaustion of that generated pool.
The inherited rounded knapsack and restricted ordering do not prove that all
pure-stack patterns, mixed stacks or the whole map are impossible.

## What was reused

- Imports `c1/fx-scout/pages.py` (patterns/pricing/diving), `method.one_stow`
  (reconstruction), and `lash_oracle.Fast` (historical-rung reconstruction only).
- Imports `c1/fx-finish/early_bound.py` unchanged, including its pinned checker
  hash, independent positive rod-compliance check and numerical guards.
- `benders.Model.solve_lp` copies C1 sparse master-row construction to specify
  one HiGHS thread and a time limit. The integer master and cut loop are new.
- Hydro providers and move replay are called through the existing source APIs;
  no alternate checker physics or parameter policy is installed.

## Papers

J. N. Hooker and G. Ottosson (2003), [Logic-based Benders decomposition](https://link.springer.com/article/10.1007/s10107-003-0375-9),
[author manuscript](https://johnhooker.tepper.cmu.edu/benders2.pdf): identify the
assumptions supporting a subproblem proof and forbid master choices preserving
them. G. Codato and M. Fischetti (2006), [Combinatorial Benders' Cuts for
Mixed-Integer Linear Programming](https://pubsonline.informs.org/doi/10.1287/opre.1060.0286):
discrete master with conflict cuts from a valid continuous infeasibility proof.
Primary sources and proof qualification are detailed in `REVIEW.md`; no
downloaded code is used.

## Initial evidence and pending measurements

First D185 plain reconstruction has 114 checked-replay lifts, matching the
archived summary. The ballast-independent securing screen returned in 0.287 s
(a repeated audit call took 0.562 s), with minimum rod ratio 1.1461768669.
The local ballast LP subsequently found a recipe passing all three nonlinear
loading probes in 6.192 s cold: 4.803 s setup, 1.000 s exact probes, and
0.00556 s aggregate LP time over two iterations. This is **loading rescue, not
a full-checker pass**: securing remains proved impossible for that stow.

Final measured tables and verification are appended after the runs complete.
Individual constructor budgets are 120 s and Benders maps are bounded. If the
11-candidate reconstruction or fixed quick gate exceeds 20 minutes, the reason
is preserving every predeclared candidate/map and its unchanged full check,
rather than dropping slow failures. There is no multi-hour search per stow.
Wall observations on a shared machine are not
controlled causal speed estimates or baseline-matched finisher speedups.

## Entry points

`audit.py` reconstructs and records all archived complete-candidate descriptions.
`ballast.py:solve_ballast` returns local LP guidance, exact loading-probe verdicts,
and only an explicitly outward mass proof. `benders.py:run` combines certified
securing cuts with the restricted integer page master. See retained commands
and results below when complete.

## Final D185 measurements

All 11 descriptions were reconstructed into complete replay-legal stows on
eight maps, and all 11 lift counts match their archived summaries. Every
reconstruction received a fresh unchanged full check; none passed. The four
428-box maps supplied no archived complete stow and were not relabelled as
ballast failures. Historical exact identity remains unknown for all 11.

| Reconstructed candidate | Lifts | Minimum rod ratio | Ballast verdict | Certificate s |
| --- | ---: | ---: | --- | ---: |
| random 185100000 plain | 114 | 1.14618 | proved cannot | .562 |
| random 185100000 rung 5 | 98 | .76916 | unknown | .046 |
| random 185100000 rung 7 | 114 | .76206 | unknown | .052 |
| random 185100000 rung 9 | 112 | .77564 | unknown | .070 |
| random 185100001 plain | 139 | 1.10999 | proved cannot | .055 |
| sim 185100010 plain | 129 | 1.17978 | proved cannot | .043 |
| sim 185100011 plain | 116 | 1.12876 | proved cannot | .051 |
| random 185100100 plain | 168 | 1.44699 | proved cannot | .440 |
| random 185100101 plain | 259 | 1.48528 | proved cannot | .095 |
| sim 185100110 plain | 238 | 1.60060 | proved cannot | .066 |
| sim 185100111 plain | 228 | 1.20910 | proved cannot | .076 |

Certificate time excludes construction and the deliberately separate full
checks. All 11 screens together cost 1.556 s. Reconstruction cost 801.87 s
plus 131.18 s evaluation, excluding the initial reused 10.8 s smoke
construction. Three constructor calls exceeded their nominal 120 s budget
because C1's balance/sequencing/replay follows its cooperatively bounded
LP/dive; measured times are retained (121.57, 122.24 and 148.90 s).

On the three bound-surviving near misses, local ballast LPs found recipes
passing all three exact nonlinear loading probes. The full checker then found
that securing, trim and draught were cured on all three. Weather and second
check failed on all three, and rung 9 also failed sight. These remain unknown,
not impossibility proofs or full passes. Aggregate LP times were .00553,
.00462 and .00876 s; complete subproblem plus full-check times were 33.28,
47.32 and 44.45 s. Full checks alone took 25.85–44.56 s.

The 98-lift rung 5 at an additional **search-only** GM floor .6 passes every
checker rule except three containership second-check area tests:

| Voyage state | Area measured | Area required, m·rad |
| --- | ---: | ---: |
| departure | .26673 | .28992 |
| mid-voyage | .27596 | .29639 |
| arrival | .24334 | .30779 |

This attempt took 18.22 s including full checking; LP time .00374 s. Raising
the search floor to .9 improves areas but causes 28 securing violations.
At 1.2 the local LP is infeasible in 1.37 s total and remains `unknown`, with
an approximate floating Farkas diagnostic marked `physical_proof=False`.
The GM search floor changes only local guidance, never the source rule.

## Four-map Benders loop

First four maps in the fixed medium12 list, seed 0, eight-iteration maximum,
150 s map budget, 15 s master iteration allowance, 40 s initial construction:

| Map | Distinct complete replay-legal stows | Certified page cuts | Full-checker passes | Wall s |
| --- | ---: | ---: | ---: | ---: |
| random 185100000 | 8 | 8 | 0 | 11.61 |
| random 185100001 | 7 | 6 | 0 | 131.78 |
| sim 185100010 | 8 | 8 | 0 | 106.69 |
| sim 185100011 | 8 | 8 | 0 | 2.41 |
| total | 31 | 30 | **0** | **252.49** |

Maps 1/4 reused retained reconstruction seeds; maps 2/3 constructed seeds and
had larger pools. These are not paired timing comparisons. All 31 securing
screens took .041–.302 s (mean .059 s). Every successor respects all prior
cuts, each cut excludes its generating selection, support box/slot pairs
match the complete witness, and selected stows are distinct per map; see
`results/benders-verification.json`. The seventh stow on map 2 survives the
securing bound and passes all three nonlinear loading probes, but fails the
full checker on securing/weather/second-check. Its status is unknown and no
false feasibility cut is added.

The proofs exclude the stated whole-page patterns at every ballast setting,
including any other disjoint pure-page stow preserving them. They do not
exclude all cargo assignments or establish whole-map infeasibility. D185
0.9.0 has no paired stored lift rows here; the documented cohort pass count
is 0/12. No failed stow is assigned a passing lift objective.

## Verification and reproduction

28 tests passed in 17.46 s: three new algebra/search-guidance tests plus all
25 shared lab tests, including a real full-checker rejection. The initial and
final manifests of the selected native checker/ballast and imported C1 files
match. The audit cache was subsequently tightened to bind settings/source
hashes; the initial measured audit was a fresh run and did not reuse audit
rows. No numerical algorithm changed in that cache fix.

```
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c2/c2-benders/audit.py --hydro-s 0 --full-check

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c2/c2-benders/benders.py --maps 4 \
  --iterations 8 --budget-s 150 --iteration-s 15 --initial-s 40 \
  --out experiments/mvp/frontier/lab/c2/c2-benders/results/benders
```

**Keep part:** the subsecond securing certificates and certified page cuts;
retain the small LP as seconds-scale loading guidance, with its limits
explicit. No D185 full pass and no unrestricted infeasibility proof were
found. Next: add the actual containership area criterion to a local ballast
search while explicitly controlling securing, then price alternative page
patterns within certified geometric families rather than extrapolating an
uncertified whole-map moment cut.

## Fixed quick regression gate

`score.py --set quick`, stored reference actions, this folder's
`finish.py:finish`, seed 0, construction budget 0, finisher budget 30 s,
one try/worker. Existing stored ballast is only an initial recipe; every
pass receives an unchanged full check here and another in the shared runner.
This is a finisher regression, not a cold-start constructor comparison.

| Size | Complete stows / maps | Full-checker passes | Stored 0.9.0 passes | Paired mean lifts, new / stored | Mean wall s |
| --- | ---: | ---: | ---: | ---: | ---: |
| tiny | 8/8 | 8/8 | 8/8 | 6.375 / 6.375 | 13.188 |
| small | 9/10 | 9/10 | 9/10 | 13.889 / 13.889 | 11.501 |
| medium | 0/3 | 0/3 | no measured rows | no pairs | .167 |

All 17 paired passes retain identical lift counts (zero change; zero-width
bootstrap intervals). The one small failure and three medium rows have no
stored candidate and do not test this ballast solver. No medium no-candidate
row is treated as a ballast impossibility proof. The D185 constructor and
Benders results above supply the actual medium evidence.

```
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py --set quick \
  --method experiments/mvp/frontier/lab/ref/stored.py:propose \
  --finish experiments/mvp/frontier/lab/c2/c2-benders/finish.py:finish \
  --budget-s 0 --finalize-s 30 --tries 1 --workers 1 --seed 0 \
  --out experiments/mvp/frontier/lab/c2/c2-benders/results/quick
```
