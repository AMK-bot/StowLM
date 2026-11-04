# D93 manual C2 patch — prepared only

`../manual-c2.patch` changes two synthetic designer recipes and adds a separate
recipe regression. It has **not** been applied to the live source. The parent
completed the lightweight baseline/candidate recipe-factory comparison below.
No full checker, solver, hull-table build, physics qualification or pytest job
was run for this preparation.

The authority for the candidate is
`experiments/mvp/solve47/physics/manual-regression/REPORT.md` and its
`fixtest.py`. Their checker measurements are cited, not rerun here.
`source-pins.json` captures exact current target/input hashes, candidate hashes,
the patch hash, static witness masses and the successful read-only apply check.
Scratch copies are under `candidate/`; these are not imported by production.

## Changes in the patch

- `fleet_manual.py`: feeder_1700 C2 retains 25,800 kg per box with a
  budget-limited count and the existing hold-first placement. The old scan's
  deck-heavy regime is replaced. Other recipes and the historical cargo-reserve
  labels remain intact. This is labelled **APPROXIMATION**, a synthetic designer
  recipe correction; no checker or operating limit changes.
- `fleet_manual_witness.py`: declares feeder_500 C2's operating ballast from the
  **exact measured helper call** `manual_ballast_masses(vessel,
  'DB+HEEL+FPT0.50')`. That helper maps FPT to **both FPT and FWD-DEEP pairs**.
  DB and HEEL remain full, FPT and FWD-DEEP become half full, other adjustable
  tanks remain empty. The historical `.38` cargo-budget label remains unchanged,
  so the recipe still places the same 249 × 18 t boxes. Provenance explicitly
  says this is a static declaration with no new finisher invocation. A small
  label-selection change prevents falsely calling this witness a finisher run.
- `test_fleet_manual_restore.py`: adds three cases requiring at least half of
  cargo mass below deck for the three synthetic **C2** recipes. This is a recipe
  regression, not an added hard operating rule. Existing complete-checker,
  physical-support, cargo-budget and voyage endpoint assertions are preserved.
  `test_manual_deck_share.py` contains the same standalone regression; the
  unpatched feeder_1700 C2 case is expected to fail.

## Existing C1 counterexample; regression scope

The parent's first admitted baseline/candidate factory comparison observed
**unchanged feeder_800 C1 deck-mass fraction 0.5048355899**. The initial helper
aborted there; it did not complete or save a nine-case table. This is retained
as an existing counterexample, not silently treated as passing or repaired.
The revised helper subsequently completed all nine rows, persisted all
deck-share failures and passed the requested A/B expectations and three C2
regression cases. The complete evidence is `recipe-factory-comparison.json`.

The source explains why C1 can exceed half: it requests budget-limited 14 t
boxes and `place_manual_cargo` fills holds first, then available deck stacks.
Those boxes are below the 25 t deck prohibition. The unchanged C1 construction
has no 50% deck-mass constraint. A finite hold can be full while slightly more
than half of total mass is on deck; this differs from the broken C2 scan's
large jump to light, deck-heavy cargo. The 50% threshold is a synthetic recipe
guard, not a checker or stability law. No C1 recipe, physical limit or existing
acceptance assertion is altered by A/B.

Following the parent's corrected scope, the patch and standalone regression
target **C2 on all three ships**. The nine-case diagnostic explicitly reports
unchanged 800 C1 at 50.48%. No threshold tolerance,
exception or unrelated recipe fix was added to hide that result.

## Completed lightweight factory verification

The parent ran `compare_recipe_factories.py`: **exit 0, 2.1371177500113845 s**,
all nine baseline/candidate records saved (`recipe-factory-comparison.json`;
console output in `recipe-comparison.log`). It read the three existing
content-keyed vessel JSON files directly and refused any missing-cache fallback.
The actual recipe factories performed their small target-displacement
calculations; no hull tables were rebuilt and no full checker was called.
Preparation estimate was 4–6 s / one core; the completed comparison used one
core, no GPU. Exact cache identities and JSON hashes are in the evidence.

| Ship | Condition | Boxes before → candidate | Deck cargo mass before → candidate |
|---|---|---:|---:|
| feeder_500 | C1 | 249 → 249 | 37.3494% → 37.3494% |
| feeder_500 | C2 | 249 → 249 | 37.3494% → 37.3494% |
| feeder_500 | C3 | 296 → 296 | 22.7958% → 22.7958% |
| feeder_800 | C1 | 517 → 517 | **50.4836% → 50.4836%** |
| feeder_800 | C2 | 256 → 256 | 0% → 0% |
| feeder_800 | C3 | 474 → 474 | 22.5708% → 22.5708% |
| feeder_1700 | C1 | 961 → 961 | 40.4787% → 40.4787% |
| feeder_1700 | C2 | 1,604 → 543 | **64.3392% → 0%** |
| feeder_1700 | C3 | 876 → 876 | 12.6582% → 12.6582% |

The three requested **C2 guards pass 3/3**. Across all nine diagnostic rows,
eight meet the same 50% heuristic; only unchanged feeder_800 C1 does not.
The helper also confirmed feeder_500 C2's exact cargo placements, cargo budget
and `.38` reserve label remain unchanged, while feeder_1700 C2 is 543 × 25.8 t,
all below deck. These are recipe-assembly results, not checker acceptance
claims. Full-checker acceptance at departure/mid-voyage/arrival remains borrowed
from the cited D93 investigation, not newly measured here.

