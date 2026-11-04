"""Staged, bounded hatch-mapping portfolio; never patches core module globals.

The authoritative aggregate allocator and release gate run through private
dependency namespaces. Panel pairing and demand balance rank mappings only;
neither preference is a hard instruction or a stow/checker certificate.
"""
from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import inspect
import math
from pathlib import Path
import re
from time import perf_counter
from types import FunctionType, SimpleNamespace

from ortools.sat.python import cp_model

from experiments.mvp.pipeline.binding import adapter, allocation
from experiments.mvp.pipeline.binding.admission import wait_for_capacity
from experiments.mvp.pipeline.binding.runtime import within_runtime_cache
from experiments.mvp.reader.constraints import InfeasibleInstruction, compile_one_port_per_hatch
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.apply import NotApplicable
from llm.instructions.schema import Refusal
from stowage.conventions import cells_of_slot, parse_slot

PORTFOLIO_POLICY_VERSION = 'logic3-hatch-portfolio-v1'
MAX_MAPPINGS = 3


def _identity_failure(evidence):
    return evidence.get('status') == 'constraint_unavailable' and (
        evidence.get('refusal_kind') == 'engine_identity'
        or evidence.get('engine_manifest', {}).get('passed') is False
        or evidence.get('engine_manifest_after', {}).get('passed') is False
        or any(word in evidence.get('reason', '').lower() for word in ('identity', 'manifest', 'source changed')))


