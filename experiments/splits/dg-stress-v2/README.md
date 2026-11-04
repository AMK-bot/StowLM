> **SUPERSEDED — NEVER USED.** `dg-stress-v2` was superseded by `dg-stress-v3` before any generation. Nothing selects this release: active loaders and run/epoch paths reject it, including explicit manifest paths. The unchanged manifest and remaining historical README are retained for audit only.

# Frozen DG-stress specification release v2

D70 is re-frozen before any generation as **15 tiny and 15 small test
specifications**, random-yard only. It supersedes `dg-stress-v1`, which was never
used. This release contains no training or validation allocation. The main `v2`
release remains unchanged. Report DG-stress separately: **never pool them with v2**
results, and always print per-rung rows.

The independently pinned canonical ordered-entries SHA256 is:

`fcdaa55f43d2927a601c128c1ff7fe8cc2d94d4fba50bfcb075cad32f43cd21e`

This is an allocation freeze, not evidence of generated/admitted cargo,
feasibility, passing plans or binding segregation pairs. The checker remains
the source of truth. No generation, pilot or evaluation runs were performed to
choose this allocation.

## Selection before generation

Selection follows fixed rung, vessel and generator-setting order without
looking at cargo draws, screens, solver results or any method's performance.
Every specification uses the random-yard producer because the simulated
producer does not route the stress cargo profile. Each of the three feeders
(`feeder_500`, `feeder_800`, `feeder_1700`) gets these five cells on each rung:

| Port | Blocking ratio | Tiny cohort | Small cohort |
|---|---:|---|---|
| Laem Chabang-like | 0.2 | mixed | flammable_water_reactive |
| Singapore-like | 0.5 | mixed | water_reactive_acid |
| Laem Chabang-like | 0.0 | mixed | mixed |
| Singapore-like | 0.2 | mixed | mixed |
| Laem Chabang-like | 0.5 | mixed | mixed |

Equal vessel allocation avoids a single-layout diagnostic. Each vessel sees
both synthetic ports and several blocking levels; Laem Chabang-like receives
the additional cell as the export reference. Tiny has 15 mixed specs. Small
has nine mixed specs plus three specs for each pair cohort, one of each per
vessel. The pairs are class 3/4.3 (`flammable_water_reactive`) and class 4.3/8
(`water_reactive_acid`). Cohort allocation raises candidate pair presence; it
does not guarantee admission, actual binding or the D70 coverage threshold.

Every specification names `cargo_profile: dg_stress`, the parameter key for
[`params/cargo/dg_stress.toml`](../../../params/cargo/dg_stress.toml), profile ID
`synthetic_dg_stress_d70`. Its explicit `dg_stress_pair` selector is null for
the mixed profile or the named cohort above. APPROXIMATION: the profile
intentionally makes all boxes loaded dry20 DG, rather than measured traffic.
The mixed profile balances ten supported classes (2.1, 2.2, 2.3, 3, 4.1, 4.3,
6.1, 8, 9, 1.4S); a pair cohort instead splits its two classes equally. Harder
legal declarations include an intended 15% of mixed-profile class-9 battery
bookings under intact-recycling SP377 scenarios, with explicit synthetic
non-short-voyage declarations requiring deck stowage. Scope binding, legal
exceptions and placement still require checker acceptance.

The new identity version `v3` records `dg_stress_pair` explicitly, including
null, and labels both rungs `equipment_profile: dry20_dg_stress`. It requires
random yard, `tiny_dg_enabled: true`, `dense_20ft: false` and `without_dg: false`.
`n_boxes: null` requests the current regional demand policy, not a diagnostic
box ceiling. Main v2 and archived v1 identities retain their original canonical
keys and digests. Changing the selector changes the complete spec identity.
Producer family tags must preserve the identity version, equipment and selector
(`mixed` is the tag spelling for a null selector); a missing v3 cohort tag fails
closed. Substituting main export cargo or dropping a selector invalidates
membership.

Seeds are `2**61 + rung_index*2**32 + candidate_index`, with tiny=0, small=1
and candidate indices starting at 1 in vessel-major table order. All 30 roots
are unique and disjoint from main v2 and superseded DG-stress v1. The draw group
is conservatively the root seed across generators and all controls. Random-yard
regeneration must retain `max_attempts <= 1_000_000` (normal cap 200), preserving
disjoint initial-cargo retry ranges. The actual cap and source/parameter hashes
belong in the source epoch, shard audits and resume metadata.

## Binding and coverage, registered before results

