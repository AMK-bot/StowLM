"""Fresh c5, finished once, followed by incumbent-preserving cover search.

The baseline arm is evaluated by the unchanged score.evaluate in this process.
No earlier plan, assignment or ballast is read. See COVER_NOTES.md.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.path[:0] = [str(HERE), str(LAB)]
EXTRA_PRIMAL_S = 240.


def _baseline():
    name = '_c6_frozen_c5_baseline'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, HERE/'baseline.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _save_baseline(row, instance, context):
    from score import write_json
    directory = Path(os.environ.get('COVER_RUN_OUT', str(HERE/'results/cover-development'))).resolve()
    if not directory.is_relative_to(HERE):
        raise ValueError('Output must remain inside c6/c6-lifts')
    path = context.get('instance_path')
    if path:
        row['input_sha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    row.update(complete=True, pairing='same-process fresh c5 checked incumbent', cold=True)
    write_json(directory/'baseline'/f'{instance.id}.json', row)


def _improve(instance, scored, *, problem, deadline, seed, metadata):
    from chainlab import condition_terms, motions
    from stowage.checker.full import departure_condition
    from stow import Assignment, Settings
    from pages_sight import Model
    from polish import polish
    from polish_wide import polish as wide_polish
    from cover_lns import polish as cover_polish
    model = Model(problem, oracle=None, deck_tiers=None)
    solver = Assignment(model, motions(condition_terms(instance, departure_condition(instance, scored.plan))), Settings())
    scored = polish(instance, scored, problem=problem, model=model, solver=solver,
                    deadline=min(deadline, monotonic()+30.), seed=seed, metadata=metadata)
    solver.motions = motions(condition_terms(instance, departure_condition(instance, scored.plan)))
    solver.row_cache = {}
    wider = {}
    scored = wide_polish(instance, scored, problem=problem, model=model, solver=solver,
                         deadline=min(deadline, monotonic()+30.), seed=seed, metadata=wider)
    metadata['wide_polish'] = wider.get('polish', wider)
    from stowage.checker import check
    from score import _parts
    pre = check(instance, scored.plan)
    metadata['pre_cover'] = dict(passed=pre.passed, lifts=float(pre.count.weighted_nonproductive),
                                 parts=_parts(pre.count))
    solver.motions = motions(condition_terms(instance, departure_condition(instance, scored.plan)))
    solver.row_cache = {}
    scored = cover_polish(instance, scored, problem=problem, model=model, solver=solver,
                          deadline=deadline, seed=seed, metadata=metadata)
    return scored


def propose(instance, *, seed, budget_s, context):
    from alns import Scored
    from score import evaluate, plugin_info
    from stowage.actions.space import Place
    from stowage.checker import check
    from stowage.model import MovePlan
    base = _baseline()
    # Preserve c5's exact two-try, 240/120 recipe. No rejected c6 proposer import.
    config = dict(seed=seed, budget_s=240., finalize_s=120., tries=2,
                  input=context.get('instance_path'), order=None,
                  method=plugin_info(f'{HERE / "baseline.py"}:propose'),
                  finish=plugin_info(f'{HERE / "baseline.py"}:finish'))
    baseline = evaluate(instance, {}, config, method=base.propose, finish=base.finish,
                        problem=context['problem'])
    baseline['config'] = config
    _save_baseline(baseline, instance, context)
    meta = context.setdefault('method_metadata', {})
    meta.update(cold=True, pairing='same-process fresh c5 checked incumbent',
                baseline={k: baseline.get(k) for k in ('passed', 'lifts', 'parts', 'wall_s', 'status')},
                baseline_method_metadata=baseline.get('method_metadata'),
                replay_legal=baseline.get('method_metadata', {}).get('replay_legal', False),
                loadavg_start=list(os.getloadavg()), extra_primal_budget_s=EXTRA_PRIMAL_S)
    if not baseline['passed']:
        meta['status'] = 'no_c5_full_pass_to_polish'
        return []
    plan = MovePlan.model_validate(baseline['plan'])
    original = Scored(tuple(Place(box=a['box'], slot=a['slot']) for a in baseline['actions']),
                      float(baseline['lifts']), plan)
    incumbent = original
    started = monotonic()
    try:
        candidate = _improve(instance, original, problem=context['problem'],
                             deadline=started+EXTRA_PRIMAL_S, seed=seed, metadata=meta)
        report = check(instance, candidate.plan)
        if report.passed and report.count.weighted_nonproductive <= original.objective:
            incumbent = Scored(candidate.actions, float(report.count.weighted_nonproductive), candidate.plan)
        else:
            meta['final_guard'] = 'original_checked_incumbent_retained'
    except Exception as error:
        meta['adapter_error'] = f'{type(error).__name__}: {error}'
    meta.update(extra_primal_wall_s=monotonic()-started, final_lifts=float(incumbent.objective),
                loadavg_end=list(os.getloadavg()))
    context['_cover_checked_plan'] = incumbent.plan
    return [incumbent.actions]


def finish(instance, plan, *, seed, finalize_s, label, context):
    # Concrete ballast is part of the retained incumbent, not replay defaults.
    return context['_cover_checked_plan'], dict(finisher='checked incumbent including ballast')
