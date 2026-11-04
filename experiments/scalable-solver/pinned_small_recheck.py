"""Recheck the pinned train incumbent after verified-incumbent admission changed."""
import hashlib,json,time
from pathlib import Path
from stowage.io import load_instance,load_plan
from stowage.moves import apply_plan
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig
from stowage.solver.scalable import improve,ScalableConfig,SCALABLE_VERSION
base=Path('experiments/exact-solver/paired-quiet-current')
rows=[json.loads(s) for s in (base/'results.jsonl').read_text().splitlines()]
x=next(r for r in rows if r.get('planner')=='scalable' and r['entry']['rung']=='small' and r.get('validation',{}).get('objective')==40)
assert x['entry']['split'] in ('train','val')
p=base/'instances'/'small'/(x['entry']['spec_key']+'.json')
i=load_instance(p); plan=load_plan(x['checked_reference_plan'])
source=lambda: hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path('src/stowage/solver').glob('scalable*.py')))).hexdigest()
out={'version':SCALABLE_VERSION,'selection':x['entry'],'input_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'diagnostic_c2_off':True,'source_before':source(),'before_checker_passed':check(i,plan).passed,'before_cost':apply_plan(i,plan).count.weighted_nonproductive}
t=time.monotonic()
r=improve(i,plan,time_limit_s=120,config=ScalableConfig(exact_window_time_limit_s=20,exact_window_max_nodes=100000,exact_window_max_boxes=16,exact_whole_plan_boxes=8,feasibility_config=FeasibilityConfig(extra={'global_screen':0})))
out.update(status=r.status,search_s=r.wall_time_s,metrics=r.metrics,after_cost=apply_plan(i,r.plan).count.weighted_nonproductive if r.plan else None,after_checker_passed=check(i,r.plan).passed if r.plan else False,total_s=time.monotonic()-t,source_after=source())
Path('experiments/scalable-solver/quality-pinned-small-v263.json').write_text(json.dumps(out,indent=2,default=str)+'\n')
print(json.dumps({k:out[k] for k in ('version','before_cost','after_cost','after_checker_passed','search_s')}))
