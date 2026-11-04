"""Constructive follow-up to an OUTER relaxation; never an infeasibility proof.

Imports C2's frozen restricted page master unchanged. A second restricted
master minimizes deviations from per-bay relaxed deck-count targets. Its
columns still pass C2's own legality gates and the resulting plan is replayed
and fully checked. Restricted-pool failure means unknown.
"""
from __future__ import annotations

from collections import defaultdict
import importlib.util
from pathlib import Path
import sys
from time import monotonic

from ortools.sat.python import cp_model

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
for directory in (LAB.parent, LAB, LAB / 'c1' / 'leader'):
    sys.path.insert(0, str(directory))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def target_master(model, targets, *, deadline, seconds=4., mass_targets=None, moment_target=None):
    """Integer exact cover of the generated pool, soft deck-count targets."""
    started = monotonic()
    problem = cp_model.CpModel()
    box_rows, stack_rows, bay_rows = defaultdict(list), defaultdict(list), defaultdict(list)
    mass_rows = defaultdict(list)
    moment_terms = []
    use = {}
    for k, column in enumerate(model.columns):
        if k in model.banned:
            continue
        page = model.pages[column.page]
        v = problem.NewBoolVar(f'p{k}')
        use[k] = v
        for b in column.boxes:
            box_rows[b].append(v)
        for j in page.stacks:
            stack_rows[j].append(v)
        if page.deck:
            # A long box contributes half a count to each supporting bay.
            for j in page.stacks:
                bay = int(model.T.cell_ids[model.T.stack_cells[j][0]][:3])
                bay_rows[bay].append((v, int(2 * len(column.boxes) / len(page.stacks))))
                mass_rows[bay].append((v, round(sum(float(model.T.mass[b]) for b in column.boxes)
                                               /len(page.stacks)/100.)))
        floor = float(model.T.stack_base_z[page.stacks[0]])
        moment = 0.
        for b in column.boxes:
            height = float(model.T.height[b])
            moment += float(model.T.mass[b])*(floor+height/2)
            floor += height
        moment_terms.append(v*round(moment/1000.))
        problem.AddHint(v, int(k in (model.seed_chosen or ())))
    if any(not box_rows[b] for b in model.boxes):
        return None, dict(status='UNCOVERED_POOL', seconds=monotonic()-started)
    for b in model.boxes:
        problem.AddExactlyOne(box_rows[b])
    for vs in stack_rows.values():
        problem.AddAtMostOne(vs)
    errors = []
    for bay in sorted(set(bay_rows) | set(targets)):
        count = sum(v * coefficient for v, coefficient in bay_rows[bay])
        error = problem.NewIntVar(0, 2 * len(model.boxes), f'dev{bay}')
        problem.AddAbsEquality(error, count - round(2 * targets.get(bay, 0.)))
        errors.append(1000*error)
    if mass_targets:
        for bay in sorted(set(mass_rows) | set(mass_targets)):
            error = problem.NewIntVar(0, 10**7, f'massdev{bay}')
            problem.AddAbsEquality(error, sum(v*c for v,c in mass_rows[bay])
                                    -round(mass_targets.get(bay,0.)/100.))
            errors.append(error)
    if moment_target is not None:
        error = problem.NewIntVar(0, 10**9, 'momentdev')
        problem.AddAbsEquality(error, sum(moment_terms)-round(moment_target/1000.))
        errors.append(error)
    problem.Minimize(sum(errors))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = max(.001, min(seconds, deadline-monotonic()))
    status = solver.Solve(problem)
    record = dict(status=solver.StatusName(status), seconds=monotonic()-started,
                  pool_columns=len(use), target_counts=targets)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return None, record
    record['weighted_target_deviation'] = solver.ObjectiveValue()
    return [k for k, v in use.items() if solver.Value(v)], record


def run(instance, witness, *, budget_s=90., ballast_s=8., seed=0):
    """One targeted C2 construction and one ballast proposal, exact final gate."""
    from e9fix import install
    install()
    from alns import Problem
    from stowage.checker import check
    from stowage.moves import apply_plan

    started = monotonic()
    rmp = load_module('_feasproof_rmp', LAB / 'c2' / 'c2-cgscale' / 'rmp_method.py')
    inherited = rmp.Model
    targets = {int(b): float(n) for b, n in witness.get('deck_count_by_bay', {}).items()}
    history = []

    class TargetModel(inherited):
        def generate(self, **kwargs):
            root = super().generate(**kwargs)
            if (kwargs.get('phase', 1) == 1 and set(kwargs['active_boxes']) == set(self.boxes)
                    and set(kwargs['active_stacks']) == set(self.stacks)):
                chosen, record = target_master(self, targets, deadline=kwargs['deadline'])
                history.append(record)
                if chosen is not None:
                    self.seed_chosen = chosen
            return root

    # The target objective is heuristic. The outer model is never modified.
    problem = Problem(instance)
    model = TargetModel(problem)
    scored, construction = rmp.one_stow(problem, model, seed=seed,
                                        deadline=started+budget_s)
    out = dict(status='unknown', physical_proof=False, construction=construction,
               target_master=history, replay_complete=False,
               full_checker_passed=False, full_checker_calls=0)
    if scored is None:
        out['wall_s'] = monotonic()-started
        return out
    # The first integer dive supplies a valid hint, making soft-target
    # optimization useful even when the initial fractional pool had no seed.
    chosen, covered = [], set()
    for k in model._warm_indices:
        chosen.append(k)
        covered.update(model.columns[k].boxes)
        if covered == set(model.boxes):
            break
    if covered == set(model.boxes):
        model.seed_chosen = chosen
        picked, target_record = target_master(model, targets, deadline=started+budget_s,
            seconds=6., mass_targets={int(k):v for k,v in witness.get('deck_mass_by_bay',{}).items()},
            moment_target=witness.get('cargo_vertical_moment_lower_kg_m'))
        history.append(target_record)
        if picked is not None:
            import random
            from alns import Sequencer
            picked, _ = model.balance(picked, random.Random(seed))
            assignment = model.clear_oog(model.assignment(picked))
            ordered = Sequencer(problem).order(assignment)
            targeted = problem.score(ordered) if ordered is not None else None
            target_record['replay_complete'] = targeted is not None
            if targeted is not None:
                scored = targeted
    out.update(replay_complete=True, plan=scored.plan.model_dump(mode='json'),
               lifts_candidate=scored.objective)
    # Cheap local-ballast guidance comes from C2. Infeasible local LP is unknown.
    ballast = load_module('_feasproof_ballast', LAB / 'c2' / 'c2-benders' / 'ballast.py')
    result = ballast.solve_ballast(instance, scored.plan, budget_s=ballast_s,
                                   search_gm_floor=.6)
    candidate = result.pop('candidate_plan', None) or scored.plan
    out['ballast'] = result
    checked_at = monotonic()
    report = check(instance, candidate)
    out.update(full_checker_calls=1, full_checker_s=monotonic()-checked_at,
               full_checker_passed=report.passed,
               full_checker_rules=sorted({v.rule_id for v in report.violations if v.severity=='hard'}),
               full_checker_report=report.model_dump(mode='json'),
               plan=candidate.model_dump(mode='json'))
    if report.passed:
        replay = apply_plan(instance, candidate)
        assert replay.legal and report.count.loads == len(instance.load_list)
        out.update(status='feasible_with_witness', lifts=report.count.weighted_nonproductive)
    out['wall_s'] = monotonic()-started
    return out
