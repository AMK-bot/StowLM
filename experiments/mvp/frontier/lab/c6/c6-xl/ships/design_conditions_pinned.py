"""Original C1/C2/C3 fleet recipes; all voyage checks retained, no success filtering."""
import argparse,importlib.util,inspect,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
NAMES=('synthetic_boundary285','synthetic_3600_closed')
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix(p.suffix+'.tmp');temp.write_text(json.dumps(v,indent=2));temp.replace(p)
def aggregate(build,name):
    rows=[]
    for condition in ('C1','C2','C3'):
        path=HERE/'ships'/f'{name}.{condition}.departure-check.json'
        if not path.exists():rows.append(dict(condition=condition,complete=False));continue
        record=json.loads(path.read_text());report=record['full_report'];parts=report['parts']
        stability=[v for k,v in parts.items()if k=='stability' or k.endswith(':stability')]
        strength=[v for k,v in parts.items()if k=='strength' or k.endswith(':strength')]
        ignored=set()
        stable=bool(stability) and all(p['ran'] and not p.get('error') and not any(v.get('severity','hard')=='hard' and v['rule_id']not in ignored for v in p['violations'])for p in stability)
        strong=bool(strength)and all(p['ran'] and not p.get('error')and not any(v.get('severity','hard')=='hard'for v in p['violations'])for p in strength)
        rows.append(dict(condition=condition,complete=True,all_parts_evaluated=all(v['ran']and not v.get('error')for v in parts.values()),
            departure_and_voyage_stability_passed=stable,strength_passed=strong,full_checker_passed=report['passed'],seconds=record['seconds'],
            exact_breaches=report['violations'],part_errors={k:v.get('error')for k,v in parts.items()if v.get('error')},
            report=str(path.relative_to(build.ROOT)),report_sha256=build.sha(path)))
    complete=all(r['complete']for r in rows)
    result=dict(ship=name,conditions=rows,all_three_conditions_checked=complete,
        all_parts_evaluated=complete and all(r['all_parts_evaluated']for r in rows),
        all_design_departure_stability_passed=complete and all(r['departure_and_voyage_stability_passed']for r in rows),
        all_design_strength_passed=complete and all(r['strength_passed']for r in rows),
        all_reference_full_checker_passed=complete and all(r['full_checker_passed']for r in rows),
        criterion='All original C1/C2/C3 recipes, byte-pinned exact fleetfixture and unchanged full voyage checker. No rule excluded; primaryqualification requires all3 fullcheckerpasses. No condition dropped or repaired.',
        driver_sha256=build.sha(__file__),fixture_sha256=build.sha(HERE/'ships/generator/fleet_manual_fixture.py'),fixture_copy='ships/generator/fleet_manual_fixture.py')
    write(HERE/'ships'/f'{name}.design-conditions.json',result)
    print(json.dumps({k:v for k,v in result.items()if k!='conditions'}),flush=True)
    return result
def worker(args):
    build=load('xl_all_design_conditions',HERE/'build_ships_maps.py');build.NAMES=(args.ship,)
    fixture=HERE/'ships/generator/fleet_manual_fixture.py'
    assert build.sha(fixture)=='609fe8eaa7f636d45e6788ed3cd8090523613a87ecc5babc1d3cf6f937f23e4d'
    source=inspect.getsource(build.validate_departure)
    assert source.count("ROOT/'tests/vessel/test_fleet_manual_restore.py'")==1
    source=source.replace("ROOT/'tests/vessel/test_fleet_manual_restore.py'","SHIPS/'generator/fleet_manual_fixture.py'")
    exec(source,build.__dict__)
    original=build.validate_departure
    def validate():
        try:original()
        finally:aggregate(build,args.ship)
    build.validate_departure=validate
    sys.argv=[str(HERE/'build_ships_maps.py'),'validate','--ship',args.ship];build.main()
def main():
    p=argparse.ArgumentParser();p.add_argument('--ship',choices=NAMES,required=True);p.add_argument('--hard-s',type=float,default=1200);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if a.worker:return worker(a)
    log=HERE/'ships'/f'design-conditions-{a.ship}.log';started=time.monotonic()
    cmd=['nice','-n','5',sys.executable,'-B',__file__,'--ship',a.ship,'--hard-s',str(a.hard_s),'--worker']
    with log.open('w')as out:
        child=subprocess.Popen(cmd,stdout=out,stderr=subprocess.STDOUT,env=dict(os.environ,C6_XL_HARD_S=str(a.hard_s)))
        write(log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=child.pid,hard_s=a.hard_s,command=cmd))
        while child.poll()is None:
            if time.monotonic()-started>a.hard_s+2:
                write(HERE/'ships'/f'stop-{child.pid}.json',dict(reason='timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    monitor=HERE/'ships'/f'monitor-{child.pid}.json'
    write(log.with_suffix('.execution.json'),dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started,monitor=json.loads(monitor.read_text())if monitor.exists()else None))
    return child.returncode
if __name__=='__main__':sys.exit(main())
