# Cycle 2: exact hatch and restow closure

Only this folder is writable. Inputs are the fixed catalog-approved `quick`
validation/TRAIN maps; sealed data remains untouched. No commits, source changes,
or process-pattern kills. Numerical jobs set OMP_NUM_THREADS=1,
OPENBLAS_NUM_THREADS=1 and VECLIB_MAXIMUM_THREADS=1 and use `nice -n 5`.
Two parallel workers are the entire team's CPU limit, including subagents.

## Model and proof gate

Boxes choose slots, slots occupy physical cells, and used (cover, discharge-port)
pairs pay a fixed cover cost. A directed dependency joins a hold target to every
deck box that must be cleared from its cover, and each removed box to boxes above
it. Closure at each port counts each surviving blocker once, so multiple edges
do not double-charge a restow. Packing, support and conservative physical limits
form a relaxation. Real replay and the full checker decide upper bounds.

The first quick_small map, random.small.40100011.0.8, has 18 loads, 14 forced
non-export yard relocations, and eight hatch lifts. The first quick_medium map,
D192.medium-feeder_500-f60-high-s192200000, has 88 loads; the same cover/port
incidence and blocker graph applies, but full physics remains coupled. These
model examples inherit the read-only Cycle 1 exact/FVS structure measurements.

The exact-bound worker copies and extends c1/fx-exact/bound.py; the repair worker
imports existing production sequencing, move replay and mask APIs unchanged.
`method.py` imports c1/leader/e9fix.py:install(), as required. It retains finished
Cycle 1 incumbents, including the 25-lift VLSN candidate, 8-lift FVS exchange,
and explicitly manual 14-lift special-box stow. All are rechecked via score.py.
These inherited gains will be separated from newly generated repairs.

## Papers and the implemented idea

