# Historical task 4.3 compute audit — generator 1.0.0

These reservations and timings describe the original run only. Current fix-wave
work is recorded separately under `fix-wave/`.

All jobs local CPU, BLAS/OpenMP threads=1, no GPU or paid calls.
Admission: shared ledger read before runs (GPU benchmark 1 core/30 GB and checker
suite 1 core/1 GB); live load 7.56, 80% memory free. Allocatable 16 cores/112 GB.
`ps` and `nice -n 10 true` both fail with Operation not permitted in this sandbox.
The scope fence excludes experiments/fleet/{ledger,history}.md, so neither was
edited; this scoped audit records our reservations for the leader to reconcile.

Measurements: cold real-hull tiny = 56.79 s (hull 56.74 s); geometry-only diagnostic
cached tiny .03 s, small .70 s, medium .63 s, full 2.14 s. An initial implementation
unnecessarily loaded ships after snapshots (medium 48 s); interrupted only our
own session12001, then used the supported retrieval hook to defer target loading.
This does not change the loading-start snapshot.

| Job | Reservation | Estimate | Actual/status |
|---|---|---|---|
| five-per-rung benchmark | 1 core / <1 GB / no GPU | <2 min from measured hull + simulation | 73.58 s accumulated shard generation including initial cold hull; incremental benchmark 16.79 s; 20/20 |
| first full pytest suite | 1 core / <2 GB / no GPU | 8 min, prior fleet history 260 s and concurrent checker estimate 8 min | exit1, seven out-of-scope failures; elapsed not instrumented (approximately 10 min) |
| required full-suite repeat | 1 core / <2 GB / no GPU | 10 min from first run | 415.13 s wall / 403.76 s user / 3.26 s sys; exit1, four out-of-scope failures; ended 08:21:21 UTC |

RAM estimates are conservative process estimates, not instrumented peak RSS.

Starter estimate after benchmark: 1 core / <1 GB / no GPU / <2 min including cold hull.
105 new shards completed; 20 benchmark shards reused. Persisted shard generation totals
91.51 s across the complete 125-shard collection (includes cold hull, excludes I/O).
Five raw cargo screening candidates per rung took 1.92 s, one core / no GPU.
