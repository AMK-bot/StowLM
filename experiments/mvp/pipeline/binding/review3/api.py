"""Public policy additions without changing the measured non-hatch search."""
from __future__ import annotations

from time import perf_counter
from types import FunctionType

from .. import adapter
from ..confirmation import confirm_brief, ConfirmedBrief
from experiments.mvp.reader.schema import OnePortPerHatch

HATCH_CONFIRMATION = ('Each hatch cover panel may contain cargo for at most one discharge port, '
                      'across its hold and deck, including retained cargo. Port and starboard '
                      'panels of the same hatch may serve different ports.')
CORRECTION_SUFFIX = ' Correct the instruction or reduce cargo, then confirm the reading again.'


def confirmation_text(receipt):
    """Text for the host review screen, derived from the authenticated reading."""
    from experiments.mvp.reader import public_brief
    reading = public_brief._open(receipt)
    return [dict(sentence=row['sentence'], text=HATCH_CONFIRMATION
                 if ((row.get('parsed') or {}).get('constraint') or {}).get('type') == 'one_port_per_hatch'
                 else row['sentence'])
            for row in reading['sentences'] if row.get('binds')]


def refusal_kind(evidence):
    """Only necessary completion screens justify a full-load impossibility claim."""
    if evidence.get('status') != 'constraint_unavailable':
        return None
    if evidence.get('refusal_kind'):
        return evidence['refusal_kind']
    reason = str(evidence.get('reason') or evidence.get('error') or '')
    lower = reason.lower()
    if ('no infeasibility proof' in lower or 'unknown' in lower and 'allocation unavailable' in lower
            or 'timeout' in lower):
        return 'solver_timeout'
    if (not (evidence.get('engine_manifest') or {}).get('passed', True)
            or any(word in lower for word in ('pinned', 'manifest', 'stub', 'taint', 'source changed'))):
        return 'engine_identity'
    if (reason.startswith(('the load list needs ', 'no instruction-allowed physical footprint:',
                           'fixed retained cargo conflicts with instruction:'))
            or 'INFEASIBLE (necessary instruction-completion screen)' in reason):
        return 'proved_full_load'
    # Invalid geometry or unsupported application is an execution-domain
    # failure. It is never an arithmetic proof about a feasible cargo plan.
    return 'engine_identity'


def planner_message(evidence):
    """The runner should use this instead of an unconditional correction suffix."""
    reason = evidence.get('reason') or evidence.get('error') or 'The instruction could not be enforced.'
    if refusal_kind(evidence) == 'proved_full_load':
        return (f"With every box aboard this limit can't be met: {reason}. "
                "A partial plan wasn't tried." + CORRECTION_SUFFIX)
    if evidence.get('status') == 'no_checked_plan':
        return evidence.get('hatch_search_message') or (str(reason) + '; this does not prove no plan exists.')
    return str(reason)


def ladder_work(evidence):
    seam = (evidence.get('stages') or {}).get('peel_seam')
    if not seam:
        return dict(ladder_work='not_run', ladder_searched_levels=[])
    # Outer entries say a level was visited/classified. Only inner proposal
    # trials prove the assignment solver actually attempted work at that level.
    levels = []
    for row in seam.get('trials', []):
        if not isinstance(row, dict) or not isinstance(row.get('level'), int) or row['level'] <= 0:
            continue
        search = row.get('search') or {}
        trials = list((search.get('proposal') or {}).get('trials') or [])
        trials += list(((search.get('partial_search') or {}).get('proposal') or {}).get('trials') or [])
        if trials:
            levels.append(row['level'])
    levels = sorted(set(levels))
    return dict(ladder_work='searched' if levels else 'none', ladder_searched_levels=levels,
                ladder_note=('Only levels listed were searched; a returned level classifies the found candidate.'
                             if levels else 'No relaxed-level search trials. L1–L3 were not searched; '
                             'a relaxed level classifies a candidate found by the ordinary search.'))


def annotate(evidence, first_checked=None):
    evidence.setdefault('first_checked_wall_s', first_checked)
    evidence.update(ladder_work(evidence))
    evidence['refusal_kind'] = refusal_kind(evidence)
    evidence['correction_required'] = evidence['refusal_kind'] == 'proved_full_load'
    if evidence.get('status') in ('constraint_unavailable', 'no_checked_plan'):
        evidence['planner_message'] = planner_message(evidence)
    return evidence


def plan_with_rules(instance, answers, *, budget_s=None, finalize_s=None, seed=0,
                    config=None, on_incumbent=None):
    # Preserve validation of generators and of the public fixed signature.
    answers = adapter._validated_answers(instance, answers)
    if on_incumbent is not None and not callable(on_incumbent):
        raise TypeError('on_incumbent must be callable')
    began = perf_counter()
    first = None
    def observed(event):
        nonlocal first
        if event.get('passed') and first is None:
            first = event.get('wall_s', perf_counter()-began)
        if on_incumbent is not None:
            on_incumbent(event)
    kwargs = dict(budget_s=budget_s, finalize_s=finalize_s, seed=seed,
                  config=config, on_incumbent=observed)
    if any(isinstance(answer, OnePortPerHatch) for answer in answers):
        from .hatch import plan_hatch
        result, evidence = plan_hatch(instance, answers, **kwargs)
    else:
        # This exact immutable adapter remains the measured planning core.
        result, evidence = adapter.plan_with_rules(instance, answers, **kwargs)
    annotate(evidence, first)
    return result, evidence


def execute_brief(instance, receipt, *, budget_s=None, finalize_s=None, seed=0,
                  config=None, on_incumbent=None):
    # Clone only the executor's globals: retain the original HMAC, exact-input,
    # unresolved-sentence and original-checker gates. Never patch adapter globals.
    original = adapter.execute_brief
    executor = FunctionType(original.__code__, dict(original.__globals__, plan_with_rules=plan_with_rules),
                            original.__name__, original.__defaults__, original.__closure__)
    executor.__kwdefaults__ = original.__kwdefaults__
    result, evidence = executor(instance, receipt, budget_s=budget_s, finalize_s=finalize_s,
        seed=seed, config=config, on_incumbent=on_incumbent)
    evidence['confirmation_text'] = confirmation_text(receipt)
    return result, evidence
