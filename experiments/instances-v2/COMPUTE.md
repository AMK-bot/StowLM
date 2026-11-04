# Task 4.3 v2 compute record

All our jobs used CPU only with BLAS/OpenMP/Accelerate thread limits of one.
Reservations were entered in the shared fleet ledger and removed on completion;
only our lines were edited. Peak RSS was not instrumented; reservations below
are conservative estimates. The sandbox rejected `nice`, `ps`, and `iostat`.
No priority change or process inspection was bypassed. Only recorded own tool
sessions were interrupted; no pattern-based process termination was used.

| Job | Estimate (cores/RAM/GPU/wall) | Actual |
|---|---|---|
| Vessel readiness | 1 / 2 GB / no / 6 min ceiling after cache misses observed | 387.04 s; cold builds 236.692/150.244/.100 s; subsequent loads .059/.055/.070 s |
| Initial diagnostic pilot | 1 / 2 GB / no / 6 min | 196.33 s, interrupted; 22 draft shards quarantined in temp after tiny-DG/family corrections |
| Final pilot, original screen | 1 / 2 GB / no / 15 min revised | 613.27 s, interrupted after exact optimization verified; committed shards reused |
| Remaining pilot, optimized, cap1000 | 2 / 4 GB / no / 5 min | 87.92 s; 59/60 committed, one rare dense seed exhausted budget |
| Pilot completion, cap10000 | 2 / 4 GB / no / 5 min | 20.02 s; 60/60, 59 reused |
| Canonical campaign | 5 cores (4 workers + coordinator) / 10 GB / no / 15 min | 108.41 s wall, 456.70 s aggregate CPU; 1100 new + 60 reused |
| First full pytest | 1 / 2 GB / no / 10 min | 1083.21 s; exit 1, 17 external failures |
| Required full pytest repeat | 1 / 2 GB / no / 20 min | 278.17 s; exit 1, same 17 external failures |
| Release validation | 1 / 2 GB / no / 3 min | 9.11 s; all 1,160 passed |
| Read-only production resume | 1 / <2 GB / no / <1 min from validation | 8.817 s; all 1,160 resumed, zero generated |

The original pilot encountered changed vessel cache keys and shared load near 24
with 19% memory free. Worker expansion was deferred. The exact screen cache was
measured at 15.7× speedup. At canonical admission the load was 1.92, memory 91%
free; every current full-hull cache key existed. The shared ledger plus the
four-worker/coordinator reservation fit 16 cores / 112 GB. The unoptimized pilot
projected 497.6 s of remaining four-worker work before I/O; the 15-minute
reservation was deliberately conservative. Existing cold/slow pilot timings
remain in their audits; post-pilot timings are separated in RESULTS.md.

Interrupted/replayed work is included in this history, not double-counted as
new independent cargo draws in the sampling-rate denominators. Source and test
work in other sessions changed cache/CPU conditions during the campaign.
