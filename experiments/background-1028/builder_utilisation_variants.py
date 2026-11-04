"""Experiment-only D58 deck envelope variants; production source stays untouched.

Reuse unchanged full physical parents to avoid rebuilding identical KN tables.
Only the builder function is replaced by a transient compiled module. Every
candidate uses the unchanged full checker and separate D56 design-GM diagnostic.
"""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from hashlib import sha256
import json,time
from stowage.vessel import fleet, fleet_balance
from stowage.vessel.fleet_conditions import departure_tank_masses,slice_condition
from stowage.vessel.fleet_slices import recondition_slice,slice_preset
from stowage.model import Vessel,Instance,MovePlan
from stowage.checker import check
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.model.fixtures import build_instance
from design_lashing import design_lashing
HERE=Path(__file__).resolve().parent
# Preserve the exact two-heavy-tier baseline used for the candidate sweep.
# The content-addressed source files make old variant hashes reproducible after
# production adopts a selected profile. Set source explicitly for final probes.
_base_rows=(HERE/'builder-utilisation-variants.jsonl').read_text().splitlines()
_base_hash=json.loads(_base_rows[0])['production_source_sha256']
source=(HERE/'variant-sources'/(_base_hash+'.py')).read_text()
assert sha256(source.encode()).hexdigest()==_base_hash
real_load=fleet.load_vessel
parents={}
for ship in ('feeder_1700','feeder_800'):
 cache=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime)
 parents[ship]=Vessel.model_validate_json(cache.read_text())
def diagnostic_load(name, *, with_hull=True):
 return parents[name].model_copy(deep=True) if with_hull and name in parents else real_load(name,with_hull=with_hull)
fleet.load_vessel=diagnostic_load
coef=dict(zip(('area_0_30_mrad','area_0_40_mrad','area_30_40_mrad','gz_at_or_above_30_m','gz_max_m','area_0_flood_mrad'),(.009,.016,.006,.033,.042,.029)))

def run(cap_t,ship,rung,tiers=3,seed=1):
 started=time.monotonic()
 text=source.replace('index >= 2',f'index >= {tiers}').replace('len(column)*(24999. if stack.on_deck else 30480.)',f'len(column)*({cap_t*1000:.1f} if stack.on_deck else 30480.)').replace('limits = [(0., 24.999 if',f'limits = [(0., {cap_t:.3f} if')
 module=ModuleType('stowage.vessel._experimental_d58_balance')
 exec(compile(text, '<experiment-only-D58-deck-variant>', 'exec'),module.__dict__)
 fleet_balance.balance_background=module.balance_background
 row=dict(label=f'{tiers}tiers_{cap_t}t',ship=ship,rung=rung,seed=seed,variant_source_sha256=sha256(text.encode()).hexdigest(),production_source_sha256=sha256(source.encode()).hexdigest())
 try:
  v=recondition_slice(slice_preset(parents[ship],rung),expected_load_mass_kg={'tiny':50000,'small':250000}[rung],seed=seed)
  p=build_instance().model_dump();p.update(id='bg-variant',vessel=v,containers=[],load_list=[],discharge_list=[],onboard=dict(vessel_id=v.id,placements=[],tank_masses_kg=departure_tank_masses(v)))
  p['yard']['stacks']=[];p['port']['max_draught_m']=v.hull.max_draught_m
  for call in p['rotation']['calls']:call['max_on_deck_tiers']=None
  inst=attach_default_voyage(Instance.model_validate(p));plan=MovePlan(id='empty',instance_id=inst.id,call_index=inst.call_index,moves=[])
  report=check(inst,plan)
  stress=design_lashing(inst,SimpleNamespace(tank_masses_kg=departure_tank_masses(v)))
  boxes=[b for load in v.fixed_loads for b in load.cargo_boxes or []]
  c=slice_condition(v)
  states={}
  for name,part in report.parts.items():
   if name.startswith('condition:') and name.endswith(':stability'):
    m=part.metrics;C=m.get('containership_form_factor')
    phase=next((x for x in ('departure','mid_voyage','arrival') if 'condition.phase: '+x in m),name)
    states[phase]=dict(ratio=min(m[k]*C/a for k,a in coef.items()) if C else None,gm=m.get('gm_m'),kg=m.get('kg_m'))
  row.update(passed=report.passed,reasons=sorted({v.rule_id for v in report.violations}),states=states,design_passed=all(x['passed']for x in stress.values()),design_max_ratio=max(v for p in stress.values() for k,v in p['metrics'].items() if k.startswith('max_') and k.endswith('_ratio')),design_stress=stress,background_boxes=len(boxes),parent_teu=len(parents[ship].cells),utilisation=len(boxes)/len(parents[ship].cells),cargo_t=sum(b.gross_mass_kg for b in boxes)/1000,deck_mean_t=sum(b.gross_mass_kg for b in boxes if int(b.cell_ids[0][-2:])>=80)/max(1,sum(int(b.cell_ids[0][-2:])>=80 for b in boxes))/1000,sub_tare_count=sum(b.gross_mass_kg<2200-1e-6 for b in boxes),screen=c['gm_screen'])
  (HERE/f'{ship}-{rung}-{tiers}tiers{cap_t}t-seed{seed}.instance.json').write_text(inst.model_dump_json())
 except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
 row['wall_s']=time.monotonic()-started
 with (HERE/'builder-utilisation-variants.jsonl').open('a')as stream:stream.write(json.dumps(row,sort_keys=True)+'\n');stream.flush()
 print({k:row.get(k) for k in ('label','ship','rung','passed','design_passed','design_max_ratio','background_boxes','utilisation','sub_tare_count','error','wall_s')},flush=True)
 return row
if __name__=='__main__':
 results=[run(cap,'feeder_1700','tiny',tiers=4) for cap in (6,7,8)]
 legal=[r for r in results if r.get('passed') and r.get('design_passed')]
 if legal:
  best=max(legal,key=lambda r:r['utilisation'])
  cap=int(best['label'].split('_')[1][:-1])
  run(cap,'feeder_1700','small',tiers=4)
  run(cap,'feeder_1700','small',tiers=4,seed=7)
  run(cap,'feeder_1700','tiny',tiers=4,seed=7)
 run(10,'feeder_800','small',tiers=3)
 run(10,'feeder_800','small',tiers=3,seed=7)
 run(10,'feeder_800','tiny',tiers=3,seed=7)
