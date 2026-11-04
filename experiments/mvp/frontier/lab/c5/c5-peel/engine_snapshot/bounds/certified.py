"""Certified exact relaxation and independently replay-gated witness starts."""
from time import monotonic

from .exact.certified import CertifiedResult, lower_bound


def repair(problem, result, *, deadline, seed=0, greedy=True):
    """Try the certified model's first-aboard witness as a construction start.

    The lower bound allows arbitrary current moves. Its relaxation witness need
    not admit the returned rank order or any real plan. Every retained order
    separately passes native masks and the real Problem.score; full finishing
    and checking remain the caller's gate. No stored result is consulted.
    """
    from alns import Sequencer, _mask_ok
    from stowage.actions.space import Place
    if isinstance(result, dict) and result.get('certificate_source') != 'certified' and result.get('certified'):
        result = result['certified']
    assignment = (result.get('relaxation_assignment') if isinstance(result, dict)
                  else result.relaxation_assignment)
    order = (result.get('first_aboard_order') if isinstance(result, dict)
             else getattr(result, 'first_aboard_order', None))
    stats = dict(origin='certified_final_stow_witness', attempted=0,
                 replay_legal=0, mask_legal=0, greedy=False, full_checker_pass_claim=False)
    repair.last_stats = stats
    if (not assignment or set(assignment) != set(problem.instance.load_list)
            or problem.instance.onboard.placements or problem.instance.discharge_list):
        stats['unsupported'] = 'requires complete empty-arrival relaxation witness'
        return []
    attempts = []
    if order and len(order) == len(assignment) and set(order) == set(assignment):
        attempts.append(tuple(Place(box, assignment[box]) for box in order))
    ordered = Sequencer(problem).order(assignment)
    if ordered is not None and ordered not in attempts:
        attempts.append(tuple(ordered))
    out = []
    for actions in attempts:
        if monotonic() >= deadline:
            break
        stats['attempted'] += 1
        scored = problem.score(actions)
        if scored is None:
            continue
        stats['replay_legal'] += 1
        if _mask_ok(problem, actions):
            stats['mask_legal'] += 1
            out.append(actions)
    if greedy and not out and monotonic() < deadline:
        from .repair import repair_witness
        stats['greedy'] = True
        out.extend(repair_witness(problem, assignment, deadline=deadline, seed=seed))
    stats['candidates'] = len(out)
    return out
