# D320 A — fresh held-out extension ext-1

Label: **fresh held-out extension, run after the freeze; not the pre-registered
sealed claim**. This folder prepares the run; preparation never launches it.
Exactly24fresh envelope maps, eight public cells with three new roots each;
72trials: frozen standard pipeline, the same15/15s simple two-step comparator,
and one frozen-template confirmed instruction on every map. Four maps at a
time, arms serial within a map. Standing is empty, expected-match confirmation
source is `sealed_expected_match`, and the actual binding confirms before
execution. See [PREREG.md](PREREG.md) and its SHA-256 sidecar.

Only the leader runs the following, when the GPU is free for the serial live
swa-last-5 prepass. The flag attests that GPU window; it does not attest an idle
fleet. The launcher detaches into a new session, records its PID, and logs under
this folder. Do not run any second GPU job until prepass finishes.

    sh experiments/mvp/pipeline/sealed/measurements/ext-1/launch_ext.sh.txt --gpu-ready --detach

The same command resumes the same run. It preserves completed outcomes and
never repeats a dispatched timed trial, spent audit or committed reading.
A concurrent controller refuses. If the prepass fatally fails before committing
its allocation/readings, no planning may begin. Every registered trial stays in
the provisional report; failed trials are not retried to improve results.
Use the same command without `--gpu-ready` to wait for a previously authorized
GPU marker; a resumed run with its allocation committed needs no GPU window.

Read-only preflight (no model inference, map payload or planner is opened):

    sh experiments/mvp/pipeline/sealed/measurements/ext-1/launch_ext.sh.txt --check

Admission waits for fresh `experiments/fleet/core-state.json`, at least8enabled
cores and load1<=45, reserving2cores relative to worker count. Concurrent fleet
work is allowed, so measured wall times are operational only and are not quoted.
Operational reservation:3–4hours plus unbounded admission/contention waits.
[runtime-projection.json](runtime-projection.json) gives the source-based
2.9–3.3hour projection before extra contention and waits.

`maps-manifest.json`, `map-seed-proof.json`, `map-digest-proof.json` and the
prior-identity inventory record the seed/digest proof without opening any old
sealed payload. The census is a closed-world statement about recorded external
metadata and path names, at its recorded cutoff; unknown unrecorded seeds or
digests cannot be certified. The actual producer is the frozen repository
generator with only output/cache routing changed, never a replacement planner.
[planning-code-identity.json](planning-code-identity.json) records the unchanged
v3→v4 planning-scope hash inventories. `PRECOMMIT.json` binds the final config,
registration, map metadata, proof artifacts and excluded coordinator source.

Results go only to `sealed/runs/ext-1`; report/CSV retain all72map-arm identities,
full-checker L0 counts and exact95% intervals, ship splits, relaxed levels,
out-of-envelope and failures, and the same3.1 binding-instruction denominator.
Results remain separate beside3.1. Any48-map pooled result is a clearly labelled
secondary figure with both component counts and epochs; interrupted attempts
are never pooled. No new lift-saving claim or reader-accuracy claim is made.

The one-development-map short-wall plumbing runs under `pilot/`, uses recorded
expected readings on CPU and real unchanged native planners, and is excluded
from all extension evidence. `pilot-attempt-1/` preserves the first20s attempt,
which expired during guard startup before engine entry; its pipeline row was
retained and the other two trials remained not_run. The corrected short pilot
uses90s outer planning walls; this does not change extension budgets. Raw pilot
artifacts and fresh audit/confirmation evidence are retained for review.

All repository writes, including numerical caches, receipt keys and peel-ladder
scratch, stay below `measurements/ext-1` or `runs/ext-1`. The child and inherited
spawn workers enforce that fence. The frozen canonical harness and old global
claim are untouched. Manifest v4 is checked with full weights before opening
and per phase; drift blocks further openings and leaves a provisional report.
