"""Real producer emission audit: no screen/checker/physics bypasses."""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import time

from stowage.generate import random_yard
from stowage.rules.catalogue import rule_freeze_v1

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profiles',nargs='+',default=['feeder_export','dg_stress'])
    parser.add_argument('--seeds',nargs='+',type=int,default=[710])
    parser.add_argument('--max-attempts',type=int,default=1)
    parser.add_argument('--case-timeout',type=int,default=300)
    parser.add_argument('--output',default='/tmp/w2-l2-cargo-producer-admission-pilot.json')
    args=parser.parse_args()
    output=Path(args.output)
    pid_path=output.with_suffix('.pid')
    pid_path.write_text(str(os.getpid())+'\n')
    paths=sorted([*Path('src/stowage').rglob('*.py'),*Path('src/stowage').rglob('*.toml'),*Path('params').rglob('*.toml')])
    def pins():
        return {str(path):sha256(path.read_bytes()).hexdigest() for path in paths}
    initial=pins()
    started=time.perf_counter()
    result={'definition':'Producer-admitted = boxes in returned emitted Instance.load_list after unmodified public random_yard producer C1 and background acceptance gates; not a full loading-plan proof. No slot-witness proxy, disabled gate, monkeypatch or demand relabelling.','size':'tiny','vessel':'feeder_500','port':'laem_chabang_like','blocking_ratio':0.3,'d62_crossing_share':0.0,'c2_screen':False,'activation':True,'max_attempts':args.max_attempts,'seeds':args.seeds,'source_pins_initial':initial,'audit_script_sha256':sha256(Path(__file__).read_bytes()).hexdigest(),'worker_env':{key:os.environ.get(key) for key in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','STOWAGE_IN_PORT_WORKERS')},'runs':[],'pid':os.getpid()}
    def save():
        result['wall_s']=time.perf_counter()-started
        result['max_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        temporary=output.with_suffix(output.suffix+'.partial')
        temporary.write_text(json.dumps(result,sort_keys=True,indent=2,default=str)+'\n')
        temporary.replace(output)
    def timeout(signum,frame):
        raise TimeoutError(f'real producer case exceeded declared {args.case_timeout}s work budget')
    signal.signal(signal.SIGALRM,timeout)
    with rule_freeze_v1(enabled=True):
        for profile in args.profiles:
            for seed in args.seeds:
                report=random_yard.RandomGenerationReport()
                entry={'profile':profile,'seed':seed,'status':'running','started_s':time.perf_counter()-started}
                result['runs'].append(entry);save()
                print('START',profile,seed,'max_attempts',args.max_attempts,flush=True)
                case_started=time.perf_counter()
                signal.alarm(args.case_timeout)
                try:
                    instance=random_yard.generate_random_instance('tiny',seed=seed,blocking_ratio=.3,
                        vessel_name='feeder_500',port_name='laem_chabang_like',report=report,
                        max_attempts=args.max_attempts,c2_screen=False,cargo_profile=profile,d62_crossing_share=0.)
                    assert report.accepted is True
                    assert report.background_checks>0
                    assert report.completion_checks>0
                    by_id={box.id:box for box in instance.containers}
                    observed=Counter(by_id[cid].dg.imdg_class for cid in instance.load_list if by_id[cid].dg)
                    admitted=report.cargo_draws['admitted']
                    assert dict(observed)==admitted['dg_by_class'],(dict(observed),admitted)
                    assert admitted['n_boxes']==len(instance.load_list)
                    instance_path=output.with_name(output.stem+f'-{profile}-seed{seed}.instance.json')
                    instance_path.write_text(instance.model_dump_json(indent=2)+'\n')
                    entry.update(status='emitted',instance_id=instance.id,instance_path=str(instance_path),
                                 load_list=list(instance.load_list),admitted=admitted)
                except Exception as error:
                    entry.update(status='not_emitted',error_type=type(error).__name__,error=str(error),
                                 admitted={'n_boxes':0,'dg_by_class':{},'dg_by_un':{},'uns_by_class':{}})
                finally:
                    signal.alarm(0)
                    entry['audit']=asdict(report)
                    entry['wall_s']=time.perf_counter()-case_started
                    entry['source_changes']=[path for path,digest in pins().items() if initial.get(path)!=digest]
                    save()
                    print(json.dumps({key:entry.get(key) for key in ('profile','seed','status','wall_s','error','source_changes')},sort_keys=True),flush=True)
                    print('stages',json.dumps({stage:{key:values.get(key) for key in ('n_boxes','dg_by_class')} for stage,values in report.cargo_draws.get('stages',{}).items()},sort_keys=True),flush=True)
                    print('admitted',json.dumps(entry['admitted'],sort_keys=True),flush=True)
    result['source_pins_final']=pins()
    save()
    print('TOTAL',result['wall_s'],'max_rss_bytes',result['max_rss_bytes'],flush=True)


if __name__ == "__main__":
    main()
