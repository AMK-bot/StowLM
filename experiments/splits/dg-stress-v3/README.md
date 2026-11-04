# Frozen DG-stress specification release v3

D70 amendment 2 re-freezes **15 tiny and 30 small test specifications** before
any generation. The allocation is random-yard only, dry 20ft only, with every
loaded box DG; foreign yard boxes are never DG. It contains no training or
validation allocation. The main `v2` release is unchanged. Report stress results
separately and **never pool them with v2**, retaining per-rung rows.

The independently pinned canonical ordered-entries SHA256 is:

`271935b242f9243a871d96bc84de88d457f8b5f5e1cdab6e7fd94423b7496504`

Both earlier DG-stress releases are superseded and never used; their manifests
remain unchanged for audit. This is an allocation freeze, not evidence of
emission, passing physics, reference-plan feasibility or binding-pair coverage.
No allocated draws or method outcomes informed selection.

## Allocation and pair choice

The small allocation expands to 30: eight small specs per pair cohort and 14
mixed specs. Sixteen cohort specs cannot fit the former 15-spec small budget.
This is a pre-generation allocation decision, not an outcome-based replacement.
Tiny retains 15 mixed specs. All three feeders (`feeder_500`, `feeder_800`,
`feeder_1700`) receive five tiny and ten small specs. Both Laem Chabang-like and
Singapore-like ports occur for every vessel. The small pair counts are:

| Vessel | 3/4.3 cohort | 4.1/3 cohort | Mixed |
|---|---:|---:|---:|
| feeder_500 | 3 | 3 | 4 |
| feeder_800 | 3 | 2 | 5 |
| feeder_1700 | 2 | 3 | 5 |
| Total | 8 | 8 | 14 |

Each pair has four specs per port. Equal vessel totals preserve layout coverage;
the unavoidable 8/3 cohort remainder rotates across vessels. The deterministic
[`freeze.py`](freeze.py) fixes every port, blocking ratio and cohort in rung,
vessel and cell order, without looking at generation, screening or solving.
Tiny uses blocking levels 0.0/0.2/0.5 and a 3:2 Laem Chabang-like/Singapore-like
mix per vessel. Small uses ten fixed cells per vessel, five per port, at the
same blocking levels. This is a synthetic coverage mix, not traffic frequency.

The explicit `dg_stress_pair` selector is:

- null: the mixed ten-class profile;
- `flammable_water_reactive`: class 3 UN1263 paint and class 4.3 UN1402 calcium
  carbide, eight small specs;
- `flammable_solid_flammable_liquid`: class 4.1 UN1333 cerium and class 3 UN1263
  paint, eight small specs. This new pair does not involve UN1402.

The new pair is deliberate: the general 4.1/3 table cell is `X`, but UN1333's
**SG25** strengthens separation from class 3 to level 2, and **SG26** additionally
requires two container spaces athwartships when both are on deck. SG17 is
irrelevant to class 3 and does not demand an invented verification. The current
checker implements these class rules. A real isolated IMDG fixture using public
H1 declarations and the scoped cargo binder passes when separated and fails
when adjacent, with both level-2 and SG26 violations. The source is the
[IMO MSC.556(108) IMDG amendments](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556%28108%29.pdf),
7.2.4 and 7.2.8, as loaded by the repository DGL/checker tables. Unlike a gas
pair, this pair does not require SW2 geometry. Class 3/8 was not chosen: its
general table cell is `X` and acid codes do not impose separation from this
paint entry.

This proof establishes assessable shipment facts and a genuine separation
requirement today, not whole-instance emission, feasibility or yield. The
synthetic fixture explicitly supplies permitted cells and separation geometry;
it is not an allocated draw, pilot or passing reference solver plan. See
`test_stress_non_un1402_pair_is_emittable_and_requires_separation` in
[`test_splits_dg_stress.py`](../../../tests/eval/test_splits_dg_stress.py).

## Cargo identity and current dependencies

Every spec names `cargo_profile: dg_stress`, the key for
[`params/cargo/dg_stress.toml`](../../../params/cargo/dg_stress.toml), profile ID
`synthetic_dg_stress_d70`. APPROXIMATION: the mixed profile deliberately samples
all loaded dry20 DG with ten balanced supported classes (2.1, 2.2, 2.3, 3, 4.1,
4.3, 6.1, 8, 9, 1.4S), not measured traffic. Named pair cohorts request only
their two classes at equal shares. Mixed-profile class-9 bookings request a
15% share of intact recycling SP377 battery declarations, with explicit
synthetic non-short voyages requiring deck stowage. Scope, applicable exceptions
and actual placement remain checker decisions. Foreign yard boxes are never DG;
yard DG segregation is not exercised by that background.

