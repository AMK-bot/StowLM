"""Read-only reductions of allowed train artifacts; no planner calls."""
from collections import Counter
import json
from pathlib import Path
import run

ROOT=run.ROOT; HERE=run.HERE
out=[]
for task in run.read(HERE/'tasks.json'):
    mid=task['map_id']; base=ROOT/'experiments/mvp/strategy/blockrule/runs/initial-medium-train/maps'/mid
    raw=run.read(base/'plans/default.json')
    entry=dict(map_id=mid,raw_result_sha256=run.sha(base/'plans/default.json'),variants={})
    for variant in ('raw','compact'):
        path=base/'reports'/f'default-{variant}.json'
        report=run.read(path); phases={}
        for name,part in report['parts'].items():
            if not name.startswith('condition:'):continue
            metrics=part.get('metrics',{})
            phases[name]=dict(rules=sorted({v['rule_id'] for v in part.get('violations',[])}),
                violations=len(part.get('violations',[])),error=part.get('error'),
                negative_margins={k:v for k,v in part.get('margins',{}).items() if v<0},
                metrics={k:v for k,v in metrics.items() if not k.startswith(('condition.','note:'))})
        entry['variants'][variant]=dict(path=str(path.relative_to(ROOT)),sha256=run.sha(path),
            aggregate_violation_count=len(report['violations']),passed=report['passed'],
            rules=run.summary(report)['rules'],conditions=phases)
    cohort=run.read(ROOT/'experiments/mvp/strategy/blockrule/cohort-medium.json')
    job=next(j for j in cohort['maps'] if j['map_id']==mid)
    instance=run.read(ROOT/job['input'])
    fixed=instance['vessel']['fixed_loads']
    # Fixed points are synthetic retained background, separate from move cargo.
    mass=sum(point['mass_kg'] for point in fixed)
    entry['fixed_loads']=dict(count=len(fixed),mass_kg=mass,
        moment_x_kg_m=sum(p['x_m']*p['mass_kg'] for p in fixed),
        moment_y_kg_m=sum(p['y_m']*p['mass_kg'] for p in fixed),
        moment_z_kg_m=sum(p['z_m']*p['mass_kg'] for p in fixed))
    cargo_path=HERE/'runs'/task['name']/'cargo.json'
    if cargo_path.exists():
        cargo=run.read(cargo_path);entry['move_cargo']={k:v for k,v in cargo.items() if k!='cargo'}
        total=mass+cargo['mass_kg']
        entry['cargo_plus_fixed']=dict(mass_kg=total,
            tcg_m=(entry['fixed_loads']['moment_y_kg_m']+cargo['transverse_moment_kg_m'])/total,
            lcg_m=(entry['fixed_loads']['moment_x_kg_m']+cargo['longitudinal_moment_kg_m'])/total,
            kg_m=(entry['fixed_loads']['moment_z_kg_m']+cargo['vertical_moment_kg_m'])/total)
    out.append(entry)
run.save(HERE/'baseline-checks.json',out)
for row in out:
    print(row['map_id'],row['variants']['raw']['rules'],row.get('cargo_plus_fixed'))
    for name,part in row['variants']['raw']['conditions'].items():
        if part['rules']:print(name,part['rules'],{k:v for k,v in part['metrics'].items() if k in ('gm_m','list_rad','max_fitting_ratio','max_corner_tension_ratio','blind_distance_m','limit_m','trim_m')})
