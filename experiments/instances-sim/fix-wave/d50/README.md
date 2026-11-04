# D50 producer and full-checker smoke

One retained instance per tiny/small/medium rung per ship, with a genuine empty
MovePlan passed through the full checker. All nine declare typed departure and
immediate-next-call arrival identities, source-qualified port density, default
10% consumable GROUP retention and no voyage ballast operations. C2 stays off,
matching the earlier input smoke; the full checker is unmodified and runs all
parts. No plan/ballast search or feasibility repair was attempted.

| Ship | Rung | Seed | Targets | Voyage ran | Endpoint physics ran | Full check passed | JSON round trip |
|---|---|---:|---:|---|---|---|---|
| feeder_500 | tiny | 0 | 4 | yes | 4 / 4 | no | yes |
| feeder_500 | small | 1 | 34 | yes | 4 / 4 | no | yes |
| feeder_500 | medium | 4 | 121 | yes | 4 / 4 | no | yes |
| feeder_1000 | tiny | 1 | 4 | yes | 4 / 4 | no | yes |
| feeder_1000 | small | 1 | 46 | yes | 4 / 4 | no | yes |
| feeder_1000 | medium | 1 | 229 | yes | 4 / 4 | no | yes |
| feeder_1700 | tiny | 1 | 3 | yes | 4 / 4 | no | yes |
| feeder_1700 | small | 1 | 58 | yes | 4 / 4 | no | yes |
| feeder_1700 | medium | 1 | 428 | yes | 4 / 4 | no | yes |

**9 / 9 voyage derivations, 36 / 36 endpoint physics parts ran, 9 / 9 instance
and report JSON round trips, 0 / 9 empty plans passed.** All reports bind the
actual instance/plan via `report_matches_inputs`. Every empty plan shuts out its
load list. Additional observed failures include securing, stack weight,
draught/trim, containership/weather stability and visibility. Their exact
endpoint IDs, run/errors, margins and rule IDs are retained in the full reports
and audit summaries. These checks demonstrate D50 wiring, not feasible plans or
infeasibility proofs.

Missing metadata is distinct from a hard-physics rejection. The current profile
catalog maps THLCH/SGSIN/THBKK/USLAX, but VNSGN/HKHKG lack profiles despite being
next calls in 50% of configured route weight. Production now fails clearly for
unmapped next environments. This integration sample explicitly preflights the
unchanged route RNG, preferring seed1 then bounded seeds0..20 until a supported
SGSIN next call is found. It changes no generator route policy. All considered
seeds/next-LOCODEs are in `seed_selection`; 500tiny uses seed0 and 500medium
seed4 because seed1's route lacked profile evidence. Other seven retain seed1.
This conditional sample is not population acceptance evidence. The port-profile
owner must supply source-qualified declarations for VNSGN/HKHKG; no density or
berth restriction was borrowed from another port.

All nine retained records share one exact source/parameter epoch, have
`source_drift=[]`, and retain instance/plan/report checksums. Local simulated
version3.1.0; condition `c3-fixed-background-9`. Default-rule lineage and every
endpoint's environment/density ID survive serialization. Sources are synthetic;
D34's profile density remains an assumption, not measured port data.

```sh
UV_CACHE_DIR=/private/tmp/fix-t4-instances-uv OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 nice -n 10 uv run --offline --no-sync python experiments/instances-sim/fix-wave/d50/smoke.py
```

Final complete run **40.254s** versus <=2min admitted,
one CPU/estimated<=2GB/noGPU. The measured first pilot was4.524s including2.712s
checker work versus<=60s estimated. Source guards refused pilot reuse after
concurrent supplier edits, and stopped an interrupted attempt after three clean
cases; the final complete run above has no drift. Only final retained cases count.
Reservations were added/released and actual/estimate appended to fleet history.
Peak RAM is unmeasured because ps is sandbox-denied. Nice was requested; the
sandbox denied priority adjustment but execution completed.
