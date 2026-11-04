# Final fixed-map outcomes

| Map suffix | Verdict | Strong outer s | Page follow-up s | Replay complete | Full pass |
| --- | --- | ---: | ---: | --- | --- |
| D192.medium-feeder_500-f60-high-s192200000 | feasible_with_witness | 10.72 | 0.00 | True | True |
| D192.medium-feeder_800-f60-low-s192200101 | feasible_with_witness | 13.84 | 0.00 | True | True |
| random.medium.185100000.0.2 | unknown | 10.74 | 30.35 | True | False |
| random.medium.185100001.0.5 | unknown | 10.57 | 37.13 | True | False |
| sim.medium.185100010 | unknown | 10.49 | 24.36 | True | False |
| sim.medium.185100011 | unknown | 10.53 | 26.28 | True | False |
| random.medium.185100100.0.2 | unknown | 15.53 | 47.35 | True | False |
| random.medium.185100101.0.5 | unknown | 15.22 | 50.56 | True | False |
| sim.medium.185100110 | unknown | 15.22 | 53.41 | True | False |
| sim.medium.185100111 | unknown | 14.99 | 10.51 | True | False |
| random.medium.185100200.0.2 | unknown | 24.94 | 51.04 | True | False |
| random.medium.185100201.0.5 | unknown | 23.62 | 15.97 | True | False |
| sim.medium.185100210 | unknown | 22.48 | 67.07 | True | False |
| sim.medium.185100211 | unknown | 22.27 | 21.49 | True | False |

D185: 0/12 proved infeasible; 12/12 outer witnesses; 12/12 complete replay-legal stows; 0/12 full-checker passes.

D185 outer time 196.59 s; constructive follow-up 435.53 s, including 12 full-checker calls.

All 96/96 D185 lashing-GM intervals survived even the occupied-root rod rows. Across all fourteen maps, all 112 intervals survived. Earlier weaker spatial sweep time is retained separately (164.24 s for D185).

The controls reuse preexisting cold passing plans and were freshly checked: 71/116 lifts, both complete and legal. Their mean is 93.5 versus V6 172.5; this experiment claims no new lift gain. No paired 0.9.0 medium lift comparison is available.

Unknown means neither a complete legal stow nor an all-stow/all-tank contradiction was established. A surviving GM branch is an outer allocation at a necessary acceleration floor, not an achieved GM/KG state. Finite-angle area upper bounds and visibility remain too weak to settle these maps.
