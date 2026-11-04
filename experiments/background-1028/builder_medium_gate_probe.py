"""D58 medium diagnosis using the unchanged assembled physical parent cache."""
from pathlib import Path
import json,time
from stowage.model import Vessel,Instance
from stowage.vessel import fleet
from stowage.generate.background_acceptance import evaluate_background
parents={}
for ship in ('feeder_500','feeder_800','feeder_1700'):
 cache=max(Path('experiments/cache/vessels').glob(ship+'.full.hull.*.json'),key=lambda p:p.stat().st_mtime)
 parents[ship]=Vessel.model_validate_json(cache.read_text())
real=fleet.load_vessel
def diagnostic_load(name, *, with_hull=True):
 return parents[name].model_copy(deep=True) if with_hull and name in parents else real(name,with_hull=with_hull)
fleet.load_vessel=diagnostic_load
for ship in ('feeder_500','feeder_1700'):
 inst=Instance.model_validate_json(Path('experiments/background-1028/'+ship+'-medium.builder.instance.json').read_text())
 started=time.monotonic()
 result=evaluate_background(inst,time_limit_s=10.,use_cache=False)
 print(json.dumps(dict(ship=ship,accepted=result.accepted,finisher=result.finisher_status,reasons=result.reasons,states=result.states,wall_s=time.monotonic()-started)),flush=True)
