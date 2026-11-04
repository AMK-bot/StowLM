"""Independent HiGHS MILP backend for sampled mixed-column pattern cover.

The objective maximizes covered boxes. Partial incumbents are diagnostics,
never stows or scores. Only full covers passing the existing native validator
reach the shared finisher/checker. No original-map infeasibility claim follows.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from mixed_patterns import MixedPatterns


def _interleave_units(patterns):
    queues = [[i for i, (_, _, width) in enumerate(patterns.units) if width == size]
              for size in (2, 1)]
    order = [queue[k] for k in range(max(map(len, queues), default=0))
             for queue in queues if k < len(queue)]
    patterns.units = [patterns.units[i] for i in order]
    patterns.allowed = [patterns.allowed[i] for i in order]


def _matrix(patterns, cuts):
    """Build sparse set packing plus conditional sufficient E6 occupancy."""
    T = patterns.T
    n_patterns = len(patterns.patterns)
    group_terms, stack_terms = defaultdict(list), defaultdict(list)
    cell_terms, heavy_deck = defaultdict(list), defaultdict(set)
    screen_e6 = bool(not patterns.freeze and T.e6_fallback and T.e6_threshold is not None)
    coverage = np.zeros(n_patterns, dtype=float)
    for i, pattern in enumerate(patterns.patterns):
        coverage[i] = sum(pattern['counts'].values())
        for g, count in pattern['counts'].items():
            group_terms[g].append((i, count))
        for j in pattern['stacks']:
            stack_terms[j].append((i, 1))
        for g, s in zip(pattern['sequence'], pattern['slots']):
            for c in T.slot_cells[s]:
                if c >= 0:
                    cell_terms[int(c)].append((i, 1))
            if screen_e6 and T.slot_deck[s] and patterns.mass[g] >= T.e6_threshold:
                heavy_deck[g].add(i)

    deck_variables = {g: n_patterns+k for k, g in enumerate(sorted(heavy_deck))}
    n_variables = n_patterns+len(deck_variables)
    rows, columns, values, lower, upper = [], [], [], [], []

    def add(terms, low=-math.inf, high=math.inf):
        row = len(lower)
        for index, coefficient in terms:
            rows.append(row)
            columns.append(index)
            values.append(float(coefficient))
        lower.append(float(low))
        upper.append(float(high))

    for g, demand in enumerate(patterns.demand):
        add(group_terms[g], high=demand)
    for j in range(len(T.stack_ids)):
        add(stack_terms[j], high=1)
    for g, variable in deck_variables.items():
        indices = sorted(heavy_deck[g])
        # deck_g is exactly the OR of selected patterns putting group g on
        # deck; every row remains satisfied by an empty pattern selection.
        for i in indices:
            add(((i, 1), (variable, -1)), high=0)
        add([(variable, 1)]+[(i, -1) for i in indices], high=0)
        b = patterns.rep[g]
        for s in range(len(T.slot_ids)):
            if T.slot_deck[s] or not T.static[b, s]:
                continue
            terms = [(variable, -1)]
            for c in T.slot_cells[s]:
                if c >= 0:
                    terms.extend(cell_terms[int(c)])
            add(terms, low=0)
    for indices in cuts:
        add(((i, 1) for i in indices), high=len(indices)-1)

    matrix = coo_matrix((np.asarray(values, dtype=float),
                         (np.asarray(rows, dtype=np.int32),
                          np.asarray(columns, dtype=np.int32))),
                        shape=(len(lower), n_variables)).tocsc()
    objective = np.zeros(n_variables, dtype=float)
    objective[:n_patterns] = -coverage
    return (objective, matrix, np.asarray(lower), np.asarray(upper),
            {'patterns': n_patterns, 'variables': n_variables, 'rows': len(lower),
             'nonzeros': matrix.nnz, 'e6_group_binaries': len(deck_variables)})


def _counts(patterns, indices):
    counts = [0]*len(patterns.groups)
    for i in indices:
        for g, count in patterns.patterns[i]['counts'].items():
            counts[g] += count
    return counts


def _missing(patterns, counts):
    return [{'group': g, 'missing': demand-counts[g], 'demand': demand,
             'representative': patterns.groups[g][0],
             'length_ft': int(patterns.T.length[patterns.rep[g]]),
             'mass_upper_kg': patterns.mass[g], 'class_rank': patterns.rank[g]}
            for g, demand in enumerate(patterns.demand) if counts[g] < demand]


class _Selection:
    """Adapt an integral MILP selection to the generator's assignment API."""
    def __init__(self, indices):
        self.indices = set(indices)

    def boolean_value(self, index):
        return index in self.indices


