"""Real unchanged-v2 innermost checker ACK on an approved saved TRAIN tiny plan."""
from pathlib import Path
import sys,os,json,hashlib,time,resource
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
RUNTIME=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(RUNTIME/'src'));os.environ['TMPDIR']=str(OUT/'tmp');(OUT/'tmp').mkdir(exist_ok=True)
import tempfile;tempfile.tempdir=os.environ['TMPDIR']
from runtime import verify_runtime,source_pins,assert_runtime_imports,save,sha
initial=verify_runtime(RUNTIME)
# Capture loaded outcome epoch before model/physics imports, as the real runner does.
from llm.exit.outcome_epoch import process_snapshot
expected=process_snapshot()
from llm.exit.checker_watchdog import run_full_check
from stowage.io import load_instance,load_plan
from stowage.checker import report_matches_inputs
from stowage.frozen import require_clean
inv=json.loads((ROOT/'experiments/mvp/batch-d132/checker/inventory.json').read_text())
case=next(c for c in inv['cases'] if c['split']=='train' and c['rung']=='tiny')
assert case['corpus_path'].startswith('train/tiny/')
assert case['input_sha256'] not in inv['exclusion']['test_input_hashes']
assert case['instance_id'] not in inv['exclusion']['test_instance_ids']
assert sha(case['input_path'])==case['input_sha256'] and sha(case['plan_path'])==case['plan_sha256']
instance,plan=load_instance(case['input_path']),load_plan(case['plan_path']);require_clean(instance,plan,what='D132v2 saved TRAIN ACK')
start=time.perf_counter();outcome=run_full_check(instance,plan,timeout_s=120,expected_outcome_epoch=expected)
ack=outcome.epoch_ack or {};bound=bool(outcome.report and report_matches_inputs(outcome.report,instance,plan))
passed=bool(outcome.available and outcome.report and outcome.report.passed)
if outcome.report:
 raw=json.dumps(outcome.report.model_dump(mode='json'),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
 (OUT/'full-report.json').write_bytes(raw)
save(OUT/'ack.json',ack)
result=dict(available=outcome.available,full_checker_passed=passed,input_binding_verified=bound,reason=outcome.reason,
 epoch_ack_error=outcome.epoch_ack_error,nonce_acknowledged=bool(ack.get('request_id')),pre_matches=ack.get('loaded_pre')==expected,
 post_matches=ack.get('loaded_post')==expected,pins=len(expected),imports=len(ack.get('imports',{})),resource_reads=len(ack.get('resources',{})),
 checker_s=outcome.checker_s,wall_s=time.perf_counter()-start,details=outcome.details(),case=case,
 full_report_sha256=sha(OUT/'full-report.json') if outcome.report else None,source_epoch_sha256=initial['source_epoch_sha256'],
 source_unchanged=source_pins(RUNTIME)==initial['files'],import_origins=assert_runtime_imports(RUNTIME),
 mlx_loaded=any(n=='mlx' or n.startswith('mlx.') for n in sys.modules),gpu=False)
save(OUT/'result.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ('details','case','import_origins')}),flush=True)
assert passed and bound and result['nonce_acknowledged'] and result['pre_matches'] and result['post_matches']
assert result['source_unchanged'] and not result['mlx_loaded']
