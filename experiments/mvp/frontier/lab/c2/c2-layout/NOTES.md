# Cycle 2: port-to-hatch layouts and medium lift bounds

Scope: every new file and result is under this directory. Read the full lab
contract, its Cycle 2 update, lab README, scout NOTES/GRAPH-MODEL/pages/method,
and fx-exact bound before implementation. No sealed data was opened. No shared
source, checker, rule, parameter or existing result was edited.

## Model and sources

The ship is a forest of stack pages joined through a bipartite graph whose
vertices are discharge ports and hatch covers. A used hold cover costs two
lifts now, each used port-cover edge costs two later lifts, and later deck
boxes above an earlier hold discharge incur projected restow lifts. The yard
forest contributes unavoidable stranger relocations independently of layout.

Concrete small example from the required scout graph census:
`random.small.40100003.0.2` has 18 load-box vertices (13/5 by port), 20 physical
stack vertices and two covers. Four strangers above load boxes must relocate.
The first medium input here has 88 load-box vertices, two ports and six hold
covers; its known page incumbent uses six covers and six port-cover edges,
giving 24 hatch lifts plus eight restow lifts and 47 relocations, total 79.
Choosing edges in this small port-cover graph is the remaining design freedom.

Primary sources searched and publisher abstracts read on 1 November 2026:

