"""Validate both ships' copied acceleration artifacts without opening maps."""
from pathlib import Path
import hashlib
import json
import os
import sys
from capacity import wait_for_slot

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / 'runtime'


def main():
    admission = wait_for_slot()
    sys.path.insert(0, str(RUNTIME / 'src'))
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(RUNTIME / 'experiments/cache/vessels')
    from stowage.vessel.fleet import _parameter_bytes, load_ship_params, load_vessel
    from stowage.vessel.fleet_slices import load_fleet_preset
    from stowage.vessel.fleet_cache import _cache_key
    from stowage.cache_warm import _covers_domain, _manifest_valid, _liquid_required_grid, _liquid_covers_domain
    from stowage.checker import stability_tables as kn, stability_liquid_tables as liquid
    from stowage.checker.stability_geometry import StabilityGeometry
    from stowage.vessel.tanks import TankGeometry

    rows = []
    for name in ('feeder_500', 'feeder_800'):
        params = load_ship_params(name)
        payload = _parameter_bytes(name)
        row = dict(ship=name, vessels=[], kn=[], tanks=[])
        for preset in ('full', 'medium'):
            key = _cache_key(name, preset, True, payload)
            target = RUNTIME / 'experiments/cache/vessels' / f'{name}.{preset}.hull.{key}.json'
            existed = target.exists()
            load_vessel(name) if preset == 'full' else load_fleet_preset(name, preset)
            assert target.exists(), f'Missing cache after preparation: {target}'
            row['vessels'].append(dict(preset=preset, reused=existed, path=str(target.relative_to(HERE)),
                sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
        geometry = StabilityGeometry(params)
        for density in (1025., 1000.):
            table = kn.cached_geometry(geometry, params, density)
            assert table is not None, f'Unqualified/missing KN: {name}, {density}'
            assert _covers_domain(table.grid, kn.default_grid(geometry)), f'KN coverage: {name}, {density}'
            row['kn'].append(dict(density=density, qualified=True, path=str(table.cache_path.relative_to(HERE))))
        solids = {}
        for raw in params.get('tanks', []):
            solid = TankGeometry.from_params(raw, ship_params=params)
            if getattr(solid, 'shape', None) != 'hull_sections':
                continue
            solids[raw['id']] = solid
            table = liquid.lookup(solid)
            assert _liquid_covers_domain(table, _liquid_required_grid(raw, liquid)), f'Tank coverage: {name}, {raw["id"]}'
            row['tanks'].append(dict(tank=raw['id'], qualified=True))
        assert _manifest_valid(params, solids), f'Unqualified vessel liquid manifest: {name}'
        row['liquid_vessel_qualified'] = True
        rows.append(row)
        print(json.dumps(dict(event='runtime_cache_validated', ship=name)), flush=True)
    (HERE / 'runtime-cache-status.json').write_text(json.dumps(dict(admission=admission, ships=rows,
        map_files_opened=False, plans_used=False), indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
