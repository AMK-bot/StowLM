"""Adapt bound checker reports for the demo's declared endpoint panels.

GM describes initial righting stiffness; GZ is the restoring lever at each heel.
The area under GZ measures resisting work (IMO MSC.267(85), B 2.3 / A 2.3).
The report alone decides compliance. Curves are sampled by the checker's own
geometry helpers, never inferred by the browser or used as a second verdict.
"""
from __future__ import annotations

from itertools import combinations
import re
from typing import Any

from stowage.checker import full
from stowage.conventions import cells_of_slot
from stowage.model import Instance, MovePlan
from stowage.moves import apply_plan
from stowage.versions import CHECKER_VERSION

from app import check_cache
from app.stability import _criteria, _gz_curve, stability_payload
from app.validation import report_validation


def _part_passed(part):
    return bool(part and part.ran and not part.error and
                not any(v.severity == 'hard' for v in part.violations))


def _notes(parts):
    return sorted({key.removeprefix('note: ').removeprefix('note:')
                   for part in parts if part is not None for key in part.metrics
                   if key.startswith('note:')})


def _strength_payload(instance, part):
    stations = []
    if part and instance.vessel.hull:
        for station in instance.vessel.hull.stations:
            key = format(station.x_m, '.12g')
            sf, bm = part.metrics.get('sf_n@'+key), part.metrics.get('bm_nm@'+key)
            if sf is not None and bm is not None:
                stations.append(dict(x_m=station.x_m, sf_n=sf, bm_nm=bm,
                    min_shear_n=station.min_shear_n, max_shear_n=station.max_shear_n,
                    min_bending_nm=station.min_bending_nm, max_bending_nm=station.max_bending_nm))
    return dict(ran=bool(part and part.ran), passed=_part_passed(part),
        error=part.error if part else 'No strength report',
        metrics=part.metrics if part else {}, margins=part.margins if part else {}, stations=stations)


def unbound_payload(evaluated_frame, reason=None):
    """No displayed checker evidence survives a failed complete-input binding.

    ``reason`` names why no report is available when that is not a binding failure (e.g. stale loaded code).
    """
    error = reason or 'unbound report'
    return dict(version=CHECKER_VERSION, passed=False, bound=False, binding_status=error, unavailable_reason=reason,
        scope='unavailable', validation_status='NOT VALIDATED', validation_reasons=[error], evaluated_frame=evaluated_frame, report=None, violations=[],
        conditions=[], notes=[], failed_parts=['report_binding'], port_stability=_port_stability(None),
        stability=dict(ran=False, passed=False, error=error, metrics={}, margins={},
            criteria=[], gz_curve=[], curve_error=None, criteria_set='Unavailable'),
        strength=dict(ran=False, passed=False, error=error, metrics={}, margins={}, stations=[]),
        dg=dict(ran=False, error=error, violations=[], pairs=[], cargo=[], areas=[]))


def _port_stability(report):
    """Render only explicit D64 cargo-operation evidence, never infer lift safety."""
    part = report.parts.get('port.stability.cargo_operations') if report else None
    available = bool(part and part.ran)
    metrics = part.metrics if available else {}
    lifts = {}
    # Until D64 freezes richer evidence, preserve all metrics and group explicit
    # per-lift scalar names. Unknown schemas stay visible as raw evidence.
    for key, value in metrics.items():
        match = re.fullmatch(r'(?:lift|frame)[.:](\d+)[.:](.+)', key)
        if match:
            lifts.setdefault(int(match[1]), {})[match[2]] = value
    k = next((metrics[key] for key in ('ballast_changeover_k', 'changeover_k', 'k') if key in metrics), None)
    return dict(available=available, passed=_part_passed(part) if available else None,
        message=('Checked cargo-operation evidence' if available else
                 'not yet checked during cargo operations (D64 pending)'),
        error=part.error if part else None, metrics=metrics, margins=part.margins if available else {},
        ballast_changeover_k=k, per_lift=[dict(index=i, metrics=lifts[i]) for i in sorted(lifts)],
        violations=[v.model_dump(mode='json') for v in part.violations] if available else [])


