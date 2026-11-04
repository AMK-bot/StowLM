"""Preselect release small cases and explicitly non-release tiny controls."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'experiments/instances-v3/manifest.json').read_text());rows=[]
for gen in ('random_yard','simulated_yard'):
 group=manifest['groups'][gen+'/small']
 shards=[s for s in group['shards'] if s['split'] in ('train','val')][:6]
 for i,s in enumerate(shards):
  source=ROOT/'experiments/instances-v3'/gen/'small'/s['instance']
  rows.append(dict(generator=gen,rung='small',vessel=s['spec']['vessel'],source=str(source),release_member=True,split=s['split']))
 for ship in ('feeder_500','feeder_800','feeder_1700'):
  for seed in (2,3):
   prefix='random' if gen=='random_yard' else 'simulated'
   source=ROOT/f'experiments/stability/b23-attribution/instances/{prefix}-{ship}-tiny-{seed}.instance.json'
   rows.append(dict(generator=gen,rung='tiny',vessel=ship,source=str(source),release_member=False,qualification='Independent attribution saved diagnostic; requested release has zero accepted tiny.'))
for i,r in enumerate(rows):
 source=Path(r['source']);path=OUT/'inputs'/f'{i:02}-{r["generator"]}-{r["rung"]}-{r["vessel"]}.json';path.parent.mkdir(exist_ok=True);shutil.copyfile(source,path)
 r.update(id=path.stem,path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
(OUT/'selection.json').write_text(json.dumps(dict(diagnostic=True,evidence_eligible=False,release_coverage_gap='tiny0 accepted; small only feeder500; no released input can satisfy allship/rung request',rows=rows),indent=2)+'\n')
print('selected',len(rows),'cases')
