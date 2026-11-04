# Industry two-step baseline (task 4.8)

This experiment evaluates the E10/D14 carrier pre-stow followed by terminal box
selection. The carrier chooses slot categories from cargo details without the
yard; terminal adjustment keeps those slot categories fixed. The source is the
expert's written process in [domain.md](../../docs/domain.md#expert-input-provisional-until-confirmed)
and [D14](../../docs/decisions.md#d14--2026-10-27--experts-written-rules-two-step-industry-practice-as-the-baseline-oog-and-special-requests-in-scope).

Run from the repository root:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
UV_CACHE_DIR=/private/tmp/stow-twostep-uv-cache \
uv run --offline --no-sync python experiments/twostep/benchmark.py
```

The runner uses existing task 3.5 full-physics random-yard instances, seed 1 per
rung. It executes one child at a time, pins numerical libraries to one thread,
allows 10 s planning and 5 s ballast finishing, and enforces a 30 s process wall
cap per rung. Calibration/setup and checker work are inside that process cap;
it is not a solver-only timeout. Checkpoints survive a timeout. Each row has
versions, planner metrics, failure diagnostics, input identity, and timings in
[results.json](results.json).

The legal-plan share requires all three: planner status `ok`, full checker pass
after ballast finishing, and every action accepted by the shared feasibility
engine with a complete final replay. Repair is disabled to preserve the measured
baseline. An unavailable feasibility engine is an unmet gate, never a pass.
Objective/lifts/gap are averaged only over plans meeting this gate. Diagnostic
counts for rejected partial plans are stored separately and cannot be interpreted
as plan quality. Gap is `(objective - lower_bound) / objective`, zero for a legal
zero-cost plan. Lift columns are load/discharge, yard relocations, current restow,
current cover, projected restow, projected cover; weights follow D11.

## Results

One instance per rung is a smoke test, not an estimate of population feasibility
or industry performance. `—` means no plan met the checker/mask gate; zero would
incorrectly imply a zero-cost legal plan. The failure stages are reported because
a pre-stow search failure must not be counted as an infeasible terminal assignment.

| Rung | Legal share | Objective | Lifts by type | Gap to LB | LB | Process time (s) | Outcome |
|---|---:|---:|---|---:|---:|---:|---|
| tiny | 0/1 | — | — | — | 2 | 7.73 | cargo-only pre-stow dead end |
| small | 0/1 | — | — | — | 11 | 8.45 | cargo-only pre-stow timeout |
| medium | 0/1 | — | — | — | 45 | 30.01 | process cap during planning |
| full | 0/1 | — | — | — | 84 | 30.01 | process cap during planning |

The final tiny and small planner times were 4.567 s and 6.129 s respectively; neither
reached terminal adjustment. Process time also includes imports and (for tiny)
the oracle probe. Medium exceeded the process cap before returning a planner
result, as did full. Terminal fixed-slot failure frequency is **unmeasured (0 completed
pre-stows)**, not 0%. No result was shipped or admitted as an operational plan.

A direct tiny seed-1 shared-engine diagnostic returned zero initial legal
actions and `DeadEnd(depth=0, reason='No checker-passing completion witness
within deterministic search budget.', rule_ids=('checker.missing_vessel_data',))`
in 1.774 s. This points to an input/model completeness problem rather than
evidence that cargo cannot be stowed. Archived task 3.5 shards are used unchanged;
the benchmark does not synthesize replacement physics or relax the checker.

The strict shared engine currently exposes only the next action of one certified
completion witness. This prevents the planner from exploring alternative certified
slot assignments or box identities in those states. Until the engine exposes
alternatives, these rows are integration evidence and cannot establish a competent
industry baseline. Disabling certification would violate the required G1 replay gate.

## Tiny exact comparison

The repository's `bounds.bruteforce.brute_force_optimum` uses Dijkstra's exhaustive
state search: expand the cheapest accumulated-lift state first, and stop only
when no unexplored state can beat the incumbent. Source: Dijkstra (1959), *A note
on two problems in connexion with graphs*. `status=limit` does not certify an
optimum. Here static rules are enabled, but this existing oracle omits ballast,
stability, strength, visibility and lashing. Its proven optimum would therefore
be an exact **relaxed** optimum, not the full constrained optimum.

A final 1 s/1,000-node probe on the tiny seed-1 instance reached its time
limit after 35 nodes in 1.007 s, with no incumbent. A full constrained optimum and
the two-step difference against it remain unverified. No evidence that joint
planning beats two-step follows from a failed or capped run. Future comparisons
must apply identical hard constraints and the same checker/mask gate to both.

## Compute

Estimate: one CPU core, less than 4 GB RAM, no GPU, at most 120 s for four rungs.
BLAS/OpenMP threads are pinned to one. No cloud calls or credits. This bounded
probe is below the heavy-job threshold; its records stay inside this task's scope.
Measured child process wall time: 74.24 s total (6.71/7.51/30.01/30.01 s).
Final tiny/small verification added 16.18 s (7.73/8.45 s); a concurrent bounded
agent rerun added 16.94 s. Both used one worker, no GPU. Final results merge
tiny/small verification with the original medium/full caps.
Final completed workers reported peak RSS of 106.5 MB (tiny) and 105.8 MB (small);
the two terminated workers did not return peak-memory measurements. No GPU used.
The separate preliminary oracle and tiny-engine diagnostics took about 1.01 s
and 1.77 s respectively, also at one CPU core.

Verification: the command above completed with exit 0 and wrote all four rows;
the child-cap outcomes are failures recorded in the data, not hidden successes.
`uv run --offline --no-sync python -m py_compile experiments/twostep/benchmark.py`
also passed. The executive runs the repository-wide test suite separately.

Final tiny/small command: `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python experiments/twostep/benchmark.py --rungs tiny small --output experiments/twostep/final-smoke.json` (exit 0).

Repository verification: the initial full suite saw two feasibility-interface
failures while task 4.4 replaced its stubs during that running process. A fresh
`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/solver/test_twostep.py tests/test_frozen_interfaces.py`
passed (including all 11 two-step tests). Full-suite rerun estimate: one CPU core,
less than 4 GB RAM, no GPU, 15 minutes. Admission read the live ledger (two
one-core jobs), load averages 7.40/9.43/8.72 and 77% free system memory; `ps` is
sandbox-denied. Compute records remain in this scope because the order forbids
edits to the fleet ledger.

The full-suite rerun finished in approximately 6.5 minutes (estimate 15 min),
exit 1: four failures and three setup errors outside task 4.8. These were the
linear.build_model frozen-body inspection, three fleet hull assembly tests, and
three strength-condition setup tests; the hull failures reported
`HullGeometry.hydrostatic_row() got an unexpected keyword argument 'kg_m'`.
Shared hydrostatics/linear code changed during verification. No out-of-scope
files were modified to suppress these failures. A fresh targeted rerun covers
`tests/solver/test_twostep.py tests/test_frozen_interfaces.py tests/vessel/test_hull.py tests/checker/test_strength_conditions.py`.

That final targeted rerun passed, exit 0 (including all 11 baseline tests). The
shared hydrostatic signature now accepts `kg_m`; the earlier full-suite failure
was not reproduced in the fresh targeted process. The task still does not claim
a clean whole-suite pass, because both full runs overlapped dependency edits.

Proposed leader status: keep task 4.8 partial. The baseline mechanism and smoke
report exist, but certified alternative-action enumeration, complete archived
physics inputs, checker-approved rung plans and a constrained tiny optimum are
required before an industry-performance or joint-planning advantage claim.
Proposed decision note: finite beam search and cargo-moment tie-breaks are labelled
heuristics; fixed slots and all hard-rule gates remain mandatory.
