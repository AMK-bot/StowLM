> **SUPERSEDED — NEVER USED.** `dg-stress-v1` was superseded by `dg-stress-v2` before any generation. Nothing selects this release: active loaders and run/epoch paths reject it, including explicit manifest paths. The unchanged manifest and the remaining historical README are retained for audit only.

# Frozen DG-stress specification release v1

This separate D70 stress test release allocates **15 tiny and 15 small test
specifications**. It measures performance under deliberately concentrated
hazardous cargo, including classes 4.1, 4.3 and 9 and segregation demands that
are rare in the main export mix. It contains no training or validation specs.
The main `v2` release remains unchanged. **Report DG-stress results separately;
never pool them with v2 results.**

The independently pinned canonical ordered-entries SHA256 is:

`f017ef49b54d0f3ddcec26402eb0ee745bda13073a91dda7273069be044baf21`

The release is an allocation freeze only. No instances, admitted cargo, feasible
plans or binding segregation pairs are claimed by this manifest. The checker
remains the source of truth for every physical and DG constraint.

## Selection before generation

Selection is a fixed rung, vessel, generator-setting order; it never examines
generated cargo, feasibility, solver success or checker outcomes. Each rung
uses the following five cells for **each** of `feeder_500`, `feeder_800` and
`feeder_1700`:

| Generator | Port | Noise | Blocking ratio |
|---|---|---:|---:|
| random yard | Laem Chabang-like | — | 0.2 |
| random yard | Singapore-like | — | 0.5 |
| simulated | Laem Chabang-like | 0.0 | — |
| simulated | Singapore-like | 0.2 | — |
| simulated | Laem Chabang-like | 0.5 | — |

Equal vessel counts prevent the panel from becoming a single-layout diagnostic.
The two random cells and three simulated cells cover both yard mechanisms and
multiple difficulty controls within a 15-spec budget. Laem Chabang-like gets
the additional simulated cell because it is the synthetic export reference;
Singapore-like still appears under both mechanisms. This is a deliberate
coverage mix, not an estimate of traffic proportions. No medium/full allocation
or separate port/noise holdout claim is made here.

Every specification names `cargo_profile: dg_stress`, the parameter key for
[`params/cargo/dg_stress.toml`](../../../params/cargo/dg_stress.toml) (profile ID
`synthetic_dg_stress_d70`). APPROXIMATION: this profile deliberately raises the
DG share to all loaded dry 20ft boxes, with ten equal supported-class shares
(2.1, 2.2, 2.3, 3, 4.1, 4.3, 6.1, 8, 9, 1.4S), to create synthetic coverage
pressure. It is not observed traffic. It adds harder legal declarations: a
15% requested share of class-9 battery bookings uses intact recycling SP377
scenarios, with explicit synthetic non-short-voyage declarations that require
deck stowage. Actual declaration scope, applicable exemptions and final
placement still require checker acceptance. This mixed-profile allocation does
not select the profile's optional concentrated pair cohorts and does not
promise that random mixed draws yield binding pairs.

Identity remains the explicit v2 specification schema. `n_boxes: null` requests
the current regional demand policy, not a historical diagnostic ceiling.
Equipment controls keep tiny DG enabled and use the ordinary generation
conditioning labels (`dry20_with_dg` on tiny, `full_mix` on small); the explicit
stress cargo profile supplies its dry20-only sampling distribution. Producers
must actually route `cargo_profile` to the stress parameters and preserve the
stress identity in emitted tags; substituting the main export profile invalidates
membership. Activation and source/parameter hashes belong in the coordinated
source epoch, not in this pre-solve allocation identity.

Seeds are `2**60 + rung_index*2**32 + candidate_index`: rung indices are tiny=0,
small=1 and the candidate index starts at 1 in the table's vessel-major order.
All 30 seeds are unique, strictly above `2**60`, and disjoint from all v2 root
seeds. A draw group conservatively means the root seed across both generators
and all controls; see [`src/eval/splits.py`](../../../src/eval/splits.py).
As with v2, random-yard regeneration must keep `max_attempts <= 1_000_000`
(normal cap 200) so initial-cargo retry ranges cannot overlap another root
seed's range. Source/parameter hashes and the actual cap remain required in
epoch, shard and resume metadata.

## Reproduce and select

From the repository root (one CPU, no GPU, no instance generation or solving):

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/splits/dg-stress-v1/freeze.py --check
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest tests/eval/test_splits_dg_stress.py -q
```

The local freeze tool's `--write` reproduces this exact allocation. Never edit
this release to accommodate outcomes: publish a new release for changed
allocation or policy, and never backfill rejected or unsuccessful draws.
Trusted pin validation rejects a changed inventory even if its self-reported
checksums and summaries have been recomputed.

Named selection uses the same frozen membership mechanism as v2:

```python
from eval.splits import load_inventory
inventory = load_inventory("dg-stress-v1")
```

Use harness `--split-manifest dg-stress-v1` and regeneration
`--split-release dg-stress-v1`. Carry `split_version` and
`split_entries_sha256` through the source epoch and all run, input, plan and
resume records; refuse pin drift or mixed releases.

## Report the complete allocation

Report each rung with its full denominator of **15 allocated specs**, retaining
all four statuses: feasible-witnessed, proven-infeasible, unknown and
generation-rejected. Show allocated/emitted/rejected counts; retain every failed
spec's audit at its original key, and never replace it with a successful draw.
Pass rates use the complete allocation, including rejected generation.

Follow [D70](../../../docs/decisions.md) and the
[evaluation coverage rule](../../../docs/evaluation.md): print per rung and per
class the number of test instances with at least one admitted box and its UNs,
plus the number of instances where each segregation pair actually binds. A
class can be called tested only with at least **10 admitted test instances**;
a pair requires at least **5 binding instances**. Raw requested/drawn counts,
all-DG sampling and allocation counts cannot establish either threshold. Print
requested, drawn, redrawn, rejected and emitted per-class counts so cargo
screening losses remain visible. Main and DG-stress measurements and their
coverage tables remain separately labelled even where both provide evidence
for a class.
