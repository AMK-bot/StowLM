# Frozen test references

Measured rungs: tiny; 15 frozen inputs. Generated 2026-10-29T16:52:43.147623+00:00.

Rule freeze OFF; C2 OFF (`FeasibilityConfig(extra={"global_screen": 0})`). The full checker gates plans. These are classical references, not StowLM results.

Own-plan checker pass rates include fully checked timeout incumbents. Objective means and pairs require passing, unrepaired, explicitly shippable plans; checked timeout incumbents remain raw best-found counts below. Missing reports and failed runs stay in the attempt denominators. Move means from different passing populations must not be ranked; use the paired table.

A checker pass alone does not establish pipeline readiness. A finisher failure can leave a checked cargo plan without ballast; it remains nonshippable and its counts are diagnostics only. Finisher outcomes and pipeline messages are reported separately from hard-rule failures below.

| Method | Attempts/frozen | Missing | Own checker passes/attempts | Shippable/verdicts | Objective n | Mean weighted lifts | Plan s total | Pipeline s total | Checker outcomes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| twostep | 15/15 | 0 | 15/15 | 15/15 | 15 | 6.467 | 10.95 | 442.35 | {"passed": 15} |
| solver | 15/15 | 0 | 15/15 | 15/15 | 15 | 6.267 | 578.166 | 738.528 | {"passed": 15} |
| exact | 15/15 | 0 | 15/15 | 13/15 | 13 | 5.692 | 465.229 | 615.289 | {"passed": 15} |

## Paired weighted nonproductive lifts

Differences are first minus second; negative favours the first. The bootstrap resamples matching instance pairs to quantify uncertainty within this test set (harness default: 2,000 samples, seed 0).

| Comparison | Eligible pairs | Excluded | Mean difference | 95% interval | Unavailable reason |
| --- | --- | --- | --- | --- | --- |
| twostep_minus_solver | 15 | 0 | 0.2 | [-0.6, 0.8666666666666667] | — |
| twostep_minus_exact | 13 | 2 | 0.615 | [0.15384615384615385, 1.0769230769230769] | — |
| solver_minus_exact | 13 | 2 | 0 | [0.0, 0.0] | — |

## Every plan attempt

R = yard rehandles; S = restow lifts; H = hatch-cover lifts. Now/later values come from the checker, including its voyage projection. All counts here are raw diagnostics; only rows with Objective=yes enter objective aggregates. A dash is missing, never an assumed zero.

