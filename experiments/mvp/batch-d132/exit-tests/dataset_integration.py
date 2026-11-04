"""Real v2 dataset: current-epoch full replay verification, then exact serial/pool bytes."""
from pathlib import Path
import sys,os,json,time,shutil,hashlib,tempfile,subprocess
def main():
    HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
    RUNTIME=Path(sys.argv[1]).resolve();OUT=Path(sys.argv[2]).resolve();OUT.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(RUNTIME/'src'));os.environ['TMPDIR']=str(OUT/'tmp');(OUT/'tmp').mkdir(exist_ok=True);tempfile.tempdir=os.environ['TMPDIR']
    os.environ['STOWLM_SFT_ROW_CACHE']=str(OUT/'sft-cache')
    from runtime import source_pins,verify_runtime,save,sha,assert_runtime_imports
    initial=verify_runtime(RUNTIME)
    def protect(event,args):
     if event=='open' and not isinstance(args[0],int):
      p=Path(os.fsdecode(args[0])).resolve()
      if p.is_relative_to(ROOT/'experiments/mvp/corpus/test') and p.name!='SHA256SUMS':raise RuntimeError('TEST body forbidden')
    sys.addaudithook(protect)
    from llm.exit import data,dataset_pool
    from llm.exit.replay import ensure_current_replay,checker_epoch
    from stowage.checker import report_matches_inputs
    from stowage.model import CheckReport
    import importlib.util
    spec=importlib.util.spec_from_file_location('dataset_v2_builder',RUNTIME/'experiments/mvp/sft/dataset/build.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    config=json.loads((ROOT/'experiments/mvp/exit/patches/d134-dataset/measurements/inputs.json').read_text())
    # Validate every fixture's actual identity/path before fullchecks/spawn; no TEST bodies.
    inputs=[]
    for group in ('winners','replay','validation_records'):
     for raw in config[group]:
      p=Path(raw);rec=json.loads(p.read_text());ip=Path(rec.get('instance_path') or rec.get('input_path'))
      assert any(part in ('train','val') for part in ip.parts) and 'test' not in ip.parts and 'showcase' not in ip.parts,(group,ip)
      inputs.append(dict(group=group,path=str(p),sha256=sha(p),input_path=str(ip),input_sha256=sha(ip)))
    prime=OUT/'prime';prime.mkdir(exist_ok=True);primes=[]
    for raw in config['replay']:
     p=Path(raw);instance,plan,report,_,_=builder.load_triple(p)
     started=time.perf_counter()
     actual_plan,actual_report,rechecked=ensure_current_replay(p,instance,plan,report,prime)
     assert rechecked is True
     checked=CheckReport.model_validate(actual_report);assert checked.passed and report_matches_inputs(checked,instance,actual_plan)
     primes.append(dict(source=str(p),rechecked=rechecked,wall_s=time.perf_counter()-started,plan_sha256=hashlib.sha256(actual_plan.model_dump_json().encode()).hexdigest()))
     save(OUT/'prime.json',dict(epoch=checker_epoch(),rows=primes,files={p.name:sha(p) for p in prime.glob('*.record.json')}))
    results={}
    for workers in (1,2):
     output=OUT/f'dataset-{workers}';output.mkdir()
     shutil.copytree(prime,output/'replay-current-epoch')
     started=time.perf_counter();report=data.build_training_dataset(out_dir=output,workers=workers,**config)
     assert report['current_rows']==2 and report['replay_rows']==2 and report['validation_rows']==1
     assert all(not row['rechecked'] for row in report['replay_epoch_admission'])
     results[str(workers)]=dict(wall_s=time.perf_counter()-started,report=report)
     save(OUT/'progress.json',results)
    hashes={}
    for name in ('train.jsonl','val.jsonl','stats.json','exit_build_report.json'):
     a=(OUT/'dataset-1'/name).read_bytes();b=(OUT/'dataset-2'/name).read_bytes();assert a==b,name
     hashes[name]=hashlib.sha256(a).hexdigest()
    probe=subprocess.run([sys.executable,str(HERE/'tests/pool_probe.py')],cwd=RUNTIME,capture_output=True,text=True,timeout=60,check=True,env={**os.environ,'PYTHONPATH':str(RUNTIME/'src')})
    assert json.loads(probe.stdout)==dict(order=[1,2,3,4],failure='fixture worker violation',environment_restored=True)
    result=dict(passed=True,results=results,byte_identical=hashes,prime_rows=primes,inputs=inputs,pool_order_fault_probe=json.loads(probe.stdout),
     source_before=initial,source_unchanged=source_pins(RUNTIME)==initial['files'],imports=assert_runtime_imports(RUNTIME),
     gpu=False,mlx_loaded=any(n=='mlx' or n.startswith('mlx.') for n in sys.modules))
    save(OUT/'result.json',result);print(json.dumps(dict(passed=True,byte_identical=hashes,phase_times={k:v['wall_s'] for k,v in results.items()})),flush=True)
    assert result['source_unchanged'] and not result['mlx_loaded']

if __name__=='__main__': main()
