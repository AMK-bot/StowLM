# Warm diagnosis of V2 development failures

This audit **reads saved V2 plans** for small/o30/331401024 and
medium/o30/331401048. It is warm diagnostic work, not another cold solve,
benchmark result, or pass. No LP was solved and no search ran. The only native
numerical calls replayed saved plans and recomputed given loading conditions.
`diagnostics.json` records source hashes, targets, raw and seeded state metrics,
and exact stored hard violations. Both top-level plans and replay evidence are
raw inherited-tank stows. Seeded versions are serialized separately in
`attempts`, source `cold-observed-candidate`; this audit includes those too.

## The cargo coefficients are correct on these stows

Every raw plan has complete legal replay. Summing assignment-edge cargo
coefficients agrees with native `_cargo_geometry`:

- Longitudinal cargo-moment disagreement: zero small, at most 8.8e-11 t m medium.
- Vertical disagreement: at most 1.5e-11 t m across all four plans.
- Transverse disagreement: below 5e-13 t m.

These maps use ordinary standard-height 20-foot boxes. Native support height
and LP support height therefore coincide exactly here. No omitted arrival
mass, doubled fixed cargo, index error, or height-reference mismatch explains
the failed draught/trim. Actual tank-centroid pose changes shift the background
longitudinal moment by only about 28.5 t m small and 13.6–13.8 t m medium,
versus thousands of tonne-metres of moment-row slack.

## Small: the inherited-tank target is outside the decision region

The all-restow branch has 612.951 t of decision cargo. Given original tank and
background moments, its even-keel LCB target is 52.475 m and total LCG window
52.175–52.775 m. That requires decision-cargo LCG **44.136–53.286 m**. Every
eligible slot is at **85.833–91.967 m**. Even the relaxation that gives every
box its own minimum-x slot has total LCG at least 54.909 m, with frozen
background. Thus this particular affine moment row necessarily needs at least
19,949.635 t m of positive slack. This proves a contradiction only inside that
frozen-background linear restriction, not ship or map infeasibility.

| Saved raw stow | Cargo LCG m | Native total LCG m | GM m | Aft / forward draught m | Trim m |
|---|---:|---:|---:|---:|---:|
| 62 lifts | 88.430 | 55.0827 | 1.6503 | 5.7238 / 8.1306 | −2.4068 |
| 57 lifts | 88.488 | 55.0865 | 1.6381 | 5.7219 / 8.1327 | −2.4108 |

At the first round's actual targets the 62-lift stow pays 21,541.458 t m of
LCG slack and 1,647.073 t m of vertical slack. The later 57-lift round shifts
the target another 0.3 m aft and lowers its reference GM; its reconstructed
LCG slack is 24,381.519 t m and vertical slack zero. These are consciously soft
construction rows. They do not certify actual loading compliance, regardless
of LP/CP solver success.

## Medium: attainable target, but accepted soft slack

The all-restow branch has 2,683.579 t of decision cargo. Required cargo LCG is
49.142–51.230 m, within the relaxed slot domain 15.833–91.967 m. The saved
291-lift stow instead has cargo LCG 52.0829 m, total LCG 53.0245 m against
52.7781 m upper target, GM 1.2806 m, and forward draught 7.10325 m. Its first
round's LCG slack is 2,288.754 t m and vertical slack 5,415.758 t m. The
292-lift stow is similar. Under its later reference GM 1.33057 and vertical
scale .98, its reconstructed vertical slack remains 1,451.410 t m, with LCG
slack 2,375.102 t m.

V2 tail logs include positive `objective_lifts` and adaptive physical slack
quanta. These observations do **not** support blaming the old C5 zero-rounded
CP moment penalty. Its newer objective prices the slack; capacity/search and
soft tradeoffs still permit it. A larger moment penalty cannot repair the
small map's frozen-background contradiction by moving boxes alone.

## The tank seed trades loading for stability

