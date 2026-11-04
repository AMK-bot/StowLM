"""Decompose visibility at a STORED checker pose; no equilibrium/physics solve.

This does not remove cargo mass or propose a modified vessel/plan. It holds the
reported pose fixed and partitions geometric occluders for causal attribution.
All reconstructed complete-scene metrics must match the unchanged checker.
"""
from itertools import product
import math
from types import SimpleNamespace
import run
import numpy as np
from stowage.io import load_instance
from stowage.model import PlannerResult
from stowage.moves import apply_plan
from stowage.checker import visibility as v
from stowage.vessel.hydrostatics import HullGeometry

def geometry(instance,ship):
    obstacles=[];lengths={20:6.058,40:12.192,45:13.716}
    for record in v._all_cargo_geometry(instance,ship):
        p,b=record.placement,record.container
        if not record.background and int(p.slot[-2:])<80:continue
        extra=b.oog
        fore=extra.over_length_fore_m if extra else 0.;aft=extra.over_length_aft_m if extra else 0.
        port=extra.over_width_port_m if extra else 0.;starboard=extra.over_width_starboard_m if extra else 0.
        obstacles.append(dict(group='fixed_cargo' if record.background else 'decision_cargo',
            x=record.x_m+(fore-aft)/2,y=record.y_m+(port-starboard)/2,bottom=record.bottom,top=record.top,
            length=lengths[b.length_ft]+fore+aft,width=2.438+port+starboard,slot=p.slot,container_id=b.id))
    for load in v._fixed_loads(instance.vessel):
        if load.kind=='cargo':continue
        e=load.silhouette
        obstacles.append(dict(group='fixed_other',x=(e.x_min_m+e.x_max_m)/2,y=(e.y_min_m+e.y_max_m)/2,
            bottom=e.z_min_m,top=e.z_max_m,length=e.x_max_m-e.x_min_m,width=e.y_max_m-e.y_min_m,
            slot=None,container_id=None))
    cells,stacks=v._cargo_catalogs(instance.vessel)
    for stack in stacks.values():
        if stack.on_deck and stack.hatch_cover_id and stack.cell_ids:
            cell=cells[stack.cell_ids[0]]
            obstacles.append(dict(group='covers',x=cell.lcg_m,y=cell.tcg_m,bottom=stack.base_z_m,
                top=stack.base_z_m,length=6.058,width=2.438,slot=None,container_id=None))
    return obstacles