def _condition_payloads(instance, ship, report):
    """Display qualified endpoint reports; never mix aggregate endpoint margins."""
    grouped = {}
    for key, part in report.parts.items():
        if not key.startswith('condition:'):
            continue
        identity, name = key.removeprefix('condition:').rsplit(':', 1)
        if name in {'stability', 'strength', 'visibility', 'lashing', 'constraints'}:
            grouped.setdefault(identity, {})[name] = part
    identities = {}
    from stowage.model import VoyageSpec
    try:
        raw = instance.voyage
        leg = VoyageSpec.model_validate(raw.model_dump() if isinstance(raw, VoyageSpec) else raw) if raw is not None else None
    except (TypeError, ValueError, AttributeError):
        leg = None  # The full report retains the invalid-declaration diagnostic.
    if leg is not None:
        for identity in (leg.departure, leg.arrival):
            identities[identity.id] = identity
    contexts = {}
    arrival_error = None
    if leg is not None:
        contexts[leg.departure.id] = (instance, ship)
        try:
            from stowage.model import derive_arrival_liquid_state
            arrival = leg.arrival
            liquids = derive_arrival_liquid_state(ship, leg, vessel=instance.vessel)
            arrival_ship = ship.model_copy(update={'tank_masses_kg': dict(liquids.tank_masses_kg)})
            port = instance.port.model_copy(update={'locode': arrival.locode,
                'water_density_kg_m3': arrival.water_density_kg_m3,
                'max_draught_m': None, 'port_rules': []})
            context = instance.model_copy(update={'call_index': arrival.call_index,
                'port': port, 'onboard': arrival_ship})
            contexts[arrival.id] = (context, arrival_ship)
        except Exception as error:
            arrival_error = f'{type(error).__name__}: {error}'
    result = []
    for identity, parts in grouped.items():
        phase = next((key.removeprefix('condition.phase: ')
                      for part in parts.values() for key in part.metrics
                      if key.startswith('condition.phase: ')), None)
        if phase is None and identity in identities:
            phase = identities[identity].phase
        if phase == 'mid_voyage' and leg is not None:
            try:
                from stowage.model import derive_voyage_liquid_state
                fraction = next(part.metrics['condition.remaining_fraction']
                                for part in parts.values() if 'condition.remaining_fraction' in part.metrics)
                liquids = derive_voyage_liquid_state(ship, leg, fraction, vessel=instance.vessel)
                midpoint = ship.model_copy(update={'tank_masses_kg': dict(liquids.tank_masses_kg)})
                departure_identity = leg.departure
                port = instance.port.model_copy(update={'locode': departure_identity.locode,
                    'water_density_kg_m3': departure_identity.water_density_kg_m3,
                    'max_draught_m': None, 'port_rules': []})
                contexts[identity] = (instance.model_copy(update={
                    'call_index': departure_identity.call_index, 'port': port, 'onboard': midpoint}), midpoint)
            except Exception as error:
                arrival_error = f'{type(error).__name__}: {error}'
        context, state = contexts.get(identity, (instance, ship))
        stability = parts.get('stability')
        unavailable = phase != 'departure' and identity not in contexts
        shown_part = stability.model_copy(update={'ran': False}) if stability and unavailable else stability
        sp = stability_payload(context, state, shown_part)
        if unavailable and stability:
            sp['ran'] = stability.ran
            sp['gz_curve'] = []
            sp['curve_error'] = 'Condition reconstruction unavailable: ' + (arrival_error or 'no declared condition')
        sp['passed'] = _part_passed(stability)
        names = ['stability', 'strength', *(name for name in parts if name not in {'stability', 'strength'})]
        failed = [name for name in names if not _part_passed(parts.get(name))]
        result.append(dict(id=identity, phase=phase or 'unavailable', passed=not failed,
            failed_parts=failed, notes=_notes(parts.values()), stability=sp,
            strength=_strength_payload(context, parts.get('strength')),
            violations=[v.model_dump(mode='json') for part in parts.values() for v in part.violations]))
    phase_order = {'departure': 0, 'mid_voyage': 1, 'arrival': 2}
    return sorted(result, key=lambda c: (phase_order.get(c['phase'], 3), c['id']))


