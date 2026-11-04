# Ballast D30 remeasurement

Run the production cache warmer successfully before measuring:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python -m stowage.cache_warm --workers 1
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/d30/review.py --check --out experiments/ballast/d30/literal.json --watchdog 180
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/d30/slices.py --out experiments/ballast/d30/slices_results
uv run python experiments/ballast/d30/summarize.py
```

The completed leader-admitted campaign is described in [report.md](report.md),
with every call in [measurements.md](measurements.md). The optional manual
variant is available with `review.py --manual --check --out .../manual.json`;
it was not run in this campaign.

The literal nine conditions use the reviewer's `harness.build_condition` and
production ships. The only source adapter changes historical tuple iteration of
`FixedLoad` to its schema 1.3 `mass_kg` field. The optional manual-budget group
uses the reviewer's actual `after_run.condition_context` and `after.json`
ballast/draught-reserve recipes, without applying the old ship-design overrides.
It resets adjustable ballast to zero before calling the finisher; the declared
ballast is separately checked as a witness and never supplied as an optimizer
seed. A failing witness is not evidence of infeasibility.

The runner checks the cache-warm summary is complete, retains its SHA256,
and uses the warmed production vessel and linear caches. This summary guard
does not revalidate certificates; the production checker validates cache
identity itself. Witness part metrics record KN/liquid-table usage and direct
fallbacks. Near-boundary conditions may intentionally use direct physics.

Every condition records first/second finisher calls separately; the second call
has warm disk caches. Process caches can remain cold after a watchdog timeout,
because the finisher must discard its stalled child. Each record includes the
worker PIDs and whether the isolated worker survived and was reused. The budget
is five seconds; construction, witness/full
checks and initial imports are timed separately. The supervisor only stops its
directly created, recorded child process on its external watchdog. Check records
for errors or incomplete conditions before computing success rates.

New condition snapshots retain source-content and vessel-content SHA256 values;
they never use file dates as physical identity. Old experiment results and
snapshots are retained untouched. Historical snapshots lack trustworthy source
content hashes, so they are deliberately not promoted to D30 snapshots.

Admit warm-up and benchmarks under the fleet CPU/RAM ledger before running
these commands. The completed campaign used the leader's explicit one-CPU /
one-GB admission override and kept all physics jobs serial.
