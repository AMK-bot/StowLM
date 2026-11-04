"""Known checker-passing row witness and continuous phase-I violation diagnosis."""
import json, math, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent)
from ortools.linear_solver import pywraplp, linear_solver_pb2
from stowage.model import Instance, MovePlan
from stowage.moves import apply_plan
from stowage.solver.construct import _build_assignment_model
from stowage.solver.construct_physics import add_construct_physics

base = Path('experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard')
for size, seed in [('tiny',110280000),('small',110380001)]:
    start = time.monotonic()
    instance = Instance.model_validate_json((base/size/f'seed-{seed}.json').read_text())
    model = _build_assignment_model(instance, physics=False)
    model.physics = add_construct_physics(model.solver, instance, model.x, mode="conservative", securing_proxy=False)
    source = model.solver
    witness = []
    if size == 'tiny':
        plan = MovePlan.model_validate_json(Path('experiments/construct-1028/runs/scalable/saved-000/final.plan.json').read_text())
        replay = apply_plan(instance, plan)
        assert replay.legal
        selected = {p.container_id:p.slot for p in replay.final_state.ship.placements}
        values = {variable.index(): float(selected.get(cid)==slot) for (cid,slot),variable in model.x.items()}
        values.update({variable.index(): plan.ballast.tank_masses_kg.get(tid,0.) for tid,variable in model.physics.ballast_vars.items()})
        decision_mass = {cell: 0. for cell in model.physics.cargo_mass_vars}
        boxes = {box.id: box for box in instance.containers}
        from stowage.conventions import cells_of_slot
        for (cid, slot), variable in model.x.items():
            if values[variable.index()]:
                footprint = cells_of_slot(slot)
                for cell in footprint:
                    decision_mass[cell] += boxes[cid].gross_mass_kg/len(footprint)
        values.update({variable.index(): decision_mass[cell]
                       for cell, variable in model.physics.cargo_mass_vars.items()})
        for row in source.constraints():
            if not row.name().startswith('construct/'): continue
            activity = sum(row.GetCoefficient(variable)*values.get(variable.index(),0.) for variable in source.variables())
            violation = max(row.lb()-activity,activity-row.ub(),0.)
            if violation>1e-8:
                witness.append(dict(name=row.name(),activity=activity,lower=row.lb(),upper=row.ub(),violation=violation))
        witness.sort(key=lambda x:-x['violation'])
        Path(f'experiments/construct-1028/physics-ablation/{size}-source-known-plan-witness.json').write_text(json.dumps(witness,indent=2))
        print(json.dumps(dict(size=size,known_plan_row_violations=len(witness),top=witness[:10])),flush=True)
    proto = linear_solver_pb2.MPModelProto()
    source.ExportModelToProto(proto)
    for var in proto.variable: var.is_integer=False
    solver = pywraplp.Solver.CreateSolver('GLOP')
    error = solver.LoadModelFromProtoKeepNames(proto)
    assert not error,error
    objective = solver.Objective()
    objective.Clear()
    slacks = []
    for row in solver.constraints():
        if not row.name().startswith('construct/'): continue
        if math.isfinite(row.lb()):
            var=solver.NumVar(0.,solver.infinity(),f'slacklo@{row.name()}')
            row.SetCoefficient(var,1.)
            objective.SetCoefficient(var,1.)
            slacks.append((row.name(),'lower',var))
        if math.isfinite(row.ub()):
            var=solver.NumVar(0.,solver.infinity(),f'slackhi@{row.name()}')
            row.SetCoefficient(var,-1.)
            objective.SetCoefficient(var,1.)
            slacks.append((row.name(),'upper',var))
    objective.SetMinimization()
    solver.SetTimeLimit(15000)
    status=solver.Solve()
    positive=[dict(name=name,side=side,normalized_slack=var.solution_value()) for name,side,var in slacks if var.solution_value()>1e-8] if status in (solver.OPTIMAL,solver.FEASIBLE) else []
    positive.sort(key=lambda x:-x['normalized_slack'])
    result=dict(size=size,status=status,objective=objective.Value() if status in (0,1) else None,positive_slacks=positive,total_s=time.monotonic()-start)
    Path(f'experiments/construct-1028/physics-ablation/{size}-source-phase1.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(size=size,status=status,objective=result['objective'],top=positive[:12],total_s=result['total_s'])),flush=True)