- J. N. Hooker and G. Ottosson (2003), [Logic-based Benders decomposition](https://doi.org/10.1007/s10107-003-0375-9),
  [author publication list](https://johnhooker.tepper.cmu.edu/papers.html):
  explains splitting assignment from logical feasibility inference. We use
  replay as a separate gate; a replay rejection alone is not a globally valid
  assignment cut because a different order can succeed. No Benders convergence
  or complete operational exactness is claimed.
- Roberto Roberti, Enrico Bartolini and Aristide Mingozzi (2015; online 2014),
  [The Fixed Charge Transportation Problem: An Exact Algorithm Based on a New Integer Programming Formulation](https://doi.org/10.1287/mnsc.2014.1947):
  fixed link activation motivates charging each used cover/port pair once.
  Our CP-SAT incidence model does not reproduce their branch-and-price method.
- Gianni Codato and Matteo Fischetti (2006), [Combinatorial Benders' Cuts for Mixed-Integer Linear Programming](https://doi.org/10.1287/opre.1060.0286):
  considered as a possible refinement; no cuts from this paper are claimed.

## Assumptions and verification plan

Exact projected costs refer to the engine's D15 fixed-coordinate, no-future-load
projection. Equality between a certified relaxation bound and a fresh full
checker upper bound proves that map's D11 optimum in this model. Timeout alone,
a relaxation witness, replay acceptance, or a restricted neighborhood optimum
does not do so. Mean gaps will state their denominator explicitly.

Offline search and warm ballast reuse are reported separately from fresh full
checker time; no cold-construction speedup is claimed. The required final quick
run may exceed twenty minutes because full checker physics dominates even when
finished ballast is inherited; its scope is all 21 fixed approved maps and it
uses at most two workers. This is the reason to let that one run finish.

Measured results, exact commands, failures and per-map proof table follow after
the frozen-source final quick sweep.

## Construction result and negative local search

The initial freeing-slot/swap/whole-column descent evaluated 3,758 geometric
proposals, of which 683 replayed and 17 promising candidates failed the mask.
Three neutral mask completions produced no improvement over the inherited
25-lift VLSN plan. This is bounded heuristic failure, not a local optimum proof.

The successful construction imports `c1/fx-chains/chain_model.py:ChainPacking`
unchanged, using singleton groups, port-sorted columns, homogeneous hold
columns, paired support heights and sufficient E6 opportunity blocking.
`repair.py:chain_candidates` adds a fixed charge for each used cover and
cover/port pair, and charges late deck cargo over an early hold. This objective
is a ranking surrogate for the measured two-port, unit-weight maps. Its
restrictions mean it provides **no global lower bound**. Complete assignments
are sequenced by native `order_legal`, then mask, carrier and real replay gated.
Failed assignments receive no-goods only in this candidate generator, never
in the certified relaxation.

On `sim.small.40100025`, the first 15 optima of the restricted candidate model
all complete replay, mask and carrier gates. The best has two current yard
relocations, four current and four future hatch lifts, and zero current/future
restows: **10 lifts**. `results/pilot-checked/` freshly passes it through
`lab/score.py` and the full checker in 12.22 seconds, versus the stored 26.
The old sixteen future restow lifts are therefore a Frontier search miss, not
forced. A wholesale port/hold/deck reassignment crosses a barrier the tested
single-box/swap descent could not cross. An imported fixed-stow FVS order model
proves two relocations for this specific new assignment; it does not prove a
global ten-lift optimum. Bound remains eight pending a stronger certificate.

Reproduction (prefix numerical commands with the environment below):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c2/c2-hatch \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c2/c2-hatch/repair.py \
--set quick_small --id sim.small.40100025 --chain --budget-s 30 --limit 15 \
--out experiments/mvp/frontier/lab/c2/c2-hatch/results/repair-chain-rerun.json

OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
--set experiments/mvp/frontier/lab/c2/c2-hatch/one40100025.txt \
--method experiments/mvp/frontier/lab/c2/c2-hatch/method.py:propose \
--finish experiments/mvp/frontier/lab/c2/c2-hatch/method.py:finish \
--budget-s 0 --finalize-s 120 --tries 20 --workers 1 \
--out experiments/mvp/frontier/lab/c2/c2-hatch/results/pilot-checked
```

Root verification so far: **58 tests passed** (30 bound, three repair, 25
shared scorecard tests), followed by **32 bound tests passed** after adding two
independent actual-checker hold-geometry cases. The added cases check both
rule-freeze settings. Final sources and full quick run are recorded below.

## Final-source verification and remaining negative results

The final test invocation passed **64 tests**: 32 packing/restow bound tests,
four joint-order bound tests, three real repair tests, and 25 shared scorer
tests. The shared suite includes an actual full-checker rejection. Reproduction:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTEST_ADDOPTS='-o cache_dir=experiments/mvp/frontier/lab/c2/c2-hatch/.pytest_cache' \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/frontier/lab/c2/c2-hatch \
nice -n 5 .venv/bin/python -m pytest \
experiments/mvp/frontier/lab/c2/c2-hatch/bound_tests.py \
experiments/mvp/frontier/lab/c2/c2-hatch/bound_joint_tests.py \
experiments/mvp/frontier/lab/c2/c2-hatch/repair_tests.py \
experiments/mvp/frontier/lab/tests -q
```

The further candidate sweep used 74.51 seconds across four maps: 6.86 s for
random.small.40100036.0.2, 4.19 s for sim.small.40100046, 3.51 s for
sim.small.40100034, and 59.94 s for 80 candidates on sim.small.40100025. This is
offline construction time, excluded from score.py's warm artifact admission.
The first map has 15 replay/mask/carrier completions at nine lifts. The second
has 15 at ten or more and retains its cheaper nine-lift incumbent. All 15 on the
third map fail native ordering, mostly at the heat-booked box QZXU0000077;
the method retains the inherited explicitly manual 14-lift full plan.
The expanded 80-candidate search does not improve the ten-lift result.

The separate `bound_joint.py` adds first-aboard ranks and lower-priced flags
for a yard export relocation or current ship restow that can break a required
order. See `bound_joint_NOTES.md` for the proof allowing arbitrary current
restows. It leaves the three remaining gap-map lower bounds at eight, eight
and six. No speculative E6 cuts were added. The final certificate table uses
the simpler, uniformly measured `bound.py` results.

The full checker matters beyond the mask: in the final sweep it rejects five
ten-lift candidates on sim.small.40100025 before accepting the sixth, and one
nine-lift candidate on random.small.40100036.0.2 before accepting the second.
Failures include securing, visibility and list at the inherited ballast. These
rejections are not relaxed or relabelled as passes. The successful ten-lift
plan uses two relocations plus eight hatch lifts and zero restows.

Final required scorecard command, with one worker while the independent bound
worker completed (at most two busy processes throughout):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py \
--set quick --method experiments/mvp/frontier/lab/c2/c2-hatch/method.py:propose \
--finish experiments/mvp/frontier/lab/c2/c2-hatch/method.py:finish \
--budget-s 0 --finalize-s 120 --tries 100 --workers 1 \
--out experiments/mvp/frontier/lab/c2/c2-hatch/results/quick
```

## Final measured outcome

The final `quick` sweep through the unchanged `lab/score.py` finished in
**283.91 seconds summed map wall time**, with one scoring worker. It measures
warm admission of frozen cargo proposals and inherited ballast, including all
rejected full-check attempts; it excludes offline construction and bound solves.
No cold-planning speed comparison is claimed.

| Fixed subset | Complete replay-legal maps | Full checker passes | Stored 0.9.0 passes | Paired mean lifts, ours / stored | Mean wall s/map |
| --- | ---: | ---: | ---: | --- | ---: |
| quick_tiny | 8/8 | 8/8 | 8/8 | 6.375 / 6.375 | 12.578 |
| quick_small | 10/10 | 10/10 | 9/10 | 11.778 / 13.889 (9 pairs) | 18.268 |
| quick_medium | 0/3 | 0/3 | unavailable | no pairs | 0.203 |

All 18 admitted plans pass the full checker freshly. The three medium rows
return no candidates because this warm repair portfolio requires incumbents;
this is neither attempted cold construction nor a medium infeasibility claim.
Mean lifts over all ten small passes are **12.0**. Paired improvement is
**2.111 lifts/map (15.2%)**, with three wins, six ties and no losses; the map
bootstrap 95% interval for candidate minus baseline is **[-5.667, -0.111]**.

Attribution: the new CP construction saves **18 lifts** over the nine baseline
pairs (16 on sim.small.40100025 and two on random.small.40100036.0.2).
The inherited Cycle 1 FVS exchange saves the remaining one on
sim.small.41100007. The tenth pass, sim.small.40100034 at 14, reuses the explicitly
manual Cycle 1 diagnostic; the new constructor did not solve that map.

| quick_small map | Fresh fully checked lifts | Tightened lower bound | Gap | Proved optimal |
| --- | ---: | ---: | ---: | :---: |
| random.small.40100011.0.8 | 22 | 22 | 0 | yes |
| random.small.40100023.0.8 | 22 | 22 | 0 | yes |
| random.small.40100036.0.2 | 9 | 9 | 0 | yes |
| random.small.41100002.0.2 | 12 | 12 | 0 | yes |
| random.small.41100018.0.2 | 10 | 10 | 0 | yes |
| sim.small.40100010 | 4 | 4 | 0 | yes |
| sim.small.40100025 | 10 | 8 | 2 | no |
| sim.small.40100034 | 14 | 6 | 8 | no |
| sim.small.40100046 | 9 | 8 | 1 | no |
| sim.small.41100007 | 8 | 8 | 0 | yes |

**Seven of ten maps are now proved globally D11 optimal** in the checker's
specified D15 model, up from three Cycle 1 proofs. Mean upper-minus-lower gap
is **1.1 lifts across all ten maps**; it is 1/3 lift over the nine baseline
paired maps. Eight of the remaining eleven gap lifts belong to the inherited
manual 40100034 stow. The other gaps are two on 40100025 and one on 40100046.
`results/CERTIFICATES.json` records per-map input digests, bound evidence,
checked plan evidence, proof flags and source digests; `results/TABLE.md`
is the concise table. `report.py` checks matching input hashes and the bound
source hash, rejects any lower bound above its checked upper, and regenerates
both artifacts.

**Verdict: keep the exact hatch/restow bound and conservative CP proposal
generator.** They produce four additional optimality proofs and a substantial
paired lift reduction. Keep the first-aboard joint bound as a documented
negative experiment, not an additional improvement. Next model final heat/OOG
geometry and ordinary E6 fallback more completely on 40100034, which dominates
the remaining mean gap; tighten relocation/assignment coupling on 40100025 and
40100046. Preserve the full checker gate: replay and masks admitted several
securing/visibility/list failures during this sweep.

All source writes, result artifacts and pytest cache remain in `c2/c2-hatch/`.
`git diff --check` passed for this folder. No sealed inputs were read and no
processes were killed. No commit or push was made. All numerical runs had one thread,
and the team never exceeded two busy processes.
