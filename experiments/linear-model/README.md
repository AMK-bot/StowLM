# Current method-1 signed model study

See [signed-family/report.md](signed-family/report.md) for the reusable model 3.0 design, direct timing pilots, and table-qualification status. Prior method-2 and model2.x results below are historical, not the current requested reference.

# Linear hydrostatic model validation

The current production KCS study uses linear model **2.3**, actual section tanks,
Gauss-weighted liquid loads and production strength envelopes. Its status,
input hashes, error table and cache verification are in
[kcs-production/report.md](kcs-production/report.md).
Use that report's measured reserve guidance for these production cases.

The material below records historical runs and superseded assumptions; it does
not describe the current production geometry or its measured accuracy.

**Legacy results:** `results.json`, `results.md`, `calibrated-errors.md` and
`benchmark.json` below describe model version 1.0: broad variable-cargo bands,
a global high-cube surrogate and artificial strength envelopes. Their 0.44–0.67 m
GM errors are historical, not the final instance-specific model. Version 2.0
uses a fixed manifest, physical box sampling, production strength stations and
content-addressed disk caching. See [narrow-report.md](narrow-report.md) for the
new nine-case study and updated benchmarks; its pilot results remain explicitly
labelled until the full study completes.

Run with a single CPU thread (no GPU):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 MKL_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/linear-model/validate.py --samples 256 --seed 3702
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 MKL_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q tests/linear
```

The script records displacement/draught/trim/heel validity bands, measured maximum
and 99th-percentile absolute errors for GM, trim, list, draught and every SF/BM
station. The first calibration used seed 3701 (64 conditions). A second batch used
seed 3702 (128 conditions) and exceeded that envelope. Production now incorporates
the componentwise maximum over all 192 measured conditions. The second batch is
calibration enrichment, no longer held-out validation. The true p99 is retained
for each batch separately; `calibrated-errors.md` reports final maxima with the
128-condition batch p99. `results.json` retains the original 64-condition maxima,
the enrichment diagnostics and timing, and the final 192-condition maxima.
`results.json` is written after each vessel to preserve completed shards.
`--ship feeder_500 --calibration-samples 1 --samples 4 --output experiments/linear-model/pilot.json`
is the pilot command; measure this before reserving a larger run.

The context comes from the shared model fixture builder. Its placeholder vessel
is replaced with a complete fleet vessel, including 2.1's numerical hull geometry.
Random cargo is represented by physical-cell masses and random ballast fills;
this tests the mass approximation rather than container-placement legality.
Forty-foot mass splitting has a separate executable test. The feasible LP test
adds a declared synthetic fixed cargo of 400,000 kg at x=0.7 Lpp, y=0, z=2 m.
The unladen original cannot meet minimum forward draught with its ballast
capacity; this fixture addition retains all fleet operating limits. Complete
vessels are cached in the system temporary directory with a digest of their
parameter files and every hull/tank/model builder source file.

APPROXIMATION: fleet hull assembly currently supplies no production strength
stations/permissible envelopes. Validation explicitly injects nine stations and
broad test-only limits (±10^10 N, ±10^12 N m). SF/BM *errors* at these stations
are meaningful; satisfying these artificial permissibles does not demonstrate
production strength safety. Production model construction rejects absent
strength stations. The exact checker remains the judge for every final plan.

The linear model is local: empirical sampled maxima are not a proof of a uniform
error bound over every possible loading. Users must stay inside the band, reserve
more than the final reported errors (for example `margin_scale=1.1`) and run the
exact checker. That reserve is an empirical policy, not a uniform error proof. Geometry discretisation
and the realism of the synthetic fleet are separate from linearisation error;
see `experiments/hydrostatics/README.md`. Sources for the mass-moment method and
free-surface correction are documented in `src/stowage/linear/physics.py`.

## Execution record

The first pilot imported the unimplemented builder before it landed and failed
with `NotImplementedError` after full-vessel assembly. A second pilot was stopped
while computing calibration errors when the exact 3.1/3.2 reference landed, to
avoid publishing measurements against the superseded reference. Both used one
CPU thread; no cloud or GPU compute was used. Neither produced accepted results.

A corrected one-sample calibration/four-sample validation pilot completed in
7.939 seconds with the full vessel cached (load 0.028 s, build 5.116 s). It
accepted 4 of 14 draws. The first complete feeder_500 hull assembly took
45.923 seconds. Subsequent source caching improved exact-reference performance;
final result files retain the timings of the code version each worker imported.


## Final sweep results

The production model originally calibrated 64 conditions. The three 128-condition
shards completed on at most two CPU threads in parallel, with no GPU. Their
recorded times include original calibration; final production calibration now
includes both batches and therefore takes longer. Warm model-cache lookup still validates/fingerprints the inputs (0.227 s in the
final benchmark). Cold model construction is tens of seconds; it does not meet
a low-single-digit-second interpretation of the performance target.

| Ship | Full hull load (s) | Original 64-condition build (s) | 128-condition sweep (s) | Accepted / attempted |
|---|---:|---:|---:|---:|
| feeder_500 | 0.022 (cached; cold 45.923) | 25.824 | 47.908 | 128 / 632 |
| feeder_1000 | 43.251 | 44.488 | 64.232 | 128 / 842 |
| feeder_1700 | 45.951 | 32.957 | 52.809 | 128 / 520 |

Estimate was two cores, <2 GB, no GPU, eight minutes for all shards after the
one-thread pilot. Actual shard wall times were 73.755, 151.972 and 131.719 seconds;
the two-worker schedule finished within that reservation. The first two workers
imported the pre-optimization reference, while feeder_500 used the semantically
identical parameter/geometry cache optimization. Peak RAM was not instrumented.

Final measured maxima and p99 per ship are in [calibrated-errors.md](calibrated-errors.md).
Every station's SF/BM values, stated band, random seed and original timing are in
[results.json](results.json). There are no production fleet strength envelopes:
the nine test stations only validate the approximation, not permissible strength.

## Safety context and exact gate

Nonempty `iscode.*`, `iacs.*` and `port.call.draft*` rule overrides are rejected
by model construction/evaluation/LP. Supply supported safety changes explicitly
as vessel/port limits and rebuild the model. A stricter port draught limit or a
changed fixed tank condition also requires rebuilding; mismatched density is
rejected. The GM requirement uses at least the catalogue minimum. Unsupported
safety settings therefore fail closed rather than silently disappearing from
the LP. An LP solution still requires the exact checker, including requirements
such as GZ/weather criteria that this linear model does not enforce.


## Final production performance

Reproduce using the single-thread environment above:

```sh
uv run python experiments/linear-model/benchmark.py
```

Measured on feeder_500 with 400 t declared fixed cargo, four forward fixture
boxes and `margin_scale=1.1`; full vessel assembly was already cached. The model
used the final 192-condition calibration. [benchmark.json](benchmark.json) retains
full timings and repetition counts.

| Operation | First call | Subsequent measurement |
|---|---:|---:|
| Cold context + model build | 39.428 s | — |
| Warm model-cache lookup | 227.361 ms | 1 call |
| Evaluate loading | 3.7065 ms | 0.1817 ms mean of 100 |
| Construct constraint matrices | 0.2395 ms | 0.1184 ms mean of 100 |
| Ballast LP | 3.1787 ms | 1.8995 ms mean of 3 |

The cold build timing includes the small cached-vessel fixture setup and no prior
linear-model cache. The LP was optimal with 98,837.814 kg ballast. Total benchmark
wall time was 39.729 s, versus the single-core, <2 min estimate; no GPU was used.
Peak RAM was not instrumented. Evaluation meets its <1 ms target after first-call
initialisation; constructing reusable constraints is cheap. Model calibration
remains the cold-build bottleneck and full hull assembly has its separate cost.

## Final verification

`UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run pytest -q -o addopts=--import-mode=importlib`
completed with **1285 passed, 7 skipped in 259.64 seconds**. The output is
[full-suite.txt](full-suite.txt); process status and wall time are in
[full-suite-status.json](full-suite-status.json). All 17 linear tests passed.
The initial full-suite failures in catalogue/IMDG/provenance were resolved by
their owners during this session; no out-of-scope fixes were made here.
Final full-suite reservation: one core, estimated 1 GB RAM, no GPU, 15 minutes;
actual wall approximately 260 seconds, peak RAM not instrumented.
