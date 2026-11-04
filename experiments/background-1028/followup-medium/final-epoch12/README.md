# Final condition-epoch12 measurements

All 156 independently drawn backgrounds pass the real empty-plan ballast
finisher/full-checker gate at all three D57 samples. All 468 securing diagnostics
also pass at each vessel's lashing design GM. The raw denominator is ten draws
per tiny/small cell and six per medium cell, across three ships and both actual
generators. C1 ran; C2 was explicitly off because its separate linear epoch was
stale. No cargo profiles or checker limits were relaxed.

* [final-tables.md](final-tables.md) contains all 18 cells, arrival B2.3/GM/KG
  distributions, ballast, mass headroom, equivalent TEU utilisation and timing.
* [final-audit.json](final-audit.json) verifies 156 unique keys, all booking mass
  and tank audits, source epochs and 468/468 design-GM securing state passes.
* [final-summary.json](final-summary.json),
  [final-state-margins.json](final-state-margins.json), and
  [final-realism-summary.json](final-realism-summary.json) retain distributions.

Tiny/small emits 50/120 independent one-attempt candidates; every cell emits at
least once, with cell rates 10–80%. All 36 independent medium backgrounds pass,
but all 36 fresh one-attempt candidates fail later cargo/C1 screens. The parent
also ran the unchanged normal retry pipeline separately: all six medium cells
emit, with 28/28 background checks passing in 28 candidate attempts. These
dependent attempts are reported separately in [retry-table.md](retry-table.md)
and [retry-summary.json](retry-summary.json), never added to the independent raw
denominator. Gate cost per emitted medium instance is 10.41–95.78 s, below the
120 s target in every cell. Total generation plus independent replay reaches
120.98 s in one cell; that total is not a gate time.

The empty background's witness ballast is distinct from the emitted booking
initializer. All 156 fully booked initial mass budgets fit D40 and design
displacement with at least 344.985 t spare. All reference tank fits are `sized`,
transverse-balanced and capacity-respecting. Largest mass/X/Y residuals are
0.00000478 kg / 0.007261 kg m / 0.000368 kg m. This upright moment fit is labelled
an approximation in the production audit and is not a certificate for an actual
completed cargo plan. The actual full checker remains authoritative.

No tiny/small arrival has beam/draught >=3.3; the largest is 3.215252. All medium
arrivals are >=3.3, with largest 3.442945, below the weather-table applicability
ceiling of 3.5. Accepted medium minimum B2.3 ratio is 1.783630 across the three
states. Worst design-GM securing utilisation is 0.870087 (12.99% capacity
clearance). The six normal retry witnesses independently replay all 18 medium
states; [retry-part-margins.json](retry-part-margins.json) gives worst shear-force
utilisation 0.392998, bending moment 0.510759 and minimum bridge-visibility
distance clearance 78.651465 m, with no negative margins.

The pre-repair random500 medium seed5 numerical failure remains outside this
final grid in the parent directory. It left about 2.45e-12 kg in both APTs,
producing undefined liquid centres. Epoch12 canonicalizes fractions within
1e-12 of an endpoint to exactly zero/one and recomputes sizing residuals. Both
the fresh-process replay and the repeated matrix draw now pass with APT masses
exactly zero. The real-checker regression is in
`tests/vessel/test_background_acceptance.py`.

[paired-tiny-small.json](paired-tiny-small.json) compares the 120 matched draws
with the previous final matrix: identical pipeline/background acceptance and
25,800 bitwise-identical numeric comparisons. The bounded 156-pair
[endpoint comparison](paired-before-endpoint.json) changes only the failed
seed5 acceptance. Maximum arrival differences are 1.60e-14 m GM, 8.88e-16 m KG,
8.44e-15 B2.3 ratio and 1.86e-9 kg displacement. These are paired observations,
not a proof about unmeasured ship conditions.

The run spans four disk-source epochs and six draws span concurrent external
edits. Only `src/stowage/checker/stability_policy.py` and
`src/stowage/checker/stability_policy_contract.json` changed during the grid;
the builder and generators did not change. Exact hash counts are in the audit
and manifests in `../sources/`. Long-lived Python imports are not hot-reloaded,
so disk-source epochs must not be interpreted as imported-module snapshots.

Background TEU is the D42 continuous equivalent representation. Medium minimum
equivalent masses range from 0.460 to 5.711 t by cell and some are below an empty
ISO container's reference mass; the realism JSON counts these explicitly. These
are not claims about physical container tare weights or measured actual-box
occupancy.

Two one-thread workers reserved 2 CPU/6 GB, no GPU. [compute.json](compute.json)
records PIDs71028/71027 and confirmed exit0 sessions85836/62239. Estimated600 s;
actual597.455 s. Peak self RSS2207.3/1874.1 MiB excludes reusable finisher
children. All postprocessing reads saved data and performs no physical reruns.
The parent reports final default suites: vessel453 passed/6 skipped,
generate441 passed. The vessel suite includes all11 acceptance tests.

To reproduce, use the commands in `../README.md` with output paths under a new
directory. Postprocessing additionally accepts:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/followup-medium/audit_results.py --directory experiments/background-1028/followup-medium/final-epoch12
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/followup-medium/compare_prior.py --current experiments/background-1028/followup-medium/final-epoch12/final-random.jsonl experiments/background-1028/followup-medium/final-epoch12/final-simulated.jsonl --out experiments/background-1028/followup-medium/final-epoch12/paired-tiny-small.json
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/followup-medium/derive_retry_margins.py --directory experiments/background-1028/followup-medium/final-epoch12
```
