"""Probe a labelled synthetic, passing property-test condition; no part mocks."""
import importlib.util
import json
from pathlib import Path
from time import perf_counter
from stowage.checker.full import check
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.model import FixedLoad, FixedLoadSilhouette, Instance, Station
from stowage.vessel.fleet import load_ship_params

root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('full_properties', root/'tests/checker/test_full_properties.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
instance, plan=module._random_plan(0)
hull=instance.vessel.hull
geometry=StabilityGeometry(load_ship_params('feeder_500'))
condition=geometry.full_submerged(5.5,5.5)
boxes={b.id:b for b in instance.containers}
cells={c.id:c for c in instance.vessel.cells}
mass=condition.displacement_kg-hull.lightship_mass_kg-sum(b.gross_mass_kg for b in boxes.values())
x=(condition.displacement_kg*condition.lcb_m-hull.lightship_mass_kg*hull.lightship_lcg_m
   -sum(boxes[m.container_id].gross_mass_kg*cells[m.dst].lcg_m for m in plan.moves))/mass
y=-sum(boxes[m.container_id].gross_mass_kg*cells[m.dst].tcg_m for m in plan.moves)/mass
# APPROXIMATION: a submerged synthetic cargo envelope and broad declared test
# strength limits isolate orchestration/fault tests, not production D22 approval.
instance.vessel.fixed_loads=[FixedLoad(x_m=x,y_m=y,z_m=4.5,mass_kg=mass,kind='cargo',
    silhouette=FixedLoadSilhouette(x_min_m=5,x_max_m=hull.lpp_m-5,
        y_min_m=-hull.beam_m/3,y_max_m=hull.beam_m/3,z_min_m=1,z_max_m=5))]
hull.stations=[Station(x_m=p,max_shear_n=1e10,min_shear_n=-1e10,
    max_bending_nm=1e12,min_bending_nm=-1e12) for p in(0,hull.lpp_m/2,hull.lpp_m)]
instance.rule_overrides={}
instance=Instance.model_validate(instance.model_dump())
start=perf_counter()
report=check(instance,plan)
result=dict(passed=report.passed,seconds=perf_counter()-start,
    parts={n:dict(ran=p.ran,error=p.error) for n,p in report.parts.items()},
    violations=[v.model_dump() for v in report.violations],
    stability=report.parts['stability'].metrics)
(root/'experiments/checker/v12-property-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='stability'},indent=2))