| Map / saved seed | Adjustable water t | Native GM m | Trim m | Forward draught m | Stored hard rules |
|---|---:|---:|---:|---:|---|
| Small, 62 lifts | 116.938 | .8570 | −1.1870 | 6.9852 | second check, trim, weather |
| Small, 57 lifts | 137.430 | .8756 | −1.1945 | 7.0017 | second check, trim, weather |
| Medium, 291 lifts | 567.725 | .5933 | +.0680 | 6.5770 | second check, weather |
| Medium, 292 lifts | 565.166 | .5934 | +.0602 | 6.5797 | second check, weather |

The seed resolves draught on these examples, and medium trim, by removing
low tank weight. It lowers GM enough to lose true area/weather compliance.
The first all-round selected metrics equal raw native GM exactly: small
1.650289 and medium 1.280571. The second small round's selected GM .875618
belongs to its seeded version. Recipe state must therefore be carried and
measured explicitly rather than inferred from trial lift count alone.

Next steps supported by this evidence: coordinate tank changes with the cargo
moment target, enforce a useful GM floor in the loading seed, refresh target
background mass/moments from an accepted recipe, and improve true area through
exact checking. The small map particularly requires buying aft tank moment
or another background change; cargo swaps within its current region cannot
meet the current target. No claim is made that any particular repair will pass.

## Warm joint row-family ablation at search GM floor 1.5 m

`joint_lane_diagnose.py` reconstructed the **first selected concrete reference**
from each `joint-v1` development run, matched by its saved outer-trial GM and
lift count. It used the current anchored joint assembler, all-restow branch,
all uncovered variables fixed to zero, and no hard TCG window. The LP retained
all ordinary assignment rows and removed only one loading-row family per run.
No integer search or full checker pass was attempted. Even pressed mode here
is its continuous LP relaxation, not a binary water recipe.

| Tank mode / map | All rows | Drop GM | Drop heel | Drop draught | Drop trim | Drop mass | Drop balances |
|---|---:|---:|---:|---:|---:|---:|---:|
| Half / small024 | 2 | 0 | 2 | 2 | 2 | 2 | 0 |
| Half / medium048 | 2 | 0 | 2 | 2 | 2 | 2 | 2 |
| Pressed / small024 | 2 | 0 | 2 | 2 | 2 | 2 | 0 |
| Pressed / medium048 | 2 | 0 | 2 | 2 | 2 | 4 | 2 |

Status 0 means the **continuous local model** found a point; 2 means that
particular affine restriction was infeasible; 4 is numerical/solver unknown.
None is a ship/map infeasibility certificate. The one-at-a-time result shows
that the inward 1.5 m GM restriction blocks completion in both reconstructed
relaxations. Draught, trim, mass or list rows alone do not explain it. Removing
GM yields witnesses that breach GM in all three phases; their saved excesses
are normalized row units, not a minimal physical GM relaxation.

The saved selected references already have much gentler draught than the old
raw 8.13 m example: small forward draughts 6.870/6.825/6.729 m and medium
7.103/7.038/7.100 m. Their native TCGs are respectively
+.006710/+.006979/+.007626 m and −.006182/−.006359/−.006729 m across the three
phases. These lie outside the later optional ±.005 m TCG search window. This
comparison motivates measuring that new restriction; it does not establish
that TCG caused every area failure.

Both diagnostic jobs exited zero, taking 35.56 s (half) and 34.09 s (pressed).
Evidence: `results/warm-joint-row-families-{half,pressed}.{json,log}`. The full
local root test suite preceding them passed **75 tests in 13.65 s**. Source
hashes changed during the chain only for root's `method.py` and
`test_finishing.py` optional finisher-gate update; therefore the run is
verification of the loaded test state, **not** a stable source freeze.
`results/verification-full-joint-tcg.json` records both hash sets and every
child PID/command/exit. The slot was released after all three sequential jobs.
