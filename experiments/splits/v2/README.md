# Frozen specification split v2

This replacement release contains **9,400 generation specifications**: 9,000
train, 100 validation, and 300 test. It is ready for the leader's coordinated
regeneration after the fix wave; no instances or plans were regenerated here.
An allocation is not evidence of generation success, feasibility, or checker
approval. Record failures against the allocated specification; never replace a
failed test draw with a successful training draw or choose labels after solving.

The independently pinned canonical ordered-entries SHA256 is:

`7fefd88f6afda2f185e3b252e61e831fc3fe09972c1ec82b2776b6f841f821f4`

`src/eval/splits_inventory.py` pins that checksum and canonical policy metadata
independently of this file and `manifest.json`. Loaders reject a coherently edited
inventory even when its own checksums and count summaries have been recomputed.
The historical v1 manifest and README are preserved unchanged for audit. V1 has
known correlated candidate streams and must not be described as draw-disjoint.

## What changed

Every v2 specification explicitly records identity version, requested `n_boxes`,
`dense_20ft`, `tiny_dg_enabled`, `equipment_profile`, and `without_dg`, alongside
the original generator, rung, vessel, slice, port, noise/blocking, seed and cargo
profile. `n_boxes: null` explicitly requests the current D36 regional booking
policy. It does not request the historical ladder's diagnostic box ceiling.
Realised quantity belongs to the generation audit and is not substituted into
this requested identity. Tiny uses `dry20_with_dg`; other rungs use `full_mix`.
This release uses `feeder_export`, while the versioned identity also supports
explicit `no_dg`, simulated tiny `empty_dry20`, and supported dense conditioning.

The draw group is conservatively the seed alone, across **both** generator
families and every scenario/quantity/equipment variant. A draw group is a set of
requests that might share random inputs; keeping it in one partition prevents
information from a related training draw contaminating a test measurement.
Non-held-out v2 policy labels hash that group with
`stowlm-splits-v2:20261027` into 10,000 buckets (train below 9,000, validation
below 9,500, otherwise test). Complete identity hashes still distinguish all
configuration controls. Held-out predicates override that policy. Validation of
the actual release separately checks that no draw group crosses partitions.

The freeze avoids even correlated variants within one partition: **each of its
9,400 selected specifications has a globally unique seed**, including random
and simulated families. Candidate seeds are
`2**48 + family_index*2**40 + rung_index*2**32 + candidate_index`.
Family order is `in_distribution`, `heldout_port`, `heldout_noise`,
`heldout_slice`; rung order is `tiny`, `small`, `medium`, `full`.
Candidate index advances through the fixed vessel × port × generator-setting
product before repeating that product. Each family/rung reserves `2**32` seeds;
exhaustion raises instead of entering the next reservation. The same
in-distribution candidate stream is filtered by policy to fill train, validation
and test quotas. Thus a selected seed cannot be reused by another partition or
held-out family, even for a generator that seeds cargo before scenario controls.

The current random-yard producer's initial cargo attempt uses
`seed*1_000_000 + attempt`. **Regeneration must cap random-yard `max_attempts`
at 1,000,000 or less** (normal default: 200), and record the actual cap in each
run/shard audit and resume fingerprint. Under that cap, unique frozen seeds give
disjoint inclusive initial-cargo retry ranges. A larger cap can enter another
specification's range and voids the release's independence guarantee; it must
not be used even though the producer currently accepts it. The simulated
producer and individual random shipment redraws use purpose-specific hashed
streams; no claim of mathematically impossible hash collisions is made. Runtime
cap enforcement belongs to the generating runner, outside this inventory fence.

## Allocation and claims

| Rung | Train | Validation | Test ID | Test slice | Test port | Test noise |
|---|---:|---:|---:|---:|---:|---:|
| tiny | 1,500 | 25 | 45 | 0 | 15 | 15 |
| small | 1,500 | 25 | 45 | 0 | 15 | 15 |
| medium | 2,500 | 25 | 30 | 15 | 15 | 15 |
| full | 3,500 | 25 | 45 | 0 | 15 | 15 |
| Total | 9,000 | 100 | 165 | 15 | 60 | 60 |

