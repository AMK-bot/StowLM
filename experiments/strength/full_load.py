"""Homogeneous 10-t/20-foot-cell strength utilization for all synthetic feeders.

Only strength is evaluated: this is not a departure-approved loading plan.
The fixture builder supplies an Instance shell and all vessel data come from
load_vessel(with_hull=True), including checksum-pinned KCS geometry. Production
assembly includes its full hydrostatic/KN tables; strength solves the actual
HullGeometry equilibrium directly. One CPU thread, no GPU, expected <1 GB;
cold three-vessel table assembly takes about three minutes before cache reuse.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from stowage.checker.strength import check_strength
from stowage.checker.strength_limits import permissible_stations
from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model import Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_ship_params, load_vessel
from stowage.vessel.tanks import build_tanks


def make_condition(name, *, mass_per_cell_kg=10_000.):
    vessel = load_vessel(name, with_hull=True)
    instance = build_instance()
    instance.vessel = vessel
    template = instance.containers[1]
    boxes, placements = [], []
    for serial, cell in enumerate((c for c in vessel.cells if c.accepts_20), 200000):
        cid = make_container_id(SYNTHETIC_OWNER_CODES[0], serial)
        boxes.append(template.model_copy(update=dict(id=cid, gross_mass_kg=mass_per_cell_kg,
            length_ft=20, height_m=2.591, reefer=False, reefer_setpoint_c=None,
            dg=None, special_stowage=[], oog=None)))
        placements.append(Placement(slot=cell.id, container_id=cid))
    instance.containers = boxes
    instance.onboard = ShipState(vessel_id=vessel.id, placements=placements)
    return instance


def main():
    rows = {}
    for name in ('feeder_500', 'feeder_800', 'feeder_1700'):
        instance = make_condition(name)
        start = perf_counter()
        report = check_strength(instance, instance.onboard, call_index=0)
        row = dict(box_count=len(instance.onboard.placements), mass_per_cell_kg=10_000,
            depth_m=instance.vessel.hull.depth_m,
            scantling_draught_m=instance.vessel.hull.scantling_draught_m,
            vessel_id=instance.vessel.id, vessel_sources=instance.vessel.sources,
            geometry_status='Production load_vessel(with_hull=True), checksum-pinned KCS offsets',
            ran=report.ran, error=report.error, seconds=perf_counter()-start,
            strength_passed=report.ran and not report.violations,
            metrics=report.metrics, margins=report.margins,
            violations=[v.model_dump(mode='json') for v in report.violations])
        rows[name] = row
        print(name, report.ran, 'SF', report.metrics.get('max_sf_ratio'),
              'BM', report.metrics.get('max_bm_ratio'), 'violations', len(report.violations), flush=True)
    (Path(__file__).parent/'full_load.json').write_text(json.dumps(rows, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    main()