def construct(instance, problem, seed, budget_s):
    from chain_model import validate

    started = monotonic()
    deadline = started+max(0., budget_s)
    detail = {'model': 'highs_max_coverage_mixed_patterns', 'backend': 'scipy.optimize.milp/HiGHS',
              'attempts': [], 'total_boxes': len(problem.engine.T.box_ids),
              'best_covered_boxes': 0, 'partial_coverage_is_not_a_stow_or_lift_score': True,
              'warm_start': False,
              'restrictions': ['sampled mixed or homogeneous columns', 'fixed long support pairs',
                               'group maximum mass', 'sufficient E6 occupancy blocking'],
              'omissions': ['physics and ballast', 'full voyage checker'],
              'thread_policy': 'threads=1 forwarded by scipy to HiGHS; numerical environment one thread'}
    construct.last_diagnostics = detail
    if instance.onboard.placements or instance.discharge_list:
        detail['unsupported'] = 'nonempty arrival or discharges'
        return []
    if budget_s <= .1:
        detail['wall_s'] = monotonic()-started
        return []
    patterns = MixedPatterns(problem, seed=seed, bucket_kg=2000)
    _interleave_units(patterns)
    generation_started = monotonic()
    generation_budget = min(12., max(.05, budget_s*.22))
    patterns.generate(64, min(deadline, monotonic()+generation_budget))
    detail.update(groups=len(patterns.groups), units=len(patterns.units),
                  generation_s=monotonic()-generation_started,
                  mixed_patterns=sum(bool(p.get('mixed_deck')) for p in patterns.patterns))
    if not patterns.patterns:
        detail['wall_s'] = monotonic()-started
        return []
    total = sum(patterns.demand)
    cuts, best_count = [], 0
    for repeat in range(6):
        if deadline-monotonic() < .05:
            break
        build_started = monotonic()
        objective, matrix, low, high, shape = _matrix(patterns, cuts)
        seconds = deadline-monotonic()
        if seconds < .05:
            break
        # SciPy intentionally warns that these two native options are not in
        # its narrow documented options list, then forwards them unchanged.
        options = {'time_limit': seconds, 'mip_rel_gap': 0., 'presolve': True,
                   'threads': 1, 'random_seed': seed % 2_147_483_647}
        build_s = monotonic()-build_started
        solve_started = monotonic()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', RuntimeWarning)
            raw = milp(objective, integrality=np.ones(len(objective), dtype=np.uint8),
                       bounds=Bounds(np.zeros(len(objective)), np.ones(len(objective))),
                       constraints=LinearConstraint(matrix, low, high), options=options)
        row = dict(shape, status=int(raw.status), message=str(raw.message),
                   solve_s=monotonic()-solve_started, build_s=build_s,
                   success=bool(raw.success), incumbent=raw.x is not None,
                   warnings=[str(w.message) for w in caught])
        for name in ('mip_gap', 'mip_node_count', 'mip_dual_bound'):
            value = getattr(raw, name, None)
            if value is not None and np.isfinite(value):
                row[name] = float(value)
        if 'mip_dual_bound' in row:
            row['sampled_model_coverage_upper_bound'] = -row['mip_dual_bound']
        detail['attempts'].append(row)
        if raw.x is None:
            break
        vector = np.asarray(raw.x)
        rounded = np.rint(vector)
        if (not np.all(np.isfinite(vector)) or np.max(np.abs(vector-rounded), initial=0.) > 1e-5
                or np.any(rounded < 0) or np.any(rounded > 1)):
            row['incumbent_rejected'] = 'nonintegral or invalid MILP incumbent'
            break
        activity = matrix@rounded
        if np.any(activity < low-1e-6) or np.any(activity > high+1e-6):
            row['incumbent_rejected'] = 'rounded incumbent violates sampled master rows'
            break
        indices = np.flatnonzero(rounded[:len(patterns.patterns)] > .5).tolist()
        counts = _counts(patterns, indices)
        count = sum(counts)
        row['incumbent_covered_boxes'] = count
        if count >= best_count:
            best_count = count
            detail['best_covered_boxes'] = count
            detail['missing_groups'] = _missing(patterns, counts)
        if count != total or counts != patterns.demand:
            break
        assignment = patterns.assignment(_Selection(indices), range(len(patterns.patterns)))
        good, checked = validate(problem, assignment)
        row['validation'] = checked
        if good:
            detail['wall_s'] = monotonic()-started
            return [assignment]
        cuts.append(tuple(indices))
    detail['wall_s'] = monotonic()-started
    return []


def propose(instance, *, seed, budget_s, context):
    from alns import Problem
    from sequence import order_legal

    here = Path(__file__).resolve().parent
    processes = here/'results'/'processes'
    processes.mkdir(parents=True, exist_ok=True)
    identity = {'pid': os.getpid(), 'parent_pid': os.getppid(), 'instance_id': instance.id,
                'budget_s': budget_s, 'finalize_s': context.get('finalize_s'),
                'start_monotonic': monotonic(), 'method': 'highs_patterns'}
    (processes/f'{os.getpid()}.json').write_text(json.dumps(identity))
    if (processes/f'stop-{os.getpid()}').exists():
        raise KeyboardInterrupt('cooperative stop requested for this recorded process')
    problem = context.get('problem') or Problem(instance)
    candidates = construct(instance, problem, seed, budget_s)
    metadata = context.setdefault('method_metadata', {})
    metadata['highs_patterns'] = construct.last_diagnostics
    metadata['source_sha256'] = {name: hashlib.sha256((here/name).read_bytes()).hexdigest()
                               for name in ('highs_patterns.py', 'mixed_patterns.py',
                                            'pattern_model.py', 'chain_model.py')}
    out = []
    for assignment in candidates:
        actions, stuck = order_legal(problem, assignment)
        if stuck is None:
            out.append(actions)
    metadata['constructor_complete_mask_legal'] = len(out)
    if out:
        evidence = {'instance_id': instance.id, 'input': context.get('instance_path'),
                    'seed': seed, 'budget_s': budget_s, 'metadata': metadata,
                    'candidates': [[{'box': a.box, 'slot': a.slot} for a in actions]
                                   for actions in out]}
        folder = here/'results'/'candidates'
        folder.mkdir(parents=True, exist_ok=True)
        tag = metadata['source_sha256']['highs_patterns.py'][:12]
        target = folder/f'{instance.id}.highspatterns.s{seed}.b{budget_s:g}.{tag}.json'
        temporary = target.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(evidence, indent=2))
        temporary.replace(target)
        metadata['constructor_evidence'] = str(target)
    return out
