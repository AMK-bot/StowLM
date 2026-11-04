"""Preparation-only fixed-manual-cargo search; never used by a scoring method.

Expose every existing adjustable P/S tank group in the frozen local exact-
physics optimizer. Only search directions and trust-region bounds differ.
Every accepted declared recipe needs a fresh unchanged full voyage checker.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, inspect, json, os
from pathlib import Path
import resource, subprocess, sys, threading, time, tomllib

HERE=Path(__file__).resolve().parent
XL=HERE.parent
ROOT=next(p for p in HERE.parents if (p/'pyproject.toml').exists())
RUNTIME=Path(os.environ.get('C6_XL_RUNTIME',XL/'runtime')).resolve()
THREADS=('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','HIGHS_THREADS','STOWAGE_IN_PORT_WORKERS')
for k in THREADS:os.environ[k]='1'
sys.dont_write_bytecode=True

RESTRICT="""    preferred = ('DB-F', 'DB-M', 'FWD-DEEP', 'HEEL')
    groups = {name: groups[name] for name in preferred if name in groups} or dict(list(groups.items())[:4])"""
REGIME="""            local_low=np.where(x>=.98,.98000001,0.)
            local_high=np.where(x<.98,.97999999,1.)"""

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

def adapt_source(source):
    """Fail closed if frozen optimizer does not have the expected two blocks."""
    if source.count(RESTRICT)!=1 or source.count(REGIME)!=1:
        raise ValueError('Frozen optimizer source differs from reviewed all-group adapter')
    source=source.replace(RESTRICT,'    # Preparation search: retain every existing adjustable paired tank group.')
    source=source.replace(REGIME,'            local_low=np.zeros_like(x)\n            local_high=np.ones_like(x)')
    # Existing exact evaluations reject each unhelpful crossing and existing
    # regime-change detection rebuilds derivatives. No physical bound changes.
    return source

def optimizer():
    original=load('xl_frozen_manual_ballast_opt',XL/'solver/ballast_opt.py')
    namespace=dict(original.__dict__)
    exec(compile(adapt_source(inspect.getsource(original.optimize)),str(__file__)+':adapted-optimize','exec'),namespace)
    os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(XL/'ships/vessel-cache')
    return namespace['optimize']

def static_audit(ship):
    """Pure raw-data capacity/moment interval audit; no optimizer or physics."""
    path=XL/'ships'/f'{ship}.toml';params=tomllib.loads(path.read_text())
    tanks=[t for t in params['tanks']if t['adjustable']]
    rows=[]
    for t in tanks:
        cap=t['capacity_m3']*t['content_density_kg_m3']
        rows.append(dict(id=t['id'],capacity_kg=cap,x_min_m=t['x_aft_m'],x_max_m=t['x_fore_m'],
            longitudinal_moment_interval_kgm=[min(0.,cap*t['x_aft_m']),max(0.,cap*t['x_fore_m'])]))
    conditions=[]
    for c in ('C1','C2','C3'):
        p=XL/'ships'/f'{ship}.{c}.declared-recipe.json'
        original=XL/'ships'/f'{ship}.{c}.departure-check.json'
        d=json.loads(p.read_text()) if p.exists()else {}
        masses=d.get('departure_tank_masses_kg',{})
        r=json.loads(original.read_text())['full_report']
        conditions.append(dict(condition=c,initial_adjustable_kg=sum(masses.get(t['id'],0.)for t in tanks) if masses else None,
            original_passed=r['passed'],original_rules=sorted({v['rule_id']for v in r['violations']}),
            original_report=str(original.relative_to(ROOT)),original_report_sha256=sha(original)))
    return dict(ship=ship,raw_parameters_sha256=sha(path),groups=sorted({t['id'].rsplit('-',1)[0]for t in tanks}),
        total_adjustable_capacity_kg=sum(r['capacity_kg']for r in rows),tanks=rows,conditions=conditions,
        interpretation='Capacity and x-envelope bounds only; upright liquid centroid changes with fill. Finite mass/moment room is not a nonlinear stability feasibility proof. Original hull/gear/limits/tanks and cargo are fixed.')

def worker(args,out):
    deadline=time.monotonic()+args.hard_s
    def guard():
        while True:
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            reason='running'
            if peak>=56_000_000_000:reason='memory_limit'
            elif time.monotonic()>=deadline:reason='timeout'
            elif (out/'monitor.stop').exists():reason='requested_stop'
            write(out/'monitor.json',dict(pid=os.getpid(),peak_rss_bytes=peak,status=reason,remaining_s=max(0.,deadline-time.monotonic())))
            if reason!='running':os._exit(137 if reason=='memory_limit' else 124)
            time.sleep(.1)
    threading.Thread(target=guard,daemon=True).start()
    sys.path.insert(0,str(RUNTIME/'src'))
    build=load('xl_manual_allgroups_build',XL/'build_ships_maps.py');build.install_runtime()
    from stowage.vessel.fleet import load_vessel,load_ship_params
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.checker.full import check
    import stowage
    assert Path(stowage.__file__).resolve().is_relative_to(RUNTIME/'src')
    helper=load('xl_allgroups_original_manual_fixture',ROOT/'tests/vessel/test_fleet_manual_restore.py')
    vessel=load_vessel(args.ship)
    recipe=next(r for r in build.design_conditions(vessel,load_ship_params(args.ship))if r['condition']==args.condition)
    helper._physical_recipe(vessel,recipe);instance=helper._manual_instance(vessel,recipe)
    plan=MovePlan(id=instance.id+'.allgroups_declared_manual',instance_id=instance.id,call_index=instance.call_index,
        ballast=BallastCondition(source='planner',tank_masses_kg={t.id:recipe['departure_tank_masses_kg'][t.id]for t in vessel.tanks if t.adjustable}))
    original_cargo=instance.onboard.model_dump(mode='json')
    optimize=optimizer()
    before=time.monotonic()
    candidate,summary=optimize(instance,plan,out=out/'optimization',budget_s=300.,max_evaluations=100,max_iterations=16,parts=('stability','lashing'))
    # Optimizer already does a full check; this fresh independent call remains
    # the sole declared-recipe acceptance gate and is explicitly timed.
    fresh=time.monotonic();report=check(instance,candidate)
    assert instance.onboard.model_dump(mode='json')==original_cargo
    assert candidate.moves==plan.moves
    masses=dict(recipe['departure_tank_masses_kg']);masses.update(candidate.ballast.tank_masses_kg)
    for tank in vessel.tanks:assert 0<=masses[tank.id]<=tank.capacity_m3*tank.content_density_kg_m3+1e-6
    record=dict(ship=args.ship,condition=args.condition,declared_recipe='NEW synthetic manual ballast only; fixed original20ft manual cargo, hull, gear, tank geometry/capacities and limits',
        departure_tank_masses_kg=masses,cargo_cells=recipe['cargo_cells'],route=recipe['route'],
        all_original_cargo_unchanged=True,full_checker_passed=report.passed,
        all_parts_evaluated=all(p.ran and not p.error for p in report.parts.values()),full_report=report.model_dump(mode='json'),
        physical_witness_plan=candidate.model_dump(mode='json'),optimizer_summary=summary,
        independent_full_checker_s=time.monotonic()-fresh,wall_s=time.monotonic()-before,
        physical_preparation_only_never_solver_warmstart=True,driver_sha256=sha(__file__))
    write(out/'declared-recipe.json',record)
    print(json.dumps({k:v for k,v in record.items()if k in ('ship','condition','full_checker_passed','all_parts_evaluated','wall_s','independent_full_checker_s')}),flush=True)
    return 0

def main():
    p=argparse.ArgumentParser();p.add_argument('--ship',choices=('synthetic_boundary285','synthetic_3600_closed'),required=True)
    p.add_argument('--condition',choices=('C1','C2','C3'),required=True);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--hard-s',type=float,default=600);p.add_argument('--worker',action='store_true');p.add_argument('--audit-only',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if not out.is_relative_to(HERE):p.error('Preparation output must stay in prep/')
    if a.hard_s>1200 or a.hard_s<=300:p.error('Hard bound must be >300 and <=1200seconds')
    out.mkdir(parents=True,exist_ok=True)
    if a.audit_only:write(out/'static-audit.json',static_audit(a.ship));return 0
    if a.worker:return worker(a,out)
    prereg=dict(created_unix=time.time(),ship=a.ship,condition=a.condition,budget_s=300.,hard_s=a.hard_s,memory_limit_bytes=56_000_000_000,
        numerical_threads=1,nice_requested=5,cold='Physical preparation only, never solver input/warmstart',
        driver_sha256=sha(__file__),optimizer_sha256=sha(XL/'solver/ballast_opt.py'),physics_sha256=sha(XL/'solver/physics.py'),
        static_audit=static_audit(a.ship),changes=['Every existing adjustable paired tankgroup, plus inherited HEEL-split','Trustbounds physical[0,1], finite differences remain regime-aware, exact physics each trial and derivative rebuild upon regimechange'],
        unchanged='Original manual cargo, geometry, limits, gear, tanks and checker; no source/runtime patch',max_evaluations=100,max_iterations=16)
    if (out/'preregistration.json').exists():raise ValueError('Refuse overwriting prior predeclared run')
    write(out/'preregistration.json',prereg)
    cmd=['nice','-n','5',sys.executable,'-B',str(Path(__file__).resolve()),'--ship',a.ship,'--condition',a.condition,'--out',str(out),'--hard-s',str(a.hard_s),'--worker']
    started=time.monotonic()
    with (out/'worker.log').open('w')as log:
        child=subprocess.Popen(cmd,cwd=ROOT,env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT)
        write(out/'ownership.json',dict(parent_pid=os.getpid(),owned_child_pid=child.pid,command=cmd))
        print(json.dumps(dict(event='owned_manual_worker',pid=child.pid,condition=a.condition,out=str(out.relative_to(ROOT)))),flush=True)
        while child.poll()is None:
            if time.monotonic()-started>a.hard_s:
                write(out/'monitor.stop',dict(reason='parent_timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    write(out/'execution.json',dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started))
    return child.returncode
if __name__=='__main__':sys.exit(main())
