# Canonical v3 request inventory

Verified 2,240 requested specifications: **595 accepted instances, 1,645 rejected request audits**. Rejected requests contain no instance and cannot be solver input.
C1 and C2 are enabled; whole-fleet C2 warm qualification passed (exit 0, 34.30 s). Main rejection cap: 20 candidate attempts.
Accepted target bookings include 3,052 DG containers across 438 instances. Physics fingerprint: `2497bc54df98534644eee1b1e3da469bdda74068599f89ef076b4563354ab7ae`.
Across 33,640 candidate attempts, 33,045 were screened out (98.23%); fail-closed provision rejections were 1,936 (5.76%). Summed generation time was 34728.484 s: 15.504 s/request and 58.367 s/accepted instance, including rejected-request cost.
Supplemental policy-bank checksum `509a7ea96711c6941889e7d9207d1b2addd1ec8ddb8858c38aff7fe6610918fe` was captured at 2026-10-27T21:51:56.978374+00:00, after main generation began. Final verification checks matching bytes; earlier unobserved bank history is not certified. File mtime is supporting evidence only.
Passing necessary screens does not certify a feasible loading plan. No full-checker-approved plans are claimed.

| Generator/profile | Requested | Accepted | Rejected | Accepted train/val/test | Rejected train/val/test | Seconds/request | Seconds/accepted | Candidate screen rate |
|---|---:|---:|---:|---|---|---:|---:|---:|
| random_yard/full | 60 | 60 | 0 | 38/4/18 | 0/0/0 | 13.830 | 13.830 | 73.45% |
| random_yard/medium | 200 | 66 | 134 | 0/0/66 | 93/6/35 | 4.910 | 14.877 | 78.00% |
| random_yard/realistic_1000 | 30 | 30 | 0 | 20/1/9 | 0/0/0 | 7.321 | 7.321 | 78.42% |
| random_yard/realistic_500 | 30 | 30 | 0 | 21/1/8 | 0/0/0 | 3.014 | 3.014 | 80.00% |
| random_yard/small | 400 | 123 | 277 | 78/8/37 | 187/11/79 | 10.293 | 33.474 | 98.10% |
| random_yard/tiny | 400 | 0 | 400 | 0/0/0 | 269/16/115 | 21.591 | — | 100.00% |
| simulated_yard/full | 60 | 52 | 8 | 26/2/24 | 3/0/5 | 15.283 | 17.634 | 88.60% |
| simulated_yard/medium | 200 | 66 | 134 | 0/0/66 | 69/1/64 | 37.797 | 114.537 | 97.69% |
| simulated_yard/realistic_1000 | 30 | 22 | 8 | 11/1/10 | 5/0/3 | 15.275 | 20.830 | 94.49% |
| simulated_yard/realistic_500 | 30 | 29 | 1 | 15/1/13 | 1/0/0 | 6.951 | 7.190 | 86.45% |
| simulated_yard/small | 400 | 117 | 283 | 55/2/60 | 130/5/148 | 10.334 | 35.329 | 98.18% |
| simulated_yard/tiny | 400 | 0 | 400 | 0/0/0 | 181/13/206 | 16.441 | — | 100.00% |

| Rung | Split | Accepted | Rejected |
|---|---|---:|---:|
| full | train | 131 | 9 |
| full | val | 10 | 0 |
| full | test | 82 | 8 |
| medium | train | 0 | 162 |
| medium | val | 0 | 7 |
| medium | test | 132 | 99 |
| small | train | 133 | 317 |
| small | val | 10 | 16 |
| small | test | 97 | 227 |
| tiny | train | 0 | 450 |
| tiny | val | 0 | 29 |
| tiny | test | 0 | 321 |

Fail-closed provision counters are separate from generic IMDG reasons; their denominator is candidate attempts, not accepted instances or DG boxes.

| Generator/profile | Candidate attempts | Fail-closed provision rejections | Provision rejection rate |
|---|---:|---:|---:|
| random_yard/full | 226 | 62 | 27.43% |
| random_yard/medium | 300 | 27 | 9.00% |
| random_yard/realistic_1000 | 139 | 44 | 31.65% |
| random_yard/realistic_500 | 150 | 47 | 31.33% |
| random_yard/small | 6472 | 353 | 5.45% |
| random_yard/tiny | 8000 | 131 | 1.64% |
| simulated_yard/full | 456 | 314 | 68.86% |
| simulated_yard/medium | 2858 | 33 | 1.15% |
| simulated_yard/realistic_1000 | 399 | 311 | 77.94% |
| simulated_yard/realistic_500 | 214 | 120 | 56.07% |
| simulated_yard/small | 6426 | 376 | 5.85% |
| simulated_yard/tiny | 8000 | 118 | 1.47% |
| All profiles | 33640 | 1936 | 5.76% |

