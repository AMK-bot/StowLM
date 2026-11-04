# Feasibility performance follow-up

`baseline/` is a byte snapshot of the corrected, exhaustive necessary-direction
engine before this follow-up. `baseline-sources.toml` pins its bytes. The oracle
loader rewrites only private dependency import names so production edits cannot
silently enter the old path. Public models/action types and linear/checker code
are shared with the running epoch.

`measure.py` uses the exact four `fix-t4-measure/*-current.json` instances and
seed 1704. It times a cold `legal_index()` once per immutable prefix, then times
action object materialization separately; `actions_ms` is their sum. Construction,
root LP, and push timings are separate. Repeated warm-cache calls are excluded.
The after campaign replays the before campaign's encoded actions and asserts a
SHA256 equality of every sorted allowed-place array. Profiler runs are explicitly
separate from latency evidence. Component timers report inclusive time, so their
rows must not be added together. Ordinary cProfile has distorted nesting around
HiGHS callbacks; wall-clock LP counters and component timers take precedence.

`test_performance_equivalence.py` compares ordered full action sets (including
discharges), copies and pop over two seeded four-step walks per tiny/small/medium
instance, C2 on. It fails if C2 construction is unavailable, preventing a vacuous
comparison of two fail-closed masks.

Compute admission and estimate/actual records are in `compute.md`; the leader
must mirror its entries to the fleet ledger/history because our fence excludes
those files. No GPU, paid compute, or physics cache warm-up is used.

The invalid-epoch artifact is intentionally retained: it demonstrates the
fail-closed result when the shared linear production qualification becomes stale.
Its C2-on timings are **not valid C2-on performance evidence**.
