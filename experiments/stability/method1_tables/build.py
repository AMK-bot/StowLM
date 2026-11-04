"""Offline resumable per-vessel liquid-cross-curve builder (one CPU core).

Each tank is a shard: atomic NPZ completion survives interruption, and a JSON
progress file records elapsed time and the exact source/geometry cache key.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from stowage.checker import stability_liquid_tables as tables
from stowage.vessel.fleet import load_ship_params
from stowage.vessel.tanks import TankGeometry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel', required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    params = load_ship_params(options.vessel)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    report = {'vessel': options.vessel, 'tanks': [], 'complete': False,
              'grid': {'fill': len(tables.DEFAULT_FILLS), 'heel': len(tables.DEFAULT_HEELS), 'trim': len(tables.DEFAULT_TRIMS)}}
    start = perf_counter()
    for record in params.get('tanks', []):
        if record.get('geometry', {}).get('shape') != 'hull_sections':
            continue
        tank_start = perf_counter()
        solid = TankGeometry.from_params(record, ship_params=params)
        existing = tables.lookup(solid, require_qualified=False)
        reused = existing is not None and all(np.array_equal(getattr(existing, name), values)
            for name, values in [('fills', tables.DEFAULT_FILLS), ('heels', tables.DEFAULT_HEELS), ('trims', tables.DEFAULT_TRIMS)])
        if not reused:
            tables.build(solid)
        row = {'id': record['id'], 'seconds': perf_counter()-tank_start, 'reused': reused,
               'provenance': tables._provenance(solid), 'cache_key': tables._identity(solid), 'cache_path': str(tables._path(solid)), 'capacity_m3': solid.capacity_m3}
        report['tanks'].append(row)
        report['seconds'] = perf_counter()-start
        temporary = options.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2)+'\n')
        temporary.replace(options.output)
        print(json.dumps(row), flush=True)
    report['complete'] = True
    report['seconds'] = perf_counter()-start
    options.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'complete': True, 'seconds': report['seconds']}), flush=True)


if __name__ == '__main__':
    main()
