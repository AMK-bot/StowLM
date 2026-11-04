"""One-thread complete-check timing with every feeder cell and slack tanks.

The synthetic stress condition is for workload timing only, not an approved
manifest. Filling all cells exercises cargo moment and windage union costs;
liquids exercise coupled equilibrium and exact finite-angle corrections.
"""
from __future__ import annotations
import contextlib
import io
import json
from pathlib import Path
from time import perf_counter
from departure import main
from stowage.checker.stability import check_stability
from stowage.conventions import make_container_id
from stowage.model import Container, Placement


def benchmark():
    with contextlib.redirect_stdout(io.StringIO()):
        instance = main()
    containers, placements = [], []
    for serial, cell in enumerate(instance.vessel.cells, 1):
        cid = make_container_id('QZXU', serial)
        containers.append(Container(id=cid,iso_type='22G1',length_ft=20,height_m=2.591,
            tare_kg=2200,gross_mass_kg=8000,pol='THLCH',pod='THBKK'))
        placements.append(Placement(slot=cell.id,container_id=cid))
    instance.containers = containers
    instance.onboard.placements = placements
    instance.onboard.tank_masses_kg = {t.id:t.capacity_m3*t.content_density_kg_m3*.5 for t in instance.vessel.tanks}
    times = []
    for _ in range(3):
        start = perf_counter()
        report = check_stability(instance,instance.onboard,call_index=0)
        times.append(perf_counter()-start)
    result = dict(condition='every cell occupied; all tanks half full; workload only',
        containers=len(containers),tanks=len(instance.vessel.tanks),times_s=times,
        ran=report.ran,error=report.error,violations=[v.rule_id for v in report.violations])
    (Path(__file__).parent/'full_feeder_timing.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    benchmark()
