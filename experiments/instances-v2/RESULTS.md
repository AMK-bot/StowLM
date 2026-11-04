# Canonical v2 results

Complete: **1,160 instances**, 580 per generator. Counts per generator: 200 tiny, 200 small, 100 medium, 40 full, 20 realistic_500, 20 realistic_1000. All three feeders, all four port archetypes and noise/blocking levels 0,.2,.8 are represented. Family restrictions and dense20 conditioning are in README.md.

The dense profiles contain **400 / 800 boxes**. Requested 450 on feeder_500 is physically impossible (440 cells). Small-feeder-port crane reach cannot accommodate the two dense profiles; its archetype remains in the standard rungs. No physical constraint or DG permission was relaxed.

| Generator/profile | Instances | Boxes/instance | Candidate draws | Rejected | Provision rejection | Mean persisted generation(s) | Post-pilot mean(s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| random_yard/full | 40 | 280 | 108 | 62.96% | 56.48% | 0.294 | 0.087 |
| random_yard/medium | 100 | 150 | 155 | 35.48% | 31.61% | 0.042 | 0.031 |
| random_yard/realistic_1000 | 20 | 800 | 1227 | 98.37% | 92.83% | 12.849 | 4.311 |
| random_yard/realistic_500 | 20 | 400 | 1569 | 98.73% | 70.68% | 8.323 | 1.514 |
| random_yard/small | 200 | 36 | 1554 | 87.13% | 10.17% | 0.019 | 0.017 |
| random_yard/tiny | 200 | 6 | 220 | 9.09% | 0.45% | 0.017 | 0.014 |
| simulated_yard/full | 40 | 280 | 109 | 63.30% | 60.55% | 1.410 | 1.002 |
| simulated_yard/medium | 100 | 150 | 163 | 38.65% | 32.52% | 0.407 | 0.355 |
| simulated_yard/realistic_1000 | 20 | 800 | 870 | 97.70% | 93.22% | 11.670 | 9.687 |
| simulated_yard/realistic_500 | 20 | 400 | 3004 | 99.33% | 72.70% | 7.268 | 3.748 |
| simulated_yard/small | 200 | 36 | 1300 | 84.62% | 10.62% | 0.041 | 0.038 |
| simulated_yard/tiny | 200 | 6 | 224 | 10.71% | 0.89% | 0.016 | 0.015 |

Rates use candidate lists as denominator. Reasons overlap. Provision rate counts candidates containing at least one unsupported DG provision; accepted lists contain none. Tiny is DG-enabled:58 accepted DG boxes in random yards and 75 in simulated yards.

Total load boxes: 117, 200; DG boxes: 5,893. Splits: {'train': 615, 'val': 31, 'test': 514}. Held-out port/noise/slice combinations explain the large test partition. Splits were assigned before generating/solving and checked against 5.3 family adapters.

## Every nonzero rejection reason

| Generator/profile | Catalogue reason | Count | Rate per candidate |
|---|---|---:|---:|
| random_yard/full | `expert.e8.on_deck` | 2 | 1.85% |
| random_yard/full | `expert.e8.under_deck` | 2 | 1.85% |
| random_yard/full | `imdg.stowage.3_2` | 65 | 60.19% |
| random_yard/medium | `expert.e8.on_deck` | 4 | 2.58% |
| random_yard/medium | `expert.e8.under_deck` | 3 | 1.94% |
| random_yard/medium | `imdg.stowage.3_2` | 52 | 33.55% |
| random_yard/realistic_1000 | `expert.physical.stack_weight` | 935 | 76.20% |
| random_yard/realistic_1000 | `imdg.stowage.3_2` | 1152 | 93.89% |
| random_yard/realistic_500 | `expert.physical.stack_weight` | 1506 | 95.98% |
| random_yard/realistic_500 | `imdg.stowage.3_2` | 1141 | 72.72% |
| random_yard/small | `expert.e8.under_deck` | 539 | 34.68% |
| random_yard/small | `expert.physical.reefer_plug` | 520 | 33.46% |
| random_yard/small | `expert.physical.size_fit` | 9 | 0.58% |
| random_yard/small | `expert.physical.slot_occupancy` | 395 | 25.42% |
| random_yard/small | `expert.physical.stack_weight` | 689 | 44.34% |
| random_yard/small | `expert.physical.tier_height` | 4 | 0.26% |
| random_yard/small | `imdg.stowage.3_2` | 158 | 10.17% |
| random_yard/tiny | `expert.e8.under_deck` | 19 | 8.64% |
| random_yard/tiny | `imdg.stowage.3_2` | 1 | 0.45% |
| simulated_yard/full | `expert.e8.on_deck` | 8 | 7.34% |
| simulated_yard/full | `expert.e8.under_deck` | 3 | 2.75% |
| simulated_yard/full | `imdg.stowage.3_2` | 67 | 61.47% |
| simulated_yard/medium | `expert.e8.on_deck` | 5 | 3.07% |
| simulated_yard/medium | `expert.e8.under_deck` | 6 | 3.68% |
| simulated_yard/medium | `imdg.stowage.3_2` | 56 | 34.36% |
| simulated_yard/realistic_1000 | `expert.physical.stack_weight` | 622 | 71.49% |
| simulated_yard/realistic_1000 | `imdg.stowage.3_2` | 820 | 94.25% |
| simulated_yard/realistic_500 | `expert.physical.stack_weight` | 2942 | 97.94% |
| simulated_yard/realistic_500 | `imdg.stowage.3_2` | 2252 | 74.97% |
| simulated_yard/small | `expert.e8.under_deck` | 406 | 31.23% |
| simulated_yard/small | `expert.physical.reefer_plug` | 481 | 37.00% |
| simulated_yard/small | `expert.physical.size_fit` | 3 | 0.23% |
| simulated_yard/small | `expert.physical.slot_occupancy` | 271 | 20.85% |
| simulated_yard/small | `expert.physical.stack_weight` | 496 | 38.15% |
| simulated_yard/small | `expert.physical.tier_height` | 2 | 0.15% |
| simulated_yard/small | `imdg.stowage.3_2` | 138 | 10.62% |
| simulated_yard/tiny | `expert.e8.under_deck` | 22 | 9.82% |
| simulated_yard/tiny | `imdg.stowage.3_2` | 2 | 0.89% |

All unlisted reasons had zero observed rejections. Passing this necessary-condition screen does not certify a loading plan.

## Timing and validation

- Current canonical campaign:`--workers 4`, **108.41 s wall / 456.70 s aggregate CPU**, 1,100 new shards and 60 pilot shards reused. Admitted estimate 15 min, based on 497.6 s unoptimized pilot projection plus I/O.
- Pilot timing includes original and exact-cache screening implementations. Post-pilot means measure the optimized implementation; persisted means include the slower pilot. Candidate sequences/reasons and accepted bytes are unchanged by caching.
- Exact screen benchmark: 5 DG-enabled 800-box lists, 1.20715→.07689 s/call (15.7×); 150 adversarial reason-set comparisons and both generators’ accepted bytes matched.
- Full validation: 1,160 hashes, schemas, clean provenance, accepted DG provisions, family/split identities and unique spec keys passed in 9.11 s wall. Read-only production shard resume path checked 1,160/1,160, zero generation, 8.817 s.
- Scoped tests: 81 passed in 2.14 s. `uv run pytest -q` ran twice: 17 outside-scope failures both times; 1083.21 s then 278.17 s. See full-suite*.log and HANDOFF.md.
- Interrupted pilots and repeated candidate computation are recorded in experiments/fleet/history.md. Persisted generation timings do not include interrupted/replayed work. All compute local CPU; no GPU/cloud/paid calls. `nice`, `ps` and `iostat` were denied by the sandbox; bounded worker/thread counts and ledger/load/memory admission were used.

Content digest:`46177d447ed2aa7b6aaaf0149d759a44248b98840c782e6f28f0a2eee1d4c33a`. Release metadata:release.json; complete per-instance specs, hashes and splits:manifest.json.
