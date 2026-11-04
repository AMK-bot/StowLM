"""Preparation-only fixed-cargo hydrostatic ballast preconditioner.

Three preregistered mass/zero-trim targets; no GZ curve is differentiated.
Native tank soundings/loading moments and direct floating equilibrium guide
search. Only an unchanged full voyage checker may accept a declared recipe.
"""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
import sys, time
import manual_ballast as base


def target_specs(initial_mass, initial_mean, design_draft):
    return [('original-displacement-zero-trim',float(initial_mass),float(initial_mean)),
            ('design-minus-0.5m-zero-trim',None,float(design_draft)-.5),
            ('design-minus-1.0m-zero-trim',None,float(design_draft)-1.)]


def worker(args,out):
    # Reuse the same independent self-watchdog as the all-group pilot via a
    # narrowly scoped worker facade, retaining its parent monitor protocol.
    import resource,threading
    hard_deadline=time.monotonic()+args.hard_s
    def guard():
        while True:
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            reason='running'
            if peak>=56_000_000_000:reason='memory_limit'
            elif time.monotonic()>=hard_deadline:reason='timeout'
            elif (out/'monitor.stop').exists():reason='requested_stop'
            base.write(out/'monitor.json',dict(pid=os.getpid(),peak_rss_bytes=peak,status=reason,remaining_s=max(0.,hard_deadline-time.monotonic())))
            if reason!='running':os._exit(137 if reason=='memory_limit' else 124)
            time.sleep(.1)
    threading.Thread(target=guard,daemon=True).start()
    sys.path.insert(0,str(base.RUNTIME/'src'))
    import numpy as np
    from scipy.optimize import linprog,brentq
    build=base.load('xl_hydro_manual_build',base.XL/'build_ships_maps.py');build.install_runtime()
    from stowage.vessel.fleet import load_vessel,load_ship_params
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.checker.full import check
    from stowage.checker.stability import _context
    from stowage.checker.stability_loading import loading_condition,liquid_cg
    from stowage.checker.stability_equilibrium import floating_equilibrium
    helper=base.manual_fixture('xl_hydro_original_manual_fixture')
    vessel=load_vessel(args.ship)
    recipe=next(r for r in build.design_conditions(vessel,load_ship_params(args.ship))if r['condition']==args.condition)
    helper._physical_recipe(vessel,recipe);instance=helper._manual_instance(vessel,recipe)
    original_onboard=instance.onboard.model_dump(mode='json')
    tanks=tuple(sorted((t for t in vessel.tanks if t.adjustable),key=lambda t:t.id))
    ids=tuple(t.id for t in tanks);capacity=np.array([t.capacity_m3*t.content_density_kg_m3 for t in tanks])
    initial=np.array([recipe['departure_tank_masses_kg'][tid]for tid in ids])/capacity
    geometry,params=_context(vessel);rho=instance.port.water_density_kg_m3
    groups={}
    for i,tid in enumerate(ids):groups.setdefault(tid.rsplit('-',1)[0],[]).append(i)
    G=np.zeros((len(tanks),len(groups)))
    for j,ix in enumerate(groups.values()):G[ix,j]=1.
    if 'HEEL-P'in ids and 'HEEL-S'in ids:
        d=np.zeros(len(tanks));d[ids.index('HEEL-P')]=1.;d[ids.index('HEEL-S')]=-1.;G=np.column_stack((G,d))
    def state(x):
        return instance.onboard.model_copy(update={'tank_masses_kg':{**recipe['departure_tank_masses_kg'],**dict(zip(ids,map(float,x*capacity)))}})
    def loading(x):
        c=loading_condition(instance,state(x),geometry=geometry,ship_params=params,precomputed_soundings=True,small_tank_exemption=True)
        if c.errors:raise ValueError('; '.join(c.errors))
        return c
    def moments(c):return np.array([c.mass_kg,c.mass_kg*c.lcg_m,c.mass_kg*c.tcg_m])
    def floating(c):return floating_equilibrium(geometry,c,rho,vessel.hull.lpp_m,liquid_cg)
    initial_condition=loading(initial);initial_float=floating(initial_condition)
    specs=target_specs(initial_condition.mass_kg,initial_float.mean_draught_m,vessel.hull.design_draught_m)
    trace=[];results=[];search_deadline=time.monotonic()+300.
    for label,target_mass,draft in specs:
        if time.monotonic()>=search_deadline:break
        if target_mass is not None:
            draft=brentq(lambda d:geometry.submerged(d,d,0.,rho).displacement_kg-target_mass,.01,vessel.hull.depth_m)
        submerged=geometry.submerged(draft,draft,0.,rho)
        target=np.array([submerged.displacement_kg,submerged.displacement_kg*submerged.lcb_m,0.])
        # Native nonlinear soundings are re-evaluated at every iterate. Their
        # Jacobian predicts mass/moment corrections only, never a pass.
        x=initial.copy();status='iteration_limit';equilibrium=None
        scale=np.array([max(target[0],1.),max(target[0]*vessel.hull.lpp_m,1.),max(target[0]*vessel.hull.beam_m,1.)])
        for iteration in range(10):
            if time.monotonic()>=search_deadline:status='search_timeout';break
            c=loading(x);residual=(moments(c)-target)/scale
            equilibrium=floating(c)
            row=dict(candidate=label,iteration=iteration,mass_kg=c.mass_kg,lcg_m=c.lcg_m,tcg_m=c.tcg_m,draught_aft_m=equilibrium.t_aft_m,draught_fwd_m=equilibrium.t_fwd_m,heel_rad=equilibrium.heel_rad,residual=residual.tolist(),fractions=x.tolist())
            trace.append(row);base.write(out/'hydro-trace.json',trace)
            print(json.dumps({k:v for k,v in row.items()if k!='fractions'}),flush=True)
            if np.max(np.abs(residual))<1e-7 and abs(equilibrium.trim_m)<.02 and abs(equilibrium.heel_rad)<.001:
                status='target_converged';break
            J=np.zeros((3,G.shape[1]))
            for j in range(G.shape[1]):
                if time.monotonic()>=search_deadline:break
                direction=G[:,j];active=np.flatnonzero(direction)
                limits=np.where(direction[active]>0,1.-x[active],x[active])
                sign=1.
                if min(limits)<1e-5:
                    limits=np.where(direction[active]>0,x[active],1.-x[active]);sign=-1.
                step=sign*min(.005,max(0.,float(min(limits)))*.5)
                if abs(step)>1e-7:J[:,j]=((moments(loading(np.clip(x+step*direction,0.,1.)))-moments(c))/scale)/step
            n=G.shape[1]
            # All physical tanks stay within capacity; pair directions include
            # a heel split so transverse balance is not assumed from symmetry.
            A=np.vstack((np.c_[G,np.zeros((len(x),n))],np.c_[-G,np.zeros((len(x),n))],np.c_[np.eye(n),-np.eye(n)],np.c_[-np.eye(n),-np.eye(n)]))
            b=np.r_[1.-x,x,np.zeros(2*n)]
            # Minimize actual fraction movement; no convexity/feasibility
            # guarantee is inferred from this local LP.
            lp=linprog(np.r_[np.zeros(n),np.ones(n)],A_ub=A,b_ub=b,A_eq=np.c_[J,np.zeros((3,n))],b_eq=-residual,bounds=[(-1.,1.)]*n+[(0.,None)]*n,method='highs',options={'threads':1,'time_limit':1.})
            if lp.status!=0:status='local_linear_target_unavailable';break
            x=np.clip(x+G@lp.x[:n],0.,1.)
        # Each predeclared target supplies at most one checker candidate.
        plan=MovePlan(id=instance.id+'.hydro_manual.'+label,instance_id=instance.id,call_index=instance.call_index,
            ballast=BallastCondition(source='planner',tank_masses_kg=dict(zip(ids,map(float,x*capacity)))))
        assert instance.onboard.model_dump(mode='json')==original_onboard and not plan.moves
        before=time.monotonic();report=check(instance,plan)
        record=dict(ship=args.ship,condition=args.condition,label=label,search_status=status,target_mass_kg=float(target[0]),target_upright_draft_m=float(draft),
            full_checker_passed=report.passed,all_parts_evaluated=all(p.ran and not p.error for p in report.parts.values()),full_report=report.model_dump(mode='json'),
            full_checker_s=time.monotonic()-before,cargo_cells=recipe['cargo_cells'],route=recipe['route'],departure_tank_masses_kg={**recipe['departure_tank_masses_kg'],**plan.ballast.tank_masses_kg},
            physical_witness_plan=plan.model_dump(mode='json'),all_original_cargo_unchanged=True,physical_preparation_only_never_solver_warmstart=True,
            declared_recipe='NEW synthetic ballast-only manual; exact original cargo/hull/gear/limits/tanks',driver_sha256=base.sha(__file__))
        path=out/(label+'.json');base.write(path,record)
        results.append(dict(label=label,full_checker_passed=report.passed,all_parts_evaluated=record['all_parts_evaluated'],report=str(path.relative_to(base.ROOT)),report_sha256=base.sha(path)))
        print(json.dumps(results[-1]),flush=True)
        if report.passed:break
    base.write(out/'summary.json',dict(ship=args.ship,condition=args.condition,results=results,full_checker_passed=any(r['full_checker_passed']for r in results),maximum_predeclared_candidates=3,search_budget_s=300.,hard_s=args.hard_s,physical_infeasibility_proven=False))
    return 0


