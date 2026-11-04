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
