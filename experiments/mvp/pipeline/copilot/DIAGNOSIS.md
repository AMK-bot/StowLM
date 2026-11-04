# Native execution diagnosis

The two interrupted native replan attempts do not demonstrate a binding
serialization defect. Neither returned a checked plan. The default attempt ran
for more than sixteen minutes; the constructor attempt ran for more than five.
Their receipts remain in `fixtures/interrupted-real-attempt.json` and
`fixtures/interrupted-smoke-attempt.json`.

The constructor traceback in `real-smoke-run.log` ends in `KeyboardInterrupt`
inside `stowage.checker.stability_liquid.metadata`, while `json.dumps(inputs)`
was preparing geometry metadata for hashing. This identifies where execution
was interrupted; it is not a serialization exception or proof of a hang there.
`binding.execute_brief` had reached the real engine and full checker.

A later, separate default-binding probe found a real copilot adapter defect:
the landed `binding.execute_brief` requires `ConfirmedBrief`, while the copilot
had only retained a host-confirmed reading ID and supplied plain `ReadBrief`.
The scripted probe failed in about two seconds with `ValueError`; a direct
background probe returned the same class of error in 0.295 seconds. This was
receipt validation before native planning, not serialization or search-budget
validation. A cheap validator regression forbids native `plan_with_rules`, so
the failure and repair do not rely on a native run.

Runtime7 fixes the default adapter: an explicit host `confirm_reading` event
seals a separate capability with `binding.confirm_brief`, named
`copilot_planner_confirmed:<reading_id>`. The detached worker validates that
capability and passes it to binding; job identity also includes it. Public IDs
and injected-replanner `ReadBrief` receipts are unchanged. Four new red-to-green
regressions and 126 offline tool/job/async/probe tests passed. The v2 native probe
against that adapter completed; its results appear below. The tuned 42 and frozen fresh 15
model campaigns precede this adapter-only change; prompt, authorization and
safety policy are unchanged, and no post-fix live campaign is claimed.

## Measurements that completed

These are separate measurements, not timings for either interrupted replan.

| Operation | Measured time | Evidence and scope |
| --- | ---: | --- |
| Binding `_set_verdict` | 0.002966 s | Replayed `fixtures/agency-native/baseline-report.json` through `CheckReport.model_validate` and the actual binding helper, using synthetic helper arguments. |
| Checker evidence JSON encoding | 0.006613 s | `json.dumps(evidence['checker'], sort_keys=True, allow_nan=False)` produced 364,779 UTF-8 bytes. All 23 checker parts and the actual passed verdict, count, violations, part names and `ran` flags were preserved. |
| Vessel parameter loading | 1.078136 s | Loaded the vessel named by `fixtures/agency-native/instance.json` through `stowage.vessel.fleet.load_ship_params`. |
| Stability metadata hashing | 0.263328 s | Actual `stability_liquid.metadata(params)` on those saved baseline vessel parameters; repeated metadata digests matched. These were not captured in-flight parameters from the interrupted smoke run. |
| Baseline full checker | 325.363531 s | `fixtures/agency-native/baseline-timing.json`; all checker parts ran. |
| Baseline CPU admission wait | 249.360696 s | Separate admission timing in that same artifact. `prepare_native.py` starts the checker timer after admission, so the checker time excludes this wait. |

The cheap binding replay and metadata measurement did not run native planning
or a new full checker. They show that the saved report can cross the binding
and JSON boundary quickly, and that metadata hashing can complete for the saved
vessel. They do not isolate the dominant cost in the interrupted runs. The full
checker measurement establishes that a real baseline check completed, with
substantial checker time as well as a separate admission wait.

## Current limit

The final [native background probe](fixtures/binding-background-probe-v2/result.json)
now passes the landed confirmation boundary. Its real binding stage ablation
returned `no_checked_plan`, without accepting the existing baseline. Replan
returned in 0.568 seconds; immediate and terminal status turns took 0.330 and
0.531 seconds. Worker elapsed time was 1.451 seconds; 18.542 seconds of host
observation includes the polling interval. This is a scripted conversation with
optional engine stages disabled, not successful production search or a Spark
behavior score. The earlier failed probe remains preserved separately.

Background `replan`/`try_ladder` jobs return a running job reference without
waiting for native completion. `wait_replan` polls without blocking, reports
real elapsed time, and publishes a result only after the full checker and
instruction audit. Tests cover nonblocking publication and stale-job rejection.
This keeps the copilot responsive; it does not establish that a default native
replan completes within an interactive latency budget. Fresh successful native
replan completion and its latency remain unproved by these interrupted attempts.

Engine stage budgets are not total wall-time limits: admission, small-map stage
floors and atomic native checker calls can extend elapsed time. The next useful
backend evidence is stage-level timing from a completed, admitted native replan,
rather than attributing the interruption location to a binding bug.
