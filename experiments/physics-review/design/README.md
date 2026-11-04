# Ship-design sanity pass — loading-manual conditions C1–C4

A harness that builds the standard loading-manual conditions for each KCS-derived feeder using the production code
(`stowage.vessel.fleet.load_vessel`, the `stowage.model.fixtures.build_instance` port/rotation, the production
**ballast finisher** `stowage.solver.ballast.finish_ballast` and the **full checker** `stowage.checker.full.check`).
For each condition it records every criterion's value against its limit. It can apply design-value overrides in
memory so that a follow-up run can fill in the "after corrections" columns. Rules and criteria are never changed.
Only the synthetic ship's design values can be overridden.

Files (everything is written only inside this folder):

| File | Role |
|---|---|
| `harness.py` | Condition builders C1–C4, in-memory overrides, finisher + full check, resumable JSON (`results_<label>.json`) |
| `sweep.py` | Stability-only sensitivity: lightship VCG and ballast design (`sweep_results.json`) |
| `report.py` | Turns the JSONs into `conditions_tables.md` (full per-criterion tables) |
| `conditions.md` | Hand-written summary and diagnosis |
| `.cache/` | Private vessel/linear caches (`STOWAGE_VESSEL_CACHE_DIR` is pointed here) |

## Run

```sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1   # single core
uv run python experiments/physics-review/design/harness.py --label before --finisher-s 45
uv run python experiments/physics-review/design/sweep.py            # ~10 s per point per ship
uv run python experiments/physics-review/design/report.py before after
```

Options: `--ships feeder_500,feeder_1000`, `--conditions C1,C3`, `--finisher-s` (finisher wall-clock budget),
`--overrides file.json`, `--label after`, `--out path.json`. Runs are resumable: a (ship, condition) pair already in
the output JSON is skipped, so delete the file (or use a new `--label`) after changing the overrides.

Cost per condition: the finisher budget (default 60 s; each finisher evaluation is about 5–10 s of exact
stability) + one full check (10–15 s) + a 5 s stability probe with every ballast tank pressed full. Full fleet
C1–C4 at 45 s is about 16 min.

## Overrides (in memory; params files untouched)

`--overrides` takes JSON `{ "<ship>" | "*": { ... } }`. Supported keys:

```json
{
  "feeder_500": {
    "lightship": {"vcg_frac_depth": 0.62, "mass_kg": 2.6e6, "lcg_m": 48.0},
    "ballast":   {"replace": true, "add_tanks": [ {"id": "DB-1-P", "kind": "ballast",
                  "content_density_kg_m3": 1025, "adjustable": true, "heated": false,
                  "geometry": {"shape": "rectangular_prism", "x_min_m": 16, "x_max_m": 35,
                               "y_min_m": 0.02, "y_max_m": 7.5, "z_min_m": 0.15, "z_max_m": 1.4}} ],
                  "double_bottom_top_m": 1.4, "scale_y": 1.5},
    "consumable_scale": 1.0,
    "strength":  {"z_margin": 1.10, "shear_area_scale": 1.25},
    "stack_mass_scale": {"deck": 1.0, "hold": 1.0},
    "max_deck_tiers": 4
  }
}
```

How each override is applied (`harness.apply_overrides`):

- **lightship**: updates `Vessel.hull.lightship_*` on a `model_copy` of the cached production vessel. When the mass
  or LCG changes, the five-block `lightship_distribution` is rescaled and exponentially re-tilted (the generator's
  method), so the strength checker's block-sum/LCG guards hold. All checkers read the lightship from `vessel.hull`.
- **ballast / consumable_scale**: edits a deep copy of the parameter tree (tank solids). Capacity is recomputed from
  the rectangular solid, and `Vessel.tanks` is rebuilt with production `build_tanks`. The stability, strength,
  visibility and linear modules re-read params via `load_ship_params`; the harness redirects those module-level
  references to the in-memory copy, wraps `stability._context` (it now reads the TOML bytes directly; re-check if stability.py changes), and clears their `lru_cache`s. `sweep.realistic_ballast(params)` and
  `harness.fit_tank(params, x0, x1, z0, z1, side=±1)` build tanks that fit inside the actual KCS sections, with a 20 mm
  margin (sampled containment, the same method as the fleet fit audit). Tanks given by hand are **not** checked for
  containment.
- **strength**: temporarily sets `strength_limits.SECTION_MODULUS_DESIGN_MARGIN` and scales the assumed peak shear
  area, then recomputes `hull.stations` with production `permissible_stations`.
- **stack_mass_scale / max_deck_tiers**: edits the Vessel's stacks (limits; cells above tier n removed).
- **Not supported**: principal dimensions (L, B, D, T) and cell layout. These need a full hull/KN rebuild
  (`fleet._build_vessel` from edited params, about 1 min per ship cold) and a regenerated fleet.

## Condition definitions (labelled assumptions; see `harness.py` docstring)

- Only 20 ft 22G1 boxes (2.591 m, tare 2.2 t). No 40/45 ft boxes, reefers, DG or OOG.
- Placement is heaviest-first in the designer's fill order: hold tiers bottom-up, then deck tiers, nearest LCB first,
  port/starboard alternating. A box never exceeds a stack's 20 ft mass or height limit and never sits on a lighter box.
  Boxes ≥ 25 t never go on deck.
- Synthetic expert-rule policy (`RULE_POLICY`; expert Q2/Q11 open): E1/E6 heavy = 25 t, E2 tolerance 0 kg.
- **C1**: homogeneous 14 t/TEU, added until design displacement or until no cell accepts a box.
- **C2**: homogeneous to scantling draught (= design draught for all three ships). The t/TEU is found by scanning a
  0.1 t grid, because cargo vs t/TEU is not monotonic (deck stack limits drop whole tiers). It reports when the
  scantling draught cannot be reached.
- **C3**: export mix from `params/cargo/feeder_export.toml` (22G1 triangular 5/22/30.48 t, 20 % empties at tare,
  seed 7), sampled until the deadweight budget to design draught is used.
- **C4**: ballast arrival: no cargo, 10 % consumables, ballast by the finisher.
- Consumables: 97 % FO/DO/FW at departure, 10 % at arrival. The port draught limit is the ship's max draught. There is
  no port tier limit.
- Finisher `not_found` → the condition is judged with empty ballast. A "probe" line gives stability with every
  ballast tank pressed full: the most ballast can do for GM, with zero free surface.