| Instance | Method | Result/proof | Checker | First hard rule | Shippable | Objective | Ballast status | Ballast present | R now | S now | H now | S later | H later | Raw weighted | Plan s | Pipeline s | Source drift |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| random.tiny.8000000.0.2 | twostep | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 4 | 7 | 0.671 | 37.978 | no |
| random.tiny.8000002.0.2 | twostep | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 2 | 5 | 1.895 | 46.785 | no |
| random.tiny.8000004.0.2 | twostep | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 4 | 7 | 0.527 | 30.181 | no |
| random.tiny.8000006.0.5 | twostep | ok | passed | — | yes | yes | ok | yes | 2 | 0 | 2 | 0 | 4 | 8 | 0.632 | 21.612 | no |
| random.tiny.8000008.0.5 | twostep | ok | passed | — | yes | yes | ok | yes | 2 | 0 | 2 | 0 | 4 | 8 | 0.559 | 37.164 | no |
| random.tiny.8000010.0.5 | twostep | ok | passed | — | yes | yes | ok | yes | 2 | 0 | 2 | 0 | 2 | 6 | 0.972 | 22.182 | no |
| random.tiny.8000012.0.8 | twostep | ok | passed | — | yes | yes | ok | yes | 3 | 0 | 2 | 0 | 4 | 9 | 0.609 | 20.774 | no |
| random.tiny.8000014.0.8 | twostep | ok | passed | — | yes | yes | ok | yes | 2 | 0 | 2 | 0 | 4 | 8 | 0.675 | 35.27 | no |
| sim.tiny.8000001 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 4 | 6 | 0.555 | 25.361 | no |
| sim.tiny.8000003 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 4 | 6 | 0.384 | 22.21 | no |
| sim.tiny.8000005 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 2 | 4 | 0.678 | 31.843 | no |
| sim.tiny.8000007 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 2 | 4 | 0.937 | 27.282 | no |
| sim.tiny.8000009 | twostep | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 4 | 7 | 0.539 | 22.413 | no |
| sim.tiny.8000011 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 4 | 6 | 0.64 | 32.875 | no |
| sim.tiny.8000013 | twostep | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 4 | 6 | 0.677 | 28.423 | no |
| random.tiny.8000000.0.2 | solver | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 4 | 7 | 18.395 | 48.672 | no |
| random.tiny.8000002.0.2 | solver | ok | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 41.22 | 45.465 | no |
| random.tiny.8000004.0.2 | solver | ok | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 42.266 | 47.216 | no |
| random.tiny.8000006.0.5 | solver | ok | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 33.085 | 35.677 | no |
| random.tiny.8000008.0.5 | solver | ok | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 64.461 | 70.377 | no |
| random.tiny.8000010.0.5 | solver | ok | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 41.022 | 45.5 | no |
| random.tiny.8000012.0.8 | solver | ok | passed | — | yes | yes | ok | yes | 3 | 4 | 2 | 0 | 4 | 13 | 60.022 | 82.642 | no |
| random.tiny.8000014.0.8 | solver | ok | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 2 | 2 | 8 | 30.258 | 34.878 | no |
| sim.tiny.8000001 | solver | ok | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 49.868 | 53.049 | no |
| sim.tiny.8000003 | solver | ok | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 36.124 | 38.337 | no |
| sim.tiny.8000005 | solver | ok | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 0 | 2 | 4 | 34.49 | 39.248 | no |
| sim.tiny.8000007 | solver | ok | passed | — | yes | yes | ok | yes | 0 | 0 | 2 | 0 | 2 | 4 | 0.277 | 37.124 | no |
| sim.tiny.8000009 | solver | ok | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 28.618 | 30.96 | no |
| sim.tiny.8000011 | solver | ok | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 38.035 | 43.029 | no |
| sim.tiny.8000013 | solver | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 0 | 4 | 7 | 60.023 | 86.355 | no |
| random.tiny.8000000.0.2 | exact | optimal | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 2 | 2 | 7 | 28.234 | 41.4 | no |
| random.tiny.8000002.0.2 | exact | optimal | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 29.532 | 44.333 | no |
| random.tiny.8000004.0.2 | exact | optimal | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 23.965 | 33.657 | no |
| random.tiny.8000006.0.5 | exact | optimal | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 28.245 | 37.229 | no |
| random.tiny.8000008.0.5 | exact | optimal | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 58.864 | 69.982 | no |
| random.tiny.8000010.0.5 | exact | optimal | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 0 | 2 | 6 | 18.672 | 26.318 | no |
| random.tiny.8000012.0.8 | exact | best-found (timeout) | passed | — | no | no | — | yes | 3 | 0 | 2 | 2 | 2 | 9 | 60.014 | 67.413 | no |
| random.tiny.8000014.0.8 | exact | optimal | passed | — | yes | yes | — | yes | 2 | 0 | 2 | 2 | 2 | 8 | 22.17 | 34.133 | no |
| sim.tiny.8000001 | exact | optimal | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 29.373 | 36.041 | no |
| sim.tiny.8000003 | exact | optimal | passed | — | yes | yes | — | yes | 1 | 0 | 2 | 0 | 2 | 5 | 22.449 | 29.423 | no |
| sim.tiny.8000005 | exact | optimal | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 0 | 2 | 4 | 24.125 | 35.848 | no |
| sim.tiny.8000007 | exact | optimal | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 0 | 2 | 4 | 16.243 | 24.979 | no |
| sim.tiny.8000009 | exact | optimal | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 14.983 | 21.344 | no |
| sim.tiny.8000011 | exact | optimal | passed | — | yes | yes | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 28.351 | 41.196 | no |
| sim.tiny.8000013 | exact | best-found (timeout) | passed | — | no | no | — | yes | 0 | 0 | 2 | 2 | 2 | 6 | 60.011 | 71.993 | no |

