"""Diagnostic homogeneous condition while production tank assembly is unavailable.

All tanks are explicitly empty by ShipState's missing-entry contract. Construct
an actual complete production HullModel and production cells/covers/fixed loads;
no fixture hull, fitted buoyancy or replacement liquid geometry is used. The
Vessel omits tank records that carry zero weight here. This DOES NOT certify
load_vessel(with_hull=True), nonempty tanks or a plan. The default full_load.py
continues to require the full production loader and does not hide its errors.
One CPU, <1 GB, no GPU; ~4 minutes for three cold complete hull tables.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from time import perf_counter

from stowage.checker.strength import check_strength
from stowage.checker.strength_limits import design_summary, permissible_stations, wave_loads
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.hull import build_hull_model


def main():
    output = Path(__file__).parent
    rows, summaries = {}, {}
    for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
        started = perf_counter()
        params = load_ship_params(name)
        hull = build_hull_model(params)
        hull.stations = permissible_stations(params, hull)
        if load_ship_params(name) != params:
            raise RuntimeError(f'{name}: inputs changed during hull construction; rerun the probe')
        vessel = load_vessel(name, with_hull=False)
        vessel.hull = hull
        instance = build_instance()
        instance.vessel = vessel
        template = instance.containers[1]
        boxes, placements = [], []
        for serial, cell in enumerate((c for c in vessel.cells if c.accepts_20), 200000):
            cid = make_container_id(SYNTHETIC_OWNER_CODES[0], serial)
            boxes.append(template.model_copy(update=dict(id=cid, gross_mass_kg=10_000,
                length_ft=20, height_m=2.591, dg=None, special_stowage=[], oog=None)))
            placements.append(Placement(slot=cell.id, container_id=cid))
        instance.containers = boxes
        instance.onboard = ShipState(vessel_id=vessel.id, placements=placements)
        report = check_strength(instance, instance.onboard, call_index=0)
        status = ('Diagnostic empty-tank state: complete production HullModel and cell geometry; '
                  'full production tank assembly remains unverified')
        row = dict(vessel_id=vessel.id, geometry_status=status, depth_m=hull.depth_m,
            vessel_sources=vessel.sources,
            box_count=len(placements), mass_per_cell_kg=10_000, tank_masses_kg={},
            ran=report.ran, error=report.error, strength_passed=report.ran and not report.violations,
            metrics=report.metrics, margins=report.margins,
            violations=[v.model_dump(mode='json') for v in report.violations],
            assembly_and_check_seconds=perf_counter()-started)
        rows[name] = row
        summary = design_summary(params, hull)
        summary['assembly_status'] = status
        summaries[name] = summary
        kw = {k:summary[k] for k in ('length_m','beam_m','draught_m','block_coefficient',
                                     'waterplane_coefficient','bow_flare_coefficient')}
        with (output/f'{name}.approved-diagnostic.csv').open('w', newline='') as stream:
            fields = ['x_m','wave_rule_x_m','max_shear_n','min_shear_n','max_bending_nm','min_bending_nm',
                      'wave_hogging_nm','wave_sagging_nm','wave_max_shear_n','wave_min_shear_n']
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for station in hull.stations:
                data = station.model_dump(exclude={'max_torsion_nm'})
                rule_x = min(max(station.x_m, 0.), hull.lpp_m)
                data['wave_rule_x_m'] = rule_x
                data.update({'wave_'+key:value for key,value in wave_loads(rule_x, **kw).items()})
                writer.writerow(data)
        print(name, 'D', hull.depth_m, 'boxes', len(placements), 'ran', report.ran,
              'SF', report.metrics.get('max_sf_ratio'), 'BM', report.metrics.get('max_bm_ratio'),
              'violations', len(report.violations), flush=True)
        # Checkpoint complete ships; a later unrelated input error preserves work.
        (output/'approved-empty-tank-probe.json').write_text(json.dumps(rows, indent=2, sort_keys=True)+'\n')
        (output/'approved-design-envelopes.json').write_text(json.dumps(summaries, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
