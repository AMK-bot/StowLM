"""Declared new ballast-only synthetic manual recipes; originals remain unchanged."""
import argparse,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
NAMES=('synthetic_boundary285','synthetic_3600_closed')
def load(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,indent=2));t.replace(p)
def repair_all(build,name):
    build.install_runtime()
    from stowage.vessel.fleet import load_vessel,load_ship_params
    from stowage.checker.full import check
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.solver.ballast import _finish_ballast
    helper=load('new_manual_original_fixture',build.ROOT/'tests/vessel/test_fleet_manual_restore.py')
    vessel=load_vessel(name);recipes=build.design_conditions(vessel,load_ship_params(name));rows=[]
    for recipe in recipes:
        condition=recipe['condition'];original=HERE/'ships'/f'{name}.{condition}.departure-check.json';initial=json.loads(original.read_text())
        helper._physical_recipe(vessel,recipe);instance=helper._manual_instance(vessel,recipe)
        plan=MovePlan(id=instance.id+'.declared_ballast_recipe',instance_id=instance.id,call_index=instance.call_index,
            ballast=BallastCondition(source='planner',tank_masses_kg={t.id:recipe['departure_tank_masses_kg'][t.id]for t in vessel.tanks if t.adjustable}))
        started=time.monotonic();outcome=None;report=initial['full_report']
        if not report['passed']:
            print(json.dumps(dict(event='recipe_ballast_repair_start',ship=name,condition=condition,budget_s=120)),flush=True)
            result=_finish_ballast(instance,plan.model_copy(update={'ballast':None}),time_limit_s=120.,seed=0,_deadline=time.monotonic()+120.)
            outcome=result.model_dump(mode='json')
            if result.plan is not None:
                plan=result.plan;report=check(instance,plan).model_dump(mode='json')
        masses=dict(recipe['departure_tank_masses_kg'])
        if plan.ballast is not None:masses.update(plan.ballast.tank_masses_kg)
        for tank in vessel.tanks:assert 0<=masses[tank.id]<=tank.capacity_m3*tank.content_density_kg_m3+1e-6
        record=dict(ship=name,condition=condition,declared_recipe='Original fixed cargo/geometry/limits, newsynthetic departureballast masses only' if outcome else 'Originalrecipe retained because fullcheckpassed',
            cargo_cells=recipe['cargo_cells'],departure_tank_masses_kg=masses,route=recipe['route'],
            original_recipe_report=str(original.relative_to(build.ROOT)),original_recipe_report_sha256=build.sha(original),
            original_recipe_full_checker_passed=initial['full_report']['passed'],full_checker_passed=report['passed'],
            full_report=report,ballast_finisher=outcome,physical_witness_plan=plan.model_dump(mode='json'),
            seconds=time.monotonic()-started,cold='Physicalpreparation only; this plan/ballast never enters solver inputs')
        path=HERE/'ships'/f'{name}.{condition}.declared-recipe.json';write(path,record)
        rows.append(dict(condition=condition,full_checker_passed=report['passed'],all_parts_evaluated=all(p['ran']and not p.get('error')for p in report['parts'].values()),
            original_recipe_full_checker_passed=initial['full_report']['passed'],seconds=record['seconds'],report=str(path.relative_to(build.ROOT)),report_sha256=build.sha(path),
            exact_breaches=report['violations']))
        print(json.dumps(dict(event='declared_recipe_result',ship=name,condition=condition,passed=report['passed'],seconds=record['seconds'])),flush=True)
    summary=dict(ship=name,conditions=rows,all_three_conditions_checked=len(rows)==3,
        all_parts_evaluated=all(r['all_parts_evaluated']for r in rows),
        all_declared_full_checker_passed=len(rows)==3 and all(r['full_checker_passed']for r in rows),
        original_recipes_unchanged=True,recipe_policy='AlloriginalC1/C2/C3 cargo preserved;120s originalballastfinisher per failingcondition, no geometry/gear/limit/cargo change; all3 unchangedfullcheckerpasses required.',driver_sha256=build.sha(__file__))
    write(HERE/'ships'/f'{name}.declared-manual-validation.json',summary)
    print(json.dumps({k:v for k,v in summary.items()if k!='conditions'}),flush=True)
def worker(a):
    b=load('declared_manual_build',HERE/'build_ships_maps.py');b.NAMES=(a.ship,);b.validate_departure=lambda:repair_all(b,a.ship)
    sys.argv=[str(HERE/'build_ships_maps.py'),'validate','--ship',a.ship];b.main()
def main():
    p=argparse.ArgumentParser();p.add_argument('--ship',choices=NAMES,required=True);p.add_argument('--hard-s',type=float,default=1200);p.add_argument('--worker',action='store_true');a=p.parse_args()
    if a.worker:return worker(a)
    log=HERE/'ships'/f'repair-conditions-{a.ship}.log';start=time.monotonic();cmd=['nice','-n','5',sys.executable,'-B',__file__,'--ship',a.ship,'--hard-s',str(a.hard_s),'--worker']
    with log.open('w')as out:
        child=subprocess.Popen(cmd,stdout=out,stderr=subprocess.STDOUT,env=dict(os.environ,C6_XL_HARD_S=str(a.hard_s)))
        write(log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=child.pid,hard_s=a.hard_s,command=cmd))
        while child.poll()is None:
            if time.monotonic()-start>a.hard_s+2:
                write(HERE/'ships'/f'stop-{child.pid}.json',dict(reason='timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    mon=HERE/'ships'/f'monitor-{child.pid}.json';write(log.with_suffix('.execution.json'),dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-start,monitor=json.loads(mon.read_text())if mon.exists()else None));return child.returncode
if __name__=='__main__':sys.exit(main())
