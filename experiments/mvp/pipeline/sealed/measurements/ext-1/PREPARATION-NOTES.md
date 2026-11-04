# ext-1 preparation notes — before held-out planning

Recorded 2026-11-03T03:24:47.173463+00:00.
The registration and its SHA-256 are unchanged. No extension map has been
planned, checked for loading feasibility, ranked by results, or used for a
pilot. No old sealed input has been opened.

## Generator bookkeeping recovery: 16 committed maps, no redraws

The initial excluded output-write guard interpreted an fd-relative
`os.remove("worker-PID.json", dir_fd=private_directory_fd)` during private
parent cleanup relative to the repository working directory. It refused that
operation after the generator had committed a valid map and its accepted
status. This was a cleanup/bookkeeping error; it was not a failed map draw.

The original failed-wrapper statuses, claims, logs, initial wrapper source and
per-map chronology/hashes are retained under `map-generation/cleanup-incident/`.
The generator controller cooperatively paused before new claims. The repaired
guard resolves each actual directory file descriptor using macOS F_GETPATH
(or the corresponding fd path on Linux); it does not waive writes outside the
extension fence. The same repair is tested in the excluded execution wrapper.

Exactly 16 already committed files were schema/provenance validated and their
byte and canonical hashes recorded, without changing map bytes or invoking
any generator again. Recovery required evidence of the original accepted-status
commit, the retained private cleanup target, exactly the known guard refusal,
and a map commit chronologically before that refusal. Requests without valid
committed files would remain failed; none was replaced or redrawn. Two children had loaded the initial wrapper before the cooperative pause;
their two later commits required the same recovery. The other eight roots
completed with the corrected guard. All 24 saved maps are accepted and unique,
with no prior seed/digest collision and no redraw or planner call.
This changes no map, seed, arm, rule, checker, planner or exclusion.

Recovery summary: `map-generation/cleanup-incident/recovery-summary.json`,
SHA-256 `eebe0a5d1486b1965f3d9c5e73b8e984118f0efeb10628eb9db236fec557860d`. Full per-map evidence is listed there. All opaque private
parent inputs are removed after recovery as registered; the diagnostic evidence
contains identities/hashes only. Final map/seed/digest manifests are pinned in
the launch precommit.

## Development-only plumbing checks

`pilot-attempt-1/` preserves an initial20second outer-wall attempt that reached
its deadline during guard startup before engine entry. Its three-row
accounting remains unchanged and excluded; two unstarted trials remain not_run.
A cooperative STOP marker drained it after a sandbox denied a SIGTERM to our
recorded controller. That denial sent no signal and no unowned PID was targeted.

The corrected check used one existing development map, three real native arms,
CPU expected-answer recordings and90second outer planning walls. It produced
three fresh independent audits and zero harness errors. Both pipeline arms
retained genuine pre-deadline checked incumbents; two-step returned no checked
plan. `dry-run-result.json` binds the original logs/report/rows. These are
plumbing evidence only, never extension pass counts or reader accuracy.
The full extension remains unlaunched and retains its registered budgets.

Second immutable recovery summary (two preloaded children):
`map-generation/cleanup-incident/recovery-summary-2.json`, SHA-256
`eabfcb4b0aa100f2e7dbd22a1265a566b6a5ff571b98aeff4719909d1c8e7f55`. The first14-map
summary and every original failure record remain unchanged.
