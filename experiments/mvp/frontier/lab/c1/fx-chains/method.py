"""Shared scorecard entry point: joint chain placement with stored fallback.

Stored plans are explicit fallback candidates, never new constructor successes.
"""
from __future__ import annotations

from time import monotonic
import hashlib
import json
import os
from pathlib import Path

from alns import Problem, _mask_ok
from sequence import order_legal


def propose(instance, *, seed: int, budget_s: float, context: dict) -> list:
    import chain_model
    from stowage.actions import get_action_space
    from stowage.model import MovePlan

    started = monotonic()
    problem = context.get('problem') or Problem(instance)
    metadata = context.setdefault('method_metadata', {})
    here = Path(__file__).resolve().parent
    identity = dict(pid=os.getpid(), parent_pid=os.getppid(), instance_id=instance.id,
                    budget_s=budget_s, finalize_s=context.get('finalize_s'),
                    start_monotonic=started)
    processes = here / 'results' / 'processes'
    processes.mkdir(parents=True, exist_ok=True)
    (processes / f'{os.getpid()}.json').write_text(json.dumps(identity))
    # Cooperative cancellation between maps; never signals unrelated processes
    # or interrupts a finisher's own subprocess halfway through evaluation.
    if (processes / f'stop-{os.getpid()}').exists():
        raise KeyboardInterrupt('cooperative stop requested for this recorded process')
    metadata['source_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(here.glob('*.py'))}
    candidates = []
    if len(instance.load_list) > 60 and budget_s > 1:
        import pattern_model
        candidates = pattern_model.construct(instance, problem, seed, budget_s*.8)
        metadata['pattern_model'] = dict(pattern_model.construct.last_diagnostics)
    if not candidates:
        remaining = max(0., budget_s-(monotonic()-started))
        candidates = chain_model.construct(instance, problem, seed, remaining)
    metadata['chain_model'] = dict(chain_model.LAST_DIAGNOSTICS)
    out = []
    metadata['native_replay'] = []
    for assignment in candidates:
        if set(assignment) != set(instance.load_list):
            metadata['native_replay'].append({'reason': 'incomplete'})
            continue
        actions, stuck = order_legal(problem, assignment)
        legal = stuck is None and _mask_ok(problem, actions)
        metadata['native_replay'].append(dict(mask_legal=legal, stuck=stuck))
        if legal:
            out.append(actions)
    metadata['constructor_complete_mask_legal'] = len(out)
    if out:
        # The shared runner retains assignments only on passes. Keep every new
        # complete native witness here so finisher failures remain reviewable.
        evidence = dict(instance_id=instance.id, input=context.get('instance_path'),
                        seed=seed, budget_s=budget_s, metadata=metadata.copy(),
                        candidates=[[dict(box=a.box, slot=a.slot) for a in actions]
                                    for actions in out])
        folder = here / 'results' / 'candidates'
        folder.mkdir(parents=True, exist_ok=True)
        tag = hashlib.sha256(json.dumps(metadata['source_sha256'], sort_keys=True).encode()).hexdigest()[:12]
        target = folder / f'{instance.id}.s{seed}.b{budget_s:g}.{tag}.json'
        temporary = target.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(evidence, indent=2))
        temporary.replace(target)
        metadata['constructor_evidence'] = str(target)
    stored_plan = context.get('stored_plan')
    if stored_plan is not None:
        plan = MovePlan.model_validate(stored_plan) if isinstance(stored_plan, dict) else stored_plan
        out.append(tuple(get_action_space('boxslot').from_plan(instance, plan)))
        metadata['stored_fallback'] = True
    elif context.get('stored') is not None:
        actions, stuck = order_legal(problem, context['stored'])
        if stuck is None:
            out.append(actions)
            metadata['stored_fallback'] = True
    metadata['elapsed_s'] = monotonic() - started
    return out


def finish(instance, plan, *, seed, finalize_s, label, context):
    """V4 finalize; the shared runner performs the fresh full checker once.

    Frontier's default _finish already checks, then score.py checks again.
    This adapter retains exactly the same finisher and independent pass gate.
    """
    from stowage.model import PlannerResult
    from stowage.planner import finalize
    result = PlannerResult(status='ok', plan=plan, planner='fx-chains',
                           planner_version='1', seed=seed)
    previous = os.environ.get('STOWAGE_BALLAST_V4')
    os.environ['STOWAGE_BALLAST_V4'] = '1'
    started = monotonic()
    try:
        result = finalize(instance, result, time_limit_s=finalize_s)
    finally:
        if previous is None:
            os.environ.pop('STOWAGE_BALLAST_V4', None)
        else:
            os.environ['STOWAGE_BALLAST_V4'] = previous
    return result, dict(label=label, finalize_status=result.status,
                        finalize_s=monotonic()-started, message=result.message,
                        metrics=result.metrics, checker='shared runner fresh full check')