def build_checker_payload(instance: Instance, plan: MovePlan, *, evaluated_frame: int) -> dict[str, Any]:
    """Run one complete verdict, retaining failures and unavailable measurements.

    Qualified curves and criteria describe the reached departure with declared
    ballast and the checker's derived arrival. They do not certify intermediate
    crane states, worst intermediate consumable conditions or later sea legs.
    An illegal plan keeps its successful prefix and remains visibly failed.
    """
    # Exact disk memo of full.check (app/check_cache.py: same inputs + same checker source epoch -> the same
    # report); a retry after a binding mismatch always runs the checker fresh.
    # Sources or environment drifted from this process's loaded epoch: no report can be certified here.
    try:
        report = check_cache.cached_check(instance, plan)
    except check_cache.CheckerEpochDrift as drift:
        return unbound_payload(evaluated_frame, str(drift))
    if not full.report_matches_inputs(report, instance, plan):
        report = full.check(instance, plan)
    if not full.report_matches_inputs(report, instance, plan):
        return unbound_payload(evaluated_frame)
    violations = [v.model_dump(mode='json') for v in report.violations]
    replay = apply_plan(instance, plan)
    # Match full.check's reached-state and invalid-ballast semantics exactly.
    try:
        ship, _ = full._ballast(instance, plan, replay.final_state.ship)
    except Exception:
        # full.check already records invalid ballast; preserve its reached state.
        ship = replay.final_state.ship
    stability = report.parts.get('stability')
    strength = report.parts.get('strength')
    conditions = _condition_payloads(instance, ship, report)
    departure = next((condition for condition in conditions if condition['phase'] == 'departure'), None)
    if departure is not None:
        sp, strength_payload = departure['stability'], departure['strength']
    else:
        sp = stability_payload(instance, ship, stability)
        sp['passed'] = _part_passed(stability)
        strength_payload = _strength_payload(instance, strength)
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    cargo = []
    for placement in ship.placements:
        box = boxes.get(placement.container_id)
        if box is not None and box.dg is not None:
            areas = sorted({stacks[cells[c].stack_id].dg_doc_area_id for c in cells_of_slot(placement.slot)
                            if c in cells and stacks[cells[c].stack_id].dg_doc_area_id is not None})
            cargo.append(dict(container_id=box.id, slot=placement.slot, class_number=box.dg.imdg_class,
                              un_number=box.dg.un_number, doc_area_ids=areas))
    dg_ids = {c['container_id'] for c in cargo}
    dg_violations = [v for v in violations if v['rule_id'].startswith('imdg.') or
                     (v['rule_id'] == 'checker.missing_vessel_data' and dg_ids.intersection(v['containers']))]
    rules = report.parts.get('rules')
    from stowage.checker.rules_imdg import _hazards
    from stowage.rules.imdg.tables import general_segregation
    from stowage.rules.imdg.dgl import lookup_dgl
    pairs = []
    for a, b in combinations(cargo, 2):
        involved = {a['container_id'], b['container_id']}
        pair_violations = [v for v in dg_violations if involved <= set(v['containers'])]
        requirements, codes, unresolved_ids = [], set(), set()
        left = boxes[a['container_id']].dg
        right = boxes[b['container_id']].dg
        left_hazards, right_hazards = list(_hazards(left)), list(_hazards(right))
        for da in left_hazards:
            for db in right_hazards:
                for ca in [da.imdg_class, *da.subsidiary_risks]:
                    for cb in [db.imdg_class, *db.subsidiary_risks]:
                        try:
                            cell = general_segregation(ca, cb)
                            requirements.append(dict(classes=[ca, cb], value=cell.value, level=cell.level))
                        except KeyError:
                            requirements.append(dict(classes=[ca, cb], value='unavailable', level=None))
        for container_id, hazards in ((a['container_id'], left_hazards), (b['container_id'], right_hazards)):
            for hazard in hazards:
                codes.update(hazard.segregation_codes)
                try:
                    codes.update(lookup_dgl(int(hazard.un_number), hazard.packing_group).segregation_codes)
                except (KeyError, ValueError):
                    unresolved_ids.add(container_id)
        # The rules checker can report missing DGL/shipment facts against one
        # box. Those facts are also prerequisites for evaluating its pairs;
        # absence of a two-box violation must not turn them into a positive badge.
        individual_diagnostics = [v for v in dg_violations
            if len(v['containers']) == 1 and involved.intersection(v['containers']) and (
                v['rule_id'] == 'checker.missing_vessel_data' or
                v['rule_id'].startswith('imdg.segregation.') or
                (unresolved_ids.intersection(v['containers']) and v['rule_id'] == 'imdg.stowage.3_2'))]
        unavailable = (bool(unresolved_ids or individual_diagnostics) or
                       any(r['value'] == 'unavailable' for r in requirements) or
                       any(v['rule_id'] == 'checker.missing_vessel_data' for v in pair_violations))
        failed = any(v['rule_id'] != 'checker.missing_vessel_data' for v in pair_violations)
        status = ('unavailable' if not rules or not rules.ran or rules.error else
                  'failed' if failed else 'unavailable' if unavailable else 'passed')
        pairs.append(dict(containers=sorted(involved), slots=[a['slot'], b['slot']],
            violations=pair_violations + individual_diagnostics,
            general_table_requirements=requirements, segregation_codes=sorted(codes),
            requirement_note='IMDG 7.2.4 general table; DGL and SG provisions take precedence. Full checker evaluates geometry.',
            status=status))
    validation = report_validation(report, instance=instance, plan=plan)
    return dict(version=CHECKER_VERSION, passed=report.passed and validation['validated'], bound=True, binding_status='bound',
        validation_status=validation['status'], validation_reasons=validation['reasons'],
        scope='departure, mid-voyage, arrival' if conditions else 'departure; no declared arrival evidence',
        evaluated_frame=evaluated_frame, conditions=conditions, notes=_notes(report.parts.values()),
        port_stability=_port_stability(report),
        failed_parts=[name for name, part in report.parts.items()
                      if not name.startswith('condition:') and not _part_passed(part)],
        report=report.model_dump(mode='json'), violations=violations, stability=sp,
        strength=strength_payload,
        dg=dict(ran=bool(rules and rules.ran), error=rules.error if rules else 'No rules report',
                violations=dg_violations, pairs=pairs, cargo=cargo,
                areas=[a.model_dump(mode='json') for a in instance.vessel.dg_doc_areas]))
