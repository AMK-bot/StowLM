# Copied from experiments/mvp/frontier/lab/c3/c2-hatch/lazy_bound.py; engine integration changes are local.
"""Valid final-state nogoods strengthen a reusable optimistic packing model."""
from __future__ import annotations
from dataclasses import asdict
import math
from time import monotonic
from stowage.model import Placement
from stowage.checker.rules import check_static_rules
from stowage.checker.rules_weight import compatible_empty_hold_slots
from stowage.conventions import cells_of_slot, parse_slot
from stowage.rules.catalogue import rule_freeze_v1_enabled


def _prefix_ids(instance, assignment, ids):
    cells = {c.id:c for c in instance.vessel.cells}
    levels = {}
    for bid in ids:
        slot = assignment[bid]
        for cid in cells_of_slot(slot):
            sid = cells[cid].stack_id
            levels[sid] = max(levels.get(sid, -1), parse_slot(slot).tier)
    return {bid for bid,slot in assignment.items()
            if any(cells[cid].stack_id in levels and parse_slot(slot).tier <= levels[cells[cid].stack_id]
                   for cid in cells_of_slot(slot))}


def _cut(context, assignment, ids):
    T, model, chosen = context['T'], context['model'], context['chosen']
    variables = [chosen[T.box_index[bid], T.slot_index[assignment[bid]]] for bid in ids]
    model.add(sum(variables) <= len(variables)-1)
    return len(variables)


def _e6_cut(context, assignment, ship, violation):
    """Ordinary heavy boxes, no lateral OOG: target-column context suffices.

    A same-column full placement pattern fixes all local placement rules and
    support. Other non-DG boxes cannot pair-block this non-DG witness. Target
    emptiness is retained explicitly; unmentioned ordinary cargo may move freely.
    """
    instance, T, chosen, model = (context[k] for k in ('instance','T','chosen','model'))
    boxes = {b.id:b for b in instance.containers}
    if rule_freeze_v1_enabled():
        return None
    for bid in violation.containers:
        box = boxes[bid]
        if box.dg is not None or box.oog is not None or box.special_stowage:
            continue
        for slot in compatible_empty_hold_slots(instance, ship, box, call_index=instance.call_index):
            target_cells = set(cells_of_slot(slot))
            stack_ids = {T.cell_stack[T.cell_index[cid]] for cid in target_cells}
            # Every lateral OOG envelope is invariant once its whole columns
            # are fixed. Close columns through spanning boxes so local checker
            # geometry cannot pick up a changing far-half support context.
            for other,other_slot in assignment.items():
                oog = boxes[other].oog
                if oog and any((oog.over_width_port_m,oog.over_width_starboard_m,
                                oog.over_length_fore_m,oog.over_length_aft_m)):
                    stack_ids.update(int(sj) for sj in T.slot_stacks[T.slot_index[other_slot]] if sj >= 0)
            while True:
                local_ids = {other for other,other_slot in assignment.items()
                             if any(int(sj) in stack_ids for sj in
                                    T.slot_stacks[T.slot_index[other_slot]] if sj >= 0)}
                expanded = set(stack_ids)
                for other in local_ids:
                    expanded.update(int(sj) for sj in T.slot_stacks[T.slot_index[assignment[other]]] if sj >= 0)
                if expanded == stack_ids:
                    break
                stack_ids = expanded
            # Fix ALL cargo in the target columns, plus forbid additional cargo
            # in currently empty column cells, so stack mass/height/class and
            # candidate support are precisely identical to the oracle witness.
            vars_on = [chosen[T.box_index[other],T.slot_index[assignment[other]]]
                       for other in local_ids]
            empty_column_cells = [ci for sj in stack_ids for ci in T.stack_cells[int(sj)]
                                  if not any(T.cell_ids[ci] in cells_of_slot(assignment[other])
                                             for other in local_ids)]
            empty_column_cells = list(set(empty_column_cells))
            deck_vars = [var for (b,si),var in chosen.items()
                         if b == T.box_index[bid] and T.slot_deck[si]]
            model.add(sum(deck_vars) + sum(vars_on)
                      <= len(vars_on) + sum(context['occupied'][ci] for ci in empty_column_cells))
            return len(vars_on) + len(empty_column_cells) + 1
    return None