## Exact ballast-floor discrepancy in the proposal report

`ballast-floor-static.json` derives these totals directly from current declared
tank capacities and historical witness maps, without constructing a vessel:

| feeder_500 condition | Before, kg | Proposed, kg |
|---|---:|---:|
| C1 | 2,520,479.20670043 | unchanged |
| C2 | 1,438,549.8066337993 | 1,481,350.5570359204 |
| C3 | 1,480,753.9093313287 | unchanged |

Thus the minimum becomes **C3: 1,480,753.9093313287 kg**, an increase of
42,204.102697529364 kg. The report's approximate new minimum of 1,474 t does not
match the exact current `FPT0.50` helper candidate. The patch follows the
**measured helper call**, not that approximate prose value. This arithmetic
does not independently establish checker acceptance. The completed **actual
public recipe-factory comparison independently confirms the same floor**:
1,438,549.8066337993 → 1,480,753.9093313287 kg for feeder_500. The feeder_800
floor stays 890,282.0520180871 kg and feeder_1700 stays 1,098,565.163536669 kg.

## Generator and distribution trace

1. `generate/random_export.py::full_export_policy` reads the C-condition floor
   **only inside `rule_freeze_v1_enabled()`**. OFF uses zero
   `minimum_departure_ballast_kg`, retains D37 target fraction/default ballast,
   and never calls `c_condition_ballast_floor_kg` through this path. Consequently
   the new 500 witness does not change OFF MVP booking-count/mass arithmetic,
   cargo RNG draws, or the reference ballast floor, on any rung.
2. ON calls `c_condition_ballast_floor_kg` → `_c_condition_ballast_floor` →
   `standard_manual_conditions`. It sums each recipe's adjustable departure
   tank masses and takes the minimum. `ExportPolicy.ballast_kg` uses this floor
   when the D64 mandatory-pair reserve is present; the floor can reduce cargo
   budgets, counts or acceptance when it binds.
3. The report's claim that only the full-export generator can be affected is
   too broad **for ON**. `random_yard.capacity_load_size` scales a full-parent
   policy to regional decision cells. `apply_booking_mass_fraction` and
   `random_export.export_reference_audit` also use the full-parent budget for
   regional mass caps/rejections. `generate.instances` and
   `instances_v2_random` share these functions. Thus ON regional tiny/small/
   medium arithmetic can depend on the floor as well. No ON changes or runs are
   requested by this patch; the MVP remains OFF.
4. `booking_tank_masses` starts from `vessel.fleet_conditions.departure_tank_masses`
   and uses full-export ballast when the selected vessel has the full physical
   cell set and its booking reaches export count. Regional slices normally keep
   their separately constructed background/booking tank recipe. That initializer
   does not directly read the C2 manual witness.
5. All fresh physical vessels read `standard_manual_conditions` through
   `fleet_lashing_design.populate_lashing_design`. D56 takes 1.10 × the maximum
   corrected GM over C1–C3 departure/arrival. The proposal's measurements say C3
   remains governing, so its **numerical cap is expected unchanged**, but this
   preparation has not recomputed it. C2 audit fields, cargo hashes, witness
   provenance and embedded manual metadata will change even when the cap does
   not. Do not claim complete generated payloads remain byte-identical.
6. Both source files are in `fleet_cache._source_files`: applying the patch
   changes the fleet construction epoch and invalidates existing canonical
   projection metadata. Full/source fingerprints and generator acceptance
   identities also change. Apply between batches, restart producers and refresh
   exact canonical metadata/caches as scheduled. `_full_vessel` LRUs in generator
   modules and `_c_condition_ballast_floor` (keyed only by parameter bytes and
   activation) do not independently notice edits to manual source files in a
   long-lived process. No empirical C2 linear requalification is part of D93.

## Read-only preparation checks

All Python commands must keep private outputs and avoid `.pyc` writes:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache uv run --no-sync --frozen python experiments/mvp/fixwindow/manual/check_prepared.py
git apply --check experiments/mvp/fixwindow/manual-c2.patch
```

Both check exact pre-apply files; neither applies the patch. AST parsing of all
three candidate files and the standalone regression also succeeded. The
preparation script can reproduce the text artifact, but the source pin check
should be used immediately before any scheduled application.

## Required checks after scheduled application (not run here)

The source edit invalidates content-addressed vessel caches, so even recipe
tests can trigger costly rebuilds. Admit/coordinate the job first; use one core
and existing fleet accounting. Then run:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --no-sync --frozen pytest -p no:cacheprovider tests/vessel/test_fleet_manual_restore.py tests/vessel/test_fleet_lashing_design.py --junitxml=experiments/mvp/fixwindow/manual/post-apply-tests.xml
```

Standalone recipe guard:

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=$PWD/experiments/mvp/fixwindow/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --no-sync --frozen pytest -p no:cacheprovider experiments/mvp/fixwindow/manual/test_manual_deck_share.py --junitxml=experiments/mvp/fixwindow/manual/deck-share-tests.xml
```

After scheduled application, verify the actual checker runs and accepts all
D57 states (departure, mid-voyage and arrival) and the D56 cap equals its prior
value bitwise. Those measurements were not rerun here; the proposal's original
checker report remains the borrowed acceptance evidence. The cargo/reserve
preservation and 543 × 25.8 t/no-deck expectations have already passed the
lightweight private factory comparison. Existing checker limits and acceptance
assertions must remain intact.
