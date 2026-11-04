# Canonical instance campaign v2

The `random_yard` and `simulated_yard` trees are independent Stage A sources for
solvers, splits and gold plans. The requested counts are **per generator**:
200 tiny, 200 small, 100 medium, 40 full, 20 realistic_500 and 20 realistic_1000.
The stable five-sample pilot is a prefix of each profile's canonical seed range.

```
UV_CACHE_DIR=$PWD/.uv-cache uv run python -m stowage.generate.instances_v2 --pilot --workers 1
UV_CACHE_DIR=$PWD/.uv-cache nice uv run python -m stowage.generate.instances_v2 --workers 6
```

Run only after compute admission and a ledger reservation. Restrict a measured
chunk with `--profiles full` or `--generators random_yard`. An audit sidecar is a
shard's commit marker; resume checks its full scenario specification, content
hash, clean provenance, target count and exact family tags. An interrupted shard
without an audit regenerates and must match its earlier bytes. Changing a profile
configuration requires a new output location rather than silently replacing a
committed shard, except that `max_attempts` is an operational cap: resume accepts
another cap when the accepted draw count fits it. The original audit config,
instance bytes and audit bytes remain unchanged; manifest rows retain that
original generation config. All other config fields must match exactly.
Old pre-hull instance trees are untouched.

DG remains enabled in every profile, including tiny. Tiny uses six dry20
boxes with the original loaded/empty mixture (20% empty) and active DG sampling;
necessary-condition rejection still applies to unsupported declarations. Medium/full use feeder_1000 and feeder_1700;
tiny/small also cover feeder_500. Each profile covers every joint vessel/port/noise combination allowed for its
vessel set, across four port archetypes and three noise levels (0.0, 0.2, 0.8).
The first five standard-profile scenarios retain their original vessel/port/noise
prefix; tiny is regenerated with DG sampling explicitly enabled. Subsequent
scenarios enumerate all allowed joint combinations. Realistic profiles exclude
small_feeder: its four-tier crane leaves only 368/616 eligible feeder_500/1000
cells, below 400/800 targets. The small rung excludes only the feeder_500 +
small_feeder pair (48 eligible cells make the 36-box mixed-length service
extraordinarily unlikely). Other profiles retain all four port archetypes. The
realistic pilot keeps its first two port choices and cycles the remaining three
allowed ports from scenario three onward. random-yard interprets the noise coordinate as its
requested blocking ratio and records both family fields.

APPROXIMATION: realistic_500 is 400 twenty-foot boxes on feeder_500 (440 physical
cells); realistic_1000 is 800 twenty-foot boxes on feeder_1000 (840 cells). These
explicit dense services condition equipment lengths rather than pretending the
usual mixed-length demand fits. DG and special-cargo screens remain active. The
requested approximate 450 boxes exceed feeder_500's 440-cell absolute capacity.
The measured 30-list diagnostic at 420 boxes averaged 6.56 million kg against
a 5.96 million kg aggregate stack upper bound, with zero weight passes. At 400
boxes, 2/30 passed weight screening and 1/30 also had supported DG provisions.
At 800 boxes on feeder_1000, 11/30 passed weight screening and 1/30 also had
supported provisions. These small diagnostic samples motivate the 400/800
profiles and a default rejection budget of 10,000 candidate draws per instance;
they do not establish feasibility or a precise acceptance rate. DG sampling and
all weight limits remain unchanged. A later pilot exhausted 1,000 draws for
simulated realistic_500 seed 10400002: 992 failed weight, 736 failed IMDG, and
none of its eight weight passes also cleared IMDG. Raising the operational draw
cap preserves the cargo distribution and all physical screening constraints.

Per-profile manifests report candidate draws, overlapping rejection reasons,
fail-closed provision rejection rates and elapsed generation time per accepted
instance. Screening passes are necessary conditions, never full feasibility
certificates. Actual plans still need the full checker. Splits use `src/eval/splits.py` before generation and are checked against the
completed instance family tags. The canonical split specification, its key, test
family label, policy and version appear in each manifest row. Refresh an existing
set with `--finalize-splits`. The base `cargo_profile` remains `feeder_export`;
`equipment_profile` explicitly records dry20_with_dg, dense_20ft or full_mix and
`profile`/`n_boxes` retain the exact campaign scenario. Seeds distinguish these
scenario populations under the current split API.

After generation, `uv run python experiments/instances-v2/validate.py` verifies
all shard hashes, schema references, clean provenance, DG provision screens and
canonical split identities, then pins the content and manifest digests in
`release.json`. This does not certify departure feasibility.