## Pipeline and reference exclusions

The first hard-rule column above can be empty when finalization or source eligibility fails. The messages below explain every recorded nonshippable attempt, including ballast search failures that do not prove infeasibility.

| Instance | Method | Status | Ballast outcome | Ballast present | Not-found cause | Pipeline message | Reference exclusion | Relevant source changes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| random.tiny.8000012.0.8 | exact | timeout | — | yes | — | optimality unproved for the move model; checker-passed: yes. Tertiary margin optimum is not proved. | — |  |
| sim.tiny.8000013 | exact | timeout | — | yes | — | optimality unproved for the move model; checker-passed: yes. Tertiary margin optimum is not proved. | — |  |

Exact `optimal` means weighted-lift optimality in the public engine model only (`engine_model_weighted_lifts`); physical-domain optimality and continuous-margin optimality are not certified. `best-found (timeout)` is a validated incumbent without an optimality proof, and is excluded from shippable objective comparisons. Exact solve budget is at most 60 s; outer ballast/checker work can add pipeline time.

## Lower bounds (no plans)

A lower bound counts work every possible solution must pay. These rows have no ballast finisher or checker verdict because they contain no plan. Zero gives no informative relative-gap certificate. Disjoint lift types add; overlapping bounds use their maximum.

| Instance | R now LB | S now LB | H now LB | S later LB | H later LB | Weighted LB | Informative | Time s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| random.tiny.8000000.0.2 | 1 | 0 | 2 | 0 | 2 | 5 | yes | 0.000065 |
| random.tiny.8000002.0.2 | 1 | 0 | 2 | 0 | 2 | 5 | yes | 0.000087 |
| random.tiny.8000004.0.2 | 1 | 0 | 2 | 0 | 2 | 5 | yes | 0.000068 |
| random.tiny.8000006.0.5 | 2 | 0 | 2 | 0 | 2 | 6 | yes | 0.000062 |
| random.tiny.8000008.0.5 | 2 | 0 | 2 | 0 | 2 | 6 | yes | 0.000062 |
| random.tiny.8000010.0.5 | 2 | 0 | 2 | 0 | 2 | 6 | yes | 0.000064 |
| random.tiny.8000012.0.8 | 3 | 0 | 2 | 0 | 2 | 7 | yes | 0.000062 |
| random.tiny.8000014.0.8 | 2 | 0 | 2 | 0 | 2 | 6 | yes | 0.000063 |
| sim.tiny.8000001 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000058 |
| sim.tiny.8000003 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000067 |
| sim.tiny.8000005 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000062 |
| sim.tiny.8000007 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000068 |
| sim.tiny.8000009 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000058 |
| sim.tiny.8000011 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000063 |
| sim.tiny.8000013 | 0 | 0 | 2 | 0 | 2 | 4 | yes | 0.000074 |

Bounds recorded: 15/15; these are not checker passes.

## Pins and limitations

Corpus manifest SHA256: `6d421f8a1372275b45aecab3216fbcf33d804082c61977a42174d5035715db2f`. Full input hashes, per-run manifests, source fingerprints and D68 JSON aggregates are in [test-bounds/summary.json](test-bounds/summary.json).

