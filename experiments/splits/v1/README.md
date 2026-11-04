# Frozen specification split v1

Task 5.3 allocates **9,400 instance specifications before solving**: 9,000 train,
100 validation, and 300 test. This manifest contains generation instructions,
not existing instances or checker-approved gold plans. Never drop a failed draw
or move it to another split based on solver success: record generation/solver
failures against its frozen specification. A gold-plan runner must still use
the full checker before admitting a plan to SFT.

The split unit is one complete instance specification. It includes generator
name, rung, vessel preset, slice preset, port archetype, noise, blocking ratio,
seed, and cargo profile. File paths, serialized vessel data, and all component
versions are excluded. Thus rebuilding vessel physics preserves allocation.
The exact identity and version-independent generator adapters are implemented
in [`src/eval/splits.py`](../../../src/eval/splits.py); the stable generator names
are `random_yard` and `simulated`, not their component-version strings.

## Test families

- **Held-out slice:** the composite `feeder_1700` / `medium` decision region.
  Every such specification is test, for either generator. Other slices of all
  three feeders remain in training and validation. Holding out the entire
  1,700 TEU vessel would deprive the model of that layout; this combination
  instead measures transfer to an unseen vessel-and-slice family while retaining
  layout exposure. It does **not** measure a wholly unseen hull or slice preset.
- **Held-out port:** `us_west_coast`, for either generator. Training/validation
  use `laem_chabang_like`, `singapore_like`, and `small_feeder`.
- **Held-out noise:** simulated snapshots at noise `0.8`. Training/validation
  simulated noise values are `0.0`, `0.2`, `0.5`; random-yard blocking ratios are
  `0.0`, `0.3`, `0.6`. Blocking and simulator noise are different controls;
  random-yard noise is null and simulated blocking is null.

All held-out predicates take precedence over the hash assignment, so no seed
can put one into train or validation. Overlapping held-out predicates receive
joined labels in the API. The frozen sample uses isolated held-outs for clear
attribution, and does not claim joint-shift measurement. The remaining test
specifications share training families but have unseen specification seeds;
report this **in-distribution** part separately from each held-out part. Never
select model settings using these test results. Validation exists for that
purpose. The split practice follows the train/validation/test distinction in
[scikit-learn's cross-validation documentation](https://scikit-learn.org/stable/modules/cross_validation.html).
Synthetic port and noise values are scenario choices, not calibrated traffic.

## Counts and SFT budget

| Rung | Train | Validation | Test ID | Test slice | Test port | Test noise | Test total | Train tokens/example | Train tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tiny | 1,500 | 25 | 45 | 0 | 15 | 15 | 75 | 556 | 834,000 |
| small | 1,500 | 25 | 45 | 0 | 15 | 15 | 75 | 1,160 | 1,740,000 |
| medium | 2,500 | 25 | 30 | 15 | 15 | 15 | 75 | 3,538 | 8,845,000 |
| full | 3,500 | 25 | 45 | 0 | 15 | 15 | 75 | 6,140 | 21,490,000 |
| **Total** | **9,000** | **100** | **165** | **15** | **60** | **60** | **300** | | **32,909,000** |

The train allocation favors medium/full decisions while keeping enough tiny
and small instances for curriculum learning. The one-epoch SFT estimate is
**32.909 million tokens**, within the requested 30–40 million budget. Validation
and test add 1.1394 million tokens if serialized once; they are not SFT training
tokens. Multiple training epochs multiply the train budget. The 15-instance
slice panel is intentionally small; report uncertainty and avoid broad claims
from it. Counts are proposed gold-plan targets, not a guarantee of feasibility
or solver throughput.

Token sizes come from the seed-1 measured persisted ladder in
[`experiments/format/README.md`](../../format/README.md), not measured averages
over this manifest. **Unverified extrapolation:** the same per-rung size is
applied to every feeder, port, noise setting, and both generators; prompt length
and rejection rates may vary. The 450/900-box extended cases (9,063/17,555 tokens)
need generator overrides absent from this specification schema and are excluded
from v1. `full` means the persisted 280-load ladder rung, not a full-capacity
1,000- or 1,700-TEU ship. These counts supersede the older 3,000-example/7.5M-token
planning assumption in [`docs/compute.md`](../../../docs/compute.md), but do not
establish training time. No solve-time or GPU benchmark was performed here.

## Determinism, freezing, and consumers

For non-held-out specifications, SHA256 hashes canonical JSON with salt
`stowlm-splits-v1:20261027` into 10,000 buckets: train below 9,000; validation
below 9,500; otherwise test. The allocation seed is **20261027**. This 90/5/5
rule assigns arbitrary future specifications; it does not require the frozen
budget to have those proportions. The manifest selects exact quotas from
ascending seeds and a fixed vessel × port × generator-setting product order.
Different random seeds refer to independent specification draws; identical
seed integers in different families are not identical specifications.

The authoritative inventory is [`manifest.json`](manifest.json). It records
every spec, split, test-family label, counts by rung/split/test family, exact
generation-family counts, and a SHA256 of the canonical ordered entries.
Train/validation labels are null; their count label is `in_distribution`.
The independently pinned canonical entries SHA256 for this release is
`b776af130fdeee71e622d08c535e5fdfdb30053ae52b843042ca817ebd8f143c`.
`split_of(spec)` can assign specifications outside the inventory, but the
gold-plan runner and evaluation harness should use the frozen manifest entries
to define this particular experiment. `load_manifest()` validates checksum,
counts, uniqueness, and policy membership before a consumer uses it.

Reproduce from the repository root (one core, no GPU, no generation/solving):

```sh
UV_CACHE_DIR=/private/tmp/stow-splits-uv-cache uv run --offline --no-sync python experiments/splits/freeze.py --check
UV_CACHE_DIR=/private/tmp/stow-splits-uv-cache uv run --offline --no-sync pytest -q tests/eval
```

`--write` reproduces the artifact explicitly. Do not mutate v1 to accommodate
changed physics, rejected instances, solver outcomes, or new sample budgets.
Freeze a new directory/version for any policy or inventory change, and retain
v1 so results remain comparable. Generation retries internal to a generator
remain part of that specification; any changed generator output still uses the
same split. An explicit `no_dg` cargo profile is a different specification;
v1 uses the standard `feeder_export` profile throughout.