def at_pose(instance,obstacles,pose):
    hull=instance.vessel.hull;bridge=instance.vessel.bridge
    ta,tf,heel=(pose[k] for k in ('draught_aft_m','draught_fwd_m','list_rad'))
    n=HullGeometry._axes(SimpleNamespace(lpp_m=hull.lpp_m),ta,tf,heel)[0]
    along=np.array([1.,0.,0.])-n*n[0];along/=np.linalg.norm(along);across=np.cross(n,along)
    eye=np.array([bridge.x_m-v.CONNING_EYE_SETBACK_M,0.,bridge.eye_z_m])
    height=float(n@eye-n[2]*ta)
    bow_z=ta+(tf-ta)*bridge.bow_x_m/hull.lpp_m
    bow=float(along@(np.array([bridge.bow_x_m,0.,bow_z])-eye))
    limit=min(2*hull.loa_m,500.);boundary=bow+limit;wedge=math.tan(math.radians(10))
    evaluated=[]
    for o in obstacles:
        x,y,bottom,top,length,width=(o[k] for k in ('x','y','bottom','top','length','width'))
        def transform(xx,yy,zz):
            p=np.array([xx,yy,zz])-eye
            return np.array([along@p,across@p,n@p+height])
        roof=[transform(xx,yy,top) for xx,yy in [(x-length/2,y-width/2),(x+length/2,y-width/2),
            (x+length/2,y+width/2),(x-length/2,y+width/2)]]
        forward=v._clip(roof,lambda p:p[0]-1e-9)
        cone=v._clip(v._clip(forward,lambda p:wedge*p[0]-p[1]),lambda p:wedge*p[0]+p[1])
        blind=0.
        for p in cone:
            if p[2]<=0:continue
            dist=math.inf if p[2]>=height-1e-10 else height*p[0]/(height-p[2])-bow
            blind=max(blind,dist)
        vertices=[transform(xx,yy,zz) for xx,yy,zz in product((x-length/2,x+length/2),
            (y-width/2,y+width/2),(bottom,top))]
        intervals=v.range_blind_intervals(vertices,height,boundary)
        evaluated.append(dict(**o,blind_distance_m=blind,sectors=intervals))
    def reduce(groups):
        subset=[o for o in evaluated if o['group'] in groups]
        merged=v._merge([interval for o in subset for interval in o['sectors']])
        blind=max((o['blind_distance_m'] for o in subset),default=0.)
        maximum=max((hi-lo for lo,hi in merged),default=0.)
        total=sum(hi-lo for lo,hi in merged)
        clear=min((b[0]-a[1] for a,b in zip(merged,merged[1:])),default=math.pi)
        individual_fail=any(hi-lo>math.radians(5 if lo<math.radians(10) and hi>-math.radians(10) else 10)+1e-10 for lo,hi in merged)
        return dict(obstacle_count=len(subset),blind_distance_m=blind,limit_m=limit,
            max_blind_sector_rad=maximum,total_blind_sector_rad=total,min_clear_sector_rad=clear,
            geometric_rules_pass=bool(blind<=limit+1e-8 and not individual_fail and total<=math.radians(20)+1e-10 and clear>=math.radians(5)-1e-10),
            merged_sectors=merged,dominant_blind_obstacles=sorted(subset,key=lambda o:o['blind_distance_m'],reverse=True)[:5])
    groups={'all':{'decision_cargo','fixed_cargo','fixed_other','covers'},'decision_only':{'decision_cargo'},
        'fixed_cargo_only':{'fixed_cargo'},'fixed_and_covers':{'fixed_cargo','fixed_other','covers'}}
    return {name:reduce(names) for name,names in groups.items()}

def main():
    results=[];cohort=run.read(run.ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
    for task in run.read(run.HERE/'tasks.json'):
        job=next(j for j in cohort['maps'] if j['map_id']==task['map_id'])
        instance=load_instance(run.ROOT/job['input'])
        raw=PlannerResult.model_validate(run.read(run.ROOT/task['raw_result']))
        ship=apply_plan(instance,raw.plan).final_state.ship;obstacles=geometry(instance,ship)
        original=run.ROOT/'experiments/mvp/strategy/blockrule/runs/initial-medium-train/maps'/task['map_id']/'reports/default-raw.json'
        report=run.read(original);conditions={}
        for name,part in report['parts'].items():
            if not name.startswith('condition:') or not name.endswith(':visibility'):continue
            pose=part['metrics'];groups=at_pose(instance,obstacles,pose)
            for key in ('blind_distance_m','max_blind_sector_rad','total_blind_sector_rad','min_clear_sector_rad'):
                assert math.isclose(groups['all'][key],pose[key],rel_tol=1e-9,abs_tol=1e-9),(name,key,groups['all'][key],pose[key])
            conditions[name]=dict(pose={k:pose[k] for k in ('draught_aft_m','draught_fwd_m','list_rad')},groups=groups)
        results.append(dict(map_id=task['map_id'],report_sha256=run.sha(original),conditions=conditions,
            meaning='geometric attribution at stored checker pose, with every mass unchanged; not a ballast or alternate-plan certificate'))
    run.save(run.HERE/'visibility-attribution.json',results)
    for row in results:
        print(row['map_id'])
        for name,phase in row['conditions'].items():
            print(name,{k:(round(vv['blind_distance_m'],3),vv['geometric_rules_pass']) for k,vv in phase['groups'].items()})
if __name__=='__main__':main()
