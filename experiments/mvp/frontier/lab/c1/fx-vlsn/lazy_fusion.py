"""Score the complete component family first; admit children best first.

Exactness is restricted to occupancy-component parent switches with the
canonical alns.Sequencer order, replay legality and feasibility-mask checks.
This module does not finish cargo plans or prove full-checker feasibility.
"""
from __future__ import annotations

from itertools import combinations
import math
import random
from time import monotonic

from alns import Sequencer, _mask_ok
from fast_score import FastScorer
from recombine import _assignment, _masks, _valid_assignment, components


def recombine(problem, parents, *, deadline, seed=0, keep=1):
    """Return cheapest verified, mask-legal children; default proves one best.

    Score every distinct whole child cheaply, then mask-check in ascending
    objective order. FastScorer returning None always invokes real score:
    its interface intentionally conflates unsupported input and rejection.
    Every returned fast-scored child is independently real-replayed and its
    numeric objective must match exactly, otherwise an AssertionError fires.

    ``last_stats['enumeration_complete']`` records full scoring coverage.
    ``best_proven`` requires that coverage plus best-first admission and real
    replay of the first feasible child before the deadline. ``complete`` also
    covers exhausting the family with no feasible child. An interrupted family
    may still produce useful checked children, but has no best certificate.
    Extra requested children cannot invalidate a best proof already obtained.
    Deadline is absolute monotonic time; indivisible calls can overrun.
    """
    if keep < 1:
        raise ValueError('keep must be positive')
    began = monotonic()
    stats = dict(pairs=[], family_size_sum=0, masks=0, unique_children=0,
                 duplicates=0, invalid_parents=0, no_order=0, replay_illegal=0,
                 fast=0, fallback=0, real_verifications=0, masks_checked=0,
                 mask_rejected=0, score_s=0., sequence_s=0., mask_s=0.,
                 verify_s=0., enumeration_complete=False, best_proven=False,
                 complete=False, deadline_hit=False, best=None, retained=[],
                 certificate_scope='canonical-order component family; replay and mask only')
    recombine.last_stats = stats
    normalized = []
    ranked = {}
    seen = set()
    returned = []

    def expired():
        if monotonic() >= deadline:
            stats['deadline_hit'] = True
            return True
        return False

    def finish():
        stats['wall_s'] = monotonic()-began
        stats['unique_children'] = len(seen)
        stats['scored_children'] = len(ranked)
        stats['retained'] = [cost for cost, _ in returned]
        return [actions for _, actions in returned]

    if expired():
        return finish()
    if problem.instance.onboard.placements or problem.instance.discharge_list:
        stats['unsupported'] = True
        return finish()
    for parent in parents:
        if expired():
            return finish()
        try:
            assignment, _ = _assignment(parent)
            if not _valid_assignment(problem, assignment):
                stats['invalid_parents'] += 1
                continue
            normalized.append(assignment)
        except (AttributeError, ValueError, TypeError, KeyError):
            stats['invalid_parents'] += 1
    if len(normalized) < 2:
        return finish()

    fast = FastScorer(problem)
    sequencer = Sequencer(problem)
    rng = random.Random(seed)
    enumeration_complete = True
    for left, right in combinations(normalized, 2):
        if expired():
            enumeration_complete = False
            break
        groups = components(problem, left, right)
        k = len(groups)
        row = dict(components=k, component_sizes=[len(group) for group in groups],
                   family_size=1 << k, masks=0, complete=False)
        stats['pairs'].append(row)
        stats['family_size_sum'] += 1 << k
        for mask in _masks(k, rng):
            if expired():
                enumeration_complete = False
                break
            child = dict(left)
            for index, group in enumerate(groups):
                if mask & (1 << index):
                    child.update((box, right[box]) for box in group)
            key = tuple(sorted(child.items()))
            stats['masks'] += 1
            row['masks'] += 1
            if key in seen:
                stats['duplicates'] += 1
                continue
            seen.add(key)
            started = monotonic()
            actions = sequencer.order(child)
            stats['sequence_s'] += monotonic()-started
            if expired():
                enumeration_complete = False
                break
            if actions is None:
                stats['no_order'] += 1
                continue
            started = monotonic()
            value = fast.score(actions)
            real_scored = None
            if value is None:
                stats['fallback'] += 1
                # Even a fast supported-domain None is not an exact illegal
                # certificate: only the real scorer may exclude this child.
                if expired():
                    stats['score_s'] += monotonic()-started
                    enumeration_complete = False
                    break
                real_scored = problem.score(actions)
                value = real_scored.objective if real_scored is not None else None
            else:
                stats['fast'] += 1
            stats['score_s'] += monotonic()-started
            if value is None:
                stats['replay_illegal'] += 1
            elif not math.isfinite(value):
                raise AssertionError('nonfinite fusion objective')
            else:
                ranked[key] = (value, tuple(actions), real_scored)
            if expired():
                enumeration_complete = False
                break
        row['complete'] = (enumeration_complete and row['masks'] == row['family_size'])
        if not enumeration_complete:
            break
    stats['enumeration_complete'] = enumeration_complete and not stats['invalid_parents']

    # No global cost is factored over components: only fully assembled and
    # sequenced children participate in this ordering.
    ordered_children = sorted(ranked.values(), key=lambda item: (item[0], tuple((a.box, a.slot) for a in item[1])))
    classified_all = True
    for value, actions, real_scored in ordered_children:
        if expired():
            classified_all = False
            break
        started = monotonic()
        legal = _mask_ok(problem, actions)
        stats['mask_s'] += monotonic()-started
        stats['masks_checked'] += 1
        if not legal:
            stats['mask_rejected'] += 1
        if expired():
            classified_all = False
            break
        if not legal:
            continue
        if real_scored is None:
            started = monotonic()
            real_scored = problem.score(actions)
            stats['verify_s'] += monotonic()-started
            stats['real_verifications'] += 1
        # A numeric fast score has an exactness contract, so any disagreement
        # invalidates the whole ranked family rather than being silently fixed.
        if real_scored is None or real_scored.objective != value:
            stats['score_mismatch'] = dict(fast=value,
                                          real=None if real_scored is None else real_scored.objective)
            raise AssertionError('fast fusion score disagrees with real replay')
        if expired():
            classified_all = False
            break
        returned.append((value, tuple(real_scored.actions)))
        if stats['best'] is None:
            stats['best'] = value
            stats['best_proven'] = stats['enumeration_complete']
        if len(returned) >= keep:
            classified_all = False
            break
    stats['complete'] = (stats['best_proven'] or
                         (stats['enumeration_complete'] and classified_all))
    return finish()


recombine.last_stats = {}
fuse = recombine
