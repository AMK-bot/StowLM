"""Fresh two complete fixed-input sample gates under combined v2 source."""
from pathlib import Path
import sys,os,json,time,shutil
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
RUNTIME=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
from runtime import configure_runtime,verify_runtime,assert_runtime_imports,save,sha,object_sha,install_write_guard
initial=configure_runtime(RUNTIME)
from worker import caches
caches(OUT)
from stowage.io import load_instance,load_plan
from stowage.checker import check,report_matches_inputs
from stowage.frozen import require_clean
from llm.exit.selection import mask_replay
install_write_guard((RUNTIME,))
rows=[]
for label in ('showcase','val-c-append'):
 source=ROOT/'experiments/mvp/batch-d132/samples'/label;dest=OUT/label;dest.mkdir()
 for name in ('instance.json','plan.json'):shutil.copy2(source/name,dest/name)
 instance,plan=load_instance(dest/'instance.json'),load_plan(dest/'plan.json');require_clean(instance,plan,what='D132v2 fixed complete sample')
 before=object_sha([instance.model_dump(mode='json'),plan.model_dump(mode='json')]);start=time.perf_counter()
 report=check(instance,plan);checker_s=time.perf_counter()-start
 assert report.passed and report_matches_inputs(report,instance,plan)
 value=report.model_dump(mode='json');payload=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
 (dest/'report.json').write_bytes(payload)
 old=json.loads((source/'report.json').read_text());oldpayload=json.dumps(old,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
 assert oldpayload==payload,'Full unchanged sample report differs (no fields stripped)'
 start=time.perf_counter();mask=mask_replay(instance,plan);mask_s=time.perf_counter()-start
 assert mask['passed'];metrics=mask['securing_metrics'];assert metrics.get('screens',0)>0 and metrics.get('evaluations',0)>0  # exit.securing_guard schema
 assert metrics.get('unavailable')==metrics.get('fail_open')==0
 assert before==object_sha([instance.model_dump(mode='json'),plan.model_dump(mode='json')])
 row=dict(label=label,passed=True,instance_id=instance.id,input_sha256=sha(dest/'instance.json'),plan_sha256=sha(dest/'plan.json'),
 report_sha256=sha(dest/'report.json'),canonical_full_report_identical_v1=True,checker_s=checker_s,mask_s=mask_s,
 production_exit_mask=mask,weighted_nonproductive=report.count.weighted_nonproductive,source_epoch=initial['source_epoch_sha256'])
 save(dest/'result.json',row);rows.append(row);save(OUT/'result.json',dict(complete=len(rows)==2,rows=rows,source_unchanged=verify_runtime(RUNTIME)==initial,imports=assert_runtime_imports(RUNTIME),gpu=False))
 print(json.dumps({k:v for k,v in row.items() if k!='production_exit_mask'}),flush=True)
