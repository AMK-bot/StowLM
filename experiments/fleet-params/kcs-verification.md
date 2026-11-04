# D20 fleet migration verification

Run date: 2026-10-27. All commands use one CPU thread via
`OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1` and
`UV_CACHE_DIR=/private/tmp/stowage-fleet-uv-cache`.

- `uv run python experiments/fleet-params/generate.py`: regenerated all three
  pinned-offset TOMLs. Initial one-ship fit took1.764s; all-ship regeneration
  is a small local job. No GPU/cloud work.
- `uv run pytest -q tests/params tests/vessel`:180 collected,169 passed,
  11 failed. The run collected the old windage expectation while its test owner
  was updating that expectation to include KCS overhangs. That single owned
  test failure is resolved in the subsequent complete parameter rerun.
- `uv run pytest -q tests/params`:57 passed on the final files/tests.
- `uv run pytest --collect-only -q tests/params tests/vessel -o addopts=`:
  180 tests collected in0.58s, confirming the combined-run count.

The combined run includes passing explicit KCS `build_hull_model` assemblies
at every feeder size. Remaining ten vessel-test failures are outside1.6's
file fence:

1. `tests/vessel/test_fleet.py::test_feeder_capacity_and_frozen_model` (three):
   hardcoded504/1008/1720 capacities are now440/840/1432 following the required
   physical hold-fit removal. Declared nominal-class tolerances are12/16/15.8%.
2. `tests/vessel/test_fleet_slices.py::test_presets_are_valid_nested_size_ladder`
   (three): tiny presets at the now deck-only end bay have two stacks, while
   the old test expects four. Loader/preset owner must reconcile the physical
   design and slice policy; no unsupported hold cells were restored.
3. `tests/vessel/test_hull.py::test_full_fleet_loader_assembles_real_hull`
   (three): KCS HullModel assembly succeeds, then
   `src/stowage/checker/strength_limits.py:110` raises
   `KeyError: 'aft_run_fraction'`. `_inputs` still calculates a retired analytic
   q(x) bow-area expression. It must integrate the KCS geometry instead.
   There is a second visible integration issue at line175: station checks
   assume0..Lpp, while the mandatory KCS source retains buoyant overhangs.
   Do not add fake retired-family keys or crop source buoyancy to bypass this.
4. `tests/vessel/test_hydrostatics_geometry.py::test_kn_is_symmetric_and_small_angle_matches_metacentre`
   (one): KN0.0152109136 versus KM*sin(phi)0.0152663035 at0.1degree
   (about−0.363%). The fixture now reads KCS; KN's default reference LCG and
   the even-keel row's actual LCB must be reconciled by2.1's owner. No test
   tolerance was weakened here.

Compute admission: existing ledger reserved1CPU/30GB/GPU for another executive;
this job reserved1CPU/1GB/noGPU/7minutes, within remaining15CPU/82GB.
`uptime` load0.59 and `memory_pressure`93% free corroborated admission;
`ps` was unavailable under sandbox. The central ledger/history contains the
measured reservation window (including final collection/report checks), not a
claimed precise subprocess runtime or measured peak RAM. Slow assembly tests
reported61.3/53.2/48.5s. Only the executive's own ledger line was added/removed.

No source/model/checker or vessel tests were edited. Final source inputs remain
checksum-pinned; removed stack IDs and tank adjustments are explicitly in each
TOML's `layout.kcs_fit_audit` and the migration review.
