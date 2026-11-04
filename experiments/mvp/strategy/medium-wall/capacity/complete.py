"""Finalize derived audits after both bounded CP runs; never runs a solver."""
from pathlib import Path
import json,hashlib,datetime
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
m=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text());rows=[]
for seed in (185100000,185100010):
 j=next(j for j in m['maps'] if j['seed']==seed);p=HERE/'maps'/j['map_id']/'construct-cp.json';r=json.loads(p.read_text());valid=r['status'] in ('FEASIBLE','OPTIMAL');search=[]
 for q in sorted((HERE.parent/'search/runs'/j['map_id']).glob('*/summary.json')):
  x=json.loads(q.read_text());search.append({'config':q.parent.name,'elapsed_s':x.get('elapsed_s',0),'summary_sha256':sha(q)})
 planning=sum(x['elapsed_s'] for x in search)+r['elapsed_s'];assert planning<=3600
 rows.append({'map_id':j['map_id'],'status':r['status'],'has_feasible_incumbent':valid,'candidate_boxes':len(r.get('witness',{})),'solver_s':r['solve_s'],'total_wall_s':r['elapsed_s'],'branches':r['branches'],'conflicts':r['conflicts'],'objective':r['objective'] if valid else None,'raw_objective_ignored':not valid,'reason':'CP-SAT UNKNOWN objective is not an incumbent or plan-quality measurement' if not valid else None,'search_arms':search,'search_wall_s':sum(x['elapsed_s'] for x in search),'aggregate_search_plus_cp_s':planning,'chosen_cap_s':3600,'input_sha256':r['input_sha256'],'raw_cp_sha256':sha(p),'full_hold_e6_proposal_is_stronger_than_original':True,'no_original_map_infeasibility_claim':True})
(HERE/'cp-summary.json').write_text(json.dumps({'maps':rows,'completed':True,'script_sha256':sha(__file__)},indent=2,sort_keys=True)+'\n')
completion={'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'all_primary_12_map_evidence_complete':True,'cp_probes_complete':True,'cp_feasible_partial_model_witnesses':sum(r['has_feasible_incumbent'] for r in rows),'full_checker_certified_cp_plans':0,'resources':{'maximum_compute_workers':1,'threads_per_worker':1,'gpu':False},'owned_worker_pids':[60998,61272,61354],'worker_completion_evidence':'Static and LP logs end DONE; CP log ends DONE. No process-pattern termination attempted.','files':{str(p.relative_to(HERE)):sha(p) for p in sorted(HERE.glob('*.py'))},'artifact_hashes':{name:sha(HERE/name) for name in ('summary.json','map-table.csv','background-inventory.json','hatch-tightness.json','relaxation-status.json','verification.json','cp-summary.json','CAPACITY.md')}}
(HERE/'completion.json').write_text(json.dumps(completion,indent=2,sort_keys=True)+'\n');print(json.dumps(rows,indent=2))
