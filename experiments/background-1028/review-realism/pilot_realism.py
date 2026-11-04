"""D1/D2 prototype: seeded per-box caps and tare-constrained rebalancing."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys,json,time,statistics,hashlib
from stowage.vessel import fleet,fleet_balance
from stowage.vessel.fleet_slices import slice_preset,recondition_slice
from stowage.vessel.fleet_conditions import slice_condition,departure_tank_masses,empty_background_witness_tank_masses
from stowage.model import Vessel,Instance,MovePlan
from stowage.model.fixtures import build_instance
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.checker import check
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from design_lashing import design_lashing
HERE=Path(__file__).resolve().parent
source=Path(fleet_balance.__file__).read_text()
source=source.replace('import warnings','import warnings\nfrom random import Random')
source=source.replace('''    available = []
    columns = []''','''    available = []
    deck_caps = {}
    rng = Random(7)
    columns = []''')
source=source.replace('''        if column:
            columns.append((stack, column))''','''        if column:
            if stack.on_deck:
                caps = sorted((rng.triangular(.7*deck_mass_limit, deck_mass_limit,
                    .9*deck_mass_limit) for _ in column), reverse=True)
                deck_caps.update(zip(column, caps))
            columns.append((stack, column))''',1)
source=source.replace('''limits = [(0., deck_mass_limit/1000 if stacks[cells[cid].stack_id].on_deck else 30.480)''','''limits = [(2.2, deck_caps[cid]/1000 if stacks[cells[cid].stack_id].on_deck else 30.480)''')
module=ModuleType('stowage.vessel._prototype_background_realism')
exec(compile(source,'<experimental D1 D2 background allocation>','exec'),module.__dict__)
fleet_balance.balance_background=module.balance_background
(HERE/'prototype_balance.py').write_text(source)
parents={};real=fleet.load_vessel
for ship in ('feeder_500','feeder_800','feeder_1700'):
 cache=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime);parents[ship]=Vessel.model_validate_json(cache.read_text())
def cached(name,*,with_hull=True):return parents[name].model_copy(deep=True)if with_hull and name in parents else real(name,with_hull=with_hull)
fleet.load_vessel=cached
for ship in (sys.argv[1:]or parents):
 for rung in ('tiny','small','medium'):
  start=time.monotonic();r=dict(ship=ship,rung=rung,prototype_sha256=hashlib.sha256(source.encode()).hexdigest())
  try:
   mass={'tiny':50000,'small':250000,'medium':{'feeder_500':1938714.641,'feeder_800':3691076.12,'feeder_1700':6999133.9}[ship]}[rung]
   v=recondition_slice(slice_preset(parents[ship],rung),expected_load_mass_kg=mass,seed=7)
   p=build_instance().model_dump();p.update(id='D1-D2-pilot',vessel=v,containers=[],load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=[],tank_masses_kg=empty_background_witness_tank_masses(v)));p['yard']['stacks']=[];p['port']['max_draught_m']=v.hull.max_draught_m
   for call in p['rotation']['calls']:call['max_on_deck_tiers']=None
   inst=attach_default_voyage(Instance.model_validate(p));plan=MovePlan(id='empty',instance_id=inst.id,call_index=inst.call_index,moves=[])
   report=check(inst,plan);stress=design_lashing(inst,SimpleNamespace(tank_masses_kg=empty_background_witness_tank_masses(v)))
   boxes=[b for l in v.fixed_loads for b in l.cargo_boxes or []];deck=[b.gross_mass_kg for b in boxes if int(b.cell_ids[0][-2:])>=80]
   c=slice_condition(v);r.update(passed=report.passed,reasons=sorted({x.rule_id for x in report.violations}),design_passed=all(x['passed']for x in stress.values()),design_max_ratio=max(z for x in stress.values()for k,z in x['metrics'].items()if k.startswith('max_')and k.endswith('_ratio')),minimum_mass_kg=min(b.gross_mass_kg for b in boxes),deck_std_kg=statistics.pstdev(deck),deck_mean_kg=statistics.mean(deck),boxes=len(boxes),utilisation=len(boxes)/len(parents[ship].cells),cargo_t=sum(b.gross_mass_kg for b in boxes)/1000,gm_screen=c['gm_screen'],witness=c['empty_background_witness_sizing'])
   (HERE/(ship+'-'+rung+'.instance.json')).write_text(inst.model_dump_json())
  except Exception as e:r['error']=type(e).__name__+': '+str(e)
  r['wall_s']=time.monotonic()-start
  with (HERE/'pilot.jsonl').open('a')as f:f.write(json.dumps(r,sort_keys=True)+'\n');f.flush()
  print({k:r.get(k)for k in ('ship','rung','passed','design_passed','minimum_mass_kg','deck_std_kg','deck_mean_kg','utilisation','error','wall_s')},flush=True)
