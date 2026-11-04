"""Independent plain-wrapper construction and optional exact restricted-master test on approved TRAIN seed210."""
from __future__ import annotations
import os
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
import hashlib
import importlib.util
import json
import random
import sys
from time import monotonic
sys.path[:0] = [str(ROOT/'src'),str(ROOT/'experiments/mvp/frontier'),str(HERE)]
from alns import Problem, Sequencer
from ortools.sat.python import cp_model
from stowage.io import load_instance
from stowage.moves import apply_plan


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    output = HERE/'results/target210.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    load('_target210_e9',ROOT/'experiments/mvp/frontier/lab/c1/leader/e9fix.py').install()
    method = load('_target210_method',HERE/'method.py')
    original = load('_target210_original',ROOT/'experiments/mvp/frontier/lab/c1/fx-scout/pages.py')
    path = ROOT/'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_1700/simulated_yard/seed-185100210.json'
    started = monotonic()
    instance = load_instance(path)
    problem = Problem(instance)
    tick = monotonic()
    model = method.Model(problem)
    row = dict(pid=os.getpid(),input=str(path.relative_to(ROOT)),instance_id=instance.id,
               n_boxes=len(model.boxes),model_init_s=monotonic()-tick,
               source_sha256={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest()
                              for name in ('pages.py','method.py')},stage='plain',complete=False)
    def write():
        row['wall_s'] = monotonic()-started
        output.write_text(json.dumps(row,indent=2)+'\n')
    write()
    print(json.dumps({'pid':os.getpid(),'stage':'plain','output':str(output)}),flush=True)
    root = {}
    generate = model.generate
    def capture(**kwargs):
        result = generate(**kwargs)
        if not root and kwargs.get('phase') == 1:
            root.update(result)
            root['pool_size'] = len(model.columns)
        return result
    model.generate = capture
    tick = monotonic()
    scored, metrics = method.one_stow(problem,model,seed=0,deadline=tick+120.)
    row['plain_s'] = monotonic()-tick
    row['plain'] = metrics
    row['root_pool_columns'] = root.get('pool_size',0)
    write()
    def record_stow(label, scored):
        replay_started = monotonic()
        replay = apply_plan(instance,scored.plan)
        loaded = {p.container_id for p in replay.final_state.ship.placements}
        row[label] = dict(replay_legal=bool(replay.legal),all_loaded=set(instance.load_list)<=loaded,
                          loaded_count=len(set(instance.load_list)&loaded),lifts=scored.objective,
                          replay_s=monotonic()-replay_started,
                          actions=[dict(box=a.box,slot=a.slot) for a in scored.actions],
                          plan=scored.plan.model_dump(mode='json'))
        write()
    if scored is not None:
        record_stow('plain_stow',scored)
        row.update(stage='done',complete=True)
        write()
        print(json.dumps({'plain':row['plain'],'wall_s':row['wall_s']}),flush=True)
        return
    row['stage'] = 'restricted_master'
    write()
    # The original per-column checks remain the admissibility gate. No checker edits.
    pool = []
    rejected = []
    for k in range(root.get('pool_size',0)):
        c = model.columns[k]
        page = model.pages[c.page]
        if original.Model.legal(model,page,c.boxes) and original.Model.lash_ok(model,page,c.boxes):
            pool.append(k)
        else:
            rejected.append(k)
    cp = cp_model.CpModel()
    variables = {k:cp.NewBoolVar(f'c{k}') for k in pool}
    cover = {b:[] for b in model.boxes}
    stacks = {s:[] for s in model.stacks}
    for k in pool:
        c = model.columns[k]
        for b in c.boxes:
            cover[b].append(variables[k])
        for stack in model.pages[c.page].stacks:
            stacks[stack].append(variables[k])
    for values in cover.values():
        cp.AddExactlyOne(values)
    for values in stacks.values():
        cp.AddAtMostOne(values)
    for k,value in root.get('x',{}).items():
        if k in variables and value>.95:
            cp.AddHint(variables[k],1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    tick = monotonic()
    status = solver.Solve(cp)
    row['restricted_master'] = dict(status=solver.StatusName(status),seconds=monotonic()-tick,
                                   pool_columns=len(pool),rejected_columns=rejected,
                                   conflicts=solver.NumConflicts(),branches=solver.NumBranches(),
                                   response=solver.ResponseStats())
    if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        chosen = [k for k in pool if solver.Value(variables[k])]
        row['restricted_master']['chosen'] = chosen
        tick = monotonic()
        chosen,heel = model.balance(chosen,random.Random(0))
        assignment = model.clear_oog(model.assignment(chosen))
        ordered = Sequencer(problem).order(assignment)
        scored = problem.score(ordered) if ordered is not None else None
        row['restricted_master'].update(postprocess_s=monotonic()-tick,heel_tm=heel,
                                       assignment=assignment,replay_complete=scored is not None)
        if scored is not None:
            record_stow('restricted_master_stow',scored)
    row.update(stage='done',complete=True)
    write()
    print(json.dumps({'plain':metrics,'restricted_master':{k:v for k,v in row['restricted_master'].items()
          if k not in ('assignment','chosen','response')},'wall_s':row['wall_s']}),flush=True)


if __name__=='__main__':
    main()
