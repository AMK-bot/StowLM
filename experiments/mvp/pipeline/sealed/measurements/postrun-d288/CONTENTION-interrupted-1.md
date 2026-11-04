# Interrupted-1 contention addendum — D278, published after D288

This addendum belongs to `sealed/runs/interrupted-1`; it leaves every archived
byte unchanged. Its companion JSON binds the archive inventory, rows and report
by SHA-256 to D284's preserved authorization.

The launcher printed “other fleet work stopped”. That attestation was inaccurate
for the first hour. Per `docs/decisions.md` D278, two freeze suites (pipeline and
app, one core each), p99’s `tests/` triage suite (one core), and one draining
lattice row ran beside this attempt. The lattice row drained at about 05:13 EST.
No GPU job ran beside it. These wall times were measured with that contention;
they are not a quiet-machine benchmark. D278 calls them upper bounds.

The archive remains provisional. Its 38 pinned worker-output identities include
36 classified completed outcomes and two later audit-drift errors. All original
statuses, passes, lifts and timings remain published as they were. The overlap
with final-3 is a separate descriptive comparison, never pooled with final-3.

The final run is labelled: “held-out maps, frozen pipeline, run after two
interrupted attempts; not the pre-registered sealed claim”.
