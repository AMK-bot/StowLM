"""Observed rule-coverage yield, independent of publication admission.

These diagnostics never confer acceptance. A missing returned plan is unknown,
not a non-binding success. DG binding means a nonzero segregation obligation on
an actual co-carried pair; it does not mean a separation-distance margin is tight.
"""
from __future__ import annotations

from hashlib import sha256
from itertools import combinations
import json

BUCKETS = ('crossing_used', 'direct_delivery_present', 'kg_tight_stacks',
           'dg_segregation_binding')


def _value(status, count=None, reason=None, **details):
    return dict(status=status, count=count, reason=reason, **details)


def _unknown(reason):
    return _value('unknown', reason=reason)


def _replay(instance, plan):
    from stowage.moves import apply_plan
    return apply_plan(instance, plan)


def _model_digest(value):
    """Hash the same in-memory models observed below, without rereading a file."""
    if not hasattr(value, 'model_dump'):
        return None  # Test doubles; real worker inputs are strict model objects.
    payload = json.dumps(value.model_dump(mode='json'), sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
    return sha256(payload).hexdigest()


def _kg_tight(instance, ship):
    from stowage.conventions import cells_of_slot, parse_slot
    boxes = {box.id: box for box in instance.containers}
    physical = {}
    pairs = set()
    for placement in ship.placements:
        if placement.container_id not in boxes:
            continue
        for cell in cells_of_slot(placement.slot):
            slot = parse_slot(cell)
            physical[(slot.bay, slot.row, slot.tier)] = placement.container_id
    for (bay, row, tier), lower in physical.items():
        upper = physical.get((bay, row, tier + 2))
        if upper is None or upper == lower:
            continue
        a, b = boxes[lower], boxes[upper]
        if abs(a.gross_mass_kg - b.gross_mass_kg) <= 1000.:
            pairs.add((lower, upper))
    if pairs:
        return _value('present', len(pairs), pairs=[list(pair) for pair in sorted(pairs)[:16]],
                      witness_list_truncated=len(pairs)>16,
                      threshold_kg=1000., scope='explicit final-state adjacent physical tiers')
    # Fixed cargo is not silently treated as an empty unobserved stack.
    if any(getattr(load, 'cargo_boxes', None) for load in instance.vessel.fixed_loads):
        return _unknown('no explicit tight pair; fixed background cargo adjacency not observed')
    return _value('absent', 0, threshold_kg=1000., scope='final-state adjacent physical tiers')


def _dg_binding(instance, ship):
    from stowage.rules.imdg.tables import general_segregation
    boxes = {box.id: box for box in instance.containers}
    aboard = [boxes[p.container_id] for p in ship.placements
              if p.container_id in boxes and boxes[p.container_id].dg is not None]
    def entries(dg):
        yield dg
        for child in getattr(dg, 'additional', ()):
            yield from entries(child)

    demands, unresolved = [], []
    for a, b in combinations(aboard, 2):
        # A positive requirement is an observed constraint, unlike DG presence.
        # X and * explicitly require additional DGL/class-1 facts; never zero.
        entries_a, entries_b = list(entries(a.dg)), list(entries(b.dg))
        hazards_a = [hazard for entry in entries_a for hazard in (entry.imdg_class, *entry.subsidiary_risks)]
        hazards_b = [hazard for entry in entries_b for hazard in (entry.imdg_class, *entry.subsidiary_risks)]
        levels = []
        for left in hazards_a:
            for right in hazards_b:
                cell = general_segregation(left, right)
                if cell.level is not None and cell.level > 0:
                    levels.append(cell.level)
                else:
                    unresolved.append(dict(containers=[a.id, b.id], classes=[left, right], value=cell.value))
        if levels:
            demands.append(dict(containers=[a.id, b.id], level=max(levels)))
        elif any(entry.segregation_codes for entry in (*entries_a, *entries_b)):
            unresolved.append(dict(containers=[a.id, b.id], reason='conditional SG constraint needs dedicated witness'))
    if demands:
        return _value('present', len(demands), pairs=demands[:16], witness_list_truncated=len(demands)>16,
                      source='public IMDG general_segregation on actual final-state pairs',
                      definition='positive required separation, not minimum-distance slack',
                      unresolved_pairs=len(unresolved))
    if unresolved:
        return _unknown('actual DG pair requires DGL/class-1/SG interpretation; no numeric binding witness')
    if any(getattr(load, 'cargo_boxes', None) for load in instance.vessel.fixed_loads):
        return _unknown('fixed background DG pairing not observed')
    return _value('absent', 0, scope='actual explicit final-state DG pairs')


def observe(instance, plan=None):
    """Derive coverage from model inputs and a fresh public move-engine replay.

    Exceptions are coverage unknowns, never grounds to relax gold admission.
    No label from an instance's generator or candidate metadata counts as proof.
    """
    result = {key: _unknown('no returned plan') for key in BUCKETS}
    try:
        applicable = set(instance.load_list) | set(instance.discharge_list)
        direct = [box.id for box in instance.containers
                  if box.id in applicable and box.direct_delivery]
        result['direct_delivery_present'] = _value('present' if direct else 'absent', len(direct),
            containers=sorted(direct), source='current-call instance direct_delivery flags')
    except Exception as error:
        result['direct_delivery_present'] = _unknown(f'input observation failed: {type(error).__name__}: {error}')
    if plan is None:
        return result
    try:
        replay = _replay(instance, plan)
    except Exception as error:
        for key in ('crossing_used', 'kg_tight_stacks', 'dg_segregation_binding'):
            result[key] = _unknown(f'public move replay unavailable: {type(error).__name__}: {error}')
        return result
    try:
        crossing = replay.d62_crossing.model_dump()
        names = ('ship_lifts_at_or_above_h', 'yard_lifts_at_or_above_h',
                 'rejected_crossing_lifts', 'yard_h_blocking_rehandles')
        if (crossing.get('source') != 'migrated_move_engine' or crossing.get('status') != 'applicable'
                or any(type(crossing.get(name)) is not int or crossing[name] < 0 for name in names)):
            result['crossing_used'] = _unknown('move-engine crossing observations not applicable or malformed')
        else:
            count = sum(crossing[name] for name in names)
            result['crossing_used'] = _value('present' if count else 'absent', count,
                source='fresh public apply_plan().d62_crossing', observations=crossing,
                count_unit='domain observations; one lift may contribute multiple counters')
    except Exception as error:
        result['crossing_used'] = _unknown(f'crossing observations unavailable: {type(error).__name__}: {error}')
    if not replay.legal:
        for key in ('kg_tight_stacks', 'dg_segregation_binding'):
            result[key] = _unknown('illegal or incomplete replay; no certified final arrangement')
        return result
    for key, observer in [('kg_tight_stacks', _kg_tight), ('dg_segregation_binding', _dg_binding)]:
        try:
            result[key] = observer(instance, replay.final_state.ship)
        except Exception as error:
            result[key] = _unknown(f'final-state observation failed: {type(error).__name__}: {error}')
    # Digests describe exactly the in-memory snapshots used above. The caller
    # additionally retains the epoch's original input file SHA and plan envelope.
    try:
        binding = dict(input_model_sha256=_model_digest(instance), plan_model_sha256=_model_digest(plan))
        for value in result.values():
            value['binding'] = binding
    except Exception as error:
        for value in result.values():
            value['binding_error'] = f'{type(error).__name__}: {error}'
    return result


def summarize(rows, published_spec_keys=None, published_observations=None):
    """Count all terminal attempts; only actual admitted publications are gold."""
    result = {}
    for key in BUCKETS:
        present, absent, unknown, published = 0, 0, 0, 0
        published_unknown = 0
        published_seen = set()
        for row in rows:
            observation = (row.get('rule_binding') or {}).get(key) or {}
            status = observation.get('status', 'unknown')
            present += status == 'present'
            absent += status == 'absent'
            unknown += status not in ('present', 'absent')
            clean = (bool(row.get('accepted')) and not row.get('diagnostic')
                     and row.get('evidence_eligible') is not False)
            admitted = (row.get('spec_key') in published_spec_keys if published_spec_keys is not None
                        else row.get('spec_key') in published_observations if published_observations is not None
                        else True)
            if clean and admitted:
                # The final writer supplies observations from the exact bytes
                # it admitted. Mutable state metadata cannot alter that count.
                spec_key = row.get('spec_key')
                if published_spec_keys is not None or published_observations is not None:
                    if spec_key in published_seen:
                        continue
                    published_seen.add(spec_key)
                admitted_status = status
                if published_observations is not None:
                    admitted_status = ((published_observations.get(spec_key) or {}).get(key) or {}).get('status', 'unknown')
                published += admitted_status == 'present'
                published_unknown += admitted_status not in ('present', 'absent')
        result[key] = dict(attempted=len(rows), present_attempted=present, absent_attempted=absent,
            unknown_attempted=unknown, published=published, published_unknown=published_unknown,
            yield_per_present_attempt=published/present if present else None,
            yield_per_all_attempts=published/len(rows) if rows else None)
    return result