Identity schema remains `v3`, with explicit null/cohort selector and
`equipment_profile: dry20_dg_stress` on both rungs. Controls keep
`dense_20ft: false`, `tiny_dg_enabled: true`, `without_dg: false` and
`n_boxes: null` (current regional demand). Main v2 canonical keys and digests
remain unchanged. Producers must preserve identity version, equipment and
cohort tags; a missing v3 selector tag fails closed (`mixed` represents null).

The existing pipeline routes the older profile/cohorts, but adoption of the
new `flammable_solid_flammable_liquid` cohort and its sampled-parameter audit
is **pending** with the cargo/producer owners. This release does not edit those
owners' code. The regeneration adoption block remains explicit; a matching
family tag alone does not prove that the requested parameters were sampled.
Initial draws, redraws and screens must use the selected cohort and retain its
actual parameter identity. SW1/SW2 geometry and per-class admission, including
2.1/2.3/6.1, require current-source pilot evidence; this fixture proves none of
those classes' admission. Do not infer current emitted coverage from the review
or the requested class list.

Unique seeds are `2**62 + rung_index*2**32 + candidate_index`, with tiny=0,
small=1 and candidate indices starting at 1 in vessel-major cell order. All 45
roots are disjoint from main v2 and both superseded stress releases. A draw
group conservatively means the root seed across all generators and controls.
Keep random-yard `max_attempts <= 1_000_000` (normal 200) so initial retry ranges
remain disjoint. Record actual caps, source/parameter hashes and release pins
in epoch, shard and resume metadata.

## Coverage, claim and pilot rule

D70 binding is measured in the reference solver's **best passing plan**: at
least one pair of the relevant classes sits at **exactly the minimum separation**
required by **IMDG 7.2.4**, accounting for applicable DGL strengthening, with
**no slack**. Mere co-presence does not count. For the new pair, counting the
unstrengthened general-table `X` cell would be wrong: SG25 and SG26 apply.
Print co-present and binding-instance counts separately. No passing reference
plan means no binding witness, while the spec stays in its allocated status.

Per class and rung, print admitted-instance counts and their UNs, plus the
binding-instance counts per pair. A class is tested only with at least
**10 admitted test instances**; a pair requires at least **5 binding instances**.
Eight allocated cohort specs provide headroom and do not prove five bindings.
Print requested/drawn/redrawn/rejected/emitted class counts and
allocated/emitted/rejected counts per cohort. Never relax the threshold or
backfill after outcomes are known.

Before regeneration, run a yield pilot on seeds **outside every frozen release**;
pilot instances never enter evaluation. Report rejection rates and reasons per
cohort and admission per class. If the pilot rejects **more than 30%** of stress
generations, lower the stress profile's DG share **before regeneration** under
D70 amendment 2. Freeze the resulting source/parameter revision and label its
actual composition before any allocated generation; publish a new allocation
release if specification identity or selection changes. This release records
the current all-loaded-box-DG profile, not an already-performed revision.
Do not silently lower DG share within an evaluation run.

Claim only **"DG safety under stress, zero-shot transfer"**. There is no stress
training allocation and no learned DG planning-skill claim. Show **base Glimmer
+ mask** beside StowLM, including mask **dead-end/repair rates**. The dry20-only
and all-DG structure changes stacking and admission; moves describe transfer to
this artificial population, not calibrated traffic.

Use full denominators of 15 tiny and 30 small. Retain the four statuses:
feasible-witnessed, proven-infeasible, unknown and generation-rejected, plus
allocated/emitted/rejected counts; never backfill. Report per-rung **Wilson
95% intervals** (tiny **n = 15**, small n = 30), alongside class/pair coverage.
Wilson intervals express uncertainty in a binary pass fraction rather than
making a small percentage look precise ([Wilson, 1927](https://doi.org/10.1080/01621459.1927.10502953)).

## Reproduce and select

One CPU, no GPU, generation or solving:

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/splits/dg-stress-v3/freeze.py --check
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest tests/eval/test_splits_dg_stress.py -q
```

The local `--write` reproduces this exact allocation. Changed policy or entries
require a new release; an independent trusted pin rejects coherent tampering.

```python
from eval.splits import load_inventory
inventory = load_inventory("dg-stress-v3")
```

Select harness `--split-manifest dg-stress-v3` and regeneration
`--split-release dg-stress-v3`; execution remains blocked pending adoption.
Both superseded stress releases are rejected even by explicit path. Carry
`split_version` and `split_entries_sha256` through the source epoch and all
run/input/plan/resume records; refuse drift and mixed releases.