def main():
    p=argparse.ArgumentParser();p.add_argument('--ship',choices=('synthetic_boundary285','synthetic_3600_closed'),required=True)
    p.add_argument('--condition',choices=('C1','C2','C3'),required=True);p.add_argument('--out',required=True,type=Path);p.add_argument('--hard-s',type=float,default=600);p.add_argument('--worker',action='store_true')
    a=p.parse_args();out=a.out.resolve()
    if not out.is_relative_to(base.HERE):p.error('Preparation output outside prep/')
    if not 300<a.hard_s<=600:p.error('Hard bound must be>300 and<=600seconds')
    out.mkdir(parents=True,exist_ok=True)
    if a.worker:return worker(a,out)
    prereg=dict(created_unix=time.time(),ship=a.ship,condition=a.condition,targets=['Original displacement, zero trim','Design draft minus0.5m, zero trim','Design draft minus1.0m, zero trim'],maximum_candidates=3,search_budget_s=300.,hard_s=a.hard_s,memory_bytes=56_000_000_000,numerical_threads=1,nice_requested=5,driver_sha256=base.sha(__file__),support_sha256=base.sha(base.__file__),static_audit=base.static_audit(a.ship),cold='Physical preparation only; never solver warmstart',acceptance='Fresh unchanged full3phase voyage checker only')
    if(out/'preregistration.json').exists():raise ValueError('Refuse overwriting predeclared run')
    base.write(out/'preregistration.json',prereg)
    import subprocess
    cmd=['nice','-n','5',sys.executable,'-B',str(Path(__file__).resolve()),'--ship',a.ship,'--condition',a.condition,'--out',str(out),'--hard-s',str(a.hard_s),'--worker']
    started=time.monotonic()
    with(out/'worker.log').open('w')as log:
        child=subprocess.Popen(cmd,cwd=base.ROOT,env=dict(os.environ),stdout=log,stderr=subprocess.STDOUT)
        base.write(out/'ownership.json',dict(parent_pid=os.getpid(),owned_child_pid=child.pid,command=cmd))
        print(json.dumps(dict(event='owned_hydro_manual_worker',pid=child.pid,out=str(out.relative_to(base.ROOT)))),flush=True)
        while child.poll()is None:
            if time.monotonic()-started>a.hard_s:
                base.write(out/'monitor.stop',dict(reason='parent_timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    base.write(out/'execution.json',dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started));return child.returncode
if __name__=='__main__':sys.exit(main())
