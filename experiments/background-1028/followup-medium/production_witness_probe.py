"""Verify production medium witness helpers with independent full checks."""
from pathlib import Path
import sys,json,time
from types import SimpleNamespace
from stowage.vessel import fleet
from stowage.vessel.fleet_slices import slice_preset,recondition_slice
from stowage.vessel.fleet_conditions import (slice_condition,departure_tank_masses,
    empty_background_witness_tank_masses)
from stowage.model import Vessel,Instance,MovePlan
from stowage.model.fixtures import build_instance
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.checker import check
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from design_lashing import design_lashing
HERE=Path(__file__).resolve().parent
parents={};real_load=fleet.load_vessel
for ship in ('feeder_500','feeder_800','feeder_1700'):
 cache=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime);parents[ship]=Vessel.model_validate_json(cache.read_text())
def cached(name,*,with_hull=True):return parents[name].model_copy(deep=True)if with_hull and name in parents else real_load(name,with_hull=with_hull)
fleet.load_vessel=cached
for ship in (sys.argv[1:]or parents):
 start=time.monotonic();mass={'feeder_500':1914802.66,'feeder_800':3691076.12,'feeder_1700':6999133.90}[ship]
 v=recondition_slice(slice_preset(parents[ship],'medium'),expected_load_mass_kg=mass,seed=0)
 c=slice_condition(v);booked=departure_tank_masses(v);witness=empty_background_witness_tank_masses(v)
 p=build_instance().model_dump();p.update(id='production-medium-witness',vessel=v,containers=[],load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=[],tank_masses_kg=witness));p['yard']['stacks']=[];p['port']['max_draught_m']=v.hull.max_draught_m
 for call in p['rotation']['calls']:call['max_on_deck_tiers']=None
 inst=attach_default_voyage(Instance.model_validate(p));plan=MovePlan(id='empty',instance_id=inst.id,call_index=inst.call_index,moves=[])
 report=check(inst,plan);design=design_lashing(inst,SimpleNamespace(tank_masses_kg=witness))
 row=dict(ship=ship,version=c['version'],passed=report.passed,reasons=sorted({x.rule_id for x in report.violations}),design_passed=all(x['passed']for x in design.values()),witness=c['empty_background_witness_sizing'],booking_budget=c['booking_departure_budget'],booking_sizing=c['booking_reference_ballast_sizing'],booked_tanks=booked,witness_tanks=witness,states={name:part.metrics for name,part in report.parts.items()if name.startswith('condition:')and name.endswith(':stability')},wall_s=time.monotonic()-start)
 (HERE/(ship+'-production-medium-witness.instance.json')).write_text(inst.model_dump_json())
 print(json.dumps(row,sort_keys=True),flush=True)