Both time averages include rejected requests; seconds/accepted amortizes the full generation cost over accepted output. A zero-acceptance group has no seconds/accepted estimate. Candidate reason rates use all attempts, including exhausted and initialization failures. Reasons overlap; shipment redraws are separate.

| Generator/profile | Screen/diagnostic | Reason | Count | Rate per candidate |
|---|---|---|---:|---:|
| All profiles | physical/provision | expert.e8.on_deck | 244 | 0.73% |
| All profiles | physical/provision | expert.e8.under_deck | 73 | 0.22% |
| All profiles | physical/provision | expert.physical.size_fit | 81 | 0.24% |
| All profiles | physical/provision | expert.physical.slot_occupancy | 30946 | 91.99% |
| All profiles | physical/provision | imdg.stowage.3_2 | 2120 | 6.30% |
| All profiles | physical/provision | iscode.loading.displacement | 362 | 1.08% |
| All profiles | depth-0 C1/C2 | c1a | 5871 | 17.45% |
| All profiles | depth-0 C1/C2 | c1b | 14 | 0.04% |
| All profiles | depth-0 C1/C2 | c1g | 5857 | 17.41% |
| All profiles | depth-0 C1/C2 | c1h | 5863 | 17.43% |
| All profiles | depth-0 C1/C2 | c2 | 18998 | 56.47% |
| random_yard/full | physical/provision | expert.e8.on_deck | 16 | 7.08% |
| random_yard/full | physical/provision | expert.e8.under_deck | 9 | 3.98% |
| random_yard/full | physical/provision | expert.physical.slot_occupancy | 89 | 39.38% |
| random_yard/full | physical/provision | imdg.stowage.3_2 | 63 | 27.88% |
| random_yard/full | depth-0 C1/C2 | c1a | 89 | 39.38% |
| random_yard/full | depth-0 C1/C2 | c1b | 12 | 5.31% |
| random_yard/full | depth-0 C1/C2 | c1g | 84 | 37.17% |
| random_yard/full | depth-0 C1/C2 | c1h | 84 | 37.17% |
| random_yard/full | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| random_yard/full | shipment redraws | replacement draws | 880 | — |
| random_yard/medium | physical/provision | expert.e8.on_deck | 5 | 1.67% |
| random_yard/medium | physical/provision | expert.physical.slot_occupancy | 204 | 68.00% |
| random_yard/medium | physical/provision | imdg.stowage.3_2 | 27 | 9.00% |
| random_yard/medium | depth-0 C1/C2 | c1a | 70 | 23.33% |
| random_yard/medium | depth-0 C1/C2 | c1g | 70 | 23.33% |
| random_yard/medium | depth-0 C1/C2 | c1h | 70 | 23.33% |
| random_yard/medium | failed request diagnostic | background_balance_infeasible | 134 | 44.67% |
| random_yard/medium | candidate initialization | background_balance_infeasible | 134 | 44.67% |
| random_yard/medium | shipment redraws | replacement draws | 280 | — |
| random_yard/realistic_1000 | physical/provision | expert.physical.slot_occupancy | 41 | 29.50% |
| random_yard/realistic_1000 | physical/provision | imdg.stowage.3_2 | 45 | 32.37% |
| random_yard/realistic_1000 | physical/provision | iscode.loading.displacement | 40 | 28.78% |
| random_yard/realistic_1000 | depth-0 C1/C2 | c1a | 41 | 29.50% |
| random_yard/realistic_1000 | depth-0 C1/C2 | c1g | 41 | 29.50% |
| random_yard/realistic_1000 | depth-0 C1/C2 | c1h | 41 | 29.50% |
| random_yard/realistic_1000 | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| random_yard/realistic_1000 | shipment redraws | replacement draws | 594 | — |
| random_yard/realistic_500 | physical/provision | expert.physical.slot_occupancy | 42 | 28.00% |
| random_yard/realistic_500 | physical/provision | imdg.stowage.3_2 | 47 | 31.33% |
| random_yard/realistic_500 | physical/provision | iscode.loading.displacement | 55 | 36.67% |
| random_yard/realistic_500 | depth-0 C1/C2 | c1a | 40 | 26.67% |
| random_yard/realistic_500 | depth-0 C1/C2 | c1g | 40 | 26.67% |
| random_yard/realistic_500 | depth-0 C1/C2 | c1h | 40 | 26.67% |
| random_yard/realistic_500 | depth-0 C1/C2 | c2 | 2 | 1.33% |
| random_yard/realistic_500 | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| random_yard/realistic_500 | shipment redraws | replacement draws | 398 | — |
| random_yard/small | physical/provision | expert.e8.on_deck | 65 | 1.00% |
| random_yard/small | physical/provision | expert.e8.under_deck | 24 | 0.37% |
| random_yard/small | physical/provision | expert.physical.size_fit | 37 | 0.57% |
| random_yard/small | physical/provision | expert.physical.slot_occupancy | 6012 | 92.89% |
| random_yard/small | physical/provision | imdg.stowage.3_2 | 407 | 6.29% |
| random_yard/small | depth-0 C1/C2 | c1a | 1656 | 25.59% |
| random_yard/small | depth-0 C1/C2 | c1g | 1655 | 25.57% |
| random_yard/small | depth-0 C1/C2 | c1h | 1655 | 25.57% |
| random_yard/small | depth-0 C1/C2 | c2 | 2727 | 42.14% |
| random_yard/small | failed request diagnostic | screening_exhausted | 277 | 4.28% |
| random_yard/small | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| random_yard/small | shipment redraws | replacement draws | 2591 | — |
| random_yard/tiny | physical/provision | expert.e8.on_deck | 1 | 0.01% |
| random_yard/tiny | physical/provision | expert.physical.slot_occupancy | 7843 | 98.04% |
| random_yard/tiny | physical/provision | imdg.stowage.3_2 | 156 | 1.95% |
| random_yard/tiny | depth-0 C1/C2 | c1a | 1091 | 13.64% |
| random_yard/tiny | depth-0 C1/C2 | c1g | 1087 | 13.59% |
| random_yard/tiny | depth-0 C1/C2 | c1h | 1091 | 13.64% |
| random_yard/tiny | depth-0 C1/C2 | c2 | 6752 | 84.40% |
| random_yard/tiny | failed request diagnostic | screening_exhausted | 400 | 5.00% |
| random_yard/tiny | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| random_yard/tiny | shipment redraws | replacement draws | 1242 | — |
| simulated_yard/full | physical/provision | expert.e8.on_deck | 90 | 19.74% |
| simulated_yard/full | physical/provision | expert.e8.under_deck | 29 | 6.36% |
| simulated_yard/full | physical/provision | expert.physical.slot_occupancy | 68 | 14.91% |
| simulated_yard/full | physical/provision | imdg.stowage.3_2 | 320 | 70.18% |
| simulated_yard/full | depth-0 C1/C2 | c1a | 68 | 14.91% |
| simulated_yard/full | depth-0 C1/C2 | c1b | 2 | 0.44% |
| simulated_yard/full | depth-0 C1/C2 | c1g | 67 | 14.69% |
| simulated_yard/full | depth-0 C1/C2 | c1h | 67 | 14.69% |
| simulated_yard/full | failed request diagnostic | screening_exhausted | 8 | 1.75% |
| simulated_yard/full | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| simulated_yard/full | shipment redraws | replacement draws | 578 | — |
| simulated_yard/medium | physical/provision | expert.e8.on_deck | 10 | 0.35% |
| simulated_yard/medium | physical/provision | expert.physical.slot_occupancy | 2749 | 96.19% |
| simulated_yard/medium | physical/provision | imdg.stowage.3_2 | 37 | 1.29% |
| simulated_yard/medium | depth-0 C1/C2 | c1a | 69 | 2.41% |
| simulated_yard/medium | depth-0 C1/C2 | c1g | 69 | 2.41% |
| simulated_yard/medium | depth-0 C1/C2 | c1h | 69 | 2.41% |
| simulated_yard/medium | failed request diagnostic | background_balance_infeasible | 2680 | 93.77% |
| simulated_yard/medium | failed request diagnostic | screening_exhausted | 134 | 4.69% |
| simulated_yard/medium | candidate initialization | background_balance_infeasible | 2680 | 93.77% |
| simulated_yard/medium | shipment redraws | replacement draws | 271 | — |
| simulated_yard/realistic_1000 | physical/provision | expert.physical.slot_occupancy | 36 | 9.02% |
| simulated_yard/realistic_1000 | physical/provision | imdg.stowage.3_2 | 320 | 80.20% |
| simulated_yard/realistic_1000 | physical/provision | iscode.loading.displacement | 181 | 45.36% |
| simulated_yard/realistic_1000 | depth-0 C1/C2 | c1a | 36 | 9.02% |
| simulated_yard/realistic_1000 | depth-0 C1/C2 | c1g | 36 | 9.02% |
| simulated_yard/realistic_1000 | depth-0 C1/C2 | c1h | 36 | 9.02% |
| simulated_yard/realistic_1000 | failed request diagnostic | screening_exhausted | 8 | 2.01% |
| simulated_yard/realistic_1000 | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| simulated_yard/realistic_1000 | shipment redraws | replacement draws | 403 | — |
| simulated_yard/realistic_500 | physical/provision | expert.physical.slot_occupancy | 39 | 18.22% |
| simulated_yard/realistic_500 | physical/provision | imdg.stowage.3_2 | 124 | 57.94% |
| simulated_yard/realistic_500 | physical/provision | iscode.loading.displacement | 86 | 40.19% |
| simulated_yard/realistic_500 | depth-0 C1/C2 | c1a | 39 | 18.22% |
| simulated_yard/realistic_500 | depth-0 C1/C2 | c1g | 39 | 18.22% |
| simulated_yard/realistic_500 | depth-0 C1/C2 | c1h | 39 | 18.22% |
| simulated_yard/realistic_500 | failed request diagnostic | screening_exhausted | 1 | 0.47% |
| simulated_yard/realistic_500 | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| simulated_yard/realistic_500 | shipment redraws | replacement draws | 326 | — |
| simulated_yard/small | physical/provision | expert.e8.on_deck | 55 | 0.86% |
| simulated_yard/small | physical/provision | expert.e8.under_deck | 11 | 0.17% |
| simulated_yard/small | physical/provision | expert.physical.size_fit | 44 | 0.68% |
| simulated_yard/small | physical/provision | expert.physical.slot_occupancy | 5974 | 92.97% |
| simulated_yard/small | physical/provision | imdg.stowage.3_2 | 425 | 6.61% |
| simulated_yard/small | depth-0 C1/C2 | c1a | 1604 | 24.96% |
| simulated_yard/small | depth-0 C1/C2 | c1g | 1603 | 24.95% |
| simulated_yard/small | depth-0 C1/C2 | c1h | 1603 | 24.95% |
| simulated_yard/small | depth-0 C1/C2 | c2 | 2736 | 42.58% |
| simulated_yard/small | failed request diagnostic | screening_exhausted | 283 | 4.40% |
| simulated_yard/small | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| simulated_yard/small | shipment redraws | replacement draws | 2138 | — |
| simulated_yard/tiny | physical/provision | expert.e8.on_deck | 2 | 0.03% |
| simulated_yard/tiny | physical/provision | expert.physical.slot_occupancy | 7849 | 98.11% |
| simulated_yard/tiny | physical/provision | imdg.stowage.3_2 | 149 | 1.86% |
| simulated_yard/tiny | depth-0 C1/C2 | c1a | 1068 | 13.35% |
| simulated_yard/tiny | depth-0 C1/C2 | c1g | 1066 | 13.33% |
| simulated_yard/tiny | depth-0 C1/C2 | c1h | 1068 | 13.35% |
| simulated_yard/tiny | depth-0 C1/C2 | c2 | 6781 | 84.76% |
| simulated_yard/tiny | failed request diagnostic | screening_exhausted | 400 | 5.00% |
| simulated_yard/tiny | candidate initialization | background_balance_infeasible | 0 | 0.00% |
| simulated_yard/tiny | shipment redraws | replacement draws | 1077 | — |

Accepted and rejected family coverage is recorded separately in verification.json; requested coverage includes both. The ordinary full feeder_1700/small_feeder request is excluded at all three noise levels: 1,260 crane-reachable TEU versus 1,363.09 expected booking TEU (fit probability ≤ 1.33e-13). This is a request-scheduling exclusion; no physics limit or cargo fact is relaxed.

Realistic profiles condition equipment to dry20 and derive counts from current mass/TEU export budgets. Mass-limited profiles may fall below the normal 75% TEU floor; accepted mass and displacement bands remain enforced. Failed background balancing is a rejection, with no invented background loads or invalid instance.
