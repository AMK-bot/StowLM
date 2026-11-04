# Checked ballast repair after a cover-changing move

Entry point: `cover_ballast.repair(instance, plan, deadline, *, incumbent=None,
incumbent_lifts=None, out=None, max_evaluations=12, max_iterations=2,
metadata=None) -> (plan_or_incumbent_or_None, diagnostics)`.

Supply current-process concrete plans and absolute `time.monotonic()` deadline.
A supplied incumbent is independently checked and provides the true lift
threshold. Without its object, `incumbent_lifts` is required and rejection
returns `None`. With an incumbent, every rejection returns that exact object.
No earlier plan, assignment, ballast or recipe is read.

The candidate must already be replay legal, complete and cheaper. The only
eligible hard violations are `iscode.loading.trim`, `iscode.loading.draft` and
`port.call.draft`. Unknown IDs and umbrella cargo-operation failures are
rejected, even if their text mentions draft. Every returned improvement passes
a fresh ordinary full checker, has every box aboard and strictly fewer lifts
than the checked incumbent. No rule is peeled.

The numerical method adapts `c4/c2-layout/ballast_opt.py`: finite differences of
production physics, a small trust-region LP, then exact trial evaluations.
Contrary to an initial suspicion, its `stability` part already includes both
signed trim bounds, aft/fwd and minimum forward draught, and port draught in all
three ordinary voyage conditions. The new repair targets those loading margins
and keeps every other named stability/securing margin hard in the LP. Actual
nonlinear trial margins must also preserve those conditions; no linearized
model is accepted as a checker certificate. Strength, visibility, operational
conditions and every other rule still enter the final unchanged full check.

The original optimizer favours DB-F, DB-M, FWD-DEEP and HEEL tank groups. For a
longitudinal cargo move this repair instead includes both longitudinal extremes
and then high-capacity groups, up to four. Each paired group fills together.
The 98%-pressed liquid regime and one-sided finite difference are retained
from c4. Bounded local failure is only 'no plan found', never infeasibility.
All numeric solvers stay single-threaded; the root executive owns CPU admission.
A six-second cooperative reserve remains for the final checker. A native
physics evaluation can exceed the deadline, so wall time must be reported.

Outputs default to `c6/c6-lifts/cover-ballast/<instance>-<current-candidate-hash>/`;
custom paths must resolve inside this experiment. The vessel cache is redirected
here during repair and the previous environment value is restored afterwards.
`trace.json` contains scalar search diagnostics only. Eight focused tests mock
physics/optimization to exercise rule gates, strict lift improvement, checker
rejection, exact incumbent fallback, exceptions, output fencing and completeness.
Verification: all eight controls passed in 0.35 s under the granted serial test slot.
The `nice -n 5` request was denied by the sandbox, consistently with prior runs.
No numerical map job was started by this subagent.

## Five shared f60 maps, scalar evidence only

These IDs exactly match the requested engine/LP lift pairs. Engine evidence is
`lab/c1/leader/bench-0.12.0/m0/` for rows 1 and 3 and `m1/` for rows 2, 4 and 5.
LP evidence is `lab/c5/c5-securing/results/frozen-48/`. Both scalar rows say
`passed=true`. These archived scalars identify maps; no archived plan is supplied
to the repair or any cold solver.

| Instance ID | Engine | LP |
| --- | ---: | ---: |
| D192.medium-feeder_500-f60-high-s192200000 | 71 | 77 |
| D192.medium-feeder_500-f60-high-s192200002 | 70 | 74 |
| D192.medium-feeder_500-f60-low-s192200000 | 63 | 73 |
| D192.medium-feeder_800-f60-low-s192200101 | 104 | 120 |
| D192.medium-feeder_800-f60-low-s192200102 | 100 | 112 |