- twostep: manifest SHA256 `13f05bafd877f6022c832017be4c8bb5af86bc9a95b60dc81d863eb1a550bbd2`; 1 source fingerprint variant(s); 0 instance(s) with source drift flagged; 0 reference exclusion(s).
- solver: manifest SHA256 `1d0eb953d72fd76845b9bf62079654406bfc8d1ad19465e7e38a89e93b4a1215`; 1 source fingerprint variant(s); 0 instance(s) with source drift flagged; 0 reference exclusion(s).
- exact: manifest SHA256 `8fcd538e91a507d3a2ef1274825d43d435bb839432412578a53a67685cd09051`; 1 source fingerprint variant(s); 0 instance(s) with source drift flagged; 0 reference exclusion(s).
- bounds: manifest SHA256 `2035d07c85524be529105529f38c29c7355ce5cc8730c155af794eaf756fe512`; 1 source fingerprint variant(s); 0 instance(s) with source drift flagged; 0 reference exclusion(s).

Source drift is disclosed rather than attributed to a single source revision; inspect the saved fingerprints before release comparisons. This report reads stored checker evidence; it does not replay plans.

Compute: estimated 55 minutes on up to 2 cores / 4 GB / no GPU; measured 20.898 minutes including validation, reduced to one core after parallel work. `nice -n 10` was invoked but the sandbox denied setpriority. [Exact commands, compute actuals and verification](test-bounds/compute.json).

APPROXIMATION: C2 is OFF under D79; the full checker remains the gate. Exact uses a 60-second requested search budget and 10,000-node cap; outer checking is separate.

Assumption: the [pinned runtime](../refs_tools/frozen-runtime/snapshot.json) uses the original parameters and shared warm numerical caches. Cold relocated linear-boundary-policy path identity remains unverified; no campaign failure was observed. [Audit](../refs_tools/review.md). These are standalone MVP input-hash references, not the historical official harness epoch. Small/medium await the leader's test-v2 go-ahead.

Verification: 17 harness-metrics tests passed; all 45 saved plan/report bindings and 15 bound records validated; all 90 frozen SHA256 entries and 259 snapshot hashes remained unchanged.

References: [D68 aggregation and paired bootstrap](../../../src/eval/harness_metrics.py), [lower-bound validity](../../../src/stowage/bounds/__init__.py), [exact scope](../../../src/stowage/solver/exact.py), [planner finalization and checker gate](../../../src/stowage/planner.py).
<!-- BEGIN SMALL TEST V2 REFERENCES -->

## Small — frozen TEST v2

Released population: **10 small instances**. Tiny rows above are preserved byte for byte. Exact remains tiny only. Two-step awaits its explicit leader go unless marked released below.

APPROXIMATION: C2 OFF (`FeasibilityConfig(extra={"global_screen": 0})`); `STOWAGE_BALLAST_V4=1` is scoped to finalization only (D88). Every displayed plan requires the full checker gate. Bounds contain no plans and have no checker verdict.

R = yard rehandles; S = restow lifts; H = hatch-cover lifts. All attempt counts include failed checkpoints. Missing/unattempted cases are listed explicitly and remain in the frozen-population denominator. Missing values are dashes, never assumed zero. Objective means use only passing, unrepaired, explicitly shippable plans; means from differing populations must not be ranked.

| Method | Release | Attempts/frozen | Unattempted | Checker passes/attempts | Checker passes/frozen | Shippable/frozen | Objective n | Mean weighted | Pipeline s total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| solver | released | 10/10 | 0 | 8/10 | 8/10 | 8/10 | 8 | 20.25 | 1293.102 |
| twostep | waiting for leader go | 0/10 | 10 | — | — | — | — | — | — |

### Every small plan attempt and unattempted case

