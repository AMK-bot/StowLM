"""Exhaustive native single-booking certificates on a small supported domain.

This is a proof about the unpeeled booking only, never a physical infeasibility
claim. Geometry enumeration deliberately ignores all legacy unary masks, in
particular unsupported empty-ship DG probes. Extra supported states weaken the
certificate and cannot remove a legal departure.
"""
from hashlib import sha256
from pathlib import Path
from time import monotonic


def certify(instance, *, tables=None, max_nodes=100000, max_supported_stows=10000,
            budget_s=600.):
    from stowage.checker.rules_special import special_violations
    from stowage.checker.perf import evaluation_cache
    from stowage.feasibility.tables import build_tables
    from stowage.frozen import active_stubs
    from stowage.model import Placement
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    import inspect
    import math

    if (not isinstance(max_nodes, int) or max_nodes < 1
            or not isinstance(max_supported_stows, int) or max_supported_stows < 1
            or not math.isfinite(budget_s) or budget_s <= 0):
        raise ValueError('positive finite enumeration limits required')
    started = monotonic()
    output = dict(status='unknown', complete=False, nodes=0, supported_stows=0,
                  full_checker_pass_claim=False)
    def finish(reason):
        output['reason'] = reason
        output['wall_time_s'] = monotonic()-started
        return output
    if (active_stubs() or rule_freeze_v1_enabled() or instance.onboard.placements
            or instance.discharge_list or instance.forecasts or instance.plan_calls
            or len(instance.load_list) > 6
            or len(instance.vessel.cells) > 16):
        return finish('outside_empty_arrival_tiny_domain')
    boxes = {b.id: b for b in instance.containers}
    ids = list(instance.load_list)
    targets = [bid for bid in ids if 'away_from_heat' in boxes[bid].special_stowage]
    if not targets:
        return finish('no_supported_single_rule_booking')
    T = tables if tables is not None else build_tables(instance)
    if T.instance is not instance:
        return finish('table_instance_identity_mismatch')
    cells = {c.id: c for c in instance.vessel.cells}
    options = {}
    for bid in ids:
        length = boxes[bid].length_ft
        choices = []
        for si, slot in enumerate(T.slot_ids):
            footprint = tuple(int(ci) for ci in T.slot_cells[si] if ci >= 0)
            if (len(footprint) == (1 if length == 20 else 2)
                    and all(getattr(cells[T.cell_ids[ci]], f'accepts_{length}') for ci in footprint)):
                choices.append((slot, footprint))
        options[bid] = choices
    violations = {bid: 0 for bid in targets}
    samples = {bid: {} for bid in targets}
    assignment = {}
    occupied = set()
    stopped = None
    def visit(depth):
        nonlocal stopped
        output['nodes'] += 1
        if output['nodes'] > max_nodes or monotonic()-started >= budget_s:
            stopped = 'enumeration_guard'
            return
        if depth < len(ids):
            bid = ids[depth]
            for slot, footprint in options[bid]:
                if occupied.intersection(footprint):
                    continue
                occupied.update(footprint)
                assignment[bid] = slot
                visit(depth+1)
                occupied.difference_update(footprint)
                del assignment[bid]
                if stopped:
                    return
            return
        # Every occupied column is a bottom prefix. Equal long-box support,
        # mass/height, OOG and all other rules remain relaxed, never restrictive.
        for stack in T.stack_cells:
            used = [i for i, ci in enumerate(stack) if ci in occupied]
            if used and used != list(range(max(used)+1)):
                return
        if output['supported_stows'] >= max_supported_stows:
            stopped = 'supported_stow_guard'
            return
        output['supported_stows'] += 1
        ship = instance.onboard.model_copy(update={'placements': [
            Placement(container_id=bid, slot=assignment[bid]) for bid in ids], 'covers_off': {}})
        try:
            report = special_violations(instance, ship, call_index=instance.call_index)
        except Exception:
            stopped = 'native_special_oracle_exception'
            return
        if any(v.severity == 'hard' and v.rule_id.startswith('checker.') for v in report):
            stopped = 'native_special_oracle_unknown'
            return
        for bid in targets:
            evidence = next((v for v in report if v.severity == 'hard'
                and v.rule_id == 'expert.e8.away_from_heat' and bid in v.containers), None)
            if evidence is not None:
                violations[bid] += 1
                samples[bid].setdefault(assignment[bid], dict(assignment=dict(assignment),
                    measured=evidence.measured, limit=evidence.limit, message=evidence.message))
    with evaluation_cache():
        visit(0)
    if stopped:
        return finish(stopped)
    output['complete'] = True
    output['violations_by_box'] = violations
    if not output['supported_stows']:
        return finish('no_supported_stows_no_single_rule_certificate')
    for bid in targets:
        if violations[bid] == output['supported_stows']:
            rule = 'expert.e8.away_from_heat'
            source = Path(inspect.getsourcefile(special_violations))
            output.update(status='certified_unary_infeasible', box=bid, rule=rule,
                tier='T3', certificate_source='exhaustive_supported_stows_native_E8',
                proof_scope='unpeeled E8 away_from_heat booking; every supported departure stow; no tier-peeling infeasibility claim',
                evidence=dict(native_source_sha256=sha256(source.read_bytes()).hexdigest(),
                    total_supported_stows=output['supported_stows'],
                    rejected_supported_stows=violations[bid], slot_examples=samples[bid],
                    legacy_unary_masks_used=False, arrival_empty=True,
                    optimistic_rules_ignored=['DG', 'mass', 'height', 'OOG', 'equal_long_support']))
            return finish('one_booking_violated_in_every_supported_stow')
    return finish('no_universal_single_rule_violation')
