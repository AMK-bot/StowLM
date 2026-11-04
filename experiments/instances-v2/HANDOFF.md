# Canonical Stage A v2 handoff (4.3)

Complete: **1,160 instances**, 580 per generator, under
`experiments/instances-v2/{random_yard,simulated_yard}/<profile>/`.
Each generator has 200 tiny, 200 small, 100 medium, 40 full, 20 realistic_500,
20 realistic_1000. Every accepted instance has a clean schema/provenance,
checksummed audit, canonical 5.3 spec/key/split and no unresolved DG provisions.
The set contains 117,200 target boxes, including 5,893 DG boxes. Split counts:
615 train / 31 val / 514 test. Held-out families explain the large test share.

**Consume manifest.json and verify each row's SHA-256.** The release content
hash is `46177d447ed2aa7b6aaaf0149d759a44248b98840c782e6f28f0a2eee1d4c33a`.
`release.json` pins the manifest/content, component versions and validation-time
source/parameter hashes. `RESULTS.md` gives every nonzero screening reason/rate,
counts and per-profile timings. Old `experiments/instances/` and
`experiments/instances-sim/` were not edited.

## Scope and assumptions

- Counts were interpreted **per generator**. All three KCS feeders, all four
  port archetypes and controls 0/.2/.8 occur; `.8` matches the held-out-noise
  policy in `src/eval/splits.py`. Random yards use the control as blocking ratio.
- Requested approximately 450 feeder_500 boxes cannot fit 440 cells. The dense
  profiles are **400 dry20 boxes on feeder_500 / 800 dry20 on feeder_1000**.
  Original empty share, gross-mass distribution and DG conditional probabilities
  remain active. These are explicit synthetic equipment-conditioned services,
  not calibrated terminal observations. No physical limit or DG permission was
  changed. The 420-box diagnostic had no weight passes in 30 draws.
- Tiny uses six real bottom cells with original dry20 loaded/empty/DG sampling;
  it is not restricted to empty cargo. DG was accepted in both tiny populations.
- Dense profiles exclude the small-feeder port: its crane reaches only 368/616
  cells on the two ships. Small excludes feeder_500 + small_feeder because its
  48-cell decision region makes 36-box mixed-length draws extraordinarily rare.
  Medium/full use the larger two feeders. Other eligible joint families are
  covered; global coverage still includes all ships/ports/controls.
- Synthetic yard footprints can repeat source blocks; storage, equipment,
  reefer and DG restrictions remain. No claim of real-terminal calibration.
- Retry cap increased from 1,000 to 10,000 after one pilot seed exhausted 1,000
  draws (992 mass failures, 736 IMDG failures). It changes computational effort,
  not demand or feasibility. Successful audits retain their original cap; resume
  allows cap changes only when the accepted draw remains within both limits.
- Screening caches are call-local and exact. Five 800-box calls improved from
  1.20715 to .07689 s; 150 independent adversarial reason comparisons and both
  generators' accepted byte outputs matched. No checks/reasons were omitted.
- Screening is necessary, not sufficient. No loading plans were generated or
  certified; joint packing and the full checker remain solver/5.4 work.

## Verification

All commands use `UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1
OMP_NUM_THREADS=1`; campaigns/suites also set `VECLIB_MAXIMUM_THREADS=1`.

- `uv run python -m stowage.generate.instances_v2 --pilot --workers 1`, resumed
  with `--pilot --workers 2`: five per profile/generator, **60/60**, canonical
  split assignment. `pilot-manifest.json` retains the pilot measurements.
- `uv run python -m stowage.generate.instances_v2 --workers 4`: **1,160/1,160**;
  1,100 generated, 60 resumed; **108.41 s wall**, 456.70 s aggregate CPU.
- `uv run python experiments/instances-v2/validate.py`: all 1,160 checksums,
  schemas, clean provenance, provision screens, unique keys and split identities
  passed; **9.11 s wall**. Validation-time fingerprint metadata was added after
  this run; the manifest digest was rechecked unchanged.
- The production `shard` resume path over `scenario_specs()` checked all 1,160:
  **1,160 resumed / zero generated**, 8.817 s, without rewriting the manifest.
- `uv run pytest -o addopts=--import-mode=importlib -q
  tests/generate/test_instances.py tests/generate/test_instances_cli.py
  tests/generate/test_instances_screen.py tests/generate/test_instances_v2.py
  tests/generate/test_instances_v2_random.py`: **81 passed in 2.14 s**.
- `uv run pytest -q` ran twice: both exit 1 with **17 outside-scope failures**;
  1083.21 s first run, 278.17 s required repeat. See full-suite*.log. Breakdown:
  13 legacy `test_random_yard.py` KCS/rung/tiny integration failures; one linear
  column-shortcut test; two prompt-budget bookings at 450/900 boxes; one IMDG
  radioactive verification-contract test. Their files were not edited.

## Files and remaining integration work

Changed/created source: `generate/instances.py`, `instances_screen.py`,
`instances_v2.py`, `instances_v2_random.py`; corresponding `test_instances*`
files. Added this experiment tree; updated only our fleet ledger/history lines.
The original `random_yard.py`, global versions registry and leader docs were not
edited. V2 wraps the original random generator safely within worker processes.

Full-suite acceptance remains **open**, despite all scoped tests and release
checks passing. Route the legacy random generator's direct KCS tiny/rung support
to 3.5; canonical artifacts already use the corrected adapter. Other failures
belong to linear/format/IMDG owners. The global component registry still records
rule_catalogue='unknown'; the release pins the exact catalogue TOML SHA-256
rather than editing another owner's version API.

Proposed leader plan/evaluation text: canonical v2 materialized (1,160 Stage A
DG-enabled inputs, frozen hashes/splits), 81 scoped tests and all data checks
pass; global suite still has 17 unrelated failures. True feasibility is
unmeasured until solver/checker validation. Proposed approximation entries are
listed above and in README.md. Compute estimates/actuals are in COMPUTE.md and
`experiments/fleet/history.md`. No cloud, GPU jobs, paid calls, commits or pushes.
