# Interrupted sealed evaluation — D282

The leader authorized one disclosed replacement in docs/decisions.md D282.
At about05:32 an explanation publish overwrote pinned fixture
explain/fixtures/development/current-cache/verification.json. Its original
bytes are lost; manifest v1 can never verify again. The guard stopped new
openings. No process was stopped by this preparation. The remaining worker
finished naturally; FAILED.json and the provisional report were published,
and the controller released its lock before this directory was renamed.

D282's incident snapshot records38 completed rows,2 drift errors and146 not_run.
Final draining produced186 rows: {"checked_plan": 18, "error": 4, "no_checked_plan": 15, "not_run": 146, "relaxed_plan": 3}.
All four final errors carry Frozen pipeline drift. There are36 completed
independently classified outcomes; do not relabel unverified raw plans as passes.
The38-worker-output overlap is pinned in measurements/d282/overlap-identities.json:
36 classified outcomes plus2 outputs whose later audit was blocked by drift.
The worker explicitly still in flight at task start (0036-pipeline,PID43712)
is excluded from that snapshot overlap and retained in this full report.
This reconciles the incident snapshot with final counts without changing rows.

Every original file retains its exact bytes; only this README is new. Original
absolute paths still refer to runs/final and have deliberately not been edited.
This run stays published as provisional and cannot support sealed claims. The
replacement, whatever it shows, is separate at runs/final-2. Both runs remain
published, with the protocol deviation disclosed alongside every3.1 number.