The [D70 amendment](../../../docs/decisions.md) defines binding in the reference
solver's **best passing plan**: at least one pair of boxes of the two classes
sits at **exactly the minimum separation** required by **IMDG 7.2.4**, with
**no slack**. Mere co-presence does not count. Keep co-present-instance counts
beside binding-instance counts. A missing passing reference plan supplies no
binding witness; retain its allocated spec and four-way status. Use the checker
geometry and the pair's applicable IMDG requirement, including strengthening
codes, rather than a newly invented distance threshold. Source:
[IMO MSC.556(108), IMDG amendments](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MSCResolutions/MSC.556%28108%29.pdf),
7.2.4 and 7.2.8; see the local review
[`logic-dgsplit.md`](../../review/w2/logic-dgsplit.md).

Apply the [evaluation coverage rule](../../../docs/evaluation.md) per class and
rung: print test instances with at least one admitted box and their UNs, plus
instances with each binding pair. Call a class tested only with at least
**10 admitted test instances**; a pair needs at least **5 binding instances**.
Print requested, drawn, redrawn, rejected and emitted per-class counts. Raw
candidate presence and all-DG sampling do not establish coverage. Three cohort
specs per small pair alone cannot meet the five-binding threshold; mixed draws
may add evidence, and an unmet threshold stays explicitly unmet. Never change
this allocation or backfill to meet a threshold after observing results.

## Claim and denominators

The registered claim is **"DG safety under stress, zero-shot transfer"**.
StowLM has no stress training allocation; this panel is not evidence of learned
DG planning skill. Always show **base Glimmer + mask** beside StowLM and report
mask **dead-end/repair rates**, alongside the solver and other baseline rows.
The all-DG, all-dry20 cargo changes the stacking mix, so move counts measure
transfer to this synthetic population, not calibrated operational traffic.

For each rung use all **15 allocated specs** in pass-rate denominators. Retain
all four statuses: feasible-witnessed, proven-infeasible, unknown and
generation-rejected; show allocated/emitted/rejected counts. Retain failures at
their original keys and never backfill. Report per-rung **Wilson 95% intervals**
with **n = 15**, which gives wide uncertainty; present per-class and per-pair
coverage beside those rows. Wilson intervals estimate uncertainty in a binary
pass fraction rather than treating a small percentage as precise (Wilson,
1927, [JASA paper](https://doi.org/10.1080/01621459.1927.10502953)). Do not replace
per-rung results with a single pooled DG pass rate.

Before regeneration, run a yield pilot on seeds **outside every frozen release**.
Pilot instances never enter evaluation. The checker SG17/SG25 fix and full
stress profile/cohort routing are dependencies of that pilot and regeneration;
this inventory does not implement those producer/checker changes or claim a
successful pilot.

## Execution admission after code review

The campaign adapter currently drops the selected stress profile/cohort and
rewrites the output's family tags from the request. Stress regeneration is
therefore explicitly blocked, including diagnostic execution, publication and
indexing of existing stress records, until producer owners implement and verify
the complete routing and identity-from-sampled-parameters path. `--plan-only`
still permits inspection of this allocation. This block is not a claim that
producer adoption is complete.

The public metric aggregation/rendering functions also still need their owner
to reject mixed release pins, preserve a single pin and print the release label.
Those functions are outside this release task's file fence. Do not publish a
stress metric row through that path until the reporting fix lands. See
[`REVIEW-FOLLOWUP.md`](REVIEW-FOLLOWUP.md) for the regression evidence and exact
remaining file ownership.

## Reproduce and select

From the repository root (one CPU, no GPU, no generation or solving):

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/splits/dg-stress-v2/freeze.py --check
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest tests/eval/test_splits_dg_stress.py -q
```

The freeze tool's `--write` reproduces only this exact manifest. Publish a new
release for changed policy or inventory rather than editing a frozen release.
The trusted independent pin rejects changed entries even if their self-reported
checksums and summaries have been recomputed.

```python
from eval.splits import load_inventory
inventory = load_inventory("dg-stress-v2")
```

Use harness `--split-manifest dg-stress-v2` and regeneration
`--split-release dg-stress-v2`. `dg-stress-v1` is superseded and cannot be selected
for a run, including through an explicit path. Carry `split_version` and
`split_entries_sha256` through the source epoch and all run/input/plan/resume
records; refuse pin drift and mixed releases. Generation must pass the complete
profile, cohort and identity controls through the v3 adapters, and check
`spec_from_instance` against frozen membership before accepting any output.