| Instance/path | Method | Pipeline | Checker | First hard rule | Ship | Objective | Ballast status | Ballast present | R now | S now | H now | S later | H later | Weighted | Plan s | Pipeline s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| random.small.28100002.0.2 | solver | ok | passed | — | yes | yes | ok | yes | 3 | 8 | 4 | 0 | 4 | 19 | 115.141 | 151.992 |
| random.small.28100006.0.5 | solver | ok | passed | — | yes | yes | ok | yes | 7 | 8 | 6 | 2 | 4 | 27 | 117.235 | 160.192 |
| sim.small.28100000 | solver | ok | passed | — | yes | yes | ok | yes | 3 | 6 | 4 | 4 | 4 | 21 | 114.719 | 157.481 |
| sim.small.28100004 | solver | ok | passed | — | yes | yes | ok | yes | 3 | 0 | 4 | 0 | 4 | 11 | 103.267 | 141.247 |
| sim.small.28100008 | solver | ok | passed | — | yes | yes | ok | yes | 1 | 0 | 2 | 4 | 4 | 11 | 105.201 | 141.588 |
| random.small.28100001.0.2 | solver | ok | passed | — | yes | yes | ok | yes | 4 | 2 | 4 | 14 | 6 | 30 | 48.711 | 85.327 |
| random.small.28100005.0.5 | solver | ok | passed | — | yes | yes | ok | yes | 9 | 0 | 4 | 4 | 6 | 23 | 47.498 | 82.095 |
| random.small.28100009.0.8 | solver | error | failed_check | iscode.loading.draft | no | no | not_found | no | 14 | 0 | 4 | 0 | 6 | 24 | 37.375 | 169.317 |
| sim.small.28100003 | solver | ok | passed | — | yes | yes | ok | yes | 8 | 2 | 4 | 0 | 6 | 20 | 37.63 | 72.517 |
| sim.small.28100007 | solver | timeout | failed_check | iscode.loading.draft | no | no | — | no | 1 | 2 | 4 | 4 | 6 | 17 | 120.009 | 131.347 |
| test/small/feeder_500/random_yard/seed-028100002.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_500/random_yard/seed-028100006.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_500/simulated_yard/seed-028100000.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_500/simulated_yard/seed-028100004.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_500/simulated_yard/seed-028100008.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_800/random_yard/seed-028100001.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_800/random_yard/seed-028100005.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_800/random_yard/seed-028100009.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_800/simulated_yard/seed-028100003.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |
| test/small/feeder_800/simulated_yard/seed-028100007.json | twostep | waiting for leader go | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

### Failed or excluded small attempts

| Instance | Method | Checker | First hard rule | Ballast status | Not-found cause | Message | Exclusion |
| --- | --- | --- | --- | --- | --- | --- | --- |
| random.small.28100009.0.8 | solver | failed_check | iscode.loading.draft | not_found | timeout:loading:arrival | Ballast not found (timeout:loading:arrival); infeasibility has not been proved. | — |
| sim.small.28100007 | solver | failed_check | iscode.loading.draft | — | — | No complete candidate found within the bounded search | — |

### Small lower bounds

A lower bound counts moves every solution must pay; zero does not certify a useful relative gap. [Bound definitions and validity](../../../src/stowage/bounds/__init__.py).

| Instance/path | Status | R now LB | S now LB | H now LB | S later LB | H later LB | Weighted LB | Time s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| random.small.28100002.0.2 | recorded | 3 | 0 | 0 | 0 | 0 | 3 | 0.714 |
| random.small.28100006.0.5 | recorded | 7 | 0 | 0 | 0 | 0 | 7 | 0.749 |
| sim.small.28100000 | recorded | 0 | 0 | 0 | 0 | 0 | 0 | 0.752 |
| sim.small.28100004 | recorded | 0 | 0 | 0 | 0 | 0 | 0 | 0.744 |
| sim.small.28100008 | recorded | 0 | 0 | 0 | 0 | 0 | 0 | 0.749 |
| random.small.28100001.0.2 | recorded | 4 | 0 | 0 | 0 | 0 | 4 | 0.78 |
| random.small.28100005.0.5 | recorded | 9 | 0 | 0 | 0 | 0 | 9 | 0.805 |
| random.small.28100009.0.8 | recorded | 14 | 0 | 0 | 0 | 0 | 14 | 0.784 |
| sim.small.28100003 | recorded | 0 | 0 | 0 | 0 | 0 | 0 | 0.8 |
| sim.small.28100007 | recorded | 0 | 0 | 0 | 0 | 0 | 0 | 0.787 |

