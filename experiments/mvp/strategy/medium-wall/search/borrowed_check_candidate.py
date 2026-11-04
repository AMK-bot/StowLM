"""Unchanged full checker on one explicitly allowed TRAIN candidate at inherited ballast."""
from pathlib import Path
import os,sys,json,time,hashlib,argparse
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[4];RUNTIME=HERE.parent/'search/runtime'
if Path(sys.prefix).resolve()!=(ROOT/'.venv').resolve():
 python=ROOT/'.venv/bin/python';os.execv(str(python),[str(python),*sys.argv])
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS'):os.environ[k]='1'
os.environ['STOWAGE_IN_PORT_WORKERS']='1';os.environ.pop('STOWAGE_BALLAST_V4',None);sys.path.insert(0,str(RUNTIME/'src'))
from stowage.io import load_instance
from stowage.model import PlannerResult
from stowage.checker import check
from stowage.rules.catalogue import rule_freeze_v1

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n');t.replace(p)
def main():
 a=argparse.ArgumentParser();a.add_argument('--map',required=True);a.add_argument('--result',required=True);a.add_argument('--out',required=True);args=a.parse_args();manifest=json.loads((ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json').read_text());j=next(j for j in manifest['maps'] if j['map_id']==args.map);assert j['split']=='train';p=ROOT/j['input'];assert sha(p)==j['sha256'];resultpath=Path(args.result).resolve();assert resultpath.is_relative_to(HERE.parent/'search/cp-candidates');out=Path(args.out).resolve();assert out.is_relative_to(HERE);out.mkdir(parents=True,exist_ok=True);save(out/'owned.json',{'pid':os.getpid(),'workers':1});r=PlannerResult.model_validate(json.loads(resultpath.read_text()));assert r.plan is not None;instance=load_instance(p);start=time.monotonic()
 with rule_freeze_v1(enabled=False):report=check(instance,r.plan)
 elapsed=time.monotonic()-start;raw=report.model_dump(mode='json');save(out/'full-report.json',raw);violations=[v for part in raw['parts'].values() for v in part.get('violations',[])];summary={'map_id':args.map,'input':j['input'],'input_sha256':j['sha256'],'result_path':str(resultpath.relative_to(ROOT)),'result_sha256':sha(resultpath),'plan_ballast':r.plan.ballast.model_dump(mode='json') if r.plan.ballast else None,'rule_freeze_v1':False,'STOWAGE_IN_PORT_WORKERS':'1','STOWAGE_BALLAST_V4':None,'runtime_snapshot_identity':json.loads((RUNTIME/'snapshot.json').read_text())['snapshot_sha256'],'elapsed_s':elapsed,'checker_passed':raw['passed'],'rules':sorted({v['rule_id'] for v in violations}),'part_failures':{name:part for name,part in raw['parts'].items() if not part.get('passed',True)},'full_report_sha256':sha(out/'full-report.json'),'script_sha256':sha(__file__),'check_only_no_ballast_search':True};save(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k not in ('part_failures','plan_ballast')},indent=2))
if __name__=='__main__':main()
