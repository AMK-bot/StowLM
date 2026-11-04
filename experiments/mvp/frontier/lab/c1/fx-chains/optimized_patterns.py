"""Max-coverage pattern master with reusable partial incumbents.

Partial coverage is search evidence only: it is never returned as a stow or
passed to the objective/finisher. Only an exact cover that passes the unchanged
native validator is a candidate. No original-map infeasibility claim follows
from the bounded pattern pool or its conservative whole-column restrictions.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
from time import monotonic

from ortools.sat.python import cp_model

from pattern_model import Patterns


def _interleave_units(patterns):
    """Give both size families early sampling time before a deadline hits."""
    short = [i for i, (_, _, width) in enumerate(patterns.units) if width == 1]
    long = [i for i, (_, _, width) in enumerate(patterns.units) if width == 2]
    order = []
    for at in range(max(len(short), len(long))):
        if at < len(long):
            order.append(long[at])
        if at < len(short):
            order.append(short[at])
    patterns.units = [patterns.units[i] for i in order]
    patterns.allowed = [patterns.allowed[i] for i in order]


def _master(patterns, incumbent, rejected, lower_bound=0):
    """The empty selection is feasible before any full-cover rejection cuts."""
    model = cp_model.CpModel()
    chosen = [model.new_bool_var(f'p{i}') for i in range(len(patterns.patterns))]
    group_terms, stack_terms = defaultdict(list), defaultdict(list)
    cell_terms, heavy_deck_terms = defaultdict(list), defaultdict(set)
    T = patterns.T
    screen_e6 = bool(not patterns.freeze and T.e6_fallback and T.e6_threshold is not None)
    for i, pattern in enumerate(patterns.patterns):
        for g, count in pattern['counts'].items():
            group_terms[g].append(count*chosen[i])
        for j in pattern['stacks']:
            stack_terms[j].append(chosen[i])
        for g, slot in zip(pattern['sequence'], pattern['slots']):
            for c in T.slot_cells[slot]:
                if c >= 0:
                    cell_terms[int(c)].append(chosen[i])
            if screen_e6 and T.slot_deck[slot] and patterns.mass[g] >= T.e6_threshold:
                heavy_deck_terms[g].add(i)
    covered = []
    for g, demand in enumerate(patterns.demand):
        value = model.new_int_var(0, demand, f'covered{g}')
        model.add(value == sum(group_terms[g]))
        covered.append(value)
    for j in range(len(patterns.T.stack_ids)):
        model.add(sum(stack_terms[j]) <= 1)
    # A sufficient E6 blocking condition remains compatible with an empty
    # initial selection. Every selected heavy deck group's static hold
    # opportunity must have at least one occupied footprint cell. Full native
    # validation still owns the exact conditional departure rule.
    for g, indices in heavy_deck_terms.items():
        deck = model.new_bool_var(f'heavydeck{g}')
        model.add_max_equality(deck, [chosen[i] for i in sorted(indices)])
        b = patterns.rep[g]
        for s in range(len(T.slot_ids)):
            if T.slot_deck[s] or not T.static[b, s]:
                continue
            model.add(sum(v for c in T.slot_cells[s] if c >= 0
                          for v in cell_terms[int(c)]) >= deck)
    for indices in rejected:
        model.add(sum(chosen[i] for i in indices) <= len(indices)-1)
    if lower_bound:
        model.add(sum(covered) >= lower_bound)

    # Exact lexicographic objective: one more covered box beats every possible
    # secondary gain. The secondary score prefers hard-domain cargo, helping
    # the search avoid reserving all easy space before constrained groups.
    maximum = max(patterns.rarity, default=1.) or 1.
    rare_weight = [max(1, round(1000*r/maximum)) for r in patterns.rarity]
    secondary_span = sum(w*n for w, n in zip(rare_weight, patterns.demand))
    model.maximize((secondary_span+1)*sum(covered)
                   + sum(w*v for w, v in zip(rare_weight, covered)))
    selected_hint = set(incumbent)
    for i, variable in enumerate(chosen):
        model.add_hint(variable, int(i in selected_hint))
    return model, chosen, covered, secondary_span+1


class _Incumbents(cp_model.CpSolverSolutionCallback):
    def __init__(self, chosen, covered, total, started):
        super().__init__()
        self.chosen, self.covered = chosen, covered
        self.total, self.started = total, started
        self.best_count = -1
        self.best_counts = []
        self.best_selected = []
        self.trace = []

    def on_solution_callback(self):
        counts = [int(self.value(v)) for v in self.covered]
        count = sum(counts)
        if count >= self.best_count:
            self.best_count = count
            self.best_counts = counts
            self.best_selected = [i for i, v in enumerate(self.chosen) if self.boolean_value(v)]
        if not self.trace or count > self.trace[-1]['covered_boxes']:
            self.trace.append({'elapsed_s': monotonic()-self.started,
                               'covered_boxes': count})
        if count == self.total:
            self.stop_search()


def _missing(patterns, counts):
    return [{'group': g, 'missing': demand-(counts[g] if g < len(counts) else 0),
             'demand': demand, 'representative': patterns.groups[g][0],
             'length_ft': int(patterns.T.length[patterns.rep[g]]),
             'mass_upper_kg': patterns.mass[g], 'class_rank': patterns.rank[g]}
            for g, demand in enumerate(patterns.demand)
            if demand > (counts[g] if g < len(counts) else 0)]


def construct(instance, problem, seed, budget_s):
    """Return exact-cover native-legal assignments; retain partial diagnostics."""
    from chain_model import validate

    started = monotonic()
    deadline = started+max(0., budget_s)
    detail = {'model': 'lexicographic_max_coverage_sampled_patterns', 'attempts': [],
              'partial_coverage_is_not_a_stow_or_lift_score': True,
              'restrictions': ['homogeneous length columns', 'fixed long support pairs',
                               'group maximum mass', 'bounded sampled pattern pool',
                               'sufficient E6 occupancy blocking'],
              'omissions': ['physics and ballast', 'full voyage checker'],
              'total_boxes': len(problem.engine.T.box_ids), 'best_covered_boxes': 0}
    construct.last_diagnostics = detail
    if instance.onboard.placements or instance.discharge_list:
        detail['unsupported'] = 'nonempty arrival or discharges'
        return []
    if budget_s <= .1:
        detail['wall_s'] = monotonic()-started
        return []

    patterns = Patterns(problem, seed=seed, bucket_kg=2000)
    _interleave_units(patterns)
    detail.update(groups=len(patterns.groups), units=len(patterns.units))
    best_selected, best_counts, rejected = [], [0]*len(patterns.groups), []
    best_count = 0
    total = sum(patterns.demand)
    # Reuse indices because the pattern pool only grows. Small initial pools
    # offer cheap incumbents; later pools add alternatives around hard groups.
    for arm, samples in enumerate((12, 24, 48, 96, 160, 256)):
        remaining = deadline-monotonic()
        if remaining < .1:
            break
        generation_started = monotonic()
        generation_seconds = min(8., max(.05, remaining*.10))
        patterns.generate(samples, min(deadline, monotonic()+generation_seconds))
        generation_s = monotonic()-generation_started
        row = {'samples_per_unit': samples, 'patterns': len(patterns.patterns),
               'generation_s': generation_s, 'solutions': []}
        detail['attempts'].append(row)
        # Preserve some budget for pool expansion. A final round spends all
        # remaining search time on its larger pool.
        remaining = deadline-monotonic()
        solve_slice = remaining if arm == 5 else min(20., max(1., remaining*.30))
        arm_end = min(deadline, monotonic()+solve_slice)
        for repeat in range(6):
            if arm_end-monotonic() < .05:
                break
            model, selected, covered, objective_scale = _master(
                patterns, best_selected, rejected, lower_bound=best_count)
            seconds = arm_end-monotonic()
            if seconds < .05:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = seconds
            solver.parameters.random_seed = (seed+arm*31+repeat) % 2_147_483_647
            # Huge pools previously exhausted a whole slice in probing before
            # any incumbent. Bound preprocessing and supply an empty/partial
            # incumbent explicitly; this changes search, never acceptance.
            solver.parameters.max_presolve_iterations = 1
            solver.parameters.cp_model_probing_level = 0
            solver.parameters.symmetry_level = 0
            callback = _Incumbents(selected, covered, total, started)
            status = solver.solve(model, callback)
            item = {'status': solver.status_name(status), 'solve_s': solver.wall_time,
                    'branches': solver.num_branches, 'conflicts': solver.num_conflicts,
                    'objective_scale': objective_scale, 'trace': callback.trace,
                    'incumbent_covered_boxes': max(0, callback.best_count)}
            row['solutions'].append(item)
            if callback.best_count >= best_count:
                best_count = callback.best_count
                best_counts = callback.best_counts
                best_selected = callback.best_selected
                detail['best_covered_boxes'] = best_count
            detail['missing_groups'] = _missing(patterns, best_counts)
            if callback.best_count != total:
                # No exact cover exists in the found incumbent. Expand the
                # sampled pool; this is not an infeasibility certificate.
                break
            assignment = patterns.assignment(solver, selected)
            good, validation = validate(problem, assignment)
            item['validation'] = validation
            if good:
                detail['wall_s'] = monotonic()-started
                return [assignment]
            rejected.append(tuple(callback.best_selected))
    detail['wall_s'] = monotonic()-started
    return []


def propose(instance, *, seed, budget_s, context):
    """Shared scorer entry point; complete witnesses are saved before finishing."""
    from alns import Problem
    from sequence import order_legal

    here = Path(__file__).resolve().parent
    processes = here/'results'/'processes'
    processes.mkdir(parents=True, exist_ok=True)
    identity = {'pid': os.getpid(), 'parent_pid': os.getppid(),
                'instance_id': instance.id, 'budget_s': budget_s,
                'finalize_s': context.get('finalize_s'),
                'start_monotonic': monotonic(), 'method': 'optimized_patterns'}
    (processes/f'{os.getpid()}.json').write_text(json.dumps(identity))
    if (processes/f'stop-{os.getpid()}').exists():
        raise KeyboardInterrupt('cooperative stop requested for this recorded process')
    problem = context.get('problem') or Problem(instance)
    candidates = construct(instance, problem, seed, budget_s)
    metadata = context.setdefault('method_metadata', {})
    metadata['optimized_patterns'] = construct.last_diagnostics
    metadata['source_sha256'] = {name: hashlib.sha256((here/name).read_bytes()).hexdigest()
                               for name in ('optimized_patterns.py', 'pattern_model.py', 'chain_model.py')}
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
        tag = metadata['source_sha256']['optimized_patterns.py'][:12]
        target = folder/f'{instance.id}.optimized.s{seed}.b{budget_s:g}.{tag}.json'
        temporary = target.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(evidence, indent=2))
        temporary.replace(target)
        metadata['constructor_evidence'] = str(target)
    return out