Bounds recorded: 10/10 (not checker passes).

### Small paired comparison

Two-step waiting for leader go.

### Small frozen input settings

| Path | Ship | Family | Booking slack | Booking mass fraction | SHA256 |
| --- | --- | --- | --- | --- | --- |
| test/small/feeder_500/random_yard/seed-028100002.json | feeder_500 | random_yard | 0.25 | 0.55 | 9bdc64444698fcf35141f0406c9779c2950e96043ccfff9cb243cbd3011343f6 |
| test/small/feeder_500/random_yard/seed-028100006.json | feeder_500 | random_yard | 0.25 | 0.55 | 0fee3fc514fd72802c5671ba5592c5cc2505e005c4ef7f51380aeefb5bbd2b10 |
| test/small/feeder_500/simulated_yard/seed-028100000.json | feeder_500 | simulated_yard | 0.25 | 0.55 | 39bb014fd7e03028cb6afaa0c114719fca6bae6a12c4f54c8ddc927a2fbe5124 |
| test/small/feeder_500/simulated_yard/seed-028100004.json | feeder_500 | simulated_yard | 0.25 | 0.55 | d1e9efc3ba9be76b64643bfc44ad87f3c84f9e9d64521b550894aff8a93326b7 |
| test/small/feeder_500/simulated_yard/seed-028100008.json | feeder_500 | simulated_yard | 0.25 | 0.55 | 8ffb29e603af2ade052cb66c193827783e146b4fa7481fdf9d3eab85c72263ad |
| test/small/feeder_800/random_yard/seed-028100001.json | feeder_800 | random_yard | 0.25 | 0.45 | 0724a34360a889d8582567f50ed2bb4d158f60950d02d6a8f7a548fafa8162af |
| test/small/feeder_800/random_yard/seed-028100005.json | feeder_800 | random_yard | 0.25 | 0.45 | 137ed795deb90528fb250eaff9a59c6699d6f055965cd1dafd89d5c07fbd5925 |
| test/small/feeder_800/random_yard/seed-028100009.json | feeder_800 | random_yard | 0.25 | 0.45 | ebbb3752f6a005f61bec7bdca08c2699220be7da2e6051fe82eb22ee3185b0d5 |
| test/small/feeder_800/simulated_yard/seed-028100003.json | feeder_800 | simulated_yard | 0.25 | 0.45 | 608b1c5b104654ff06c09259164223d44e1d45b7147f3be7380691b69141267f |
| test/small/feeder_800/simulated_yard/seed-028100007.json | feeder_800 | simulated_yard | 0.25 | 0.45 | 9f3838e44835fccb0aedecc6d28e1c897ce605cf9caceca8bd1d2baf52b36d72 |

Full D68 aggregates, run records and release pins: [small summary](test-bounds-small-v2/summary.json). This report reads saved full-checker evidence; it does not replay plans. Further v2.1 instances require an explicit release selection/count.

### Small execution and verification

Measured pilot: 151.992 s. Remaining nine cases: 9.828 min on up to two one-thread CPU workers (12 min estimated; 30 min stage-cap ceiling), 4 GB reserved, no GPU. `nice -n 10` was attempted but sandbox-denied. The recorded child RSS high-water mark is 1.178 GB; simultaneous aggregate RSS was not measured.

35 targeted tests passed. All ten saved input/report bindings, weighted objective arithmetic, and absent/1/absent D88 stage environments validated. The approved tiny prefix is unchanged. The original ten input hashes remain the selection despite live v2.1 metadata relabelling. [Exact commands, failures and compute actuals](test-solver-small-v2/compute.json). [Source/provenance audit](../refs_tools/review-small-v2.md).

Prepared baseline command (not executed; leader GO required):

`UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/mvp/refs_tools/run_small_twostep.py`
<!-- END SMALL TEST V2 REFERENCES -->