def _clone(function, replacements):
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    result = FunctionType(function.__code__, namespace, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = dict(function.__kwdefaults__ or {})
    return result


def _preference_facts(instance):
    boxes = {box.id: box for box in instance.containers}
    retained = {p.container_id: p.slot for p in instance.onboard.placements
                if p.container_id not in set(instance.discharge_list)}
    loads = list(dict.fromkeys(bid for bid in instance.load_list if bid not in retained))
    active = [*loads, *retained]
    if not active:
        raise InfeasibleInstruction('no cargo from which to derive a hatch allocation')
    if set(active)-boxes.keys():
        raise InfeasibleInstruction('unknown cargo in hatch allocation')
    order = {str(call.locode): call.index for call in instance.rotation.calls}
    ports = sorted({str(boxes[bid].pod) for bid in active},
                   key=lambda pod: (order.get(pod, float('inf')), pod))
    covers = sorted(cover.id for cover in instance.vessel.hatch_covers)
    empty = instance.model_copy(update={'onboard': instance.onboard.model_copy(update={'placements': []})})
    geometry = compile_one_port_per_hatch(empty, {cover: ports[0] for cover in covers})
    capacities = Counter(geometry.cell_covers.values())
    for slot in retained.values():
        for cell in cells_of_slot(slot):
            if cell not in geometry.cell_covers:
                raise InfeasibleInstruction('retained cargo has unknown physical cells')
            capacities[geometry.cell_covers[cell]] -= 1
    groups = defaultdict(list)
    cells_by_cover = defaultdict(set)
    for cell, cover in geometry.cell_covers.items():
        if cover is not None:
            cells_by_cover[cover].add(parse_slot(cell).bay)
    for cover in instance.vessel.hatch_covers:
        # P/S panels covering the same declared physical bay set are paired.
        # Geometry supplies a fallback for minimal/public model fixtures.
        bays = tuple(sorted(set(getattr(cover, 'bays', ()) or cells_by_cover[cover.id])))
        if bays:
            groups[bays].append(cover.id)
    pairs = [(left, right) for panels in groups.values()
             for i, left in enumerate(sorted(panels)) for right in sorted(panels)[i+1:]]
    demand = Counter()
    for bid in loads:
        demand[str(boxes[bid].pod)] += 1 if boxes[bid].length_ft == 20 else 2
    return dict(ports=ports, covers=covers, capacities=capacities, pairs=pairs, demand=demand)


def _rank_model(model, facts, previous):
    selectors = model.hatch_selectors
    covers, ports, capacities = facts['covers'], facts['ports'], facts['capacities']
    expected = {(i, j) for i in range(len(covers)) for j in range(len(ports))}
    if set(selectors) != expected:
        raise RuntimeError('aggregate allocator selector contract changed; refusing an unranked mapping')
    choose = {(cover, pod): selectors[i, j] for i, cover in enumerate(covers)
              for j, pod in enumerate(ports)}
    for mapping in previous:
        model.Add(sum(choose[cover, mapping[cover]] for cover in covers) <= len(covers)-1)
    splits = []
    for index, (left, right) in enumerate(facts['pairs']):
        split = model.NewBoolVar(f'review3_panel_split_{index}')
        for pod in ports:
            model.Add(split >= choose[left, pod]-choose[right, pod])
            model.Add(split >= choose[right, pod]-choose[left, pod])
            model.Add(split <= 2-choose[left, pod]-choose[right, pod])
        splits.append(split)
    total_demand = sum(facts['demand'].values())
    total_capacity = sum(capacities[cover] for cover in covers)
    bound = max(0, total_demand*total_capacity)
    deviations = []
    if total_demand and total_capacity:
        for index, pod in enumerate(ports):
            assigned = sum(capacities[cover]*choose[cover, pod] for cover in covers)
            deviation = model.NewIntVar(0, bound, f'review3_capacity_deviation_{index}')
            model.AddAbsEquality(deviation,
                total_demand*assigned-facts['demand'][pod]*total_capacity)
            deviations.append(deviation)
    # Sum of L1 capacity deviations is at most 2*D*C. One avoided panel split
    # therefore dominates every possible improvement in the secondary score.
    split_weight = 2*bound+1
    model.Minimize(split_weight*sum(splits)+sum(deviations))
    return split_weight


def enumerate_hatch_mappings(instance, base=None, *, workers=1, seed=0, max_mappings=MAX_MAPPINGS):
    """Return ``(strict_mapping_rows, evidence)`` for at most three mappings.

    A row is ``(HatchInstructionConstraint, compilation_metadata)``. Native
    INFEASIBLE on the first aggregate model proves only full-load completion
    unavailable. An allocator timeout and failed finite engine searches never
    establish that the instruction has no plan.
    """
    began = perf_counter()
    if (workers != 1 or isinstance(max_mappings, bool) or not isinstance(max_mappings, int)
            or not 1 <= max_mappings <= MAX_MAPPINGS):
        raise ValueError('hatch enumeration uses one worker and one to three mappings')
    evidence = dict(portfolio_policy_version=PORTFOLIO_POLICY_VERSION,
        max_mappings=max_mappings, mappings_generated=0, mapping_solve_attempts=[],
        necessary_relaxation=True, checker_certificate=False, refusal_kind=None)
    rows, previous = [], []
    try:
        facts = _preference_facts(instance)
    except (InfeasibleInstruction, ValueError, KeyError) as error:
        evidence.update(status='constraint_unavailable', refusal_kind='engine_identity', reason=str(error),
                        failure_scope='invalid geometry or compiler contract',
                        wall_s=perf_counter()-began)
        return rows, evidence
    for index in range(max_mappings):
        state = {}
        class RankedModel(cp_model.CpModel):
            def __init__(self):
                super().__init__()
                self.hatch_selectors = {}
            def new_bool_var(self, name):
                # CpModel.__init__ installs the legacy NewBoolVar instance
                # alias from this snake-case method, so intercept its source.
                variable = super().new_bool_var(name)
                if match := re.fullmatch(r'cover_(\d+)_pod_(\d+)', name):
                    self.hatch_selectors[tuple(map(int, match.groups()))] = variable
                return variable
        class RankedSolver(cp_model.CpSolver):
            def Solve(self, model):
                state['split_weight'] = _rank_model(model, facts, previous)
                status = super().Solve(model)
                state['status'] = status
                state['status_name'] = self.StatusName(status)
                return status
        private_cp = SimpleNamespace(**dict(vars(cp_model), CpModel=RankedModel, CpSolver=RankedSolver))
        derive = _clone(allocation.derive_hatch_allocation,
                        dict(cp_model=private_cp, wait_for_capacity=wait_for_capacity))
        try:
            compiled, metadata = derive(instance, base=base, workers=1, seed=seed)
        except (InfeasibleInstruction, NotApplicable, RuntimeError, ValueError, KeyError) as error:
            status = state.get('status')
            if not rows and status == cp_model.INFEASIBLE:
                evidence.update(status='constraint_unavailable', refusal_kind='proved_full_load',
                    reason='the physical hatch-panel capacity model is infeasible',
                    partial_plan_tried=False)
            elif status == cp_model.INFEASIBLE:
                evidence.update(status='enumerated', stop_reason='all aggregate mappings enumerated')
            elif status == cp_model.UNKNOWN:
                evidence.update(status='enumerated' if rows else 'constraint_unavailable',
                    stop_reason='allocator time limit',
                    refusal_kind=None if rows else 'solver_timeout',
                    reason='Hatch allocation timed out; this does not prove that no plan exists.')
            else:
                evidence.update(status='constraint_unavailable', refusal_kind='engine_identity',
                                failure_scope='invalid geometry or compiler contract', reason=str(error))
            evidence['mapping_solve_attempts'].append(dict(index=index+1,
                status=state.get('status_name', 'compiler_unavailable'), error=str(error)))
            break
        mapping = dict(compiled.cover_ports)
        split_count = sum(mapping[left] != mapping[right] for left, right in facts['pairs'])
        assigned = Counter()
        for cover, pod in mapping.items():
            assigned[pod] += facts['capacities'][cover]
        D, C = sum(facts['demand'].values()), sum(facts['capacities'][cover] for cover in facts['covers'])
        imbalance = sum(abs(D*assigned[pod]-facts['demand'][pod]*C) for pod in facts['ports'])
        preference = dict(panel_pairs=[list(pair) for pair in facts['pairs']],
            split_panel_pairs=split_count, demand_teu=dict(facts['demand']),
            assigned_panel_cell_capacity=dict(assigned), capacity_ratio_deviation=imbalance,
            lexicographic_split_weight=state['split_weight'], objective_optimal=state['status'] == cp_model.OPTIMAL,
            scope='whole-hatch panel pairing preference first, load-TEU demand ratio second; not hard rules')
        metadata.update(portfolio_policy_version=PORTFOLIO_POLICY_VERSION,
                        mapping_index=index+1, preference=preference)
        rows.append((compiled, metadata))
        previous.append(mapping)
        evidence['mapping_solve_attempts'].append(dict(index=index+1,
            status=state['status_name'], cover_ports=mapping, preference=preference))
        evidence.update(status='enumerated', mappings_generated=len(rows))
    evidence['wall_s'] = perf_counter()-began
    return rows, evidence


def _trial(instance, answers, compiled, metadata, kwargs):
    original_compile = adapter.compile_constraint
    def compile_trial(inst, answer, *, workers=1, seed=0, base=None):
        if isinstance(answer, OnePortPerHatch):
            if inst is not instance:
                raise ValueError('hatch trial belongs to another instance')
            return compiled, dict(metadata)
        return original_compile(inst, answer, workers=workers, seed=seed, base=base)
    body = _clone(adapter.plan_with_rules.__wrapped__, dict(compile_constraint=compile_trial))
    return within_runtime_cache(body)(instance, answers, **kwargs)


def plan_hatch(instance, answers, **kwargs):
    """Try ranked strict mappings until a full pass; release a relaxed plan last."""
    began = perf_counter()
    inspect.signature(adapter.plan_with_rules).bind(instance, answers, **kwargs)
    answers = adapter._validated_answers(instance, answers)
    policy = dict(portfolio_policy_version=PORTFOLIO_POLICY_VERSION,
                  portfolio_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    if not any(isinstance(answer, OnePortPerHatch) for answer in answers) or any(
            isinstance(answer, Refusal) for answer in answers):
        result, evidence = adapter.plan_with_rules(instance, answers, **kwargs)
        evidence.update(policy, mappings_tried=0, per_trial_budgets=[])
        return result, evidence
    config = kwargs.get('config')
    if config is not None and not isinstance(config, adapter.Config):
        raise TypeError('config must be engine.config.Config')
    for value in (kwargs.get('budget_s'), kwargs.get('finalize_s')):
        if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value < 0):
            raise ValueError('budgets must be finite and nonnegative')
    callback = kwargs.get('on_incumbent')
    if callback is not None and not callable(callback):
        raise TypeError('on_incumbent must be callable')
    # Preserve the original source/provenance/refusal gates before any new CP
    # work. These paths require no alternative cover mapping.
    identity = adapter.verify_manifest()
    try:
        if not identity['passed'] or adapter.active_stubs():
            raise adapter.StubTaintError('native identity unavailable')
        adapter.require_clean(instance, what='hatch portfolio input')
        ordinary = [adapter.compile_constraint(instance, answer, workers=1,
                    seed=kwargs.get('seed', 0))[0] for answer in answers
                    if not isinstance(answer, OnePortPerHatch)]
    except (adapter.StubTaintError, InfeasibleInstruction, NotApplicable):
        result, evidence = adapter.plan_with_rules(instance, answers, **kwargs)
        evidence.update(policy, mappings_tried=0, per_trial_budgets=[])
        return result, evidence
    rows, enumeration = enumerate_hatch_mappings(instance, adapter.Intersection(instance, ordinary),
                                               seed=kwargs.get('seed', 0))
    if not rows:
        reason = enumeration.get('reason', 'Hatch allocation unavailable; no impossibility is established.')
        def unavailable(inst, answer, *, workers=1, seed=0, base=None):
            if isinstance(answer, OnePortPerHatch):
                raise InfeasibleInstruction(reason)
            return adapter.compile_constraint(inst, answer, workers=workers, seed=seed, base=base)
        body = _clone(adapter.plan_with_rules.__wrapped__, dict(compile_constraint=unavailable))
        result, evidence = within_runtime_cache(body)(instance, answers, **kwargs)
        failed_identity = _identity_failure(evidence)
        evidence.update(policy, hatch_enumeration=enumeration, mappings_tried=0,
                        per_trial_budgets=[], wall_s=perf_counter()-began)
        if failed_identity:
            evidence['refusal_kind'] = 'engine_identity'
        else:
            evidence.update(refusal_kind=enumeration['refusal_kind'], reason=reason)
        return result, evidence
    config = adapter.Config() if config is None else config
    trial_kwargs = dict(kwargs, config=replace(config, page_share=min(.15, config.page_share)))
    for name in ('budget_s', 'finalize_s'):
        if kwargs.get(name) is not None:
            trial_kwargs[name] = kwargs[name]/len(rows)
    budget_policy = dict(divisor=len(rows),
        explicit_budgets='requested extra and finalize budgets divided equally across enumerated mappings',
        defaults='None retains the native engine default independently for each attempted mapping',
        caveat='Native historical small-map floors and Config assignment/peel/finishing budgets remain '
               'additive per trial; these controls are not an overall wall deadline.')
    page_note = ('Medium page budget remains the pinned engine hardcoded 0.80 of extra budget; '
                 'Config.page_share does not control this lane. No private engine patch is applied.'
                 if len(instance.load_list) > 60 else
                 'Hatch trial Config.page_share is capped at 0.15; native historical small-map floors remain.')
    trials, best_relaxed, last = [], None, None
    published_best_lifts = math.inf
    def decorate(evidence):
        evidence.update(policy, hatch_enumeration=enumeration, mappings_tried=len(trials),
                        hatch_mapping_trials=list(trials), page_budget_note=page_note,
                        budget_s=kwargs.get('budget_s'), finalize_s=kwargs.get('finalize_s'),
                        mapping_budget_policy=budget_policy,
                        per_trial_budgets=[dict(index=row['index'], budget_s=row['budget_s'],
                            finalize_s=row['finalize_s']) for row in trials],
                        wall_s=perf_counter()-began)
        return evidence
    def finish(result, evidence):
        final_identity = adapter.verify_manifest()
        evidence['engine_manifest_after'] = final_identity
        if not adapter.same_identity(evidence['engine_manifest'], final_identity):
            evidence.update(status='constraint_unavailable', passed=False, plan_released=False,
                            refusal_kind='engine_identity',
                            reason='pinned engine/native identity changed during hatch portfolio')
            result = None
        return result, decorate(evidence)
    for index, (compiled, metadata) in enumerate(rows, 1):
        if callback is not None:
            def publish(event, *, _index=index, _mapping=dict(compiled.cover_ports)):
                nonlocal published_best_lifts
                lifts = event.get('lifts')
                if (event.get('passed') is not True or not isinstance(lifts, (int, float))
                        or not math.isfinite(lifts) or lifts >= published_best_lifts):
                    return
                published_best_lifts = lifts
                callback(dict(event, hatch_mapping_index=_index, hatch_mapping=dict(_mapping),
                    trial_wall_s=event.get('wall_s'), wall_s=perf_counter()-began, **policy))
            trial_kwargs['on_incumbent'] = publish
        result, evidence = _trial(instance, answers, compiled, metadata, trial_kwargs)
        trials.append(dict(index=index, cover_ports=dict(compiled.cover_ports),
            status=evidence.get('status'), passed=evidence.get('passed', False),
            lifts=evidence.get('lifts'), level=evidence.get('level'), broken_rules=evidence.get('broken_rules', []),
            wall_s=evidence.get('wall_s'), stage_attribution=evidence.get('stage_attribution'),
            lane=evidence.get('lane'), stages=evidence.get('stages', {}),
            budget_s=trial_kwargs.get('budget_s'), finalize_s=trial_kwargs.get('finalize_s')))
        last = result, evidence
        if _identity_failure(evidence):
            evidence['refusal_kind'] = 'engine_identity'
            return finish(None, evidence)
        if evidence.get('passed') is True and evidence.get('status') == 'checked_plan':
            return finish(result, evidence)
        if result is not None and evidence.get('status') == 'relaxed_plan':
            key = (evidence.get('level') if evidence.get('level') is not None else 99,
                   len(evidence.get('boxes_ashore', [])), len(evidence.get('broken_rules', [])),
                   evidence.get('lifts') if evidence.get('lifts') is not None else math.inf)
            if best_relaxed is None or key < best_relaxed[0]:
                best_relaxed = key, result, evidence, index
    note = f"no plan on the {len(trials)} hatch mappings tried; this doesn't prove none exists"
    if best_relaxed is not None:
        _, result, evidence, selected_index = best_relaxed
        evidence.update(hatch_search_note=note, hatch_search_message=note,
                        no_plan_claim_scope='full native checker pass',
                        selected_mapping_index=selected_index)
        return finish(result, evidence)
    result, evidence = last
    evidence.update(status='no_checked_plan', passed=False, plan_released=False,
                    reason=note, hatch_search_note=note, hatch_search_message=note)
    return finish(None, evidence)