def lower_bound(instance, *, budget_s=30., seed=0, tables=None, joint=True, max_iterations=10000, cost_cap=None, eager_oog=True):
    from . import bound_joint as implementation_joint
    from . import bound as implementation_plain
    implementation = implementation_joint if joint else implementation_plain
    started = monotonic()
    objective = instance.objective
    if cost_cap is not None:
        integral = all(math.isfinite(float(w)) and float(w)>=0 and float(w).is_integer() for w in (
            objective.relocate,objective.restow,objective.hatch_cover,
            objective.future_restow,objective.future_hatch_cover))
        if not integral or not math.isfinite(float(cost_cap)) or abs(float(cost_cap))>=2**53 or not float(cost_cap).is_integer():
            raise ValueError('integer threshold proof requires integer objective weights and cap')
    # Prove the inexpensive packing bound before constructing birth-order or
    # eager pair rows. A bound above the requested cutoff already settles it.
    plain_context = implementation_plain.build_model(instance,budget_s=budget_s,seed=seed,tables=tables)
    if not isinstance(plain_context,dict):
        return {'result':asdict(plain_context),'iterations':0,'cuts':[],'static_legal':False}
    plain_context['solve_budget_s'] = min(8.,max(.001,budget_s*.2))
    initial = implementation_plain.solve_model(plain_context)
    initial_bound = initial.lower_bound
    if cost_cap is not None and initial_bound>float(cost_cap)+1e-9:
        initial.note += f' Initial packing lower bound {initial_bound} already exceeds cutoff {cost_cap}; no joint model or lazy search is needed.'
        initial.status = 'initial_bound_above_cutoff'
        initial.wall_time_s = monotonic()-started
        return {'result':asdict(initial),'iterations':0,'cuts':[],
                'log':[{'status':'initial_bound_above_cutoff','bound':initial_bound}],
                'static_legal':False,'eager_oog_pairs':0,'initial_bound':initial_bound}
    remaining = budget_s-(monotonic()-started)
    if remaining<=.01:
        initial.wall_time_s = monotonic()-started
        return {'result':asdict(initial),'iterations':0,'cuts':[],
                'static_legal':False,'eager_oog_pairs':0,'initial_bound':initial_bound}
    context = (implementation_joint.build_model(instance,budget_s=remaining,seed=seed,tables=plain_context['T'])
               if joint else plain_context)
    if eager_oog:
        from .geometry_cuts import add_all_oog_pairs
        context['eager_oog_pairs'] = add_all_oog_pairs(context)
    if cost_cap is not None:
        base_cost = context['base'].relocations*int(objective.relocate)
        context['stop_after_first_solution'] = True
        context['model'].add(sum(context['terms'])+context['constant'] <=
                             (int(cost_cap)-int(base_cost))*context['scale'])
    best_bound = max(context['base'].weighted,initial_bound)
    cuts, log = [], []
    result = None
    static_legal = False
    for iteration in range(max_iterations):
        remaining = budget_s-(monotonic()-started)
        if remaining <= .01:
            break
        # A fresh solve does not retain learned clauses. Give an unknown
        # model the genuine remaining search time, never identical restarts.
        context['solve_budget_s'] = remaining
        result = implementation.solve_model(context)
        best_bound = max(best_bound,result.lower_bound)
        assignment = result.relaxation_assignment
        if assignment is None:
            log.append({'iteration':iteration,'status':result.status,'bound':result.lower_bound})
            if result.status == 'infeasible':
                if cost_cap is not None:
                    best_bound = max(best_bound,float(cost_cap)+1)
                    result.status = 'threshold_infeasible'
                    result.note += f' No relaxation solution costs at most {cost_cap}; integral objective proves at least {cost_cap+1}.'
                break
            continue
        ship = instance.onboard.model_copy(update={'placements':[
            Placement(container_id=bid,slot=slot) for bid,slot in assignment.items()], 'covers_off':{}})
        report = check_static_rules(instance,ship,call_index=instance.call_index)
        violations = [v for v in report.violations if v.severity == 'hard']
        if (not report.ran or report.error
                or any(v.rule_id.startswith('checker.') for v in violations)):
            log.append({'iteration': iteration, 'status': 'static_oracle_unknown',
                        'bound': result.lower_bound})
            break
        log.append({'iteration':iteration,'status':result.status,'bound':result.lower_bound,
                    'incumbent':result.relaxation_incumbent,
                    'violations':[v.rule_id for v in violations]})
        if not violations:
            from .securing_cuts import add_securing_cut
            securing_cut,securing = add_securing_cut(context,assignment,ship)
            log[-1]['securing_status'] = securing.get('status')
            if securing_cut is not None:
                cuts.append(dict(rule='iacs.member_irs.container_securing',**securing_cut))
                continue
            static_legal = True
            break
        added = False
        for violation in violations:
            ids = set(violation.containers) & set(assignment)
            if violation.rule_id == 'expert.e6.heavy_under_deck':
                size = _e6_cut(context,assignment,ship,violation)
                if size is not None:
                    cuts.append({'rule':violation.rule_id,'kind':'ordinary_e6_columns','size':size})
                    added = True
            elif violation.rule_id in {'expert.e8.away_from_heat','expert.e9.over_width_length'} and ids:
                from .geometry_cuts import add_heat, add_oog_pair
                success = (any(add_heat(context,assignment,bid) for bid in ids)
                           if violation.rule_id == 'expert.e8.away_from_heat'
                           else add_oog_pair(context,assignment,ids))
                if success:
                    cuts.append({'rule':violation.rule_id,'kind':'outward_floor_disjunction','size':len(ids)})
                    added = True
        if not added:
            size = _cut(context,assignment,set(assignment))
            cuts.append({'rule':violations[0].rule_id,'kind':'full_final_assignment','size':size})
    if result is None:
        result = implementation.solve_model(context)
        best_bound = max(best_bound,result.lower_bound)
    result.lower_bound = best_bound
    result.wall_time_s = monotonic()-started
    result.note += ' Valid final-state lazy cuts; does not certify move order or full checker.'
    return {'result':asdict(result),'iterations':len(log),'cuts':cuts,'log':log,'static_legal':static_legal, 'eager_oog_pairs':context.get('eager_oog_pairs',0), 'initial_bound':initial_bound}
