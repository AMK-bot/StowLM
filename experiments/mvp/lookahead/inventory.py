"""Finite synthetic input and historical gold snapshot; no solver or physics calls."""
from __future__ import annotations
import hashlib,json
from collections import defaultdict,deque
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,sort_keys=True,indent=2)+'\n')
def pin(src,dst):
 src=Path(src);dst.parent.mkdir(parents=True,exist_ok=True);data=src.read_bytes()
 if dst.exists():assert dst.read_bytes()==data
 else:dst.write_bytes(data)
 return dict(source=str(src),path=str(dst),sha256=sha(dst))
def stratified(rows,n):
 groups=defaultdict(deque)
 for row in sorted(rows,key=lambda r:r['source']):groups[(row['ship'],row['family'])].append(row)
 chosen=[]
 while len(chosen)<n and any(groups.values()):
  for group in sorted(groups):
   if groups[group]:chosen.append(groups[group].popleft())
   if len(chosen)==n:break
 assert len(chosen)==n,(len(chosen),n)
 return chosen
def identity(path,cohort):
 d=json.loads(path.read_text());parts=path.parts;family=path.parent.name;ship=path.parent.parent.name
 return dict(case=f'{cohort}-{d["seed"]}',cohort=cohort,source=str(path),instance_id=d['id'],seed=d['seed'],ship=ship,family=family,rung=d['size_class'],loads=len(d['load_list']))
def main():
 configpath=ROOT/'experiments/mvp/runs/base-mask/small/config.json';config=json.loads(configpath.read_text());tests=[]
 for entry in config['inputs']:
  path=ROOT/entry['path'];assert sha(path)==entry['sha256'];row=identity(path,'test15');row['original_sha256']=entry['sha256'];tests.append(row)
 assert len(tests)==15
 trainroot=ROOT/'experiments/mvp/corpus/train/small';available=[identity(p,'train20')for p in trainroot.glob('*/*/seed-*.json')if not p.name.endswith('.audit.json')];train=stratified(available,20)
 rollouts=[]
 for row in tests+train:
  row['instance']=pin(row['source'],HERE/'inputs'/row['case']/'instance.json');rollouts.append(row)
 contracts=[];goldcounts={}
 for rung in ['tiny','small']:
  accepted=[]
  for p in sorted((ROOT/'experiments/mvp/gold/train'/rung).glob('*/*/seed-*/result.json')):
   d=json.loads(p.read_text())
   if not(d.get('completed')and d.get('passed')and all(d.get('stages',{}).get(k,{}).get('passed')for k in ['checker','mask'])):continue
   inp=Path(d['input_path']);plan=p.with_name('plan.json');report=p.with_name('checker.json')
   if not(inp.exists()and plan.exists()and report.exists()):continue
   if sha(inp)!=d['input_sha256']or sha(plan)!=d['plan_sha256']:continue
   evidence=json.loads(report.read_text())
   if not evidence['passed']:continue
   row=identity(inp,'gold-'+rung);row.update(source=str(p),gold_input=str(inp),gold_plan=str(plan),gold_checker=str(report),historical_runtime=d.get('runtime_epoch_sha256'),historical_checker_source=d.get('versions'),gold_result_sha256=sha(p));accepted.append(row)
  goldcounts[rung]=len(accepted)
  for row in stratified(accepted,50):
   out=HERE/'contracts'/row['case'];row['instance']=pin(row['gold_input'],out/'instance.json');row['plan']=pin(row['gold_plan'],out/'plan.json');row['checker']=pin(row['gold_checker'],out/'historical-checker.json');row['gold_result']=pin(row['source'],out/'historical-result.json');contracts.append(row)
 assert len(contracts)==100 and len(rollouts)==35
 save(HERE/'inventory.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),rollout_instances=rollouts,contracts=contracts,available_gold_passes=goldcounts,base_run_config_sha256=sha(configpath),rollouts_per_arm_per_instance=20,arms=['production+securing','production+securing+lookahead'],planned_rollouts=1400,selection='Exact15base-masktestv2.1inputs;20traininputs and50goldperrung chosen deterministic round-robin across ship/family, earliest available path within each group. Train20 NOTfiltered by gold availability. Historical checker acceptance remains labeled historical; no physics rerun or randomcompletion pass claim.'))
 print(json.dumps(dict(rollout_instances=35,contracts=100,available_gold_passes=goldcounts,planned_rollouts=1400)))
if __name__=='__main__':main()