Held-out port is `us_west_coast`; held-out simulated noise is `0.8`; the held-out
slice is the `feeder_1700`/`medium` combination. Training sees every vessel at
other rungs. This measures an unseen combination, port and noise level, not an
entire unseen vessel. Isolated held-out panels avoid claiming a joint-shift
measurement. Fifteen slice examples support only a small diagnostic panel;
report uncertainty and the panels separately. Scenario noise and ports remain
synthetic, not calibrated operating traffic. No token-budget or solver-throughput
claim is carried forward from v1: D36 changed quantities, so measure the new
realised data before scheduling training.

## Reproduce and adopt

From the repository root (one CPU, no GPU, no generation or solving):

```sh
UV_CACHE_DIR=/private/tmp/fix-t5-splits-uv uv run --offline --no-sync python experiments/splits/freeze.py --version v2 --check
UV_CACHE_DIR=/private/tmp/fix-t5-splits-uv uv run --offline --no-sync python experiments/splits/freeze.py --version v1 --check
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 UV_CACHE_DIR=/private/tmp/fix-t5-splits-uv nice -n 10 uv run --offline --no-sync pytest -q -p no:cacheprovider tests/eval/test_splits*.py
```

`--write` deterministically writes the requested specification manifest; it does
not generate instances. Retain published releases and introduce a new release
for changed inventory or policy, rather than editing a release to fit outcomes.

Generation runners must iterate this inventory, rather than inventing campaign
specifications and checking only `split_of`. The exact membership API is:

```python
from eval.splits import InstanceSpec, spec_from_instance, spec_key
from eval.splits_inventory import load_inventory

inventory = load_inventory("experiments/splits/v2/manifest.json")
release = {"split_version": inventory.version,
           "split_entries_sha256": inventory.entries_sha256}
for allocated in inventory.manifest["entries"]:
    spec = InstanceSpec(**allocated["spec"])
    entry = inventory.require_membership(spec, split=allocated["split"])
    # Persist release, entry['spec'], spec_key(spec), and entry['split'] before
    # generation/solving. Retain failed/rejected shard audits at the same key.
```

For a generated instance, require both full identity agreement and allocated
membership before admitting it to gold/evaluation:

```python
actual = spec_from_instance(instance)
entry = inventory.require_membership(actual, split=recorded_split)
assert spec_key(actual) == recorded_spec_key
# Persist release in run, input, plan, and resume metadata; refuse checksum drift.
```

`membership_label(spec)` looks up an allocated label and raises for nonmembers;
`split_of(spec)` assigns an arbitrary future policy label and does not prove
membership. Full-checker and mask replay requirements continue to apply before
publishing gold plans. Never erase explicit conditioning from generated family
tags to make a nonmember match.

The producer calls for this release are `generate_simulated_instance` from
`stowage.generate.instances` and `generate_random_v2` from
`stowage.generate.instances_v2_random`. Both accept `rung` as the first `size`
argument and keyword `seed`, `vessel_name=vessel`, `port_name=port`,
`n_boxes`, `dense_20ft`, `tiny_dg_enabled`, plus a retained generation `report`
and explicitly recorded `max_attempts` (use 200; random must remain <=1,000,000). Simulated adds `noise=spec.noise` and
`without_dg=spec.without_dg`; random adapter adds
`noise=spec.blocking_ratio` (its compatibility parameter names blocking as
noise). The random release uses `without_dg=False`; that producer has no
without-DG argument. Do not pass `equipment_profile` or `cargo_profile` as
unsupported producer keywords: they describe and validate the controls already
passed. Compare `spec_from_instance` to the allocated identity after generation.
The random adapter changes module globals temporarily, so do not call it in
parallel threads; isolate workers in processes if concurrency is needed.

Regeneration must pin cargo 3.1.0, random-yard 2.0.0, simulator 1.1.0, move engine
2.0, schema 1.6 and the current vessel contracts, plus actual runtime/source and
parameter hashes. Component versions belong to artifact provenance rather than
the allocation identity. A regenerated physical output can keep its allocation,
but it is not interchangeable with an output produced by different physics.
No cached physics content changes in this release, so no D30 warm-up is required
by this freeze itself.

**Pending outside this fence:** `src/eval/harness.py`,
`experiments/gold-plans/tiny/driver.py`, and campaign producers/consumers must
adopt this membership lookup, preserve the full v2 requested identity, record
`split_version` and `split_entries_sha256`, include the pin in resume checks, and enforce the random retry cap above.
Existing historical v3 campaign shards have not been migrated or regenerated
and are not evidence of v2 membership. The leader owns these caller edits and
the coordinated regeneration; this manifest alone cannot enforce those paths.
