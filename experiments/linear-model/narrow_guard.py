"""Check public ballast LP on full-cargo cases using cached calibrated models.

This performs no calibration. A source/input cache miss is reported explicitly.
Expanded estimation bands never change the vessel's operational limits.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import runpy

import numpy as np

from stowage.conventions import cells_of_slot
from stowage.linear.bands import _sample_whole_columns, departure_manifest
from stowage.linear.cache import load_model, model_cache_key
import stowage.linear.cache as numerical_cache
from stowage.linear.model import solve_ballast_lp
from stowage.linear.physics import _params_for
from stowage.model import Placement, ShipState

_HERE = Path(__file__).resolve().parent
_HELPERS = runpy.run_path(str(_HERE/'narrow.py'))


def ship_from_columns(model, instance):
    mass, heights = _sample_whole_columns(model, instance, np.random.default_rng(3741))
    index = {cid: i for i,cid in enumerate(model.cell_ids)}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {stack.id: stack for stack in instance.vessel.stacks}
    from collections import defaultdict
    available = defaultdict(list)
    for stack in instance.vessel.stacks:
        for tier,cid in enumerate(stack.cell_ids):
            i = index[cid]
            region = 'on_deck' if stack.on_deck else 'under_deck'
            if np.isclose(heights[i], stack.base_z_m+(tier+.5)*2.591, rtol=0, atol=1e-6):
                available[(20, round(float(mass[i]),5), 2.591, region)].append(cid)
            if (stack.bay-1)%4 == 0 and np.isclose(heights[i], stack.base_z_m+(tier+.5)*2.896, rtol=0, atol=1e-6):
                slot = f'{stack.bay+1:03d}'+cid[3:]
                support = cells_of_slot(slot)
                if all(key in index and np.isclose(mass[index[key]], mass[i], rtol=0, atol=1e-6)
                       and np.isclose(heights[index[key]], heights[i], rtol=0, atol=1e-6) for key in support):
                    available[(40, round(float(2*mass[i]),5), 2.896, region)].append(slot)
    placements = []
    for box in departure_manifest(instance):
        region = 'on_deck' if 'on_deck' in box.special_stowage else 'under_deck'
        key = (box.length_ft, round(box.gross_mass_kg,5), box.height_m, region)
        if not available[key]:
            raise RuntimeError(f'Cannot reconstruct full-column box {box.id}')
        placements.append(Placement(slot=available[key].pop(), container_id=box.id))
    return ShipState(vessel_id=instance.vessel.id, placements=placements)


def main():
    result = {}
    for name in ('feeder_500', 'feeder_1000', 'feeder_1700'):
        instance, _ = _HELPERS['cargo_instance'](name, 'full')
        band = _HELPERS['case_band'](instance, name, 'full')
        _, params = _params_for(instance)
        key = model_cache_key(instance, band, 64, params)
        model = load_model(key)
        cache_status = 'current_key_hit'
        if model is None:
            recorded = json.loads((_HERE/f'narrow-{name}-full.json').read_text())
            for manifest in sorted((numerical_cache._DEFAULT_ROOT/'model').glob('*.json')):
                raw = json.loads(manifest.read_text())['payload']['dict']
                fields = raw['fields']['dict']
                metadata = raw['metadata']['dict']
                if fields['vessel_id']['scalar'] != name:
                    continue
                if metadata.get('_cargo_mass_kg', {}).get('scalar') != recorded['manifest']['cargo_mass_kg']:
                    continue
                if metadata.get('measurement_diagnostics', {}).get('dict', {}).get('samples', {}).get('scalar') != 192:
                    continue
                candidate = load_model(manifest.stem)
                if candidate is None or asdict(candidate.band) != recorded['band']:
                    continue
                if getattr(candidate, '_reference_params', None) != params:
                    continue
                if numerical_cache._canonical(getattr(candidate, '_tank_mass_bounds_kg', {})) != numerical_cache._canonical(recorded['tank_windows_kg']):
                    continue
                if not all(np.array_equal(getattr(candidate.errors, name), value)
                           for name,value in recorded['model_errors'].items()):
                    continue
                model, key, cache_status = candidate, manifest.stem, 'current_key_miss; exact_recorded_artifact_replay'
                break
        if model is None:
            result[name] = {'status': 'cache_miss', 'qualification': 'No expensive rebuild was attempted.'}
            continue
        current_hull = instance.vessel.hull
        hard_arrays = (('station_x_m', [s.x_m for s in current_hull.stations]),
                       ('sf_max_n', [s.max_shear_n for s in current_hull.stations]),
                       ('sf_min_n', [s.min_shear_n for s in current_hull.stations]),
                       ('bm_max_nm', [s.max_bending_nm for s in current_hull.stations]),
                       ('bm_min_nm', [s.min_bending_nm for s in current_hull.stations]))
        hard_match = all(np.array_equal(getattr(model, key), values) for key,values in hard_arrays)
        ship = ship_from_columns(model, instance)
        answer = solve_ballast_lp(model, ship, instance, margin_scale=2.)
        result[name] = {**asdict(answer), 'cargo_boxes': len(ship.placements),
                        'gm_limit_m': model.gm_min_m, 'trim_limit_m': model.max_trim_m,
                        'estimation_band_trim_m': model.band.max_abs_trim_m,
                        'cache_status': cache_status, 'recorded_artifact_key': key,
                        'measurement_model_version': model.version,
                        'hard_strength_arrays_match_current_vessel': hard_match,
                        'qualification': 'Archived measurement replay matched vessel, band, tank windows, cargo mass, geometry parameters and all 192-condition error vectors; current public LP operations replay the archived measured model. Current source/version key miss is retained explicitly; this does not claim a current-source checker-approved departure.'}
    (_HERE/'narrow-full-lp.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