- P. C. Gilmore and R. E. Gomory (1961),
  [A Linear Programming Approach to the Cutting-Stock Problem](https://doi.org/10.1287/opre.9.6.849).
  The inherited stack-page master and pricing use their column-generation idea.
- R. Roberti, E. Bartolini and A. Mingozzi (2015),
  [The Fixed Charge Transportation Problem: An Exact Algorithm Based on a New Integer Programming Formulation](https://doi.org/10.1287/mnsc.2014.1947).
  The fixed-charge interpretation motivates searching the port-cover design.
  We did not reproduce that paper's exact algorithm or read its paywalled body.

## Implementation and assumptions

`method.py:propose` is the original cold constructor; `method.py:finish` retains the
normal full-checker/ballast gate. Its one-stow, verdict, finishing and greedy
layout routines adapt scout's implementation; the layout queue replaces its
ladder. `pages.py` was copied from scout, with the
asymmetric deck-port permission described in [search_notes.md](search_notes.md).
The securing oracle is imported unchanged. The authorized
`c1/leader/e9fix.py:install` wrapper runs in process. `repair.py` broadens OOG
swap partners, checking both columns and both boxes' placement rules before
accepting a repair. The final score runner still decides every pass.

The method enumerates all two-port binary cover assignments (up to 16 covers),
ranks their resource slack, then packs a bounded shortlist using the page LP
and integer dives. Its LP is a construction screen, **not a certified bound**:
pure-stack patterns and fitted securing weights can exclude valid plans.
Strict hold/deck layouts are tried first; the asymmetric mode permits early
cargo on deck above late hold cargo; softer/free fallbacks preserve a chance
to pack when capacity prevents strict partitioning. The four-lifts-per-cover
stopping target is only a useful pure-layout target, not a whole-map bound.

`warm.py` is a separate diagnostic portfolio: it uses Cycle 1's checked plan
as an incumbent and its final loading condition as the securing reference,
then tries the old greedy layout. Its results must be labelled warm; they
must not be attributed to the cold constructor. The selected reference
settings in that diagnostic are documented literally in its source.

`train4.txt` was fixed before testing outcomes: the two D192 quick maps plus
the next two lexicographic V6-certified TRAIN summaries, high/192200002 and
low/192200000 on feeder_500. `extras2.txt` is its final two lines.
`quick_medium` also includes the difficult D185 input; its replay and checker
gates are reported separately. V6 is a separate comparator; there are no
stored 0.9.0 passes for these medium maps, so no paired 0.9.0 lift claim is made.

## Certified lower bound and validity

See [bound_notes.md](bound_notes.md) for the full proof and tests.
`bound.py` aggregates real slots into cover/hold-or-deck regions and retains
integer box-group allocation, necessary unary eligibility, summed physical
cell/mass/height limits, used-cover and port-cover costs, and cover-mediated
deck restows. A legal actual assignment maps to those signatures. Mass and
height are rounded down and capacities up. Stack support, individual stack
limits, securing, E6, interactions, whole-ship physics and in-column restows
are relaxed. Therefore the feasible set includes all legal stows and its
minimum cannot exceed the true hatch-plus-restow minimum.

Only CP-SAT's `BestObjectiveBound` is returned; an incumbent is never a bound.
Add the distinct non-load yard boxes above load boxes, the same forced-set
argument as `c1/fx-fvs/certificates.py`, checked against `stowage.bounds`.
These cost components are disjoint, so addition is valid. Bounds are
51=47+4, 90=84+6, 52=46+6, and 51=47+4 in `train4.txt` order. All four aggregate
relaxations solved optimally in under 0.3 seconds, with zero relaxed restows.
This proves only relaxation optima. A separate 20-second/map fx-exact run
did not strengthen them. The bounds remain loose because relaxing securing
and E6 permits much more deck cargo than the constructive method.

## Eligibility setup budget correction

The original cold quick run passed the 88-box map at 71 but returned no
candidate on the 162-box map: constructing its heat-filtered unary eligibility
table took 76 seconds, longer than the 54-second reference-stage allowance.
The reference LP therefore returned 162 uncovered before pricing began. This
was a scheduling failure, not packing infeasibility. The warm diagnostic
independently reached and fully checked 116 lifts on that map.

`cached_method.py` preserves the original method but starts the reference
stage's allowance after model setup (still capped by the total method
deadline). `pages_cached.py` reuses the inherited, exact state-free unary
filter on the same problem between layout trials. Its inputs are the fixed
instance, arrival state and feasibility tables; none depends on layout or
securing settings. Every model receives a copy to prevent aliasing. This
changes neither eligibility nor any checker rule. The cache test checks
per-problem ownership and copy isolation. The original run and source remain
available rather than replacing its failure with a claim of success.

## Reproduction and compute

Every numerical run sets `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1`; CP-SAT uses one worker.
At most two CPU-busy processes ran across this executive and both agents.
Long jobs were invoked with `nice -n 5`; the sandbox refused `setpriority`,
so they ran at inherited priority. No other session's process was stopped.

The score commands use `lab/score.py`, method and finish from this directory,
budget 300 seconds, finalize 60 seconds, two candidate tries and one worker.
Results are under `results/quick-medium-1/` and `results/extras-1/`.
The warm diagnostic uses `train4.txt`, budget 180, finalize 30, one worker,
and `results/warm-1/`. `report.py` joins score rows to certified V6 and bounds.

Final cold score command (repeat with `--set .../extras2.txt` and
`--out .../results/extras-cached` for the two additional TRAIN inputs):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
  --set quick_medium \
  --method experiments/mvp/frontier/lab/c2/c2-layout/cached_method.py:propose \
  --finish experiments/mvp/frontier/lab/c2/c2-layout/cached_method.py:finish \
  --budget-s 300 --finalize-s 60 --tries 2 --workers 1 \
  --out experiments/mvp/frontier/lab/c2/c2-layout/results/quick-medium-cached
```

The standalone bound uses `bound.py --list train4.txt --out bound_results.json`
with repository-relative list/output paths under this directory and the same
environment. The final comparison joins the two cached run directories, and
`audit.py --out audit_final.json` checks those selected finished plans afresh.

## Measured results

The best checked portfolio currently has the following four D192 plans. Parts
are raw relocations / hatch now / hatch later / restow now / restow later;
all objective weights on these inputs are one. Relative gap is `(UB-LB)/UB`.

| D192 suffix | Best lifts | Parts | Bound | Absolute gap | Relative gap | Scout quick-3 | V6 |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| feeder_500-f60-high-s192200000 | 71 | 47 / 12 / 12 / 0 / 0 | 51 | 20 | 28.17% | 79 | 131 |
| feeder_800-f60-low-s192200101 | 116 | 84 / 16 / 16 / 0 / 0 | 90 | 26 | 22.41% | 184 | 214 |
| feeder_500-f60-high-s192200002 | 70 | 46 / 12 / 12 / 0 / 0 | 52 | 18 | 25.71% | unavailable | 128 |
| feeder_500-f60-low-s192200000 | 71 | 47 / 12 / 12 / 0 / 0 | 51 | 20 | 28.17% | unavailable | 127 |

Four target maps: 4/4 complete replay-legal stows and 4/4 full-checker passes.
Their paired mean is 82 against V6's 150, a 68-lift (45.33%) reduction. On the
two quick D192 maps, mean 93.5 against scout's 131.5 and V6's 172.5. Relative
to scout this removes 64 projected restow lifts and 12 hatch lifts in total;
relocations remain at their forced minima. No paired comparison with 0.9.0
is available because its medium stored rows are unmeasured.

Attribution matters: the 88-box gain comes from permitting early deck cargo
over late hold cargo. The warm 162-box run makes scout's strict greedy
partition pass E9 at 116. The final **cold** 162-box run reaches 120 on its
greedy partition, then 116 on a different enumerated hold partition with
asymmetric deck permissions. It enumerates all 256 binary assignments,
retains a ranked shortlist of 64, and finds the winner after screening seven
other design/mode trials. Thus the cold run measures a four-lift benefit
from moving beyond its first partition. Oracle/reference conditions and
packing randomness also matter: the warm result shows 116 can be attained
on the original partition. We have not exhaustively optimised complete
stows for every enumerated partition.

The original cold `quick-medium-1` run reached complete replay-legal stows on
2/3 maps and full passes on 1/3. It failed to start pricing on the 162-box
map because setup exceeded the reference-stage time allowance. The D185
map reached a 114-lift legal replay but failed securing, GM second check,
draught, trim and visibility; that is not a pass. Its later restricted-page
LP screens left over 12 boxes uncovered, which does not prove infeasibility.
Original additional TRAIN run: 2/2 replay legal, 2/2 fully checked, mean wall
166.40 seconds/map. Warm diagnostic: 2/2 quick D192 passes (the two extras
have no scout warm reference), retaining 79 on the first and improving the
second to 116. Its checked 116 is a real improvement, explicitly warm.
The 162-box warm diagnostic took 262.35 seconds in `propose` despite its
180-second request: constructing its second model was not budgeted before
entry. This is a budget-management failure, so that diagnostic is not used
for the final cold timing claim or final selected plans. The cached cold
replacement completes that map's `propose` in 272.06 of its 300 seconds.

`audit.json` independently reloaded all four initial selected finished plans in a
fresh Python process, ran `apply_plan` and the unchanged full checker, and
verified exact counts, all load-list boxes loaded, and zero violations.
`audit_final.json` repeats this on all four final cold selected plans, again
with exact counts, all boxes loaded, and zero violations.
Six tests passed: three layout/port-mode tests, two bound/certificate tests,
and the unary cache ownership/isolation test.

For completeness, `bound_quick_results.json` also applies the same aggregate
relaxation to the D185 quick input: 30 = 24 forced relocations + 6 hatch/restow
lifts, optimal for the relaxation in 0.41 seconds. There is no checked upper
bound here, so its operational gap is unavailable.

The final cold entry point is `cached_method.py:propose` with
`cached_method.py:finish`. Its two D192 quick rows are 71 in 91.43 seconds and
116 in 275.52 seconds; its two extra TRAIN rows are 70 in 141.17 seconds and
71 in 67.70 seconds. Mean over the four fully checked maps is **143.95 s**.
The extra TRAIN run's mean is 104.43 s, versus 166.40 before caching. The
four-map means use the same cold implementation and seed 0 throughout.

Final `quick_medium` (`results/quick-medium-cached`): **3/3 complete
replay-legal stows, 2/3 full-checker passes**, mean wall **221.80 s/map**
including the failure. Its two passing D192 maps average 93.5 lifts. The
D185 map again reaches a 114-lift complete legal replay, but fails securing,
GM second check, draught, trim and visibility; it returns no finishable
candidate. Its 298.46-second run is not a pass or an infeasibility proof.
Final extra TRAIN run (`results/extras-cached`): **2/2 complete replay legal,
2/2 full passes**, mean wall **104.43 s/map**. `gates.json` records these
separate gates. `comparison.json` contains every target map's final evidence,
parts, bound and absolute/relative gap. All final cold `propose` times are
below their 300-second budget. No individual score run exceeded 20 minutes.

The twelve Python source hashes in `source.sha256.json` were checked after
the final run and all matched. No commit or push was made.

## Verdict and next experiment

**Keep** the asymmetric deck permission, layout search, actual OOG repair and
unary cache. They give four checked medium TRAIN plans with zero restows and
substantial lift savings against V6; the two quick results also improve
scout. Keep the aggregate bound as the first valid medium lift certificate,
with its looseness visible. Do not treat a page-LP rejection as infeasibility
or its objective as a whole-map bound.

Next, allow unused covers in the layout design (the present binary pure-port
family can still leave them unused through packing, but does not explicitly
search their fixed charge). Strengthen the aggregate bound with rigorously
necessary securing/physics inequalities before claiming a small optimality
gap. Improving D185 needs physical capacity and securing work as well as
port blocking; reshuffling covers alone has not supplied a pass here.
