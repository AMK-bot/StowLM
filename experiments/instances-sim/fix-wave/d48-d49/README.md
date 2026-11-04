# D48/D49 real-hull rerun

One specification per ship/rung, seed 1, noise 0.2, Laem Chabang-like port,
explicit non-DG service, default D36 quantities, maximum three candidate attempts.
All production hull/background/cargo/C1 screens remain active. C2 is off, matching
this smoke's original recipe; its explicit necessary-direction delegation is
verified separately in generator tests. No plan feasibility claim is made.

| Ship | Rung | Accepted / requested | Targets | Candidate attempts | Rejected-candidate reasons | JSON round trip |
|---|---|---:|---:|---:|---|---|
| feeder_500 | tiny | 1 / 1 | 4 | 3 | displacement/mass band: 2 | pass |
| feeder_500 | small | 1 / 1 | 34 | 1 | none | pass |
| feeder_500 | medium | 1 / 1 | 121 | 2 | on-deck + under-deck requests: 1 candidate | pass |
| feeder_1000 | tiny | 1 / 1 | 4 | 3 | displacement/mass band: 2 | pass |
| feeder_1000 | small | 1 / 1 | 46 | 1 | none | pass |
| feeder_1000 | medium | 1 / 1 | 229 | 1 | none | pass |
| feeder_1700 | tiny | 1 / 1 | 3 | 1 | none | pass |
| feeder_1700 | small | 1 / 1 | 58 | 2 | physical slot occupancy/C1: 1 | pass |
| feeder_1700 | medium | 1 / 1 | 428 | 1 | none | pass |

**9 / 9 accepted requests, 15 candidate attempts, 6 rejected candidates,
0 allocation rejections, 9 JSON round trips.** The 500 medium's two E8 rule IDs
refer to the same rejected candidate. Exact IDs, counts, completion reasons,
capacities and requested/realised utilisation are in each `.audit.json` and
`manifest.json`. Never sum overlapping reasons as distinct rejected candidates.

All nine records share one exact producer-source/parameter fingerprint epoch;
each has `source_drift=[]`. Condition version is `c3-fixed-background-9`.
D48's bounded heavy-low booking reference and D42 background records are retained
inside the serialized vessels. None of the reference masses is added as onboard
cargo or a move. All nine actual instances were saved and reloaded with equality
asserted; file SHA-256 values are retained. Inputs require actual solver and full
checker approval before use as shipping plans.

The former three-attempt medium allocation rejection is resolved on these
bounded cases. The retained prior smoke remains historical evidence under its
recorded condition epoch. This nine-case sample does not establish population
acceptance rates or every seed's feasibility.

```sh
UV_CACHE_DIR=/private/tmp/fix-t4-instances-uv OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 nice -n 10 uv run --offline --no-sync python experiments/instances-sim/fix-wave/d48-d49/smoke.py
```

The script writes only nine accepted instances and audits; `--limit 1` measures
a pilot, and `--resume` verifies exact source/config/file identity. The first
2.423s pilot passed. Resume then correctly refused source drift as the feasibility
owner landed its D49 migration; the clean full restart completed in **24.545s**
versus **<=3min** admitted estimate, one CPU, estimated <=2 GB, no GPU. Generation
within shards took 24.471s. Fleet ledger reservations were added and released;
actual/estimate is appended to fleet history. Peak RAM was not measured (`ps`
is sandbox-denied). Nice priority adjustment was denied but execution completed.

A later resume detected subsequent random/feasibility edits and refused reuse. The final complete rerun took **24.243s** versus **<=60s**, with the same outcomes above, one shared source epoch and zero source drift. Its reservation is released.
