"""Cheap import/patch/layout setup checks, no solve or physics evaluation."""
import importlib.util,json,sys,inspect,ast,hashlib,time
from pathlib import Path
H=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('medium_wall_search',H/'search.py');search=importlib.util.module_from_spec(spec);spec.loader.exec_module(search)
name=sys.argv[1];choices=search.read(H/'preregister.json')['configs']+(search.read(H/'supplemental-config.json')['configs'] if (H/'supplemental-config.json').exists() else []);config=dict(next(c for c in choices if c['name']==name))
if name=='constructor_wide':config.update(securing_screen=True,heavy_first_ordering=True,max_restarts=3,carrier_fraction=.9)
folder=H/'smoke'/name;folder.mkdir(parents=True,exist_ok=True)
tw=search.patch(config,folder)
assert str(search.RUNTIME/'src') in tw.__file__
for p in folder.glob('effective*.py'):ast.parse(p.read_text())
fast=(folder/'effective_fast_prestow.py').read_text()
if config.get('restricted_first'):
 assert 'return (cid in load, not bool(b.dg or b.reefer or b.oog or b.special_stowage),' in fast
if config.get('carrier_fraction'):
 assert f"deadline=start + limit * {config['carrier_fraction']!r}" in (folder/'effective_planner_method.py').read_text()
if config['staged']:
 assert fast.count('cover_station =')==1 and fast.count('station_fill[station(covers)] =')==1
 assert '-station_fill.get(station(covers), 0)' in fast
 from stowage.io import load_instance
 jobs=search.read(H/'preregister.json')['jobs'];groups=[]
 for job in jobs:
  inst=load_instance(search.ROOT/job['input']);stacks={s.id:s for s in inst.vessel.stacks}
  by={h.id:tuple(sorted({stacks[sid].bay for sid in h.hold_stack_ids+h.deck_stack_ids if sid in stacks})) for h in inst.vessel.hatch_covers}
  grouped={k:[h for h,v in by.items() if v==k] for k in set(by.values())}
  assert all(len(v)==2 for v in grouped.values()),grouped
  groups.append(dict(map_id=job['map_id'],stations={str(k):v for k,v in grouped.items()}))
 search.save(folder/'stations.json',groups)
if config['widen']:
 import stowage.solver.twostep_construct as tc
 cfg=tc.ScalableConfig(constructor_v4=True,max_neighborhoods=0)
 assert cfg.backtrack_limit==32768 and cfg.max_restarts==config.get('max_restarts',15)
 assert cfg.securing_screen==config.get('securing_screen',False)
 if config.get('legacy_beam'):assert cfg.constructor_v4 is False and cfg.beam_width==16 and cfg.branch_factor==64 and cfg.max_neighborhoods==0
 source=(folder/'effective_constructor_choices.py').read_text();assert 'reps[:min(12, len(reps))]' in source
modules={k:str(v.__file__) for k,v in sys.modules.items() if k.startswith('stowage') and getattr(v,'__file__',None)}
assert all(Path(v).resolve().is_relative_to(search.RUNTIME/'src') for v in modules.values())
search.save(folder/'verification.json',dict(passed=True,config=config,checked_unix=time.time(),pinned_module_count=len(modules),files={p.name:search.digest(p) for p in folder.glob('effective*.py')}))
print(name,'PASS',len(modules),'pinned modules')
